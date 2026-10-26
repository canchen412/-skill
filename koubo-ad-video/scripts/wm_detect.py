#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ImageGen「AI生成 WORKBUDDY」水印检测。
原理：水印是**半透明白色叠加**，在右下角固定区域让像素比「局部背景」更亮。
     局部背景用大核形态学闭运算估计（闭运算会抹掉比结构元小的亮笔画）。
用法: python wm_detect.py [--scan] [--fix]
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import os, glob, json, sys
import numpy as np
import cv2

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
LIB = os.environ.get("AD_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库"
# 右下角搜索区（相对宽高）
RX0, RY0 = 0.72, 0.78
K = 41  # 闭运算核（须大于笔画宽度）


def load(name):
    hits = glob.glob(os.path.join(LIB, "**", name), recursive=True)
    return hits[0] if hits else None


def analyze(path):
    img = cv2.imread(path)
    if img is None:
        return None
    h, w = img.shape[:2]
    x0, y0 = int(w * RX0), int(h * RY0)
    roi = img[y0:h, x0:w].astype(np.float32)
    # 背景估计：闭运算（先膨胀后腐蚀）抹掉亮笔画
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (K, K))
    bg = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, ker)
    bg = cv2.GaussianBlur(bg, (0, 0), 6)
    d = (roi - bg).max(axis=2)          # 亮度超出量（水印笔画为正）
    dn = np.clip(d, 0, 255)
    frac = float((dn > 14).mean())      # 超阈值像素占比
    strong = float((dn > 30).mean())
    return {"file": os.path.basename(path), "path": path, "size": f"{w}x{h}",
            "roi_frac": round(frac, 4), "strong_frac": round(strong, 4),
            "maxd": round(float(dn.max()), 1)}


if __name__ == "__main__":
    names, seen = [], set()
    for v in ("s1", "s2", "s3"):
        c = json.load(open(f"{W}/{v}/config.json"))
        for p in c["phrases"]:
            b = os.path.basename(p.get("src") or "")
            if b and b not in seen:
                seen.add(b); names.append(b)
    res = []
    for n in names:
        p = load(n)
        if not p:
            continue
        r = analyze(p)
        if r:
            res.append(r)
    res.sort(key=lambda r: -r["roi_frac"])
    json.dump(res, open(f"{W}/qc/wm_detect.json", "w"), ensure_ascii=False, indent=1)
    print(f"{'素材':<44} {'尺寸':>10} {'占比%':>7} {'强%':>6} {'峰值':>6}  判定")
    for r in res:
        verdict = "🔴 有水印" if r["roi_frac"] > 0.020 and r["strong_frac"] > 0.004 else (
            "🟡 疑似" if r["roi_frac"] > 0.010 else "  ok")
        print(f"{r['file'][:42]:<44} {r['size']:>10} {r['roi_frac']*100:>6.2f} {r['strong_frac']*100:>5.2f} {r['maxd']:>6.1f}  {verdict}")
    n_bad = sum(1 for r in res if r["roi_frac"] > 0.020 and r["strong_frac"] > 0.004)
    print(f"\n合计 {len(res)} 张，判有水印 {n_bad} 张")
