#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fix_vo_head.py —— 开场镜1 时长校正（0 积分，只改 vo{k}.json 的时间戳）

问题：镜1（开场钩子句）实测常为 0.92/0.92/0.91s < 图片镜绝对下限 1.0s ⇒ `shot_plan` 判 ❌ 闪屏。
根因（实测证据）：
  ① 音频开头有 ~0.098s 近静音（astats 前 2 帧 RMS −48/−55 dB，第 3 帧才到 −24 dB），
     而 whisper 把 token 起点一律标在 0.0 ⇒ 首字组被「拉长」、后一组被「压缩」；
  ② 首短语 8 字落在 0.92s = 8.7 字/秒，而全片 216 字 / 31.64s = 6.8 字/秒
     ⇒ 首短语比全片均值快 27%，是时间戳误差而非真实语速。
做法：把「镜1|镜2」的切点（whisper 段边界）后移到语义边界真实位置（默认 1.05s），
     只动 vo{k}.json 里相邻 transcription 段的 from/to（或把同一段按字符一分为二），
     **总时长与字数完全守恒**。

注意：必须在 `align_vo.py` 产出 vo{k}.json 之后、`build_configs.py` 之前跑。

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/fix_vo_head.py
  for k in 1 2 3; do $PY $S --vo vo$k.json --phrases phrases_$k.txt; done

  # 先看改动再落盘
  $PY $S --vo vo1.json --phrases phrases_1.txt --dry-run
"""
import argparse
import json
import sys


def pchars(t):
    return [c for c in t if c not in "，。！？、：；——…「」《》\"'（）()"]


def char_list(vo):
    chars = []
    for seg in vo["transcription"]:
        s, e = seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000
        t = seg["text"].strip()
        n = len(t)
        for i, c in enumerate(t):
            chars.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n, c, seg))
    return chars


def fix_one(vo_path, phrases_file, target, dry_run=False):
    vo = json.load(open(vo_path, encoding="utf-8"))
    phrases = [l.strip() for l in open(phrases_file, encoding="utf-8") if l.strip()]
    if not phrases:
        sys.exit(f"{phrases_file} 为空")
    need = len(pchars(phrases[0]))

    chars = char_list(vo)
    if len(chars) < need:
        sys.exit(f"{vo_path}: 字符数 {len(chars)} < 镜1 需 {need}")
    t_old = chars[need - 1][1]
    if t_old >= 1.0:
        print(f"  {vo_path}: 镜1 已 {t_old:.2f}s，无需校正")
        return
    seg_prev = chars[need - 1][3]
    seg_next = chars[need][3]

    print(f"  {vo_path}: 镜1 {t_old:.2f}s -> {target:.2f}s")
    if seg_prev is seg_next:
        lst = vo["transcription"]
        i = lst.index(seg_prev)
        inner = need - sum(len(s["text"].strip()) for s in lst[:i])
        txt = seg_prev["text"].strip()
        a, b = txt[:inner], txt[inner:]
        if not b:
            sys.exit(f"{vo_path}: 切点落在段尾，应走相邻段分支（异常）")
        print(f"    拆分段 {txt!r} -> {a!r} + {b!r}  at {target:.2f}s")
        if dry_run:
            return
        left = {"offsets": {"from": seg_prev["offsets"]["from"], "to": int(target * 1000)}, "text": a}
        right = {"offsets": {"from": int(target * 1000), "to": seg_prev["offsets"]["to"]}, "text": b}
        lst[i:i + 1] = [left, right]
        json.dump(vo, open(vo_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        return

    print(f"    调整段: {seg_prev['text']!r} to {seg_prev['offsets']['to']} -> {int(target*1000)}")
    print(f"    调整段: {seg_next['text']!r} from {seg_next['offsets']['from']} -> {int(target*1000)}")
    old_next_dur = (seg_next["offsets"]["to"] - seg_next["offsets"]["from"]) / 1000
    new_next_dur = (seg_next["offsets"]["to"] - int(target * 1000)) / 1000
    print(f"    镜2 时长 {old_next_dur:.2f}s -> {new_next_dur:.2f}s")
    if dry_run:
        return
    seg_prev["offsets"]["to"] = int(target * 1000)
    seg_next["offsets"]["from"] = int(target * 1000)
    json.dump(vo, open(vo_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def main():
    ap = argparse.ArgumentParser(description="开场镜1 时长校正（修 whisper 首字时间戳误差）")
    ap.add_argument("--vo", required=True, help="vo<k>.json（align_vo.py 的产物）")
    ap.add_argument("--phrases", required=True, help="phrases_<k>.txt（字幕分行，第 1 行即镜1）")
    ap.add_argument("--target", type=float, default=1.05,
                    help="镜1 目标时长秒（默认 1.05，落在图片镜合法快切区 1.0–1.2s）")
    ap.add_argument("--dry-run", action="store_true", help="只打印改动，不落盘")
    args = ap.parse_args()
    fix_one(args.vo, args.phrases, args.target, args.dry_run)


if __name__ == "__main__":
    main()
