#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""img_to_motion.py —— 单张静图 → 「有镜头感」的动态短片（0 积分，纯本地）

为什么存在：成片里绝大多数镜是静图，仅靠推拉摇仍像"会动的照片"。
本工具把六层动效叠在一张图上，观感接近实拍/视频：

  ① 主运动   缓出推近/拉远 + 斜向漂移（比 ffmpeg zoompan 更自由的 Ken Burns）
  ② 对焦呼吸 入画 0.45s「由虚合实」+ 中段轻微呼吸（模拟镜头自动对焦）
  ③ 手持微晃 三个不同频率正弦叠加（高频小振幅＝真手持，不是匀速平移）
  ④ 光效     斜向光束扫过 + 高光点脉冲（镜头光晕感）
  ⑤ 漂浮粒子 尘埃/光斑，按场景色温着色，视差速度（近快远慢）
  ⑥ 画质层   胶片颗粒 + 暗角 + 起手色差脉冲（RGB 分离）

★全部程序化，0 积分；1920×1080/30fps，4s ≈ 120 帧约 20~40s 渲完。

用法：
  $PY img_to_motion.py 输入图 输出.mp4 [--dur 4] [--fps 30]
      [--kind product|person|food|scene]   # 决定粒子色温/光效强度
      [--motion in|out|pan_l|pan_r]        # 主运动方向
      [--intensity 1.0]                    # 全局幅度
      [--no-dust] [--no-beam]              # 关单项
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys

import cv2
import numpy as np

FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")

# 场景 → (粒子色 RGB, 光束强度, 光晕色温)
KINDS = {
    "product": ((255, 248, 224), 0.16, (255, 246, 214)),
    "person":  ((255, 240, 228), 0.13, (255, 238, 220)),
    "food":    ((255, 244, 226), 0.15, (255, 240, 210)),
    "scene":   ((214, 236, 255), 0.11, (226, 240, 255)),
}


def cover_to(im: np.ndarray, W: int, H: int) -> np.ndarray:
    """等比放大到铺满 W×H（超出部分居中裁掉），零变形。"""
    h, w = im.shape[:2]
    k = max(W / w, H / h)
    nw, nh = int(round(w * k)), int(round(h * k))
    r = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
    x0, y0 = (nw - W) // 2, (nh - H) // 2
    return r[y0:y0 + H, x0:x0 + W].copy()


def ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 3


def build_vignette(W: int, H: int, strength: float = 0.30) -> np.ndarray:
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    cx, cy = W / 2, H / 2
    r = np.sqrt(((xx - cx) / (W * 0.62)) ** 2 + ((yy - cy) / (H * 0.62)) ** 2)
    return (1.0 - strength * np.clip(r - 0.55, 0, 1.6) ** 1.4).clip(0.55, 1.0)[..., None]


def make_dust(n: int, W: int, H: int, rng: np.random.Generator):
    """粒子：近处大而快、远处小而慢（视差）。"""
    ps = []
    for _ in range(n):
        depth = rng.random()          # 0=远 1=近
        ps.append(dict(
            x=float(rng.random() * W), y=float(rng.random() * H),
            r=float(2.0 + 7.0 * depth),
            vx=float((rng.random() - 0.3) * (12 + 40 * depth)),
            vy=float(-(6 + 26 * depth) * (0.5 + rng.random())),
            a=float(0.10 + 0.28 * depth),
            ph=float(rng.random() * 6.283),
        ))
    return ps


