#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
重点字落位终选定：在候选网格里找真正不压主体的矩形
====================================================
比 find_blank 更进一步：直接检查「字将要占据的那块矩形」平不平。
  · 窗口 = 字的实际包围盒（含描边余量）
  · 排除：底部字幕带 y>880 / 产品卡矩形 / 画面外溢出
  · 打分 = 窗口内边缘密度均值（低=平=不压主体）
"""
import os
import numpy as np
import cv2

H, W = 1080, 1920
G = "/Users/mac/WorkBuddy/2026-10-05-09-33-49/jisuan_alan_video_261006/gen"
P = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/肌酸/爱兰纯/图片"

CARD = (1500, 90, 1900, 400)      # 产品卡占位 x0,y0,x1,y1（镜9-13 生效）
SUB  = 880                        # 字幕带上沿

TARGETS = [
    # idx, path, 词, 半宽, 半高, 是否受产品卡影响
    (5,  f"{G}/r15.png",                        "CountUp含量", 250, 130, False),
    (6,  f"{G}/t_mirror.png",                   "超不超标",     280,  80, False),
    (8,  f"{G}/t_run2.png",                     "检测费",       210,  80, False),
    (10, f"{P}/产品_举罐过顶竖拇指_健身房_中景.png",  "一批批",       210,  80, True),
    (11, f"{P}/产品_开箱肌酸罐与报告纸_中景_16x9.png", "随货",        150,  80, True),
    (13, f"{P}/产品_爱兰纯肌酸配摇杯展示_中景.png",    "三个月",       210,  80, True),
    (15, f"{G}/t_pull.png",                     "随便挑着比",   345,  80, False),
]

XS = [300, 420, 560, 720, 880, 1040, 1200, 1360, 1520, 1660]
YS = [220, 300, 380, 460, 560, 660, 760]


def edge_map(path):
    img = cv2.imread(path)
    img = cv2.resize(img, (W, H))
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return np.abs(cv2.Sobel(g, cv2.CV_32F, 1, 1))


def usable(x, y, hw, hh, card_on):
    """矩形是否完全落在可用区内"""
    if x - hw < 20 or x + hw > W - 20:
        return False
    if y - hh < 150 or y + hh > SUB - 10:
        return False
    if card_on:
        cx0, cy0, cx1, cy1 = CARD
        if not (x + hw < cx0 or x - hw > cx1 or y + hh < cy0 or y - hh > cy1):
            return False
    return True


def main():
    print(f"{'镜':>3}  {'词':<12} {'坐标':<14} {'密度':>7}   备选")
    picks = {}
    for idx, path, word, hw, hh, card_on in TARGETS:
        ae = edge_map(path)
        cands = []
        for y in YS:
            for x in XS:
                if not usable(x, y, hw, hh, card_on):
                    continue
                win = ae[max(0, y - hh):y + hh, max(0, x - hw):x + hw]
                cands.append((win.mean(), x, y))
        cands.sort()
        if not cands:
            print(f"{idx:>3}  {word:<12} ⚠️ 无可用位置")
            continue
        best = cands[0]
        picks[idx] = {"word": word, "x": best[1], "y": best[2], "hw": hw, "hh": hh}
        alts = "  ".join(f"({x},{y}){v:.1f}" for v, x, y in cands[1:4])
        print(f"{idx:>3}  {word:<12} ({best[1]:>4},{best[2]:>3}) {best[0]:>7.2f}   {alts}")
    print()
    print("→ 终选坐标（给 Remotion spec 用）")
    for idx, v in picks.items():
        print(f'   镜{idx:>2} {v["word"]:<8} x={v["x"]:<5} y={v["y"]}')


if __name__ == "__main__":
    main()
