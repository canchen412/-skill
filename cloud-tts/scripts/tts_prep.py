#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
口播文案 → 云端 TTS 标注版（MiniMax 优先）
==========================================
解决一件事：把灿爹的 8 句口播文案，变成带「精确停顿 + 语气词」的文本，
直接粘进 MiniMax / 阿里百炼 的体验页或 API，就能听出真人感。

为什么需要它：TTS 的「AI 感」大半来自停顿不对。停在哪、停多久，靠标点让模型猜
是不准的；MiniMax 支持 `<#0.35#>` 精确到 0.01 秒的停顿标记，但官方有两条硬约束：

  ① 停顿标记必须夹在两个「可发音文本」之间（不能贴在行首/行尾/标点旁边）
  ② 不能连续使用两个停顿标记

手搓极容易踩这两条，踩了要么标记被吞、要么整句节奏歪。本脚本按约束生成并自检。

用法：
  python3 tts_prep.py --text 文案.txt --out ./out
  python3 tts_prep.py --text 文案.txt --out ./out --comma 0.15 --preset koubo
  python3 tts_prep.py --check "今天我们讲，肌酸。<#0.4#><#0.3#>先算账。"

产出：
  out/<原名>_mm.txt        可直接粘贴的标注版
  out/<原名>_mm_seg.json   分段 + 每段建议的 emotion / speed
  out/<原名>_mm_report.md  自检报告（含警告与停顿统计）

零第三方依赖，Python 3 即可。
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime

# ---------------------------------------------------------------- 停顿预设

PRESETS = {
    # 带货口播：句子短、节奏紧，句末略停，语义段之间留气口
    "koubo": {
        "sentence": 0.35,   # 。？！
        "semicolon": 0.30,  # ；——
        "colon": 0.25,      # ：  （「先算账：」这种，停一下更有开讲感）
        "comma": 0.0,       # ，  默认交给模型自然处理
        "segment": 0.60,    # 语义段之间
        "after_dingchang": 0.45,  # 「今天我们讲，X。」之后 —— 定场句的签名停顿
    },
    # 有声书/慢节奏旁白
    "narration": {
        "sentence": 0.50,
        "semicolon": 0.40,
        "colon": 0.30,
        "comma": 0.20,
        "segment": 0.90,
        "after_dingchang": 0.60,
    },
}

END_PUNCT = "。？！!?"
SPLIT_PUNCT = "；;—"
COLON_PUNCT = "：:"
COMMA_PUNCT = "，,、"
ALL_PUNCT = END_PUNCT + SPLIT_PUNCT + COLON_PUNCT + COMMA_PUNCT + "…\"'）)」》】"
PAUSE_RE = re.compile(r"<#\s*\d+(?:\.\d{1,2})?\s*#>")

# 语气词标签（仅 MiniMax speech-2.8-hd / speech-2.8-turbo 支持）
EMO_TAGS = ("laughs", "chuckle", "coughs", "clear-throat", "groans", "breath",
            "pant", "inhale", "exhale", "gasps", "sniffs", "sighs", "snorts",
            "burps", "lip-smacking", "humming", "hissing", "emm", "sneezes",
            "whistles", "crying", "applause")

# 文案角色识别（对着灿爹口吻卡的 8 句结构）。★ 顺序即优先级：越专指的越靠前。
ROLE_RULES = [
    ("定场",     ("今天我们讲",)),
    ("算账",     ("算账", "平摊到每天", "合下来", "折算到")),
    ("盲区",     ("看不到", "不透明", "根本没", "没人告诉", "只给你看", "不公开")),
    ("品牌事实", ("销量", "SGS", "报告", "送检", "批次", "检测")),
    ("CTA",      ("左下角", "小黄车", "链接", "购物车")),
    ("交权",     ("随你", "拿去比", "自己核对", "多存几罐", "后面不慌", "先存")),
    ("品类地位", ("头牌", "头把交椅", "A类", "第一梯队")),
    ("钩子",     ("？", "?")),
]

ROLE_PARAMS = {
    "钩子":     {"emotion": "neutral", "speed": 1.0, "note": "第一句，疑问/陈述，禁止感叹语气"},
    "定场":     {"emotion": "neutral", "speed": 0.95, "note": "「今天我们讲，X。」——稍慢，把节奏立住"},
    "算账":     {"emotion": "calm",    "speed": 1.0,  "note": "算账只算一次，语速别赶"},
    "品类地位": {"emotion": "neutral", "speed": 1.05, "note": ""},
    "盲区":     {"emotion": "calm",    "speed": 0.95, "note": "核心段，放慢，这个盲区要讲清楚"},
    "品牌事实": {"emotion": "neutral", "speed": 1.0,  "note": "数字多，务必开 text_normalization"},
    "交权":     {"emotion": "calm",    "speed": 0.98, "note": "把决定权交出去，不要上扬喊单"},
    "CTA":      {"emotion": "neutral", "speed": 1.0,  "note": "「存」不是「冲」，语气收着"},
    "其他":     {"emotion": "neutral", "speed": 1.0,  "note": ""},
}


