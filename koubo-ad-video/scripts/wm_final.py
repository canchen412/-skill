#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去「AI生成 WORKBUDDY」水印 —— 定稿：左侧同高纹理 + 泊松融合。

为什么用这个而不是 inpaint / 中位数重建（实测对比）：
  · inpaint(TELEA)：笔画被打断后逐点填充 -> 留下散点
  · 中位数背景重建：平滑背景完美，但**结构化背景**（器械横杆/沙发边）被抹成黑块
  · 左侧同高纹理 + seamlessClone(NORMAL_CLONE)：纹理结构完整保留、边界无缝  ✅ 实测最佳
前提：水印右侧紧贴图边，取 x[W-2mw, W-mw] 的左侧块自身无水印（水印只占最右 mw）。
用法: python wm_final.py [--apply]
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import os, glob, json, shutil, argparse
import numpy as np
import cv2

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
LIB = os.environ.get("AD_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库"
CL = f"{W}/assets_clean"
TPL = f"{W}/qc/wm_tpl_wb.npy"
MH, MW = 0.070, 0.140
SCORE_TH = 0.045


def find(name):
    h = glob.glob(os.path.join(LIB, "**", name), recursive=True)
    return h[0] if h else None


def score_of(img, tpl):
    h, w = img.shape[:2]
    mh, mw = int(round(h * MH)), int(round(h * MW))
    roi = img[h - mh:h, w - mw:w].astype(np.float32)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    bg = cv2.GaussianBlur(cv2.morphologyEx(roi, cv2.MORPH_CLOSE, ker), (0, 0), 2.0)
    a = (roi - bg).max(axis=2) / np.clip(255.0 - bg.min(axis=2), 30.0, None)
    t = cv2.resize(tpl, (mw, mh), interpolation=cv2.INTER_AREA)
    tm = t > 0.20
    return float(a[tm].mean()) if tm.any() else 0.0


def remove(img, mh, mw):
    h, w = img.shape[:2]
    y0, x0 = h - mh, w - mw
    src = np.zeros_like(img)
    src[y0:y0 + mh, x0:x0 + mw] = img[y0:y0 + mh, x0 - mw:x0]     # 左侧同高块
    m = np.zeros((h, w), np.uint8)
    m[y0:y0 + mh, x0:x0 + mw] = 255
    return cv2.seamlessClone(src, img, m, (x0 + mw // 2, y0 + mh // 2), cv2.NORMAL_CLONE)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    tpl = np.load(TPL)
    names, seen = [], set()
    for v in ("s1", "s2", "s3"):
        c = json.load(open(f"{W}/{v}/config.json"))
        for p in c["phrases"]:
            b = os.path.basename(p.get("src") or "")
            if b and b not in seen:
                seen.add(b); names.append(b)
    if a.apply:
        os.makedirs(CL, exist_ok=True)
    rows = []
    for n in names:
        p = find(n)
        if not p:
            continue
        img = cv2.imread(p, cv2.IMREAD_COLOR)
        if img is None:
            continue
        s = score_of(img, tpl)
        has = s > SCORE_TH
        rows.append((n, s, has))
        if a.apply:
            dst = os.path.join(CL, n)
            if not has:
                shutil.copy2(p, dst)                     # 无水印 -> 逐位复制
                continue
            h, w = img.shape[:2]
            out = remove(img, int(round(h * MH)), int(round(h * MW)))
            if n.lower().endswith((".jpg", ".jpeg")):
                cv2.imwrite(dst, out, [cv2.IMWRITE_JPEG_QUALITY, 96])
            else:
                cv2.imwrite(dst, out, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    rows.sort(key=lambda r: -r[1])
    bad = [r for r in rows if r[2]]
    print(f"共 {len(rows)} 张；判有水印并处理 {len(bad)} 张：")
    for n, s, _ in bad:
        print(f"  {n[:46]:<48} score={s:.4f}")
    print("模式：" + ("已写出 → " + CL if a.apply else "dry-run"))