def render_frame(i: int, n: int, base: np.ndarray, W: int, H: int,
                 cfg: dict, dust, vig: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    t = i / max(1, n - 1)
    I = cfg["intensity"]
    # ease: "out"=三次缓出（快起慢收，默认）｜"inout"=smoothstep（全程均匀，适合长镜）
    E = ease_out(t) if cfg.get("ease", "out") == "out" else t * t * (3 - 2 * t)

    z0, z1 = cfg["z0"], cfg["z1"]
    z = z0 + (z1 - z0) * E
    # 极缓慢呼吸（低频，非抖动）：周期 ≈ 镜长，幅度小
    br = float(cfg.get("breath", 0.0))
    if br:
        z += br * math.sin(2 * math.pi * 0.5 * t)
    mx = (z - 1.0) * W / 2          # 该缩放下的可位移余量
    my = (z - 1.0) * H / 2

    # ① 主运动：斜向漂移（缓出）
    dx = cfg["dx"] * I * mx * (0.35 + 0.65 * E)
    dy = cfg["dy"] * I * my * (0.35 + 0.65 * E)

    # ③ 手持微晃：三频叠加（永不匀速）—— cfg["shake"]=0 可整体关掉
    sh = 0.010 * I * float(cfg.get("shake", 1.0))
    sx = (1.30 * math.sin(2 * math.pi * 1.7 * t + 0.4)
          + 0.75 * math.sin(2 * math.pi * 3.9 * t + 1.9)
          + 0.40 * math.sin(2 * math.pi * 7.3 * t + 3.1)) * sh * W
    sy = (1.10 * math.cos(2 * math.pi * 1.3 * t + 1.1)
          + 0.65 * math.cos(2 * math.pi * 4.3 * t + 2.4)
          + 0.35 * math.cos(2 * math.pi * 8.1 * t + 0.6)) * sh * H

    # ② 对焦：入画 0.45s 由虚合实 + 中段呼吸（cfg["focus"]=0 可整镜关掉）
    fk = float(cfg.get("focus", 1.0))
    if fk > 0:
        tc = min(1.0, t / 0.45)
        sigma = (5.5 * (1.0 - tc) ** 2
                 + (0.55 + 0.45 * math.sin(2 * math.pi * 0.45 * t)) * 0.55 * I) * fk
    else:
        sigma = 0.0

    tx, ty = -dx + sx, -dy + sy
    tx = float(np.clip(tx, -mx * 0.98, mx * 0.98))
    ty = float(np.clip(ty, -my * 0.98, my * 0.98))

    M = np.float32([[z, 0, tx], [0, z, ty]])
    fr = cv2.warpAffine(base, M, (W, H), flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    if sigma > 0.35:
        fr = cv2.GaussianBlur(fr, (0, 0), sigma)

    f = fr.astype(np.float32)

    # ⑤ 漂浮粒子（screen 叠加）
    pc = np.array(cfg["dust_rgb"], np.float32)
    for p in dust:
        px = (p["x"] + p["vx"] * t * cfg["dur"]) % (W + 80) - 40
        py = (p["y"] + p["vy"] * t * cfg["dur"]) % (H + 80) - 40
        a = p["a"] * (0.55 + 0.45 * math.sin(2 * math.pi * 0.8 * t + p["ph"]))
        if a <= 0.01:
            continue
        r = int(max(2, p["r"] * (1 + 0.06 * math.sin(2 * math.pi * 0.9 * t + p["ph"]))))
        x0, x1 = int(px) - r, int(px) + r
        y0, y1 = int(py) - r, int(py) + r
        if x1 < 0 or y1 < 0 or x0 >= W or y0 >= H:
            continue
        x0c, y0c, x1c, y1c = max(0, x0), max(0, y0), min(W, x1), min(H, y1)
        yy, xx = np.mgrid[y0c:y1c, x0c:x1c]
        d = np.sqrt((xx - px) ** 2 + (yy - py) ** 2) / max(1, r)
        g = np.clip(1.0 - d, 0, 1) ** 2 * a
        roi = f[y0c:y1c, x0c:x1c]
        # screen 叠加：out = 255-(255-roi)*(1-g) + color*g
        out = 255.0 - (255.0 - roi) * (1.0 - g[..., None]) + pc * g[..., None]
        f[y0c:y1c, x0c:x1c] = np.clip(out, 0, 255)

    # ④ 斜向光束扫过（screen）
    if cfg["beam"] > 0:
        bt = (t - 0.18) / 0.62                     # 0.18~0.80 之间扫过
        if 0.0 < bt < 1.0:
            cx = -0.35 * W + bt * (1.55 * W)
            sig = W * 0.075
            xs = np.arange(W, dtype=np.float32)
            prof = np.exp(-((xs - cx) ** 2) / (2 * sig * sig))
            amp = cfg["beam"] * math.sin(math.pi * bt) ** 0.7
            tilt = 0.20                                  # 斜度
            band = np.zeros((H, W), np.float32)
            for yy in range(H):
                off = int((yy - H / 2) * tilt)
                band[yy] = np.roll(prof, off)
            g = (band * amp)[..., None]
            bc = np.array(cfg["beam_rgb"], np.float32)
            f = np.clip(255.0 - (255.0 - f) * (1.0 - g) + bc * g, 0, 255)

    # ⑥ 起手色差脉冲（前 0.35s，RGB 分离）
    if t < 0.35:
        k = int(round(6 * (1 - t / 0.35) * I))
        if k > 0:
            g = f[:, :, 1].copy()
            f[:, :, 2] = np.roll(f[:, :, 2], k, axis=1)
            f[:, :, 0] = np.roll(f[:, :, 0], -k, axis=1)
            f[:, :, 1] = g

    # 暗角 + 颗粒
    f *= vig
    if cfg["grain"] > 0:
        noise = rng.normal(0, cfg["grain"], (H, W, 1)).astype(np.float32)
        f += noise
    return np.clip(f, 0, 255).astype(np.uint8)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("out")
    ap.add_argument("--dur", type=float, default=4.0)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--kind", default="product", choices=list(KINDS))
    ap.add_argument("--motion", default="in", choices=["in", "out", "pan_l", "pan_r"])
    ap.add_argument("--intensity", type=float, default=1.0)
    ap.add_argument("--grain", type=float, default=3.2)
    ap.add_argument("--no-dust", action="store_true")
    ap.add_argument("--no-beam", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    a = ap.parse_args()

    W, H = 1920, 1080
    n = max(2, int(round(a.dur * a.fps)))
    rng = np.random.default_rng(a.seed)

    src = cv2.imread(a.src, cv2.IMREAD_COLOR)
    if src is None:
        sys.exit(f"读不到图：{a.src}")
    base = cover_to(src, W, H)

    dust_rgb, beam_amp, beam_rgb = KINDS[a.kind]
    cfg = dict(
        intensity=a.intensity, dur=a.dur, grain=a.grain,
        z0=1.085, z1=1.150,                     # 基准放 1.085 留出晃动/位移余量
        dx=0.0, dy=0.0,
        dust_rgb=dust_rgb,
        beam=(0.0 if a.no_beam else beam_amp), beam_rgb=beam_rgb,
    )
    if a.motion == "in":
        cfg.update(z0=1.090, z1=1.155, dx=-0.20, dy=-0.10)
    elif a.motion == "out":
        cfg.update(z0=1.155, z1=1.090, dx=0.20, dy=0.10)
    elif a.motion == "pan_l":
        cfg.update(z0=1.120, z1=1.130, dx=0.85, dy=-0.12)
    else:
        cfg.update(z0=1.120, z1=1.130, dx=-0.85, dy=0.12)

    dust = [] if a.no_dust else make_dust(56, W, H, rng)
    vig = build_vignette(W, H, 0.30)

    cmd = [FFMPEG, "-v", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
           "-r", str(a.fps), "-i", "-",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", a.out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for i in range(n):
        fr = render_frame(i, n, base, W, H, cfg, dust, vig, rng)
        p.stdin.write(fr.tobytes())
        if (i + 1) % 30 == 0:
            print(f"  …{i+1}/{n} 帧", flush=True)
    p.stdin.close()
    p.wait()
    print(f"✅ 输出 {a.out}  ({n} 帧 / {a.dur}s / {W}x{H})")


if __name__ == "__main__":
    main()