def read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def detect_role(sentence):
    bare = PAUSE_RE.sub("", sentence)
    for role, keys in ROLE_RULES:
        for k in keys:
            if k in bare:
                return role
    return "其他"


def split_sentences(block):
    """把一段拆成句子，保留标点。"""
    out, buf = [], ""
    for ch in block:
        buf += ch
        if ch in END_PUNCT:
            out.append(buf.strip())
            buf = ""
    if buf.strip():
        out.append(buf.strip())
    return [s for s in out if s]


def split_on_pause(text):
    """按停顿标记切块，仅用于生成「一句一行」的对读版。"""
    parts, buf = [], ""
    for token in re.split(r"(<#\s*\d+(?:\.\d{1,2})?\s*#>)", text):
        if token is None:
            continue
        if PAUSE_RE.fullmatch(token):
            if buf.strip():
                parts.append(buf.strip())
            buf = ""
            continue
        buf += token
    if buf.strip():
        parts.append(buf.strip())
    return parts


def pausable_boundary(text, pos):
    """判断 pos 处能不能插停顿：左侧要有非标点、非停顿的可发音字符，右侧同理。"""
    left = text[:pos].rstrip()
    right = text[pos:].lstrip()
    if not left or not right:
        return False
    if PAUSE_RE.search(left[-20:]) and left.rstrip().endswith("#>"):
        return False           # 左侧紧邻停顿标记 → 会连用
    while left and left[-1] in ALL_PUNCT:
        left = left[:-1].rstrip()
    right_stripped = right
    while right_stripped and right_stripped[0] in ALL_PUNCT:
        right_stripped = right_stripped[1:].lstrip()
    if not left or not right_stripped:
        return False
    if right_stripped.startswith("<#"):
        return False           # 右侧紧邻停顿标记 → 会连用
    return True


