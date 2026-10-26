#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""水印 ROI 接触表：把每张素材的三个「水印可能位置」切下来拼图，一次目视全部。
ROI: 右下(小云雀 & ImageGen) / 左上(部分平台固定「AI生成」) / 左中(小云雀漂移位)
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import os, json, glob, sys
import numpy as np
from PIL import Image, ImageDraw

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
LIB = os.environ.get("AD_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库"

ROIS = {
    "右下": (0.78, 0.82, 1.00, 1.00),
    "左上": (0.00, 0.00, 0.24, 0.16),
    "左中": (0.00, 0.38, 0.24, 0.62),
}
CW, CH = 260, 130


def tiles(names, roi_key, zoom=1.6):
    x0f, y0f, x1f, y1f = ROIS[roi_key]
    out = []
    for n in names:
        hits = glob.glob(os.path.join(LIB, "**", n), recursive=True)
        if not hits:
            continue
        im = Image.open(hits[0]).convert("RGB")
        w, h = im.size
        box = (int(w * x0f), int(h * y0f), int(w * x1f), int(h * y1f))
        r = im.crop(box)
        r = r.resize((int(r.width * zoom), int(r.height * zoom)), Image.LANCZOS)
        # 统一贴到 CW x CH 白底
        c = Image.new("RGB", (CW, CH), (245, 245, 245))
        sc = min(CW / r.width, CH / r.height)
        r2 = r.resize((max(1, int(r.width * sc)), max(1, int(r.height * sc))), Image.LANCZOS)
        c.paste(r2, ((CW - r2.width) // 2, (CH - r2.height) // 2))
        out.append((n, c))
    return out


def sheet(items, out, cols=6, label=True):
    LH = 16
    rows = (len(items) + cols - 1) // cols
    sh = Image.new("RGB", (cols * (CW + 6) + 6, rows * (CH + LH + 6) + 6), (255, 255, 255))
    d = ImageDraw.Draw(sh)
    for i, (n, im) in enumerate(items):
        r, c = divmod(i, cols)
        x = c * (CW + 6) + 3
        y = r * (CH + LH + 6) + 3
        sh.paste(im, (x, y))
        if label:
            d.text((x, y + CH + 1), n.replace(".png", "").replace(".jpg", "")[:26], fill=(0, 0, 0))
    sh.save(out, quality=88)
    print("→", out, sh.size)


if __name__ == "__main__":
    names, seen = [], set()
    for v in ("s1", "s2", "s3"):
        c = json.load(open(f"{W}/{v}/config.json"))
        for p in c["phrases"]:
            b = os.path.basename(p.get("src") or "")
            if b and b not in seen:
                seen.add(b)
                names.append(b)
    print(f"共 {len(names)} 张素材")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if only:
        it = tiles(names, only)
        it.sort(key=lambda t: t[0])
        sheet(it, f"{W}/qc/水印_{only}.jpg")
    else:
        for k in ROIS:
            it = tiles(names, k)
            it.sort(key=lambda t: t[0])
            sheet(it, f"{W}/qc/水印_{k}.jpg")
