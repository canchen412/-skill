#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""add_dust_to_video.py —— 给**已有成片**叠加「漂浮粒子」（画面零改动，只加光点，0 积分）

为什么不直接 ffmpeg `blend=screen`：实测本机 ffmpeg 的 blend 在 YUV/RGB 转换上
会把整帧颜色带偏（输出 BGR 均值变成品红 [216,59,209]），故改为逐帧 Python 合成，
颜色完全可控，且原帧除粒子外零改动。

用法：
  $PY add_dust_to_video.py --src 原片.mp4 --out 加粒子.mp4
        [--count 72] [--strength 1.6] [--warm 1.0] [--seed 2026]
"""
from __future__ import annotations

import argparse
import math
import os
import subprocess
import sys

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from img_to_motion import make_dust  # noqa: E402

FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--count", type=int, default=72)
    ap.add_argument("--strength", type=float, default=1.6, help="粒子亮度倍数")
    ap.add_argument("--warm", type=float, default=1.0, help="1=暖白尘粒，0=冷白")
    ap.add_argument("--size", type=float, default=1.0, help="粒子半径倍数")
    ap.add_argument("--seed", type=int, default=2026)
    ap.add_argument("--table", default=None, help="《分镜—文案—素材对应表》HTML，用来取镜边界")
    ap.add_argument("--focus-every", type=int, default=0,
                    help="每隔 N 镜在镜开头做「对焦呼吸」(0=关)；2=隔一张做一次")
    ap.add_argument("--focus-sec", type=float, default=0.45, help="合焦时长")
    ap.add_argument("--focus-max", type=float, default=5.5, help="起始模糊 sigma")
    a = ap.parse_args()

    cap = cv2.VideoCapture(a.src)
    W = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    H = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    dur = total / fps if total else 0
    rng = np.random.default_rng(a.seed)
    dust = make_dust(a.count, W, H, rng)
    for q in dust:
        q["a"] = min(0.85, q["a"] * 2.1)      # 整体调亮，肉眼可见
    col = np.array([255, 236 - 36 * (1 - a.warm), 198 + 40 * (1 - a.warm)], np.float32)

    shots = []
    if a.table and a.focus_every > 0:
        from motion_fullfilm import parse_table
        shots = [(no, t0, t1) for (no, t0, t1, _t, _s, _h) in parse_table(a.table)]
        print(f"  对焦呼吸：每 {a.focus_every} 镜做一次（共 {len(shots)} 镜边界）")

    def focus_sigma(t_abs: float) -> float:
        if not shots:
            return 0.0
        for no, t0, t1 in shots:
            if t0 <= t_abs < t1:
                if (no - 1) % a.focus_every != 0:
                    return 0.0
                k = (t_abs - t0) / max(1e-6, a.focus_sec)
                return 0.0 if k >= 1.0 else a.focus_max * (1.0 - k) ** 2
        return 0.0

    tmp = a.out + ".v.mp4"
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    i = 0
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        t = (i / max(1, total - 1)) if total else (i / fps / max(dur, 1e-6))
        sig = focus_sigma(i / fps if fps else 0.0)
        if sig > 0.3:
            fr = cv2.GaussianBlur(fr, (0, 0), sig)
        img = fr.astype(np.float32)
        for q in dust:
            px = (q["x"] + q["vx"] * t * dur) % (W + 80) - 40
            py = (q["y"] + q["vy"] * t * dur) % (H + 80) - 40
            al = q["a"] * (0.55 + 0.45 * math.sin(2 * math.pi * 0.8 * t + q["ph"])) * a.strength
            if al <= 0.015:
                continue
            r = int(max(2, q["r"] * a.size * (1 + 0.06 * math.sin(2 * math.pi * 0.9 * t + q["ph"]))))
            x0, x1, y0, y1 = int(px) - r, int(px) + r, int(py) - r, int(py) + r
            x0c, y0c = max(0, x0), max(0, y0)
            x1c, y1c = min(W, x1), min(H, y1)
            if x1c <= x0c or y1c <= y0c:
                continue
            yy, xx = np.mgrid[y0c:y1c, x0c:x1c]
            d = np.sqrt((xx - px) ** 2 + (yy - py) ** 2) / max(1, r)
            g = (np.clip(1.0 - d, 0, 1) ** 2 * al)[..., None]
            roi = img[y0c:y1c, x0c:x1c]
            # screen 混合（在 RGB 上做，颜色保真）
            img[y0c:y1c, x0c:x1c] = 255.0 - (255.0 - roi) * (1.0 - g) + col * g * 0.55
        p.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
        i += 1
        if i % 300 == 0:
            print(f"  …{i}/{total or '?'} 帧", flush=True)
    cap.release()
    p.stdin.close()
    p.wait()
    if p.returncode != 0:
        sys.exit("ffmpeg 写失败")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp, "-i", a.src,
                    "-map", "0:v:0", "-map", "1:a:0?", "-c:v", "copy",
                    "-c:a", "copy", "-shortest", a.out], check=True)
    os.remove(tmp)
    print(f"✅ {i} 帧 → {a.out}")


if __name__ == "__main__":
    main()
