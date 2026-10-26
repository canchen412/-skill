#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成「开场冲击」元素素材（纯程序化，0 积分）。

输出 PNG 全部为 1920x1080 RGBA 透明图，内容刻意画在画面中心、占比偏小，
留给 ffmpeg zoompan 放大 1.0→3~4 倍的空间（z 必须 >= 1，不能靠缩小来做动画）。

素材清单（assets/burst/）：
  rays.png   放射线爆发  —— 能量爆发
  bolt.png   闪电分叉    —— 闪电劈下
  spark.png  粒子点云    —— 粉末炸开
  shock.png  同心冲击环  —— 铁片砸地 / 心跳泵感
  lines.png  斜向速度线  —— 数字冲刺 / 速度感

用法：python make_burst_assets.py [输出目录]
"""
import math
import os
import random
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

W, H = 1920, 1080
CX, CY = W // 2, H // 2
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "assets", "burst")

YY, XX = np.ogrid[:H, :W]
RR = np.sqrt((XX - CX) ** 2.0 + (YY - CY) ** 2.0)


def radial_mask(radius, power=1.0):
    """半径 radius 内从中心 1 衰减到 0 的软掩膜。"""
    return np.clip(1.0 - RR / float(radius), 0.0, 1.0) ** float(power)


def ring_mask(radius, width, power=1.0):
    """以 radius 为峰、width 为宽度的环状软掩膜。"""
    d = np.abs(RR - float(radius))
    return np.clip(1.0 - d / (float(width) * 0.5), 0.0, 1.0) ** float(power)


def save(alpha, name, gamma=1.0):
    a = np.clip(np.asarray(alpha, np.float32), 0.0, 1.0)
    if gamma != 1.0:
        a = a ** gamma
    rgba = np.dstack([
        np.full_like(a, 255.0), np.full_like(a, 255.0), np.full_like(a, 255.0),
        a * 255.0,
    ]).astype(np.uint8)
    p = os.path.join(OUT, name)
    Image.fromarray(rgba, "RGBA").save(p)
    print(f"  {name}  ({os.path.getsize(p) / 1024:.0f} KB)")


def as_alpha(img):
    """取 PIL 图的 alpha 通道为 0..1 浮点数组。"""
    return np.asarray(img.split()[-1], np.float32) / 255.0


def make_rays():
    """放射线爆发：中心白核 + 长短不一的放射线。"""
    random.seed(7)
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    n = 168
    for i in range(n):
        a = 2.0 * math.pi * i / n + random.uniform(-0.010, 0.010)
        L = random.uniform(160.0, 430.0)
        wid = random.choice([2, 3, 4, 6])
        br = int(random.uniform(120, 255))
        d.line([(CX, CY), (CX + math.cos(a) * L, CY + math.sin(a) * L)], fill=br, width=wid)
    rays = as_alpha(img.filter(ImageFilter.GaussianBlur(1.1)))
    a = np.maximum(rays * 0.92, radial_mask(96, 1.7))
    save(a, "rays.png")


def make_bolt():
    """闪电劈下：主干折线 + 分叉，外加辉光。"""
    random.seed(11)
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)

    def branch(x, y, x2, y2, wid, depth):
        pts = [(x, y)]
        steps = 26
        for s in range(1, steps + 1):
            t = s / steps
            jx = x + (x2 - x) * t + random.uniform(-46, 46) * math.sin(math.pi * t)
            jy = y + (y2 - y) * t
            pts.append((jx, jy))
        d.line(pts, fill=255, width=wid, joint="curve")
        if depth > 0:
            for _ in range(random.randint(1, 2)):
                i = random.randint(7, steps - 5)
                bx, by = pts[i]
                ex = bx + random.uniform(-300, 300)
                ey = by + random.uniform(120, 420)
                proj = max(0.0, min(1.0, (ey - 0) / H))
                branch(bx, by, ex, ey, max(2, wid - 4), depth - 1) if proj < 1.0 else None

    branch(CX + 40, -30, CX - 70, H + 30, 13, 2)
    core = as_alpha(img)
    glow = as_alpha(img.filter(ImageFilter.GaussianBlur(26)))
    mid = as_alpha(img.filter(ImageFilter.GaussianBlur(7)))
    a = np.clip(core * 1.0 + mid * 0.55 + glow * 0.55, 0.0, 1.0)
    save(a, "bolt.png")


def make_spark():
    """粒子点云：面积均匀分布 + 亮度随半径衰减，中心留白核。"""
    random.seed(23)
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    for _ in range(3000):
        r = 420.0 * math.sqrt(random.random())
        a = random.uniform(0.0, 2.0 * math.pi)
        x = CX + math.cos(a) * r
        y = CY + math.sin(a) * r * 0.62
        s = random.choice([3, 4, 5, 6, 8, 11])
        br = int(255 * max(0.25, 1.0 - r / 460.0) * random.uniform(0.65, 1.0))
        d.ellipse([x - s, y - s, x + s, y + s], fill=br)
    pts = as_alpha(img.filter(ImageFilter.GaussianBlur(0.7)))
    a = np.maximum(pts, radial_mask(74, 2.2) * 0.95)
    save(a, "spark.png")


def make_shock():
    """同心冲击环：三圈环带 + 中心软光。"""
    a = (ring_mask(210, 60, 1.5) * 1.00
         + ring_mask(330, 46, 1.6) * 0.72
         + ring_mask(455, 34, 1.7) * 0.48
         + radial_mask(150, 1.9) * 0.55)
    save(np.clip(a, 0.0, 1.0), "shock.png")


def make_lines():
    """斜向速度线：外围放射细线，中心留空（避免糊住主体）。"""
    random.seed(31)
    img = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(img)
    r_out = 1400.0
    for _ in range(520):
        a = random.uniform(0.0, 2.0 * math.pi)
        r0 = random.uniform(480.0, 900.0)
        r1 = r0 + random.uniform(180.0, 520.0)
        wid = random.choice([2, 3, 4])
        br = int(random.uniform(90, 235))
        d.line([(CX + math.cos(a) * r0, CY + math.sin(a) * r0),
                (CX + math.cos(a) * min(r1, r_out), CY + math.sin(a) * min(r1, r_out))],
               fill=br, width=wid)
    a = as_alpha(img.filter(ImageFilter.GaussianBlur(1.0))) * 0.9
    save(a, "lines.png")


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    print(f"输出目录: {os.path.normpath(OUT)}")
    make_rays()
    make_bolt()
    make_spark()
    make_shock()
    make_lines()
    print("完成: 5 个冲击元素（0 积分）")
