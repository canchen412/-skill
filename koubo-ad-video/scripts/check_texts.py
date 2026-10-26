#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""check_texts.py —— 三套文本一致性守恒校验（0 积分）

「原文 == phrases(字幕) == text_tts(配音)」去标点后必须**逐字相同**。
这是防止字幕与配音错位的唯一可靠闸门：字幕按 phrases 分行、配音按 text_tts 分段，
两条路径一旦有一处多字/少字，成片就会出现「字幕跳读」或「念白对不上」。

★ TTS 允许「读音写法」不同：字幕保留品牌官方写法（如「21金维他」），
  配音文本为便于连读可写成汉字（「二十一金维他」）——读法一致、仅书面形式不同。
  用 --equiv 声明这类等价对（默认内置「二十一金维他=21金维他」）。

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/check_texts.py
  for k in 1 2 3; do
    $PY $S --src s$k/文案.txt --phrases phrases_$k.txt --tts s$k/text_tts.txt
  done

  # 追加自定义读音等价（可多次）
  $PY $S ... --equiv "二十一金维他=21金维他" --equiv "一百克=100g"
"""
import argparse
import re
import sys

PUNC = r"[，。、！？；：—…·「」『』（）()\"'“”‘’《》〈〉\s,\.!?;:]"
DEFAULT_EQUIV = [("二十一金维他", "21金维他")]


def strip(s):
    return re.sub(PUNC, "", s)


def main():
    ap = argparse.ArgumentParser(description="原文/phrases/tts 三套文本守恒校验")
    ap.add_argument("--src", required=True, help="文案.txt（原文，含标点）")
    ap.add_argument("--phrases", required=True, help="phrases_<k>.txt（字幕分行，1 行 1 镜）")
    ap.add_argument("--tts", required=True, help="text_tts.txt（配音合成文本）")
    ap.add_argument("--equiv", action="append", default=[], metavar="A=B",
                    help="TTS 读音写法等价对，如 二十一金维他=21金维他（可多次）")
    ap.add_argument("--est-sec-per-char", type=float, default=0.15, help="逐 phrase 估时用（默认 0.15）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论与差异")
    args = ap.parse_args()

    equiv = list(DEFAULT_EQUIV)
    for e in args.equiv:
        if "=" not in e:
            sys.exit(f"--equiv 格式应为 A=B，收到：{e}")
        a, b = e.split("=", 1)
        equiv.append((a.strip(), b.strip()))

    def norm_tts(s):
        for a, b in equiv:
            s = s.replace(a, b)
        return s

    src = strip(open(args.src, encoding="utf-8").read())
    phr = [l.strip() for l in open(args.phrases, encoding="utf-8") if l.strip()]
    tts_raw = strip(open(args.tts, encoding="utf-8").read())
    tts = norm_tts(tts_raw)
    phr_cat = strip("".join(phr))

    ok = True
    print(f"===== {args.src} =====")
    if tts_raw != tts:
        print(f"  · TTS 读音写法归一化 {len(tts_raw) - len(tts)} 字符（数字↔汉字，读法一致）")
    print(f"  原文字数 {len(src)} | phrases {len(phr)}行 拼接 {len(phr_cat)} | TTS {len(tts)}")

    for name, got, exp in (("phrases", phr_cat, src), ("TTS", tts, src)):
        if got == exp:
            print(f"  ✅ {name} 守恒")
            continue
        ok = False
        print(f"  ❌ {name} 与原文不一致")
        for i, (a, b) in enumerate(zip(exp, got)):
            if a != b:
                print(f"     首个差异 @{i}: 原文'{exp[max(0,i-12):i+12]}' vs {name}'{got[max(0,i-12):i+12]}'")
                break
        if len(exp) != len(got):
            print(f"     长度差 {len(exp) - len(got)}")

    if not args.quiet:
        print("  逐 phrase 字数：")
        for i, t in enumerate(phr):
            n = len(strip(t))
            print(f"    {i+1:2d}. {n:3d}字 ~{n*args.est_sec_per_char:4.2f}s  {t}")
    print("总校验:", "✅ 通过" if ok else "❌ 有问题")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
