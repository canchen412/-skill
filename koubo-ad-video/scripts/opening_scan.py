#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opening_scan.py —— 标准开头「第一张图」的 RGB 故障 + 扫描重建入场动效（0 积分）· 备选款

⚠️ 2026-10-21 二次定稿：技能里的**默认款已换成「条带汇聚」**（`opening_bands.py`）。
   本款保留为备选——config 写 `"first_frame_fx_style": "scan"` 才启用。
   适用：数码 / 潮牌 / 装备这类「故障感本身值钱」的品类；
   别用在讲成分、养生的片上（会像盗版软件广告）。

背景（2026-10-21 灿爹）：单图开头动效的三种手法里，**B 扫描重建**最炸、也最适合
带货口播的钩子镜。定稿做成技能的常驻能力：标准开头（--skip-banner）时，
流水线自动把**镜 1 的静图**换成一条带扫描重建入场的短视频，动效结束后画面
回到原图，可直接切下一镜——**不改任何时间轴、不加时长、0 积分**。

与「截图式复刻」的关键差别（都是实测踩出来的）：
  · 未重建区不是简单压暗，而是**去饱和 + 压暗**（×0.22）——有内容的图才读得出「没建好」
  · 噪点按亮度加权（暗部少加）——否则黑底被噪点糊成一层灰布
  · 白闪用「柔闪」（按亮度加权），a≥0.6 才走硬白闪——纯黑不被冲成中灰
  · 结尾必须**回到 z=1.0、暗角归零**，否则切下一镜硬跳（肌酸那条踩过两轮）
  · dur 按 2.30s 基准**等比压缩**，短钩子镜（1.2~1.5s）也能完整跑完动效

动效时间轴（基准 2.30s，实际按 --dur 等比缩放）：
  0.00–0.44  26 级台阶自上而下「数字重建」+ RGB 错位 / 切片错位 / 整行撕裂 / 扫描线 / 噪点
  0.46       白闪归位 0.95，推镜 1.36 → 1.0
  1.00–1.24  二次故障（强度 0.62）
  1.92–2.10  三次弱故障（强度 0.30）
  尾部       轻推近（避免死帧）+ 暗角淡出

用法：
  python opening_scan.py --src 图.png --out 出.mp4 --dur 2.6
  python opening_scan.py --src 图.png --out 出.mp4 --dur 2.6 --strength 1.15
