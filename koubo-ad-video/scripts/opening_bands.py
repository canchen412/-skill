#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""opening_bands.py —— 标准开头「第一张图」的**条带汇聚**入场动效（0 积分）

背景（2026-10-21 灿爹二次定稿）：单图开头动效三种手法里，**A 条带汇聚**是定稿款——
一张静图被切成 9 条横带，奇偶错时从左右飞入拼合成整图，拼合瞬间有合缝光，
随后准星框收紧 + 速度线 + 白闪冲击，最后回落到原图。

为什么它比 B 扫描重建更适合当**默认**：
  · 不挑品类。B 的 RGB 故障在「讲成分 / 养生」的片上像盗版软件广告；
    A 是「机械拼装」，中性、干净、力量感强，健身/数码/食品都能用。
  · 暗部友好。B 依赖「重建区 vs 未重建区」的亮度差，暗帧上一片糊；
    A 靠几何位移，画面再黑也读得出「一条条飞进来」。
  · 收尾天然干净。A 的 zoom 在基准末点精确回到 1.0，切下一镜不跳。

动效时间轴（基准 2.00s，实际按 --dur 压缩，系数 k 由 --emit-k 写出）：
  0.02–0.63  9 条横带奇偶错时飞入（0.026 阶梯 / 每条 0.40s / 条内反向视差 0.36）
  0.63       合缝光（细、快、淡）
  0.68–1.42  速度线 + 准星框从 1.26x 收紧贴合画面 + 推镜 1.0 → 1.185
  1.30–1.42  ★冲击：白闪 0.52 + 推镜加速（音效压这里）
  1.42–2.00  回落到 z=1.0、暗角淡出（切下一镜不跳）

用法：
  python opening_bands.py --src 图.png --out 出.mp4 --dur 2.6
  python opening_bands.py --src 图.png --out 出.mp4 --dur 2.6 --strength 0.8   # 更克制的版本
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
from PIL import Image, ImageDraw

# ── 基准时间轴（2.00s）。--dur 不等时整体按 k=dur*TAIL_KEEP/2.0 压缩（只压不放）──
BASE_DUR = 2.00
# 动效必须在 dur*TAIL_KEEP 之前跑完，剩下的是干净尾巴（末帧＝原图，接下一镜不跳）
TAIL_KEEP = 0.86

# 9 条横带，高度不等（等分像百叶窗，不等分才像「切片」）；合计必须 = 1080
BANDS_H = [88, 132, 96, 150, 118, 90, 144, 112, 150]
BANDS, _acc = [], 0
for _h in BANDS_H:
    BANDS.append((_acc, _acc + _h))
    _acc += _h
assert _acc == 1080, f"BANDS_H 合计 {_acc} ≠ 1080"
SEAMS = [b[1] for b in BANDS[:-1]]

A_T0, A_STAG, A_BAND_D = 0.02, 0.026, 0.40
A_MERGE = A_T0 + A_STAG * (len(BANDS) - 1) + A_BAND_D      # ≈ 0.628
A_PUNCH0, A_PUNCH1 = 1.30, 1.42

W, H = 1920, 1080


def clamp(x, a=0.0, b=1.0):
    return a if x < a else (b if x > b else x)


def eo(u, p=3.0):
    u = clamp(u)
    return 1 - (1 - u) ** p


def eo_cubic(u):
    u = clamp(u)
    return 1 - (1 - u) ** 3


def ei(u, p=2.0):
    u = clamp(u)
    return clamp(u) ** p


def smooth(u):
    u = clamp(u)
    return u * u * (3 - 2 * u)


def find_ffmpeg() -> str:
    for c in (os.environ.get("FFMPEG"), "/Users/mac/.local/bin/ffmpeg", shutil.which("ffmpeg")):
        if c and os.path.exists(c):
            return c
    raise RuntimeError("找不到 ffmpeg（设置 FFMPEG 环境变量或装 ffmpeg）")


