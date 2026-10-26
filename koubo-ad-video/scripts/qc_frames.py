#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""qc_frames.py —— 渲染后抽帧质检（每镜中点抽一帧 → 拼联络图）

用途：成片出来后逐镜目视一遍「素材是否入片、是否撞图、画面是否正常」。
配合 kwcheck.py（专查重点字）一起构成渲染后质检闭环。

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/qc_frames.py
  OUT="/Volumes/陈灿会展/广告视频/<分类>/<品牌>/<日期>"

  for k in 1 2 3; do
    $PY $S --config config_$k.json \
           --video "$OUT/<片名>_${k}_1080P.mp4" \
           --out   qc/sheet_$k.jpg
  done
"""
import argparse
import json
import os
import subprocess
import sys

from PIL import Image, ImageDraw

FF = "/Users/mac/.local/bin/ffmpeg"


def main():
    ap = argparse.ArgumentParser(description="渲染后抽帧质检（每镜中点一帧）")
    ap.add_argument("--config", required=True, help="config_<k>.json")
    ap.add_argument("--video", required=True, help="渲染出的成片 mp4")
    ap.add_argument("--out", required=True, help="输出联络图 jpg")
    ap.add_argument("--cols", type=int, default=4, help="联络图列数（默认 4）")
    ap.add_argument("--cell-w", type=int, default=470, help="单元宽（默认 470）")
    ap.add_argument("--label-len", type=int, default=22, help="帧标题取文案前 N 字（默认 22）")
    ap.add_argument("--frame-dir", help="单帧缓存目录（默认 <out 同目录>/frames_<config 名>）")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shot_plan as sp

    cfg = json.load(open(args.config, encoding="utf-8"))
    tl, warn = sp.build_timeline(cfg)
    if not os.path.exists(args.video):
        sys.exit(f"找不到成片：{args.video}")
    if warn:
        print(f"⚠️ 时间轴告警：{warn}")

    d = args.frame_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.out)) or ".",
        "frames_" + os.path.splitext(os.path.basename(args.config))[0])
    os.makedirs(d, exist_ok=True)

    tiles = []
    for i, (p, s, e) in enumerate(tl, 1):
        t = (s + e) / 2
        f = os.path.join(d, f"f{i:02d}.jpg")
        subprocess.run([FF, "-v", "error", "-ss", f"{t:.2f}", "-i", args.video,
                        "-frames:v", "1", "-vf", f"scale={args.cell_w}:-1", "-q:v", "3", "-y", f],
                       check=False)
        if os.path.exists(f):
            tiles.append((i, p["text"], Image.open(f)))
        else:
            print(f"⚠️ 抽帧失败 #{i} @ {t:.2f}s")

    if not tiles:
        sys.exit("没有抽到任何帧")
    rows = (len(tiles) + args.cols - 1) // args.cols
    ch = tiles[0][2].height + 22
    sheet = Image.new("RGB", (args.cols * args.cell_w, rows * ch), (18, 18, 18))
    dr = ImageDraw.Draw(sheet)
    for idx, (i, txt, im) in enumerate(tiles):
        r, c = divmod(idx, args.cols)
        sheet.paste(im, (c * args.cell_w, r * ch + 22))
        dr.text((c * args.cell_w + 6, r * ch + 6), f"#{i} {txt[:args.label_len]}", fill=(255, 220, 90))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sheet.save(args.out, quality=82)
    print(f"{len(tl)} 镜 → {args.out}  {sheet.size}")


if __name__ == "__main__":
    main()
