#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把重点字层做成「从人物后面半露出来」的效果（用底片做遮罩挖空人物区域）

★为什么需要这个（灿爹 2026-10-07 要的「重点字做出在人物后面半遮住的状态」）：
   重点字是**独立渲染的一层**，ffmpeg overlay 时永远在最上面。
   想让它「在人物后面」，物理上做不到 —— 只能**把人物区域的字挖掉**，
   视觉上就等于字被人物挡住了。

做法：
   1. 从底片取该重点字所在时刻的帧
   2. 转灰度 → 高通/阈值 → 得到「人物/亮部」遮罩
   3. 字层 alpha ×= (1 − 遮罩)   ⇒ 人物处的字变透明
   4. 写回 PNG 序列（仍 4 通道）

用法：
  python3 mask_kw_behind.py <字层目录> <底片mp4> <输出目录> '<镜号:秒:秒,...>'
"""
import os
import re
import subprocess
import sys

import cv2
import numpy as np

FF = "/Users/mac/.local/bin/ffmpeg"


def build_mask(base, dilate=17, blur=21, keep=0.82):
    """从底片帧生成「人物区域」遮罩（白色=人物，黑色=背景）

    ★修正（灿爹反馈第一版几乎看不到字）：
       原用亮度双阈值 70~205 选「人物」⇒ **画面里所有非黑区域都是「人物」**，
       字所在位置也被判成人物 ⇒ 连字一起挖掉。
       改用「饱和度」判人物：小绿人是**高饱和绿**，背景多是低饱和棕/灰。
       `keep` 控制遮挡比例：字被挡 82% ⇒ 只露 18%（半遮效果）。
    """
    hsv = cv2.cvtColor(base, cv2.COLOR_BGR2HSV)
    sat = hsv[:, :, 1]                      # 饱和度 0-255
    val = hsv[:, :, 2]                      # 明度
    # 人物 = 高饱和 且 不是纯黑
    mask = np.where((sat > 70) & (val > 50), 255, 0).astype(np.uint8)
    # 形态学：连成整块 + 膨胀（多挡一些，把「被挡」范围扩大）
    k = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=3)
    mask = cv2.morphologyEx(mask, cv2.MORPH_DILATE, np.ones((dilate, dilate), np.uint8), iterations=1)
    # 羽化边缘
    mask = cv2.GaussianBlur(mask, (blur, blur), 0)
    # ★ 只保留 keep 比例的遮挡强度（半遮 ⇒ 不是全挖）
    mask = (mask.astype(np.float32) * keep).astype(np.uint8)
    return mask


def main():
    kw_dir, base_mp4, out_dir, spans = sys.argv[1:5]
    os.makedirs(out_dir, exist_ok=True)
    # spans: "1:14.3:16.1,2:21.3:23.2,..."
    zones = []
    for s in spans.split(","):
        idx, t0, t1 = s.split(":")
        zones.append((int(idx), float(t0), float(t1)))

    base = cv2.VideoCapture(base_mp4)
    fps = base.get(cv2.CAP_PROP_FPS) or 30

    frames = sorted(f for f in os.listdir(kw_dir) if f.endswith(".png"))
    n = len(frames)
    touched = 0

    for i, name in enumerate(frames):
        path = os.path.join(kw_dir, name)
        im = cv2.imread(path, cv2.IMREAD_UNCHANGED)
        if im is None or im.ndim != 3 or im.shape[2] != 4:
            continue
        t = i / fps
        # 找该帧落在哪个重点字区间
        for idx, t0, t1 in zones:
            if not (t0 <= t <= t1):
                continue
            base.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
            ok, bf = base.read()
            if not ok:
                break
            mk = build_mask(bf)
            # 字层 alpha：已有 alpha × (1 - 人物占比)  ⇒ 人物处的字被挖掉
            inv = 255 - mk
            # 中间最实（字被完全挡住），边缘羽化
            a = im[:, :, 3].astype(np.float32) * (inv.astype(np.float32) / 255.0)
            im[:, :, 3] = np.clip(a, 0, 255).astype(np.uint8)
            cv2.imwrite(os.path.join(out_dir, name), im)
            touched += 1
            break
        else:
            # 不在任何区间 ⇒ 原样复制
            cv2.imwrite(os.path.join(out_dir, name), im)

    base.release()
    print(f"处理 {n} 帧，其中 {touched} 帧被人物遮罩挖空 → {out_dir}")


if __name__ == "__main__":
    main()