class BandsFX:
    """一张图 → 条带汇聚帧序列。所有时间都用「基准时间」T = t / k。"""

    def __init__(self, src: str, dur: float, fps: int = 30,
                 seed: int = 20261021, strength: float = 1.0, fit: str = "cover"):
        self.dur = float(dur)
        self.fps = int(fps)
        self.k = min(1.0, (self.dur * TAIL_KEEP) / BASE_DUR)
        self.s = float(strength)
        self.rng = random.Random(seed)
        img = Image.open(src).convert("RGB")
        if img.size != (W, H):
            if fit == "contain":                       # 竖图/异形：等比缩放 + 模糊铺底
                from PIL import ImageFilter
                bg = img.resize((W, H), Image.LANCZOS)
                bg = bg.filter(ImageFilter.GaussianBlur(42)).point(lambda v: int(v * 0.55))
                sc = min(W / img.width, H / img.height)
                im2 = img.resize((max(1, int(img.width * sc)), max(1, int(img.height * sc))), Image.LANCZOS)
                bg.paste(im2, ((W - im2.width) // 2, (H - im2.height) // 2))
                img = bg
            else:                                      # cover：满屏裁切（默认，与渲染器一致）
                sc = max(W / img.width, H / img.height)
                im2 = img.resize((max(1, int(img.width * sc)), max(1, int(img.height * sc))), Image.LANCZOS)
                x0 = (im2.width - W) // 2
                y0 = (im2.height - H) // 2
                img = im2.crop((x0, y0, x0 + W, y0 + H))
        self.img = img
        rng = self.rng
        self.speedlines = [(rng.uniform(0, H), rng.uniform(200, 760), rng.uniform(0.65, 1.0),
                            rng.uniform(2800, 5600), rng.uniform(0, 1.0), rng.choice([2, 3]))
                           for _ in range(38)]

    # ---------- 基础操作 ----------
    def base_at(self, z: float) -> Image.Image:
        if z <= 1.0005:
            return self.img
        w, h = W / z, H / z
        x0, y0 = (W - w) / 2, (H - h) / 2
        return self.img.crop((int(x0), int(y0), int(x0 + w), int(y0 + h))).resize((W, H), Image.LANCZOS)

    # ---------- 时间轴 ----------
    def zoom(self, T: float, t: float) -> float:
        if T < A_MERGE:
            z = 1.0
        elif T < A_PUNCH0:
            z = 1.0 + 0.080 * smooth((T - A_MERGE) / (A_PUNCH0 - A_MERGE))
        elif T < A_PUNCH1:
            z = 1.080 + 0.105 * ei((T - A_PUNCH0) / (A_PUNCH1 - A_PUNCH0), 1.4)
        else:
            z = 1.185 - 0.185 * eo_cubic((T - A_PUNCH1) / (BASE_DUR - A_PUNCH1))
        # 干净尾巴：给一点点极慢推近，避免静帧看起来「卡住了」（幅度小到看不出来，但画面活着）
        t_end = BASE_DUR * self.k
        if t > t_end and self.dur > t_end + 0.02:
            z += 0.028 * smooth((t - t_end) / (self.dur - t_end))
        return z

    # ---------- 单帧 ----------
    def frame(self, t: float) -> Image.Image:
        T = t / self.k if self.k > 0 else t
        s = self.s
        z = self.zoom(T, t)
        full = self.base_at(z)
        frame = Image.new("RGB", (W, H), (0, 0, 0))

        # ① 条带飞入（奇偶错侧；条内内容反向视差 = 假 3D）
        for i, (y0, y1) in enumerate(BANDS):
            u = clamp((T - (A_T0 + i * A_STAG)) / A_BAND_D)
            if u <= 0:
                continue
            eu = eo(u, 3.4)
            side = -1 if i % 2 == 0 else 1
            dist = W + 180
            xoff = side * dist * (1 - eu)
            sx = 1.0 + 0.55 * (1 - eu)
            bh = y1 - y0
            band = full.crop((0, y0, W, y1))
            w2 = max(W, int(W * sx))
            b2 = band.resize((w2, bh), Image.BILINEAR)
            cshift = -xoff * 0.36                      # 条内反向视差（越靠边越明显）
            cx0 = int(clamp((w2 - W) / 2 + cshift, 0, w2 - W))
            win = b2.crop((cx0, 0, cx0 + W, bh))
            frame.paste(win, (int(xoff), y0))
            # 飞行中的白边（收到位就消失）
            if 0.02 < u < 0.97 and s > 0.01:
                e = xoff if side > 0 else xoff + W
                if 0 <= e <= W:
                    lay = Image.new("RGB", (W, H), (0, 0, 0))
                    ImageDraw.Draw(lay).line([(e, y0), (e, y1)], fill=(255, 255, 255), width=5)
                    frame = Image.blend(frame, Image.composite(lay, frame, lay.convert("L")),
                                        clamp(0.62 * s) * (1 - eu))

        # ② 合缝光：横向细线从中心向两侧张开（细、快、淡）
        if A_MERGE - 0.02 <= T <= A_MERGE + 0.14 and s > 0.01:
            u = clamp((T - (A_MERGE - 0.02)) / 0.16)
            span = W * 0.55 * eo_cubic(u)
            lay = Image.new("RGB", (W, H), (0, 0, 0))
            dd = ImageDraw.Draw(lay)
            for sy in SEAMS:
                dd.line([(W / 2 - span, sy), (W / 2 + span, sy)], fill=(255, 255, 255), width=2)
            frame = Image.blend(frame, Image.composite(lay, frame, lay.convert("L")),
                                clamp(0.30 * s) * (1 - u) ** 2)

        # ③ 速度线（横向掠过，随冲击一起收）
        if A_MERGE + 0.05 <= T <= A_PUNCH1 and s > 0.01:
            u = clamp((T - A_MERGE - 0.05) / (A_PUNCH1 - A_MERGE - 0.05))
            lay = Image.new("RGB", (W, H), (0, 0, 0))
            dd = ImageDraw.Draw(lay)
            for y, ln, al, sp, ph, wd in self.speedlines:
                p = ((T * sp / W + ph) % 1.6) - 0.3
                x0 = p * W
                v = int(255 * al)
                dd.line([(x0, y), (x0 + ln, y)], fill=(v, v, v), width=wd)
            frame = Image.blend(frame, Image.composite(lay, frame, lay.convert("L")),
                                clamp(0.78 * s) * math.sin(math.pi * u))

        # ④ 准星框：从 1.26x 收缩贴合画面边缘
        if A_MERGE + 0.10 <= T <= A_PUNCH1 and s > 0.01:
            cu = clamp((T - A_MERGE - 0.10) / (A_PUNCH0 - A_MERGE - 0.10))
            kk = 1.0 + 0.26 * (1 - eo_cubic(cu))
            ix, iy = int((W - W / kk) / 2), int((H - H / kk) / 2)
            lay = Image.new("RGB", (W, H), (0, 0, 0))
            ImageDraw.Draw(lay).rectangle([ix, iy, W - ix, H - iy], outline=(255, 255, 255), width=3)
            frame = Image.blend(frame, Image.composite(lay, frame, lay.convert("L")),
                                clamp(0.40 * s) * math.sin(math.pi * min(1.0, cu * 1.1)))

        # ⑤ 白闪：合缝一记轻的，冲击一记重的
        fa = 0.0
        if A_MERGE - 0.02 <= T <= A_MERGE + 0.07:
            fa = 0.24 * s * (1 - clamp((T - A_MERGE + 0.02) / 0.09))
        if A_PUNCH0 - 0.02 <= T <= A_PUNCH0 + 0.09:
            fa = max(fa, 0.52 * s * (1 - clamp((T - A_PUNCH0 + 0.02) / 0.11)))
        if fa > 0.003:
            frame = self.flash(frame, fa)

        # ⑥ 暗角：基准尾段淡出到 0（切下一镜不跳）
        vf = 1 - clamp((T - (BASE_DUR - 0.30)) / 0.28)
        if vf > 0.01:
            frame = self.vignette(frame, 0.28 * vf, 2.2)
        return frame

    # ---------- 公用滤镜 ----------
    @staticmethod
    def flash(frame: Image.Image, a: float) -> Image.Image:
        """柔闪：只抬亮有亮度的区域，纯黑不被冲成灰；a>=0.6 才走硬白闪"""
        a = clamp(a)
        if a <= 0.003:
            return frame
        arr = np.asarray(frame).astype(np.float32)
        if a >= 0.60:
            return Image.fromarray(np.clip(arr + (255.0 - arr) * a, 0, 255).astype(np.uint8))
        lw = np.clip(arr.max(axis=2, keepdims=True) / 110.0, 0, 1) ** 0.75
        return Image.fromarray(np.clip(arr + (255.0 - arr) * (a * lw), 0, 255).astype(np.uint8))

    @staticmethod
    def vignette(frame: Image.Image, strength=0.28, power=2.2) -> Image.Image:
        if strength <= 0.01:
            return frame
        yy, xx = np.mgrid[0:H, 0:W]
        d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / 1.4142
        m = (1 - strength * np.clip(d, 0, 1) ** power)[..., None]
        a = np.asarray(frame).astype(np.float32) * m
        return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))

    # ---------- 运动模糊 ----------
    def subs(self, T: float) -> int:
        """快动段多采样几帧做平均（条带飞入、冲击）。慢段不采样，省时间。"""
        if 0.05 < T < A_MERGE + 0.05 or (A_PUNCH0 - 0.05) < T < A_PUNCH1:
            return 4
        return 1


