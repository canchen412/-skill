#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★云端配音的 token 时间轴生成（2026-10-16）—— 替代 mk_tokens_f5.py

原理（和 F5 版的根本区别）：
  F5 版：句界来自 F5 实测，句内字符按「字数比例」猜时间。
  本版：云端返回**逐字**时间戳（words.json），句内也是实测值 —— 字幕不会再有累积漂移。

为什么要做「规范化对齐」：
  云端 TTS 送进去的不是原文，而是它自己转写过的文本：
      19块9  → 十九块九      50多万 → 五十多万      SGS → sgs
  所以 words.json 的字序列和 phrases.txt 对不上（字数都不同）。
  本脚本把原文做同样的规范化，两边就能逐字对上，再把时间映射回原镜位。

用法:
  # 只做对齐校验（不写文件）
  python3 mk_tokens_cloud.py --words X.words.json --phrases phrases.txt --check
  # 正式产出
  python3 mk_tokens_cloud.py --words X.words.json --phrases phrases.txt --out-dir s1/audio
"""
import argparse
import difflib
import json
import os
import re
import sys

# 与配音稿一致：这些字符不发音，不占时间轴
PUNCT = set('，。！？、：；—…·～「」『』《》""''（）()【】〔〕<>《》 \t\n\r')

DIGITS = "零一二三四五六七八九"
UNITS = ["", "十", "百", "千", "万"]


def num2cn(s):
    """阿拉伯整数 → 中文读法（19→十九, 50→五十, 9→九, 100→一百）"""
    n = int(s)
    if n == 0:
        return "零"
    sec = []
    v = n
    while v > 0:
        sec.append(v % 10)
        v //= 10
    if len(sec) > 5:
        return s  # 超过万级不猜，原样保留（交给对齐器兜底）
    out = []
    for i in range(len(sec) - 1, -1, -1):
        d = sec[i]
        if d:
            out.append(DIGITS[d] + UNITS[i])
        elif out and out[-1] != "零":
            out.append("零")
    res = "".join(out).rstrip("零")
    if res.startswith("一十"):
        res = res[1:]
    return res


def cn_number(s):
    """数字串 → 中文读法。支持小数点：99.9 → 九十九点九"""
    if "." in s:
        a, b = s.split(".", 1)
        return num2cn(a) + "点" + "".join(DIGITS[int(c)] if c.isdigit() else c for c in b)
    return num2cn(s)


def normalize(text):
    """
    把原文规范化成「云端转写文本」的形态。
    实测踩到的三类（2026-10-16）：
        19块9   → 十九块九
        99.9%   → 百分之九十九点九   ← 百分号会前置「百分之」、小数点读「点」
        SGS     → sgs
    ★ 顺序不能反：必须先吃掉 %，否则 99.9 会被先转成「九十九点九」，
      再补「百分之」就变成错位的「九十九点九百分之」。
    """
    t = re.sub(r"(\d+(?:\.\d+)?)%", lambda m: "百分之" + cn_number(m.group(1)), text)
    t = re.sub(r"\d+(?:\.\d+)?", lambda m: cn_number(m.group()), t)
    # ★2026-10-25（液体钙 261025 实测补规则）：云端会把英文单位**读成中文**，
    #   原文留着 ASCII 单位 ⇒ 对齐闸挂（实测 800mg/12500mg 三处 + 「两/二」= 0.9641 不达标）。
    #   补单位映射（先长后短：mg 必须在 g 之前，否则 mg→「m克」）＋ 数字读法归一。
    t = re.sub(r"(?i)(?:μg|ug|mcg)", "微克", t)
    t = re.sub(r"(?i)mg", "毫克", t)
    t = re.sub(r"(?i)(?<![a-z])g(?![a-z])", "克", t)
    t = re.sub(r"(?i)ml", "毫升", t)
    t = re.sub(r"(?i)kg", "千克", t)
    t = re.sub(r"(?i)kcal", "千卡", t)
    t = t.replace("两", "二")          # 云端 12500 读「一万两千五百」，cn_number 出「一万二千五百」
    t = re.sub(r"[A-Za-z]+", lambda m: m.group().lower(), t)
    return t


def spoken_chars(words):
    """words.json → [(char, begin_ms, end_ms)]，标点保留但标记出来（不发音）"""
    out = []
    for w in words:
        ch = w["text"]
        is_punct = all(c in PUNCT for c in ch)
        out.append({
            "chars": ch,
            "begin": w["begin_time"],
            "end": w["end_time"],
            "punct": is_punct,
        })
    return out


def build_map(words, src_text):
    """
    返回 src_chars（去掉标点的规范化原文）的每个字符 → (begin_s, end_s)
    用 difflib 对齐 cloud 字序列与 src 字序列，replace 块内按顺序等分。
    """
    items = spoken_chars(words)
    # ★ 云端会把标点和字母凑成一个词元（实测到 "，sgs"），
    #   所以必须**逐字符**判定标点，整词元判定会漏掉夹在中间的标点。
    cloud_chars = []
    for it in items:
        chars = list(it["chars"])
        n = len(chars)
        span = max(1, it["end"] - it["begin"])
        for i, c in enumerate(chars):
            if c in PUNCT:
                continue
            b = it["begin"] + span * i / n
            e = it["begin"] + span * (i + 1) / n
            cloud_chars.append((c, b / 1000.0, e / 1000.0))

    norm = normalize(src_text)
    src_chars = [(i, c) for i, c in enumerate(norm) if c not in PUNCT]
    src_seq = "".join(c for _, c in src_chars)

    cl_seq = "".join(c for c, _, _ in cloud_chars)

    # 比较时统一小写（云端对 "SGS" 转小写、却把孤立的 "A" 留成大写，不统一）；
    # 大小写不改变字符数，所以位置映射依然正确。
    sm = difflib.SequenceMatcher(None, cl_seq.lower(), src_seq.lower(), autojunk=False)
    times = {}          # src 位置 → (begin_s, end_s)
    ops = sm.get_opcodes()
    for tag, i1, i2, j1, j2 in ops:
        if tag == "equal":
            for k in range(i2 - i1):
                times[src_chars[j1 + k][0]] = (cloud_chars[i1 + k][1], cloud_chars[i1 + k][2])
        else:
            # 非 equal：按源侧范围，用云侧的可用时间做线性插值
            cspan = cloud_chars[i1:i2]
            if not cspan:
                # 源侧多出的字（云端漏读），沿用前一字时间
                prev = None
                for k in range(j1):
                    if src_chars[k][0] in times:
                        prev = times[src_chars[k][0]]
                for k in range(j1, j2):
                    if prev:
                        times[src_chars[k][0]] = prev
            else:
                tb, te = cspan[0][1], cspan[-1][2]
                n = max(1, j2 - j1)
                for k in range(n):
                    times[src_chars[j1 + k][0]] = (
                        tb + (te - tb) * k / n,
                        tb + (te - tb) * (k + 1) / n,
                    )
    ratio = sm.ratio()
    diffs = [(tag, cl_seq[i1:i2], src_seq[j1:j2])
             for tag, i1, i2, j1, j2 in ops if tag != "equal"]
    return norm, src_chars, times, ratio, ops, diffs


def ts(x):
    """vo_mapped_v2 用的 HH:MM:SS.ss 格式"""
    h, m = divmod(x, 3600)
    m, s = divmod(m, 60)
    return "%02d:%02d:%05.2f" % (int(h), int(m), s)


def srt_ts(x):
    """标准 SRT 用 HH:MM:SS,mmm（三位毫秒）—— 两位会被播放器读成几十毫秒"""
    if x < 0:
        x = 0
    h = int(x // 3600)
    m = int((x % 3600) // 60)
    s = int(x % 60)
    ms = int(round((x - int(x)) * 1000))
    if ms >= 1000:          # 四舍五入进位保护
        ms = 999
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--words", required=True, help="cloud_cosyvoice_ws.py 产出的 .words.json")
    ap.add_argument("--phrases", help="镜位句文件（每行一镜）；给了就切镜")
    ap.add_argument("--text", help="原文文件；不给则用 words.json 里的 original_text")
    ap.add_argument("--out-dir", help="输出目录（--check 时可省）")
    ap.add_argument("--check", action="store_true", help="只做对齐校验，不写文件")
    ap.add_argument("--debug", action="store_true", help="打印对齐差异块（排查规范化为题）")
    ap.add_argument("--group", type=int, default=4, help="字幕每 token 最多几个字（默认 4）")
    ap.add_argument("--tail-keep", type=float, default=1.0,
                    help="镜尾字幕保留系数（1.0=用到实测音尾；<1 提前收，防压音尾）")
    a = ap.parse_args()

    d = json.load(open(a.words, encoding="utf-8"))
    src_text = (open(a.text, encoding="utf-8").read().strip()
                if a.text else d["original_text"])

    norm, src_chars, times, ratio, ops, diffs = build_map(d["words"], src_text)
    missing = [i for i, _ in src_chars if i not in times]
    sub = len(diffs)

    print("[对齐] 云端词元 %d ｜ 原文 %d 字（去标点）｜ 相似度 %.4f ｜ 非等块 %d"
          % (len(d["words"]), len(src_chars), ratio, sub))
    print("[对齐] 未映射字符 %d 个%s" % (len(missing), "" if not missing else "：%r" % norm[missing[0]:missing[0]+20]))
    if a.debug and diffs:
        print("[对齐] 差异块（云端 → 原文）—— 若差异大说明规范化规则要补：")
        for tag, cl, sr in diffs[:20]:
            print("   %-8s 云端 %-22r → 原文 %r" % (tag, cl, sr))

    if a.check:
        # 对齐质量把关：相似度低就报错，避免安静地产出错字幕
        bad = ratio < 0.98 or len(missing) > 0
        print("[对齐] 结论：%s" % ("✗ 不达标 —— 别拿它出片" if bad else "✓ 达标"))
        return 1 if bad else 0

    if not a.out_dir:
        print("缺少 --out-dir", file=sys.stderr)
        return 2
    os.makedirs(a.out_dir, exist_ok=True)

    # ---- 切镜 ----
    if a.phrases:
        phrases = [l.strip() for l in open(a.phrases, encoding="utf-8") if l.strip()]
    else:
        phrases = None

    out, shot_bounds = [], []
    if phrases:
        # 规范化同样长度：逐镜消费原文的字（按规范化后的长度对齐）
        # 用「规范化文本」的子串在 norm 上顺序推进
        cursor = 0            # 在 norm 中的游标
        src_pos_list = [i for i, _ in src_chars]
        for ph in phrases:
            ph_norm_len = len([c for c in normalize(ph) if c not in PUNCT])
            assert ph_norm_len > 0, ph
            # 该镜覆盖的原文规范化区间
            seg_pos = src_pos_list[cursor:cursor + ph_norm_len]
            assert len(seg_pos) == ph_norm_len, (
                "短语超界：镜 %r 需要 %d 字，原文只剩 %d" % (ph[:20], ph_norm_len, len(seg_pos)))
            cursor += ph_norm_len
            # 镜界：首字 begin → 末字 end
            lo = times[seg_pos[0]][0]
            hi = times[seg_pos[-1]][1]
            shot_bounds.append([round(lo, 3), round(hi, 3)])
            # 镜内 token：按原文（未规范化）字分组，保证字幕显示原文（19块9，不是十九块九）
            chars = [c for c in ph if c not in PUNCT]
            n = len(chars)
            i = 0
            while i < n:
                k = min(a.group, n - i)
                # 对应到该镜的规范化字范围（长度可能因数字规范化而不同）
                # 简化：用字符比例映射（镜内已按比例均匀落在实测区间内）
                aa = lo + (hi - lo) * i / n
                zz = lo + (hi - lo) * (i + k) / n
                if i + k >= n:
                    zz = lo + (hi - lo) * a.tail_keep
                    zz = max(aa + 0.08, zz)
                out.append({"timestamps": {"from": ts(aa), "to": ts(zz)},
                            "offsets": {"from": int(round(aa * 1000)), "to": int(round(zz * 1000))},
                            "text": "".join(chars[i:i + k])})
                i += k
        left = len(src_pos_list) - cursor
        if left:
            print("[警告] 镜位句只消费了原文 %d/%d 字，剩 %d 字未进镜"
                  % (cursor, len(src_pos_list), left))
        total = shot_bounds[-1][1]
    else:
        # 无镜位：整篇按 group 切 token
        n = len(src_chars)
        i = 0
        while i < n:
            k = min(a.group, n - i)
            aa = times[src_chars[i][0]][0]
            zz = times[src_chars[i + k - 1][0]][1]
            out.append({"timestamps": {"from": ts(aa), "to": ts(zz)},
                        "offsets": {"from": int(round(aa * 1000)), "to": int(round(zz * 1000))},
                        "text": "".join(norm[p] for p, _ in src_chars[i:i + k])})
            i += k
        total = times[src_chars[-1][0]][1]
        shot_bounds = [[0.0, round(total, 3)]]

    doc = {"systeminfo": "cloud-cosyvoice-words",
           "model": {"name": d.get("model"), "voice_id": d.get("voice_id"), "rate": d.get("rate")},
           "params": {"align_ratio": round(ratio, 4)},
           "result": {"language": "zh"},
           "transcription": out}
    json.dump(doc, open(os.path.join(a.out_dir, "vo_mapped_v2.json"), "w"),
              ensure_ascii=False, indent=1)
    json.dump(shot_bounds, open(os.path.join(a.out_dir, "shot_bounds.json"), "w"))

    # ★ durations.json —— 与 f5_batch.py 同接口（pipeline_run.py 第 283 行要
    #   `["sentences"]` 且 len == 镜数，第 287 行要 durs[0]["end"] 当轮播切点）。
    #   云端无「gap 拼接」，start/end 就是每镜首末字的实测时间，语义与 F5 一致。
    if phrases:
        durs = [{"i": i + 1, "text": ph,
                 "start": round(shot_bounds[i][0], 3),
                 "end": round(shot_bounds[i][1], 3),
                 "speed": d.get("rate")}
                for i, ph in enumerate(phrases)]
    else:
        durs = [{"i": 1, "text": src_text, "start": 0.0,
                 "end": round(shot_bounds[0][1], 3), "speed": d.get("rate")}]
    json.dump({"sentences": durs},
              open(os.path.join(a.out_dir, "durations.json"), "w"),
              ensure_ascii=False, indent=1)

    # 可读字幕（便于肉眼核对）
    srt = []
    for i, t in enumerate(out, 1):
        srt.append("%d\n%s --> %s\n%s\n" % (
            i,
            srt_ts(t["offsets"]["from"] / 1000.0),
            srt_ts(t["offsets"]["to"] / 1000.0),
            t["text"]))
    open(os.path.join(a.out_dir, "vo_cloud.srt"), "w", encoding="utf-8").write("\n".join(srt))

    print("[产出] %d 镜 / %d token / 全片 %.1fs → %s" %
          (len(shot_bounds), len(out), total, a.out_dir))
    print("       vo_mapped_v2.json / shot_bounds.json / durations.json / vo_cloud.srt")
    if ratio < 0.98:
        print("[警告] 对齐相似度偏低（%.4f），字幕可能错位，建议人工核一遍" % ratio)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
