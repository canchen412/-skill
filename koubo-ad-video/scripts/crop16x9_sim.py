#!/usr/bin/env python3
"""crop16x9_sim.py — 生图交付前安全区检查：模拟渲染器 3:2→16:9 中心裁切。
用法: python3 crop16x9_sim.py 图1.png 图2.png ... [--out 网格.jpg]
不做裁切判断，输出模拟裁切后的拼图供 Read 目检（主体/罐必须完整、字幕带留白）。
"""
import sys, argparse
from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("images", nargs="+")
ap.add_argument("--out", default=None)
ap.add_argument("--tile", type=int, default=640)
a = ap.parse_args()

tiles = []
for p in a.images:
    im = Image.open(p).convert("RGB")
    w, h = im.size
    th = int(w * 9 / 16)
    if th <= h:
        top = (h - th) // 2
        im = im.crop((0, top, w, top + th))
    else:  # 已是或比16:9更扁，模拟左右裁
        tw = int(h * 16 / 9)
        left = (w - tw) // 2
        im = im.crop((left, 0, left + tw, h))
    tiles.append(im.resize((a.tile, int(a.tile * 9 / 16))))

cols = 2
rows = (len(tiles) + 1) // 2
g = Image.new("RGB", (a.tile * cols, int(a.tile * 9 / 16) * rows))
for k, t in enumerate(tiles):
    g.paste(t, ((k % cols) * a.tile, (k // cols) * int(a.tile * 9 / 16)))
out = a.out or "crop16x9_sim.jpg"
g.save(out, quality=88)
print("saved", out, "（目检要点：主体/罐完整在画面内、底部预留字幕带）")