def render(src: str, out: str, dur: float, fps: int = 30, seed: int = 20261021,
           strength: float = 1.0, fit: str = "cover", quiet: bool = False):
    ff = find_ffmpeg()
    fx = BandsFX(src, dur, fps=fps, seed=seed, strength=strength, fit=fit)
    n = max(1, int(round(fps * dur)))
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    cmd = [ff, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "17",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(n):
            t = i / fps
            T = t / fx.k if fx.k > 0 else t
            sub = fx.subs(T)
            if sub <= 1:
                fr = fx.frame(t)
            else:                                        # 运动模糊：时间上多点平均
                acc = None
                for q in range(sub):
                    tt = max(0.0, t - q / (fps * sub))
                    a = np.asarray(fx.frame(tt)).astype(np.float32)
                    acc = a if acc is None else acc + a
                fr = Image.fromarray(np.clip(acc / sub, 0, 255).astype(np.uint8))
            p.stdin.write(fr.tobytes())
    finally:
        p.stdin.close()
        if p.wait() != 0:
            raise RuntimeError(f"ffmpeg 编码失败：{out}")
    if not quiet:
        print(f"[opening_bands] {n} 帧 / {dur:.2f}s / k={fx.k:.2f} → {out}")
    return fx.k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="镜 1 的静图")
    ap.add_argument("--out", required=True, help="输出 mp4（必须无音轨，否则会屏蔽口播）")
    ap.add_argument("--dur", type=float, required=True, help="目标时长（= 镜 1 时长 + 少量余量）")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--strength", type=float, default=1.0, help="白边/速度线/准星/闪光强度倍率（1.0 定稿）")
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
