#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""数字「中文读法」时间轴 → 原文案字符流 映射（koubo-ad-video 技能常驻工具）

背景（SKILL.md #86 / #107 / §数字中文读法铁律）：
  TTS 合成文本里数字+单位要写中文读法（`300mg`→`三百毫克`、`98%`→`百分之九十八`），
  但字幕文本保持阿拉伯数字（`300mg` 上屏更醒目）。**两者字符数不等** ⇒
  渲染器 `build_ad_video.py` 是按 config 文案字符数贪心切 tokens 的，直接喂会**全片漂移**。
  所以必须把「中文读法时间轴」映射回「原文案字符流」再喂渲染器。

标准链路（config.tokens 永远指向**原文流**）：
  1) whisper_small_align.py <vo.mp3> -o <whisper.json>
  2) align_vo.py <whisper.json> <text_tts.txt> <读法流.json>        ← 对齐到「读法文本」
  3) map_num_time.py <读法流.json> <text_tts.txt> <phrases.txt> <原文流.json>   ← 本脚本
  4) fix_vo_head（若首字时间异常）

用法：
  python3 map_num_time.py <读法流.json> <text_tts.txt> <phrases.txt> <out_原文流.json>

原理：把「读法字符流」与「原文案字符流」做 difflib 序列比对；
      equal 段逐字搬时间；replace 段（＝数字读法差异处，如 三百毫克↔300mg）
      把读法段的总时长按原文段字数均分；读法多出来的插入字（分隔词）直接丢弃。
"""
import difflib
import json
import sys

# ★必须与 build_ad_video.py 的 pchars() 完全一致（L419），否则字符数对不上
PUNCT = "，。！？、：；—…·～「」『』《》\u201c\u201d\u2018\u2019（）()"


def pchars(t):
    return [c for c in t if c not in PUNCT and not c.isspace()]


def load_stream(path):
    """读 align_vo 产出的 tokens：展平成 (逐字, 起止秒) 两个列表。"""
    w = json.load(open(path, encoding="utf-8"))
    chars, times = [], []
    for seg in w["transcription"]:
        s = seg["offsets"]["from"] / 1000.0
        e = seg["offsets"]["to"] / 1000.0
        txt = seg["text"]
        n = max(1, len(txt))
        for i, c in enumerate(txt):
            chars.append(c)
            times.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n))
    return chars, times


def flat_text(path):
    """phrases.txt / text_tts.txt → 忽略标点与换行的字符列表。"""
    s = open(path, encoding="utf-8").read()
    return pchars(s)


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(2)
    read_json, read_txt, orig_txt, out_json = sys.argv[1:5]

    rc, rt = load_stream(read_json)          # 读法字符流 + 时间
    want_read = flat_text(read_txt)          # text_tts.txt 的字符流（校验用）
    oc = flat_text(orig_txt)                 # phrases.txt 原文流

    if rc != want_read:
        print(f"⚠️ 读法流 {len(rc)} 字 != text_tts.txt {len(want_read)} 字"
              f"（align_vo 未完全对齐，仍按 difflib 继续）")

    sm = difflib.SequenceMatcher(None, rc, oc, autojunk=False)
    out_chars, out_times = [], []
    n_rep = 0
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                out_chars.append(oc[j1 + k])
                out_times.append(rt[i1 + k])
        elif tag == "replace":
            n_rep += 1
            seg = oc[j1:j2]
            if i2 > i1:
                t0, t1 = rt[i1][0], rt[i2 - 1][1]
            else:                            # 原文有、读法无 → 借用邻字时间
                t0 = t1 = rt[max(0, i1 - 1)][1] if rt else 0.0
            n = len(seg)
            for k, ch in enumerate(seg):
                out_chars.append(ch)
                out_times.append((t0 + (t1 - t0) * k / n, t0 + (t1 - t0) * (k + 1) / n))
        elif tag == "insert":                # 原文有、读法无（罕见）：借用前字时间
            base = rt[i1 - 1][1] if i1 > 0 else 0.0
            for ch in oc[j1:j2]:
                out_chars.append(ch)
                out_times.append((base, base))
        elif tag == "delete":                # 读法多出来（分隔词等）：丢弃
            pass

    # ---- 写回：逐字分段（渲染器按 len(text) 均分，单字最精确）----
    trans = [{"offsets": {"from": int(round(t0 * 1000)), "to": int(round(t1 * 1000))},
              "text": c} for c, (t0, t1) in zip(out_chars, out_times)]
    json.dump({"transcription": trans}, open(out_json, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    ok = (out_chars == oc)
    print(f"  读法流 {len(rc)} 字 → 原文流 {len(out_chars)} 字（目标 {len(oc)}）"
          f"  {'✅ 逐字一致' if ok else '❌ 不一致'}")
    print(f"  数字读法替换点 {n_rep} 处 ｜ 时间轴 {out_times[0][0]:.2f}s → {out_times[-1][1]:.2f}s")
    if not ok:
        print("  ❌ 输出字符流与 phrases.txt 不一致 —— 渲染会漂移，需检查")


if __name__ == "__main__":
    main()
