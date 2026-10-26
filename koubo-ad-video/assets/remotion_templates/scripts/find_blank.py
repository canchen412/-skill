#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重点字落位实测：找每帧画面的「最空区域」
========================================
方法（沿用 curcumin 已验证做法）：
  1. 转灰度 → Sobel 边缘图
  2. 按竖条（bin）统计边缘密度 = 主体/纹理集中区；密度低 = 空白
  3. 再按横条统计，两者交叉 ⇒ 得到候选空白矩形
  ★ 排除区：
    · 底部 y>880   成片 ASS 字幕带
    · 右上 1440-1900 × 60-560  产品卡占位（镜9-13）
"""
import json, os, sys
import numpy as np
import cv2

G = "/Users/mac/WorkBuddy/2026-10-05-09-33-49/jisuan_alan_video_261006/gen"
LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
H, W = 1080, 1920

P = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/肌酸/爱兰纯/图片"
TARGETS = [
    (5,  f"{G}/r15.png",                              "4个9 → CountUp"),
    (6,  f"{G}/t_mirror.png",                         "超不超标 → warn"),
    (8,  f"{G}/t_run2.png",                           "检测费 → warn"),
    (10, f"{P}/产品_举罐过顶竖拇指_健身房_中景.png",        "一批批 → stamp"),
    (11, f"{P}/产品_开箱肌酸罐与报告纸_中景_16x9.png",      "随货 → stamp"),
    (13, f"{P}/产品_爱兰纯肌酸配摇杯展示_中景.png",         "三个月 → price"),
    (15, f"{G}/t_pull.png",                           "随便挑着比 → price"),
]


def analyze(path):
    img = cv2.imread(path)
    if img is None:
        return None
    img = cv2.resize(img, (W, H))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    e = cv2.Sobel(g, cv2.CV_32F, 1, 1)           # 对角边缘，避开纯色块被误判
    ae = np.abs(e)
    return ae


def band_profile(ae, axis=1, bin_px=60, exclude_mask=None):
    """axis=1 → 竖条剖面（按 x 分箱）；axis=0 → 横条剖面（按 y 分箱）"""
    m = np.ones_like(ae, dtype=bool) if exclude_mask is None else exclude_mask
    if axis == 1:
        n = W // bin_px
        prof = np.array([ae[:, i * bin_px:(i + 1) * bin_px][m[:, i * bin_px:(i + 1) * bin_px]].mean()
                         if m[:, i * bin_px:(i + 1) * bin_px].any() else np.nan
                         for i in range(n)])
    else:
        n = H // bin_px
        prof = np.array([ae[i * bin_px:(i + 1) * bin_px, :][m[i * bin_px:(i + 1) * bin_px, :]].mean()
                         if m[i * bin_px:(i + 1) * bin_px, :].any() else np.nan
                         for i in range(n)])
    return prof


def main():
    # 全局排除：底部字幕带
    mask = np.ones((H, W), dtype=bool)
    mask[880:, :] = False

    out = {}
    for idx, path, note in TARGETS:
        ae = analyze(path)
        if ae is None:
            print(f"镜{idx:2d}  ⚠️ 读不到 {path}")
            continue
        # 该产品段镜位再把右上产品卡排除
        m = mask.copy()
        if 9 <= idx <= 13:
            m[60:560, 1440:1900] = False

        col = band_profile(ae, axis=1, exclude_mask=m)     # x 方向
        row = band_profile(ae, axis=0, exclude_mask=m)     # y 方向
        bin_px = 60
        order_c = np.argsort(np.nan_to_num(col, nan=1e9))
        order_r = np.argsort(np.nan_to_num(row, nan=1e9))
        top_c = [(int(i * bin_px + bin_px / 2), int(col[i])) for i in order_c[:4]]
        top_r = [(int(i * bin_px + bin_px / 2), int(row[i])) for i in order_r[:5]]
        out[idx] = {"note": note, "coldest_x": top_c, "coldest_y": top_r}
        print(f"镜{idx:2d}  {note}")
        print(f"      最空竖带 x = {[f'{v[0]}({v[1]})' for v in top_c]}")
        print(f"      最空横带 y = {[f'{v[0]}({v[1]})' for v in top_r]}")
    json.dump(out, open("s3/blank_zones.json", "w"), ensure_ascii=False, indent=1)
    print("\n→ s3/blank_zones.json")


if __name__ == "__main__":
    main()