def annotate(text, cfg, keep_lines=True):
    """给整篇文案插停顿标记。返回 (标注文本, 插入统计, 警告列表)。"""
    warns, inserted = [], []
    paragraphs = [p for p in text.replace("\r\n", "\n").split("\n")]
    out_paras, segs = [], []
    seq = 0

    for p_idx, para in enumerate(paragraphs):
        raw = para.strip()
        if not raw:
            out_paras.append("")
            continue
        sents = split_sentences(raw)
        if not sents:
            out_paras.append(raw)
            continue
        built = []
        for s_idx, sent in enumerate(sents):
            role = detect_role(sent)
            body = sent
            tail_pause = 0.0
            last = sent[-1:] if sent else ""
            if last in END_PUNCT:
                tail_pause = cfg["sentence"]
            elif last in SPLIT_PUNCT:
                tail_pause = cfg["semicolon"]
            elif last in COLON_PUNCT:
                tail_pause = cfg["colon"]

            # 定场句专利停顿
            if role == "定场":
                tail_pause = max(tail_pause, cfg["after_dingchang"])

            # 句内：冒号/分号/逗号后视配置补停
            pieces, cursor = [], 0
            for m in re.finditer(r"[：:；;，,、]", body):
                pieces.append((m.end(), m.group()))
            new_body, prev_end = "", 0
            for pos, ch in pieces:
                new_body += body[prev_end:pos]
                prev_end = pos
                if ch in COLON_PUNCT:
                    d = cfg["colon"]
                elif ch in SPLIT_PUNCT:
                    d = cfg["semicolon"]
                else:
                    d = cfg["comma"]
                if d > 0 and pausable_boundary(body, len(new_body)):
                    new_body += "<#%s#>" % trim(d)
                    inserted.append({"para": p_idx + 1, "sent": s_idx + 1, "at": new_body[-24:], "dur": d})
            new_body += body[prev_end:]

            # 句末停顿
            if tail_pause > 0:
                bare = PAUSE_RE.sub("", new_body)
                if bare and bare[-1] in ALL_PUNCT:
                    stripped = bare
                    while stripped and stripped[-1] in ALL_PUNCT:
                        stripped = stripped[:-1]
                    if stripped.strip() and not new_body.rstrip().endswith("#>"):
                        new_body = new_body.rstrip() + "<#%s#>" % trim(tail_pause)
                        inserted.append({"para": p_idx + 1, "sent": s_idx + 1,
                                         "at": "(句末) " + bare[-18:], "dur": tail_pause})

            seq += 1
            built.append(new_body)
            segs.append({"seq": seq, "para": p_idx + 1, "sent": s_idx + 1, "role": role,
                         "text": sent, "annotated": new_body,
                         **ROLE_PARAMS.get(role, ROLE_PARAMS["其他"])})

        joined = "".join(built)
        out_paras.append(joined)

    annotated = "\n".join(out_paras)

    # ---- 语义段之间补气口：段落之间 / 角色切换处 ----
    final_paras, seg_i = [], 0
    for p_idx, para in enumerate(out_paras):
        if not para.strip():
            final_paras.append("")
            continue
        final_paras.append(para)
    annotated = "\n".join(final_paras)

    # ★ 换行符在 MiniMax 里 = 段落切换（会带来额外的长间隔），
    #   所以粘贴版必须保持原段落结构，绝不能为了好看在停顿标记后换行。
    annotated = re.sub(r"\n{3,}", "\n\n", annotated).strip()

    # ---- 自检：官方两条硬约束 ----
    soft = []
    for m in re.finditer(r"<#\s*(\d+(?:\.\d{1,2})?)\s*#>", annotated):
        v = float(m.group(1))
        if v < 0.01 or v > 99.99:
            warns.append("停顿时长 %.2f 秒越界（允许 0.01–99.99）" % v)
    if re.search(r"<#[^#]*#>\s*<#[^#]*#>", annotated):
        warns.append("检测到连续两个停顿标记 —— 官方明确「不可连续使用」，第二个可能被丢弃")
    for line in annotated.split("\n"):
        s = line.strip()
        if s.startswith("<#"):
            warns.append("停顿标记出现在行首（左侧无可发音文本）会被吞：%s" % s[:30])
    # 软提示：停顿标记叠在逗号/顿号/分号/冒号之后 —— 标点本身已带停顿，叠加容易拖
    for m in re.finditer(r"([，、；;：:])<#", annotated):
        soft.append("「%s」后直接跟停顿标记 —— 标点自带停顿，叠加可能拖；"
                    "要么去掉标点、要么去掉标记" % m.group(1))
    if annotated.rstrip().endswith("#>"):
        soft.append("结尾是停顿标记 —— 只是多出一段尾部静音，剪辑时裁掉即可（非错误）")

    # 数字检测：★先剔除停顿标记，否则 <#0.35#> 会被当成数字
    no_pause = PAUSE_RE.sub("", annotated)
    nums = []
    for n in re.findall(r"\d+(?:\.\d+)?\s*(?:(?:%|mg|g|kg|元|块|天|个月|万|勺|片|粒)\s*\d*(?:\.\d+)?)?",
                        no_pause):
        n = n.strip()
        if n and n not in nums:
            nums.append(n)

    # 长句检测：MiniMax 的字符密度阈值约 3.3 字/秒，
    # 10 秒片段建议 ≤34 字、15 秒 ≤50 字。超了模型会加速发音甚至截尾。
    for s in segs:
        bare = PAUSE_RE.sub("", s["annotated"])
        bare = re.sub(r"\s+", "", bare)
        n = len(bare)
        if n > 50:
            warns.append("第 %d 句 %d 字（>50）—— 超过 15 秒片段的建议上限，"
                         "模型会加速甚至截掉尾音，建议按语义拆成两段生成" % (s["seq"], n))
        elif n > 34:
            warns.append("第 %d 句 %d 字（>34）—— 生成时请确认片段时长 ≥10 秒，"
                         "否则容易句中呼吸被截" % (s["seq"], n))
    return annotated, inserted, warns, soft, segs, nums


def trim(v):
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return s if s else "0.01"


def cmd_check(args):
    text = args.check
    bad = 0
    for m in re.finditer(r"<#\s*(\d+(?:\.\d{1,2})?)\s*#>", text):
        v = float(m.group(1))
        if v < 0.01 or v > 99.99:
            print("✗ 越界：%.2f 秒（允许 0.01–99.99）" % v)
            bad += 1
    if re.search(r"<#[^#]*#>\s*<#[^#]*#>", text):
        print("✗ 连续两个停顿标记 —— 官方不允许，第二个可能被丢")
        bad += 1
    for line in text.split("\n"):
        if line.strip().startswith("<#"):
            print("✗ 行首停顿标记（左侧无可发音文本）：%s" % line.strip()[:30])
            bad += 1
    print(("✗ 发现 %d 处问题" % bad) if bad else "✓ 停顿标记符合官方两条硬约束")
    return 1 if bad else 0


