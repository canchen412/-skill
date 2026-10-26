#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从配音 token 抓每个重点字的真实发声时刻
========================================
★踩坑：不能用「跨 token 累加直到包含 word」的办法 —— lo 会取到累加起点那个
  token，导致区间虚胖（实测「4个9」被算成 7.85-11.56，实际 10.9-11.5）。
正确做法：把全部 token 拼成一串，**记录每个字符的中心时间**，再 str.find 定位，
  用命中区间的首/尾字符时间拼出精确区间。
"""
import json
import re

BASE = "/Users/mac/WorkBuddy/2026-10-05-09-33-49/jisuan_alan_video_261006/s3/audio/"
d = json.load(open(BASE + "vo_mapped_v2.json"))
toks = d["transcription"]

# ① 逐字符时间：一个 token 内的字符按字数均分
chars, ctime = [], []
for s in toks:
    raw = s["text"]
    keep = re.sub(r"[，。、！？\s]", "", raw)
    if not keep:
        continue
    lo = s["offsets"]["from"] / 1000
    hi = s["offsets"]["to"] / 1000
    step = (hi - lo) / len(raw)
    for k, ch in enumerate(raw):
        if ch in "，。、！？ \n":
            continue
        chars.append(ch)
        ctime.append((lo + step * k, lo + step * (k + 1)))
text = "".join(chars)

WANT = [
    (5,  "4个9",     "count"),
    (6,  "超不超标",  "warn"),
    (8,  "检测费",   "warn"),
    (10, "一批批",   "stamp"),
    (11, "随货",    "stamp"),
    (13, "三个月",   "price"),
    (15, "随便挑着比", "price"),
]

# 所属镜的时间窗（shot_bounds），用于同名歧义时挑正确那次
SHOT_WIN = {5: (9.85, 11.60), 6: (12.05, 13.41), 8: (14.90, 17.55),
            10: (19.81, 21.20), 11: (21.65, 22.76), 13: (23.62, 25.10),
            15: (26.93, 28.05)}

print(f"{'镜':>3}  {'词':<8} {'发声区间':<18} {'时长':>6}  {'建议start':>9} {'durSec':>7}")
res = {}
for idx, word, anim in WANT:
    # ★同一个词可能在全片出现多次（「三个月」在第1句和第13镜都有）
    #   ⇒ 枚举全部出现位置，只保留落在该镜时间窗内的那次
    hits, p = [], 0
    while True:
        p = text.find(word, p)
        if p < 0:
            break
        hits.append(p)
        p += 1
    win = SHOT_WIN.get(idx)
    picked = None
    for h in hits:
        t0 = ctime[h][0]
        if win is None or (win[0] - 0.6) <= t0 <= (win[1] + 0.3):
            picked = h
            break
    if picked is None:
        print(f"{idx:>3}  {word:<8} ⚠️ 找不到（{len(hits)} 次出现都不在镜窗内）")
        continue
    lo = ctime[picked][0]
    hi = ctime[picked + len(word) - 1][1]
    # 起始略提前（字出现前 0.06s 起跳，让弹簧起跳与开口同步），停留 = 发声 + 尾巴
    start = round(max(0.0, lo - 0.06), 2)
    tail = 1.05 if anim != "count" else 1.35
    dur = round((hi - lo) + tail, 2)
    res[idx] = {"word": word, "anim": anim,
                "voice": [round(lo, 2), round(hi, 2)],
                "start": start, "dur": dur}
    print(f"{idx:>3}  {word:<8} {lo:6.2f}-{hi:6.2f}     {hi-lo:5.2f}s  {start:8.2f}  {dur:6.2f}")

json.dump(res, open(BASE + "kw_times.json", "w"), ensure_ascii=False, indent=1)
print("\n→ s3/audio/kw_times.json")
