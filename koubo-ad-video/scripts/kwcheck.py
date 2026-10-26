#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""kwcheck.py —— 重点字落帧核验（渲染后必跑）

为什么必跑：`shot_plan.py --config` 只能查时长与素材，**查不出大字是否压在主体身上**（语义错位）。
本脚本按每个 keyword 的 kw 在其 phrase 文本内的字符比例，推出大字入场动画走完的真实时刻 →
从成片抽帧 → 拼联络图，供逐张目视确认「大字落在空白带、没压人」。

实测教训（2026-09-28 金维他烟酰胺条3）：镜14「维C维E」自动落位正压在小绿人左臂二头肌上
（字框内主体占比 0.185）；手钉到空白带后降到 0.001。

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/kwcheck.py
  OUT="/Volumes/陈灿会展/广告视频/<分类>/<品牌>/<日期>"

  for k in 1 2 3; do
    $PY $S --config config_$k.json \
           --video "$OUT/<片名>_${k}_1080P.mp4" \
           --out   qc/kwsheet_$k.jpg
  done
"""
import argparse
import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw

FF = "/Users/mac/.local/bin/ffmpeg"


def main():
    ap = argparse.ArgumentParser(description="重点字落帧核验（抽帧拼联络图）")
    ap.add_argument("--config", required=True, help="config_<k>.json")
    ap.add_argument("--video", required=True, help="渲染出的成片 mp4")
    ap.add_argument("--out", required=True, help="输出联络图 jpg")
    ap.add_argument("--cols", type=int, default=6, help="联络图列数（默认 6）")
    ap.add_argument("--cell-w", type=int, default=320, help="单元宽（默认 320）")
    ap.add_argument("--lead", type=float, default=0.35, help="大字入场动画补偿秒数（默认 0.35）")
    ap.add_argument("--frame-dir", help="单帧缓存目录（默认 <out 同目录>/kwsrc_<config 名>）")
    args = ap.parse_args()

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import shot_plan as sp

    cfg = json.load(open(args.config, encoding="utf-8"))
    tl, _ = sp.build_timeline(cfg)
    kws = cfg.get("keywords", [])
    if not kws:
        print("无 keywords，跳过")
        return
    if not os.path.exists(args.video):
        sys.exit(f"找不到成片：{args.video}")

    d = args.frame_dir or os.path.join(
        os.path.dirname(os.path.abspath(args.out)) or ".",
        "kwsrc_" + os.path.splitext(os.path.basename(args.config))[0])
    os.makedirs(d, exist_ok=True)

    rows = []
    for item in kws:
        ph, kw = item["phrase"], item["kw"]
        if ph < 1 or ph > len(tl):
            print(f"⚠️ phrase {ph} 超出镜数 {len(tl)}，跳过「{kw}」")
            continue
        s, e = tl[ph - 1][1], tl[ph - 1][2]
        txt = tl[ph - 1][0]["text"]
        plain = re.sub(r"[^\w]", "", txt)
        idx = plain.find(re.sub(r"[^\w]", "", kw))
        ratio = (idx / max(len(plain), 1)) if idx >= 0 else 0.3
        t = min(s + ratio * (e - s) + args.lead, e - 0.05)
        f = os.path.join(d, f"kw{ph:02d}.jpg")
        subprocess.run([FF, "-v", "error", "-ss", f"{t:.2f}", "-i", args.video,
                        "-frames:v", "1", "-vf", f"scale={args.cell_w}:-1", "-q:v", "3", "-y", f],
                       check=False)
        if os.path.exists(f):
            rows.append((f"p{ph} 【{kw}】 {t:.2f}s", Image.open(f)))
        else:
            print(f"⚠️ 抽帧失败 p{ph} @ {t:.2f}s")

    if not rows:
        sys.exit("没有抽到任何帧")

    ch = rows[0][1].height + 20
    sheet = Image.new("RGB", (args.cols * args.cell_w,
                              ((len(rows) + args.cols - 1) // args.cols) * ch), (18, 18, 18))
    dr = ImageDraw.Draw(sheet)
    for i, (lab, im) in enumerate(rows):
        r, c = divmod(i, args.cols)
        sheet.paste(im, (c * args.cell_w, r * ch + 20))
        dr.text((c * args.cell_w + 5, r * ch + 5), lab, fill=(120, 255, 160))
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    sheet.save(args.out, quality=85)
    print(f"{len(rows)} 个重点字 → {args.out}  {sheet.size}")
    print("★ 逐张目视：大字须落在空白带；若压主体，在 config 的 keywords 项里加 pos=[x,y] 手钉重渲")


if __name__ == "__main__":
    main()
