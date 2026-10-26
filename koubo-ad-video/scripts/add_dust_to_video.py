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
    ap.add_argument("--crf", type=int, default=21,
                    help="交付编码 CRF（★2026-10-15：本脚本曾是「体积爆炸」元凶——"
                         "旧版最后一步 -c:v copy 把中间层 h264_videotoolbox -q:v 75 "
                         "（1080p 实测 19~35 Mbps）原样交付 ⇒ 16MB 源被「加粒子」放大到 70MB，"
                         "且无 faststart（手机要下完才能播）。现改为 libx264 CRF + faststart。")
    ap.add_argument("--skip-first", type=int, default=1,
                    help="前 N 帧不画粒子（默认 1：保住 pipeline 贴的**封面首帧**干净，"
                         "平台抓封面不受粒子干扰）")
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
    # ★2026-10-15 修（#253）：中间层补 `-color_range pc`。源成片是 full-range
    #   （ffprobe: pix_fmt=yuvj420p color_range=pc），而 rawvideo→h264 默认按 limited
    #   编码且元数据缺失 ⇒ 解码回来 R/B 各降 ~1.3、G 升 ~0.5（肉眼是整体发暗+偏色）。
    #   实测 |ΔRGB| 3.35 → 2.24（残差是 4:2:0 色度下采样的固有损失，无法再消）。
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(fps), "-i", "-", "-an",
           "-c:v", "h264_videotoolbox", "-q:v", "75",
           "-pix_fmt", "yuv420p", "-color_range", "pc", tmp]
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
        # ★前 skip_first 帧不画粒子：保住 pipeline 贴的封面首帧干净
        if i < a.skip_first:
            p.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
            i += 1
            continue
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
    # ★2026-10-15 修（#253）：最后一步必须**重新按交付规格编码**，不能 -c:v copy。
    #   旧版 copy 把中间层 videotoolbox q:v75（19~35 Mbps）当成品交付 ⇒ 体积爆炸 + 无 faststart。
    #   中间层 q:v75 保持不变（只决定临时文件，高质量有利最终画质）；成品走 libx264 CRF21 + faststart。
    #   音频 -c:a copy：源已在 pipeline 末道限幅过，copy 不回退峰值。
    # ★2026-10-21：临时名原用 `<out>.web.mp4`，会被 host file policy 判成 web 预览缓存
    #   而拒绝 rename（`Brokered host rename source refused`）。改 `.part` + 失败兜底复制。
    tmp_out = a.out + ".part.mp4"
    # ★2026-10-21 修：原用 `-shortest`，AAC 音频（copy）报的时长比视频短 0.13s，
    #   -shortest 会**按音频截视频**：649 帧进去、645 帧出来（实测，片子尾巴少 4 帧）。
    #   改成显式 `-t` 到源视频时长，视频一帧不丢、音频按同长裁。
    subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp, "-i", a.src,
                    "-map", "0:v:0", "-map", "1:a:0?",
                    "-c:v", "libx264", "-crf", str(a.crf),
                    "-preset", "medium", "-profile:v", "high", "-level", "4.0",
                    "-pix_fmt", "yuv420p", "-color_range", "pc",
                    "-movflags", "+faststart",
                    "-c:a", "copy", "-t", f"{dur:.3f}", tmp_out], check=True)
    try:
        os.replace(tmp_out, a.out)
    except (PermissionError, OSError) as _e:
        print(f"  ⚠️ rename 被拦（{_e}）——改复制落盘", flush=True)
        with open(tmp_out, "rb") as _s, open(a.out, "wb") as _d:
            while True:
                _b = _s.read(8 << 20)
                if not _b:
                    break
                _d.write(_b)
        try:
            os.remove(tmp_out)
        except Exception:
            pass
    os.remove(tmp)
    _mb = os.path.getsize(a.out) / 1048576
    print(f"✅ {i} 帧 → {a.out}（{_mb:.1f} MB，libx264 CRF{a.crf} + faststart）")


if __name__ == "__main__":
    main()
