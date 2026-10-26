#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""3:2 素材在 cover+crop_y=0 下的裁切审计。
crop=crop=2880:1620:0:cy  输入 1536x1024 先 scale=2880:1920（1.875x）
  cy=0   -> 取顶部 1620 -> 切掉原图底部 160px（15.625%），裁切线 y_frac=84.375%
  cy=300 -> 取底部 1620 -> 切掉原图顶部 160px
output: per-image 粉标签 bbox + 建议 crop_y + 顶部被切区内容量
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import sys, os, json, glob
import numpy as np
from PIL import Image

LIB = os.environ.get("AD_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库"
CUT = 0.84375           # crop_y=0 时的裁切线（图片高度比例）
HEAD = 160              # 调 crop_y=300 时会切掉的原图顶部像素数


def pink_mask(a):
    r, g, b = a[..., 0].astype(np.int16), a[..., 1].astype(np.int16), a[..., 2].astype(np.int16)
    return (r > 180) & (g > 110) & (g < 195) & (b > 110) & (b < 200) & ((r - g) > 35) & (np.abs(g - b) < 45)


def audit(path):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    if W == 0 or H == 0:
        return None
    a = np.asarray(im)
    # 小图放大测：为速度先缩到高 512
    if H > 640:
        k = 512.0 / H
        im2 = im.resize((max(1, int(W * k)), 512), Image.BILINEAR)
        a = np.asarray(im2)
        H2, W2 = a.shape[:2]
    else:
        H2, W2 = H, W
    m = pink_mask(a)
    rows = m.sum(axis=1)
    cols = m.sum(axis=0)
    res = {"file": os.path.basename(path), "size": f"{W}x{H}", "ratio": round(W / H, 3)}
    if m.sum() < 40:
        res["pink_px"] = int(m.sum())
        res["pink_bot"] = None
        res["need_cy"] = 0
        res["note"] = "无粉标签(非本品产品图/场景图)"
    else:
        ys = np.nonzero(rows >= max(1, int(W2 * 0.002)))[0]
        if ys.size == 0:
            ys = np.nonzero(m)[0]
        y0, y1 = int(ys.min()), int(ys.max())
        fb, ft = y1 / H2, y0 / H2
        need = int(np.ceil(fb * 1920 - 1620))
        res.update({"pink_px": int(m.sum()), "pink_top": round(ft, 4), "pink_bot": round(fb, 4),
                    "need_cy": max(0, min(300, need)), "over": bool(fb > CUT)})
    # 顶部 160px（原图比例）内是否有粉标签 -> 调大 crop_y 会切到
    hb = int(HEAD / H * H2)
    res["top_pink"] = int(m[:hb].sum())
    # 顶部/底部 160px 的内容量（灰度梯度能量，越大越"有东西"）
    g = a.mean(axis=2)
    gx = np.abs(np.diff(g, axis=1)).mean()
    res["gx_ref"] = round(float(gx), 3)
    res["top_energy"] = round(float(np.abs(np.diff(g[:hb], axis=1)).mean()), 3) if hb > 2 else 0
    res["bot_energy"] = round(float(np.abs(np.diff(g[-hb:], axis=1)).mean()), 3) if hb > 2 else 0
    return res


def find(name):
    hits = glob.glob(os.path.join(LIB, "**", name), recursive=True)
    return hits[0] if hits else None


if __name__ == "__main__":
    W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
    names = []
    for v in ("s1", "s2", "s3"):
        c = json.load(open(f"{W}/{v}/config.json"))
        for p in c["phrases"]:
            if p.get("kind") == "img":
                b = os.path.basename(p["src"])
                if b not in names:
                    names.append(b)
    out = []
    for n in names:
        p = find(n)
        if not p:
            out.append({"file": n, "note": "NOT FOUND"})
            continue
        r = audit(p)
        r["path"] = p
        out.append(r)
    json.dump(out, open(f"{W}/qc/crop_audit.json", "w"), ensure_ascii=False, indent=1)
    print(f"{'素材':<46} {'尺寸':>10} {'粉底缘':>7} {'需cy':>5} {'顶粉':>6} {'顶能':>6} {'底能':>6}")
    for r in out:
        if r.get("note") == "NOT FOUND":
            print(f"{r['file'][:44]:<46} NOT FOUND")
            continue
        pb = f"{r['pink_bot']:.3f}" if r.get("pink_bot") is not None else "  -  "
        flag = " ⚠切" if r.get("over") else ("  ok" if r.get("pink_bot") else "  无")
        print(f"{r['file'][:44]:<46} {r['size']:>10} {pb:>7} {r['need_cy']:>5} {r['top_pink']:>6} {r['top_energy']:>6} {r['bot_energy']:>6}{flag}")