"""
from __future__ import annotations

import argparse
import math
import os
import random
import shutil
import subprocess
import sys

import numpy as np
from PIL import Image

# ── 基准时间轴（2.30s）。--dur 不等时整体按 k=dur/2.3 压缩（只压不放） ──
BASE_DUR = 2.30
# ★结尾要留出「干净画面」再交棒：动效必须在 dur*TAIL_KEEP 之前跑完，
#   否则镜 1 切走的那一帧还在故障里（实测 2.48s 镜只留 0.08s 干净，接下一镜太赶）。
TAIL_KEEP = 0.78
B_HIT = 0.46
B_SCAN0, B_SCAN1 = 0.00, 0.44
W, H = 1920, 1080


def clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)


def eo(u, p=3.0):
    u = clamp(u)
    return 1 - (1 - u) ** p


def smooth(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def find_ffmpeg() -> str:
    for c in (os.environ.get("FFMPEG"), "/Users/mac/.local/bin/ffmpeg", shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 ffmpeg（设置 FFMPEG 环境变量或装 ffmpeg）")


class ScanFX:
    """一张图 → 扫描重建帧序列。所有时间都用「基准时间」再乘 k。"""

    def __init__(self, src: str, dur: float, fps: int = 30,
                 seed: int = 77, strength: float = 1.0, fit: str = "cover"):
        self.dur = float(dur)
        self.fps = int(fps)
        # k = 时间压缩系数：动效整体塞进 dur*TAIL_KEEP 里，剩下的是干净尾巴
        self.k = min(1.0, (self.dur * TAIL_KEEP) / BASE_DUR)
        self.s = float(strength)
        self.rng = random.Random(seed)
        img = Image.open(src).convert("RGB")
        if img.size != (W, H):
            if fit == "contain":                    # 竖图/异形图：等比缩放 + 模糊铺底
                bg = img.resize((W, H), Image.LANCZOS)
                from PIL import ImageFilter
                bg = bg.filter(ImageFilter.GaussianBlur(42)).point(lambda v: int(v * 0.55))
                s = min(W / img.width, H / img.height)
                im2 = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
                bg.paste(im2, ((W - im2.width) // 2, (H - im2.height) // 2))
                img = bg
            else:                                   # cover：满屏裁切（默认，与渲染器一致）
                s = max(W / img.width, H / img.height)
                im2 = img.resize((max(1, int(img.width * s)), max(1, int(img.height * s))), Image.LANCZOS)
                x0 = (im2.width - W) // 2
                y0 = (im2.height - H) // 2
                img = im2.crop((x0, y0, x0 + W, y0 + H))
        self.img = img

    # ---------- 基础操作 ----------
    def base_at(self, z: float) -> Image.Image:
        z = max(1.0, z)
        w, h = W / z, H / z
        x0, y0 = (W - w) / 2, (H - h) / 2
        return self.img.crop((int(x0), int(y0), int(x0 + w), int(y0 + h))).resize((W, H), Image.LANCZOS)

    @staticmethod
    def shift(a, dx, dy=0):
        out = np.zeros_like(a)
        xs0, xe0 = max(0, dx), min(W, W + dx)
        ys0, ye0 = max(0, dy), min(H, H + dy)
        if xe0 > xs0 and ye0 > ys0:
            out[ys0:ye0, xs0:xe0] = a[ys0 - dy:ye0 - dy, xs0 - dx:xe0 - dx]
        return out

    # ---------- 时间轴 ----------
    def glitch(self, t):
        """故障强度（t 为实际秒；内部换算到基准时间）"""
        T = t / self.k if self.k > 0 else t
        s = self.s
        if T < B_HIT:
            return min(1.0, s * (0.30 + 0.70 * (T / B_HIT) ** 1.5))
        if T < 1.00:
            return 0.0
        if T < 1.06:
            return min(1.0, s * 0.62) * clamp((T - 1.00) / 0.06)
        if T < 1.24:
            return min(1.0, s * 0.62) * (1 - clamp((T - 1.06) / 0.18))
        if T < 1.92:
            return 0.0
        if T < 1.98:
            return min(1.0, s * 0.30) * clamp((T - 1.92) / 0.06)
        if T < 2.10:
            return min(1.0, s * 0.30) * (1 - clamp((T - 1.98) / 0.12))
        return 0.0

    def zoom(self, t):
        T = t / self.k if self.k > 0 else t
        end = BASE_DUR
        if T < B_HIT:
            z = 1.36 - 0.32 * (T / B_HIT) ** 0.6
        elif T < B_HIT + 0.06:
            z = 1.04 + 0.11 * (1 - clamp((T - B_HIT) / 0.06))
        else:
            z = 1.01 + 0.055 * smooth(clamp((T - B_HIT - 0.06) / 0.80))
        # 干净尾巴：把推近缓回 1.0，末帧就是原图全幅 —— 切下一镜不跳（肌酸那条踩过两轮）
        _t_end = BASE_DUR * self.k
        if t > _t_end:
            u = smooth((t - _t_end) / max(0.01, self.dur - _t_end))
            z = 1.0 + (z - 1.0) * (1.0 - u)
        return z

    def scan_rows(self, t):
        """已重建的行数（26 级台阶，台阶数随压缩比减少）"""
        T = t / self.k if self.k > 0 else t
        u = clamp((T - B_SCAN0) / (B_SCAN1 - B_SCAN0))
        n = max(8, min(26, int(round(26 * self.k)))) if self.k < 1 else 26
        kk = min(n, int(round(eo(u, 1.9) * n)))
        return int(H * kk / n)

    # ---------- 单帧 ----------
    def frame(self, t: float) -> Image.Image:
        g = self.glitch(t)
        z = self.zoom(t)
        im = self.base_at(z)
        a = np.asarray(im).astype(np.float32)
        rng = self.rng

        if g > 0.01:
            # RGB 三通道错位（故障的主体）
            dx = int(g * 92 * math.sin(t * 113.0))
            dy = int(g * 14 * math.cos(t * 71.0))
            a = np.stack([self.shift(a[..., 0], dx, dy),
                          a[..., 1],
                          self.shift(a[..., 2], -dx, -dy)], axis=-1)
            # 横向切片错位
            for _ in range(int(g * 14)):
                ys = rng.randint(0, H - 20)
                hh = rng.randint(4, 90)
                off = int(g * 170 * rng.uniform(-1, 1))
                a[ys:min(H, ys + hh)] = np.roll(a[ys:min(H, ys + hh)], off, axis=1)
            # 整行撕裂
            for _ in range(int(g * 8)):
                ys = rng.randint(0, H - 6)
                hh = rng.randint(2, 6)
                off = int(g * 265 * rng.uniform(-1, 1))
                a[ys:ys + hh] = np.roll(a[ys:ys + hh], off, axis=1)
            # 扫描线
            a[::3] *= 0.80
            a[1::3] *= 0.93
            # 噪点（按亮度加权：暗部少加，纯黑不被糊成灰）
            lum = a.mean(axis=2, keepdims=True) / 255.0
            a += np.random.normal(0, 24 * g, a.shape) * (0.18 + 0.82 * np.clip(lum * 3, 0, 1))
            # 亮度爆闪
            a *= 1 + g * 0.30 * math.sin(t * 96.0)
            # 故障色块
            for _ in range(int(g * 4)):
                ys = rng.randint(0, H - 14)
                hh = rng.randint(3, 10)
                xs = rng.randint(0, W - 300)
                wd = rng.randint(120, 620)
                col = rng.choice([(0, 255, 210), (255, 0, 130), (120, 160, 255)])
                a[ys:ys + hh, xs:xs + wd] = (np.array(col, dtype=np.float32) * 0.85
                                            + a[ys:ys + hh, xs:xs + wd] * 0.15)

        # 扫描重建：未重建区「去饱和 + 压暗」（比单纯压暗更像没建好）
        sy = self.scan_rows(t)
        if sy < H:
            reg = a[sy:]
            g_lum = reg.mean(axis=2, keepdims=True)
            a[sy:] = (g_lum * 0.78 + reg * 0.22) * 0.22
            if t < 0.18 * self.k:                    # 早期碎片闪现
                full = np.asarray(im).astype(np.float32)
                for _ in range(4):
                    ys = rng.randint(0, H - 40)
                    hh = rng.randint(8, 60)
                    xs = rng.randint(0, W - 400)
                    wd = rng.randint(200, 700)
                    a[ys:ys + hh, xs:xs + wd] = full[ys:ys + hh, xs:xs + wd]

        frame = Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

        # 扫描亮线 + 下方余晖带
        if sy < H:
            lay = Image.new("RGB", (W, H), (0, 0, 0))
            from PIL import ImageDraw
            dd = ImageDraw.Draw(lay)
            dd.line([(0, sy), (W, sy)], fill=(200, 250, 255), width=3)
            dd.rectangle([0, sy + 3, W, min(H, sy + 26)], fill=(60, 120, 130))
            frame = Image.blend(frame, Image.composite(lay, frame, lay.convert("L")), 0.55)

        # 白闪：归位 0.95 → 二次 0.30
        T = t / self.k if self.k > 0 else t
        fa = 0.0
        if B_HIT - 0.03 <= T <= B_HIT + 0.09:
            fa = 0.95 * (1 - clamp((T - B_HIT + 0.03) / 0.11))
        if 1.00 <= T <= 1.14:
            fa = max(fa, 0.30 * (1 - clamp((T - 1.00) / 0.14)))
        if fa > 0.003:
            arr = np.asarray(frame).astype(np.float32)
            if fa >= 0.60:
                frame = Image.fromarray(np.clip(arr + (255.0 - arr) * fa, 0, 255).astype(np.uint8))
            else:
                lw = np.clip(arr.max(axis=2, keepdims=True) / 110.0, 0, 1) ** 0.75
                frame = Image.fromarray(np.clip(arr + (255.0 - arr) * (fa * lw), 0, 255).astype(np.uint8))

        # 暗角：结尾 0.30s 淡出到 0（切下一镜不跳）
        vf = 1 - clamp((t - (self.dur - 0.32)) / 0.30)
        if vf > 0.01:
            frame = self.vignette(frame, 0.40 * vf, 2.0)
        return frame

    @staticmethod
    def vignette(frame, strength=0.40, power=2.0):
        if strength <= 0.01:
            return frame
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 1.4142
        m = (1 - strength * np.clip(d, 0, 1) ** power)[..., None]
        a = np.asarray(frame).astype(np.float32) * m
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def render(src: str, out: str, dur: float, fps: int = 30, seed: int = 77,
           strength: float = 1.0, fit: str = "cover", quiet: bool = False):
    ff = find_ffmpeg()
    fx = ScanFX(src, dur, fps=fps, seed=seed, strength=strength, fit=fit)
    n = max(1, int(round(fps * dur)))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cmd = [ff, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            p.stdin.write(fx.frame(i / fps).tobytes())
    finally:
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg 编码失败：{out}")
    if not quiet:
        print(f"[opening_scan] {n} 帧 / {dur:.2f}s / k={fx.k:.2f} → {out}")
    return fx.k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="镜 1 的静图")
    ap.add_argument("--out", required=True, help="输出 mp4（必须无音轨，否则会屏蔽口播）")
    ap.add_argument("--dur", type=float, required=True, help="目标时长（= 镜 1 时长 + 少量余量）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=77)
    ap.add_argument("--strength", type=float, default=1.0, help="故障强度倍率（1.0 定稿）")
    ap.add_argument("--fit", choices=("cover", "contain"), default="cover")
    ap.add_argument("--emit-k", help="把本次时间压缩系数写到该文件（流水线据此对点音效）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(a.src):
        raise SystemExit(f"源图不存在：{a.src}")
    k = render(a.src, a.out, a.dur, a.fps, a.seed, a.strength, a.fit, a.quiet)
    if a.emit_k:
        with open(a.emit_k, "w") as f:
            f.write(f"{k:.6f}")


if __name__ == "__main__":
    sys.exit(main())
