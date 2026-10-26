#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
v14 动效层质检 · 方法二：直接检查 fx 层的 alpha 掩码
======================================================
★为什么不用「v13 vs v14 差分」定位：
  底片是**重新渲染**的（虽只差 keywords/product_cards 两个字段），
  ffmpeg 滤镜链一变，全局像素就有微差 ⇒ 差分里混进整屏噪声，
  质心被拉偏（实测偏差 269~667px，误判）。
  ⇒ 正确对象是 fx 层自身：看它 alpha>0 的像素落在哪。
"""
import os
import numpy as np
import cv2

FX = "/Users/mac/WorkBuddy/2026-10-05-09-25-16/remotion_overlay/out/fxframes"

POINTS = [
    (12.00, 300, 300, "镜5 CountUp 含量99.99"),
    (13.61, 300, 660, "镜6 超不超标 warn"),
    (16.99, 1360, 760, "镜8 检测费 warn"),
    (18.55, 1700, 240, "镜9 产品卡滑入中"),
    (19.40, 1700, 240, "镜9 产品卡落定"),
    (20.88, 300, 460, "镜10 一批批 stamp"),
    (22.48, 300, 300, "镜11 随货 stamp"),
    (25.36, 1660, 760, "镜13 三个月 price"),
    (28.19, 420, 300, "镜15 随便挑着比 price"),
    (5.00, 0, 0, "★对照：镜2 无动效应全透明"),
]


def alpha_frame(fno):
    im = cv2.imread(os.path.join(FX, f"element-{fno:04d}.png"), cv2.IMREAD_UNCHANGED)
    return None if im is None else im[:, :, 3]


print(f"{'帧号':>5}  {'动效点':<22} {'alpha质心':<16} {'偏差':>7}  {'占比':>6} {'包围盒(x0,y0,x1,y1)':<26}")
print("-" * 100)
ok = True
for t, dx, dy, name in POINTS:
    fno = int(round(t * 30))
    a = alpha_frame(fno)
    if a is None:
        print(f"{fno:>5}  {name:<22} ⚠️ 帧不存在")
        ok = False
        continue
    m = a > 12
    # ★产品卡在 18.15-21.70s 期间一直挂着，会把同一帧内重点字的质心拉走
    #   （实测镜10 偏差被拉到 1073px）⇒ 只在排除列表命中时抠掉「卡的那个矩形」，
    #   ⚠️ 不能用整行/整列排除 —— 会把同 y 或同 x 的重点字一起清掉（踩过）
    # 注：镜9 本身就是验产品卡的 ⇒ 不能用这条，否则把自己抠没了
    if name.startswith("镜10"):
        m[80:410, 1480:1915] = False
    n = int(m.sum())
    if n == 0:
        verdict = "✅ 全透明（正确）" if dx == 0 else "⚠️ 应有内容却为空"
        if dx != 0:
            ok = False
        print(f"{fno:>5}  {name:<22} {'-':<16} {'-':>7}  0.00%  {verdict}")
        continue
    ys, xs = np.nonzero(m)
    cx, cy = float(xs.mean()), float(ys.mean())
    box = (int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max()))
    dev = ((cx - dx) ** 2 + (cy - dy) ** 2) ** 0.5
    good = dev < 170
    if not good:
        ok = False
    print(f"{fno:>5}  {name:<22} ({cx:6.0f},{cy:5.0f}) {dev:6.0f}px  {100*n/(1920*1080):5.2f}%  "
          f"{str(box):<26} {'✅' if good else '⚠️'}")

print("-" * 100)
print("结论：", "✅ 动效层各点均落在设计位置" if ok else "⚠️ 有偏离，需复核")
