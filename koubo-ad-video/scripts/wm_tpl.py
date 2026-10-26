#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""反解「AI生成 WORKBUDDY」水印 alpha 模板。

原理：水印是半透明白 P = B + a*(255-B)  =>  a = (P-B)/(255-B)
      背景 B 用大核闭运算估计（闭运算抹掉比核小的亮笔画）。
      深色背景上 a 反解最准 ⇒ 用深背景标定图，逐像素取中位数得模板。
      模板形状与目标图背景无关 ⇒ 可以拿去做 mask，浅色背景也不会漏。
产出：qc/wm_tpl_wb.npy  +  qc/wm_tpl_preview.jpg
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import os, glob, json
import numpy as np
import cv2
from PIL import Image

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
LIB = os.environ.get("AD_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库"
MH, MW = 0.070, 0.140          # 模板框（相对图高的高 / 宽）
KER = 11                        # 闭运算核
CAL = [                         # 标定图（深背景、目视确认有水印）
    "产品_托新款粉瓶竖大拇指_中景.png",
    "产品_举新款粉瓶过顶_健身房_中景.png",
]


def roialpha(path):
    img = cv2.imread(path)
    h, w = img.shape[:2]
    mh, mw = int(round(h * MH)), int(round(h * MW))
    roi = img[h - mh:h, w - mw:w].astype(np.float32)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (KER, KER))
    bg = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, ker)
    bg = cv2.GaussianBlur(bg, (0, 0), 2.0)
    a = (roi - bg) / np.clip(255.0 - bg, 30.0, None)
    return np.clip(a.max(axis=2), 0, 1), (mh, mw)


if __name__ == "__main__":
    stack = []
    shape = None
    for n in CAL:
        p = glob.glob(os.path.join(LIB, "**", n), recursive=True)
        if not p:
            print("缺标定图", n); continue
        a, (mh, mw) = roialpha(p[0])
        if shape is None:
            shape = (mh, mw)
        elif (mh, mw) != shape:
            a = cv2.resize(a, (shape[1], shape[0]))
        stack.append(a)
        print(f"标定 {n}  ROI {mw}x{mh}  alpha峰值 {a.max():.3f}")
    tpl = np.median(np.stack(stack), axis=0)          # 逐像素中位数
    tpl = cv2.GaussianBlur(tpl, (3, 3), 0.8)
    tpl = tpl / max(tpl.max(), 1e-6)
    np.save(f"{W}/qc/wm_tpl_wb.npy", tpl)
    print(f"模板 {tpl.shape[1]}x{tpl.shape[0]}  峰值 {tpl.max():.3f}  "
          f"覆盖(>0.15) {100*(tpl>0.15).mean():.1f}%")
    v = (np.clip(tpl, 0, 1) * 255).astype(np.uint8)
    v = cv2.resize(v, (v.shape[1] * 4, v.shape[0] * 4), interpolation=cv2.INTER_NEAREST)
    v = cv2.applyColorMap(v, cv2.COLORMAP_INFERNO)
    cv2.imwrite(f"{W}/qc/wm_tpl_preview.jpg", v)
    print("→", f"{W}/qc/wm_tpl_preview.jpg")
