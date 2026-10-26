#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""生成 3:2 裁切目视对比图：原图(带 84.375% 裁切线) | crop_y=0 现状 | crop_y=该镜建议值
用 ffmpeg 复现渲染器的真实裁切规则。
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import os, json, subprocess, sys
from PIL import Image, ImageDraw

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
FF = os.environ.get("FFMPEG") or "/Users/mac/.local/bin/ffmpeg"
TMP = "/tmp/cropfix"
os.makedirs(TMP, exist_ok=True)

CUT = 0.84375


def sim(src, cy, out, w=560):
    h = int(round(w * 9 / 16))
    vf = (f"scale=2880:1620:force_original_aspect_ratio=increase,"
          f"crop=2880:1620:0:{cy},scale={w}:{h}:flags=lanczos")
    r = subprocess.run([FF, "-y", "-loglevel", "error", "-i", src, "-vf", vf, "-frames:v", "1", out],
                       capture_output=True)
    return r.returncode == 0


def main(items):
    """items: [(path, need_cy, label)]"""
    CW, CH = 560, 315
    rows = []
    for p, cy, lab in items:
        orig = os.path.join(TMP, os.path.basename(p) + ".orig.png")
        im = Image.open(p).convert("RGB")
        ow = 560
        oh = int(round(im.height * ow / im.width))
        im.resize((ow, oh), Image.LANCZOS).save(orig)
        a = os.path.join(TMP, os.path.basename(p) + ".cy0.png")
        b = os.path.join(TMP, os.path.basename(p) + ".cyN.png")
        sim(p, 0, a)
        sim(p, cy, b)
        rows.append((lab, orig, a, b, cy, oh))
        print(f"  已生成 {lab}  cy0 / cy{cy}")

    # 拼接
    HDR = 34
    maxoh = max(r[5] for r in rows)
    sheet_h = HDR + sum(max(maxoh, CH) + 46 for r in rows)
    sheet = Image.new("RGB", (CW * 3 + 40, sheet_h), (255, 255, 255))
    d = ImageDraw.Draw(sheet)
    y = HDR
    for lab, orig, a, b, cy, oh in rows:
        d.text((10, y - 22), lab, fill=(20, 20, 20))
        # 列1 原图 + 裁切线
        o = Image.open(orig).convert("RGB")
        cd = ImageDraw.Draw(o)
        cy_y = int(oh * CUT)
        cd.line([(0, cy_y), (o.width, cy_y)], fill=(255, 0, 0), width=3)
        cd.rectangle([0, cy_y, o.width, oh], fill=(255, 0, 0, 0), outline=(255, 0, 0), width=2)
        cd.text((6, max(4, cy_y - 16)), "cut 84.375%", fill=(255, 0, 0))
        sheet.paste(o, (0, y))
        # 列2 cy=0
        sheet.paste(Image.open(a).convert("RGB"), (CW + 20, y))
        d = ImageDraw.Draw(sheet)
        d.text((CW + 20, y - 22), "crop_y=0 (当前)", fill=(200, 0, 0))
        # 列3 cy=need
        sheet.paste(Image.open(b).convert("RGB"), (CW * 2 + 40, y))
        d = ImageDraw.Draw(sheet)
        d.text((CW * 2 + 40, y - 22), f"crop_y={cy}", fill=(0, 120, 0))
        y += max(maxoh, CH) + 46
    out = f"{W}/qc/裁切对比.jpg"
    sheet.save(out, quality=90)
    print("→", out)


if __name__ == "__main__":
    aud = json.load(open(f"{W}/qc/crop_audit.json"))
    names = [
        "产品_指空蔬菜格托瓶_中景.png",
        "产品_手持诺特粉瓶配葡萄籽红藻_中景.png",
        "产品_比六手势六瓶排开_中景.png",
        "产品_新款粉瓶配葡萄籽虾青素_中景.png",
        "产品_抱新款粉瓶腾空欢跳_中景.png",
        "产品_仰拍手持新款粉瓶怼镜头_中近景.png",
        "产品_新款粉瓶配果蔬坚果分格盘_中景.png",
        "产品_双手捧烟酰胺罐_中近景.png",
    ]
    items = []
    for r in aud:
        if r["file"] in names and r.get("path"):
            cy = int(r["need_cy"]) if r.get("need_cy") else 200
            items.append((r["path"], cy, r["file"].replace(".png", "").replace(".jpg", "")))
    order = {n: i for i, n in enumerate(names)}
    items.sort(key=lambda t: order.get(t[0].split("/")[-1], 99))
    main(items)