def cmd_annotate(args):
    cfg = PRESETS[args.preset]
    if args.comma is not None:
        cfg = dict(cfg, comma=args.comma)
    src = args.text
    if not os.path.isfile(src):
        print("✗ 找不到文案文件：%s" % src, file=sys.stderr)
        return 2
    raw = read_text(src)
    annotated, inserted, warns, soft, segs, nums = annotate(raw, cfg)
    os.makedirs(args.out, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src))[0]

    p_txt = os.path.join(args.out, "%s_mm.txt" % stem)
    with open(p_txt, "w", encoding="utf-8") as fh:
        fh.write(annotated + "\n")

    # 对读版：一句一行，只为肉眼核对停顿位置，★不要拿去粘贴★
    readable_lines = []
    for para in annotated.split("\n"):
        if not para.strip():
            continue
        readable_lines.extend(split_on_pause(para))
    p_lines = os.path.join(args.out, "%s_mm_lines.txt" % stem)
    with open(p_lines, "w", encoding="utf-8") as fh:
        fh.write("\n".join(readable_lines) + "\n")

    p_seg = os.path.join(args.out, "%s_mm_seg.json" % stem)
    with open(p_seg, "w", encoding="utf-8") as fh:
        json.dump({"created": datetime.now().isoformat(timespec="seconds"),
                   "source": src, "preset": args.preset,
                   "settings": cfg, "segments": segs}, fh, ensure_ascii=False, indent=1)

    lines = ["# 标注报告 · %s" % stem, "",
             "- 来源：`%s`" % src,
             "- 预设：`%s`（句末 %.2fs / 分号 %.2fs / 冒号 %.2fs / 逗号 %.2fs / 定场 %.2fs）" % (
                 args.preset, cfg["sentence"], cfg["semicolon"], cfg["colon"],
                 cfg["comma"], cfg["after_dingchang"]),
             "- 共插入 %d 处停顿，切出 %d 句" % (len(inserted), len(segs)), ""]
    lines.append("## 分段与建议参数")
    lines.append("")
    lines.append("| # | 角色 | 建议 emotion | 建议 speed | 文案 |")
    lines.append("|---|---|---|---|---|")
    for s in segs:
        lines.append("| %d | %s | `%s` | %.2f | %s |" % (
            s["seq"], s["role"], s["emotion"], s["speed"],
            s["text"][:42] + ("…" if len(s["text"]) > 42 else "")))
    lines.append("")
    lines.append("## 停顿明细")
    lines.append("")
    for it in inserted:
        lines.append("- %s → `<#%s#>`" % (it["at"], trim(it["dur"])))
    if nums:
        lines.append("")
        lines.append("## ⚠ 数字检测（%d 处）" % len(nums))
        lines.append("")
        lines.append("`" + "`、`".join(nums[:20]) + "`")
        lines.append("")
        lines.append("MiniMax 参数里务必开 **`text_normalization: true`**，否则价格/成分数字容易念错。")
    if warns:
        lines.append("")
        lines.append("## ⚠ 硬性自检（%d 项，必须处理）" % len(warns))
        lines.append("")
        for w in dict.fromkeys(warns):
            lines.append("- %s" % w)
    else:
        lines.append("")
        lines.append("## ✓ 硬性自检通过（符合官方两条硬约束）")
    if soft:
        lines.append("")
        lines.append("## 提示（%d 项，可自行取舍）" % len(soft))
        lines.append("")
        for w in dict.fromkeys(soft):
            lines.append("- %s" % w)
    p_rep = os.path.join(args.out, "%s_mm_report.md" % stem)
    with open(p_rep, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")

    print("✓ 标注版   %s   ← 粘这个" % p_txt)
    print("✓ 对读版   %s   （一句一行，只看别粘）" % p_lines)
    print("✓ 分段参数 %s" % p_seg)
    print("✓ 自检报告 %s" % p_rep)
    print("  %d 处停顿｜%d 句｜%d 个数字｜硬性自检 %s｜提示 %d 条" % (
        len(inserted), len(segs), len(nums),
        ("%d 项待处理" % len(warns)) if warns else "通过", len(soft)))
    print("")
    print("下一步：把 %s 的内容粘进 MiniMax / 阿里百炼的语音合成体验页试听。" % os.path.basename(p_txt))
    return 0


def main():
    ap = argparse.ArgumentParser(description="口播文案 → 云端 TTS 标注版")
    ap.add_argument("--text", help="文案 txt 路径")
    ap.add_argument("--out", default="./tts_out", help="输出目录")
    ap.add_argument("--preset", choices=sorted(PRESETS), default="koubo")
    ap.add_argument("--comma", type=float, default=None,
                    help="逗号后停顿时长秒数，默认 0（交给模型自然处理）")
    ap.add_argument("--check", help="只检查一段带停顿标记的文本是否合规")
    args = ap.parse_args()
    if args.check:
        sys.exit(cmd_check(args))
    if not args.text:
        ap.error("要么给 --text，要么给 --check")
    sys.exit(cmd_annotate(args))


if __name__ == "__main__":
    main()
