#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 alpha 模板去「AI生成 WORKBUDDY」水印（模板形状约束 + 低阈值 alpha）。

mask = (a_est > A_TH) & (tpl_dilate > T_TH)
  a_est = 局部亮度超出量 / (255-局部背景)   ← 与背景明暗无关
  模板形状约束 ← 排除跑道白线/高光等假阳性
判定有水印：score = mean(a_est[tpl>0.20]) > SCORE_TH
用法: python wm_apply.py [--apply]
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
KER = 11
A_TH = 0.035        # alpha 阈值（低：保证浅背景不漏）
T_TH = 0.10         # 模板形状阈值
SCORE_TH = 0.045    # 判有水印


def find(name):
    h = glob.glob(os.path.join(LIB, "**", name), recursive=True)
    return h[0] if h else None


def run(img, tpl):
    h, w = img.shape[:2]
    mh, mw = int(round(h * MH)), int(round(h * MW))
    roi = img[h - mh:h, w - mw:w].astype(np.float32)
    ker = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (KER, KER))
    bg = cv2.morphologyEx(roi, cv2.MORPH_CLOSE, ker)
    bg = cv2.GaussianBlur(bg, (0, 0), 2.0)
    a = (roi - bg) / np.clip(255.0 - bg, 30.0, None)      # 限制放大倍数，防浅背景噪声爆炸
    a = a.max(axis=2)                                     # 亮度超出量取最大通道
    t = cv2.resize(tpl, (mw, mh), interpolation=cv2.INTER_AREA)
    tm = t > 0.20
    score = float(a[tm].mean()) if tm.any() else 0.0
    # 模板膨胀成连片形状
    td = cv2.dilate((t > T_TH).astype(np.uint8), np.ones((5, 5), np.uint8), iterations=2)
    m = ((a > A_TH) & (td > 0)).astype(np.uint8)
    # 清掉孤立小点
    n, lab, st, _ = cv2.connectedComponentsWithStats(m, 8)
    keep = np.zeros_like(m)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= 3:
            keep[lab == i] = 1
    m = keep * 255
    full = np.zeros((h, w), np.uint8)
    full[h - mh:h, w - mw:w] = m
    return full, score, int((m > 0).sum())


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
        m, score, px = run(img, tpl)
        has = score > SCORE_TH
        rows.append((n, score, px, has))
        if a.apply:
            dst = os.path.join(CL, n)
            if not has:
                shutil.copy2(p, dst)               # 无水印 -> 逐位复制，零损耗
                continue
            dil = cv2.dilate(m, np.ones((5, 5), np.uint8))
            out = cv2.inpaint(img, dil, 4, cv2.INPAINT_TELEA)
            if n.lower().endswith((".jpg", ".jpeg")):
                cv2.imwrite(dst, out, [cv2.IMWRITE_JPEG_QUALITY, 96])
            else:
                cv2.imwrite(dst, out, [cv2.IMWRITE_PNG_COMPRESSION, 3])
    rows.sort(key=lambda r: -r[1])
    print(f"{'素材':<46} {'score':>7} {'maskpx':>7}  判定")
    for n, s, px, has in rows:
        print(f"{n[:44]:<46} {s:>7.4f} {px:>7}  {'🔴 有水印' if has else ''}")
    bad = [r for r in rows if r[3]]
    print(f"\n共 {len(rows)} 张；判有水印 {len(bad)} 张：")
    for r in bad:
        print("  ", r[0], f"score={r[1]:.4f} mask={r[2]}px")
    print("模式：" + ("已写出 → " + CL if a.apply else "dry-run"))
