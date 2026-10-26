#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""textfx_render.py —— 文字动效库渲染器（灿爹拍板入库的 4 款）

入库动效（2026-10-10）：
  pop    弹入       scale 0.4→1 过冲（back.out(1.7)）+ 快速浮现     → kw_spring
  slide  滑入       整字从画外左/右滑到位（power3.out）              → kw_snap
  rotate 旋转进场   rotate -24°→0 + scale 1.7→1 + opacity（back.out(1.4)）→ kw_shine
  drop   落下弹跳   translateY 落下 bounce.out 弹跳 + 落点阴影       → kw_drop

用法：
  渲单个 demo 成片：
    python3 textfx_render.py --fx pop --text "个位数一瓶" --out out.mp4
  可选参数：
    --size 200         字号            --color pink|white|yellow|black|R,G,B
    --bg grid|black|green   背景（green=绿幕，抠像叠加用）
    --dur 2.4          总时长 s        --in-at 0.3   进场开始时刻
    --sfx kw_spring    音效名（assets/sfx/ 内短名，不含 .wav）
    --sfx-at auto      音效触发时刻 s（auto=进场起点；drop=首次触地）
  供其他脚本 import：
    from textfx_render import render_fx   # render_fx(out, fx, text, ...)
所有缓动与 GSAP 同名曲线 1:1（back.out(1.7) ≙ ease_out_back，bounce.out ≙ ease_out_bounce）。
"""
import os, math, shutil, argparse, subprocess

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    raise SystemExit(
        "[动效库] 缺少 Pillow：请用带 Pillow 的 python 运行。\n"
        "  本机可用: /Users/mac/.workbuddy/binaries/python/envs/default/bin/python3\n"
        "  或安装:   python3 -m pip install pillow")

W, H, FPS = 1920, 1080, 30
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # koubo-ad-video/

# ffmpeg 回退链：PATH → 常见位置
def _find_ffmpeg():
    p = shutil.which("ffmpeg")
    if p:
        return p
    for c in ["/Users/mac/.local/bin/ffmpeg", "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"]:
        if os.path.exists(c):
            return c
    raise SystemExit("[动效库] 找不到 ffmpeg，请安装（brew install ffmpeg）或加入 PATH")

FF = _find_ffmpeg()

def _find_sfx_dir():
    """音效池多级回退：技能版位置 → 动效库外置副本位置 → 环境变量"""
    here = os.path.dirname(os.path.abspath(__file__))
    cand = [
        os.path.join(os.path.dirname(here), "assets", "sfx"),        # 技能内 scripts/ 旁
        os.path.join(os.path.dirname(here), "音效_通用"),             # 动效库/渲染器/ 旁（kw_*/pop/whoosh）
        os.path.join(os.path.dirname(here), "音效_爆款叙事"),          # 动效库叙事音效
        os.environ.get("TEXTFX_SFX_DIR", ""),
    ]
    for c in cand:
        if c and os.path.isdir(c):
            return c
    return cand[0]
SFX_DIR = _find_sfx_dir()
FONT_PATH = "/System/Library/Fonts/Hiragino Sans GB.ttc"
FONT_IDX = 2   # W6 最粗

PINK = (255, 45, 105)
WHITE = (255, 255, 255)
YELLOW = (255, 225, 77)
BLACK = (30, 32, 38)
BG_GRID = (208, 212, 218)
GRID = (197, 201, 208)
GREEN = (0, 230, 80)

COLORS = {"pink": PINK, "white": WHITE, "yellow": YELLOW, "black": BLACK}

_fc = {}
def F(sz):
    k = sz
    if k not in _fc: _fc[k] = ImageFont.truetype(FONT_PATH, sz, index=FONT_IDX)
    return _fc[k]

def clamp01(x): return max(0.0, min(1.0, x))
def ease_out(x):
    x = clamp01(x); return 1 - (1 - x) ** 3
def ease_out_back(x):
    """≙ GSAP back.out(1.7)"""
    x = clamp01(x)
    c1, c3 = 1.70158, 2.70158
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2
def ease_out_bounce(x):
    """≙ GSAP bounce.out"""
    x = clamp01(x)
    n1, d1 = 7.5625, 2.75
    if x < 1 / d1: return n1 * x * x
    if x < 2 / d1: return n1 * (x - 1.5 / d1) ** 2 + .75
    if x < 2.5 / d1: return n1 * (x - 2.25 / d1) ** 2 + .9375
    return n1 * (x - 2.625 / d1) ** 2 + .984375

# 首次触地时刻（bounce.out 第一段结束）p = 1/2.75
BOUNCE_TOUCH = 1 / 2.75


def make_canvas(bg):
    if bg == "black":
        return Image.new("RGB", (W, H), (12, 13, 16)).convert("RGBA")
    if bg == "green":
        return Image.new("RGB", (W, H), GREEN).convert("RGBA")
    im = Image.new("RGB", (W, H), BG_GRID)
    d = ImageDraw.Draw(im)
    for x in range(0, W + 60, 60):
        d.line([(x, 0), (x, H)], fill=GRID, width=1)
    for y in range(0, H + 60, 60):
        d.line([(0, y), (W, y)], fill=GRID, width=1)
    return im.convert("RGBA")


def text_layer(text, size, color, stroke, stroke_fill=(16, 16, 20, 255),
               shadow=True, soff=12):
    f = F(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    b = probe.textbbox((0, 0), text, font=f)
    tw, th = b[2] - b[0], b[3] - b[1]
    pad = stroke * 4 + 60
    layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tx, ty = pad - b[0], pad - b[1]
    if shadow:
        d.text((tx + soff, ty + int(soff * 1.2)), text, font=f,
               fill=(40, 40, 50, 110), stroke_width=stroke, stroke_fill=(40, 40, 50, 110))
    d.text((tx, ty), text, font=f, fill=color + (255,),
           stroke_width=stroke, stroke_fill=stroke_fill)
    return layer


def put(im, layer, cx, cy, alpha=255):
    if alpha <= 2: return
    lay = layer
    if alpha < 250:
        lay = lay.copy()
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha / 255)))
    im.alpha_composite(lay, (int(cx - lay.size[0] / 2), int(cy - lay.size[1] / 2)))


def scaled(layer, s):
    s = max(0.02, s)
    nw, nh = max(2, int(layer.size[0] * s)), max(2, int(layer.size[1] * s))
    return layer.resize((nw, nh), Image.LANCZOS)


# ============ 1. pop 弹入 ============
def fx_pop(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.55)
    if p <= 0: return
    s = ease_out_back(p)
    a = int(255 * clamp01((t - ctx["in_at"]) / 0.10))
    put(im, scaled(ctx["layer"], s), W // 2, H // 2, a)


# ============ 2. slide 滑入 ============
def fx_slide(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.60)
    if p <= 0: return
    e = ease_out(p)                      # ≙ power3.out
    lay = ctx["layer"]
    lw = lay.size[0]
    off = lw // 2 + 80                   # 完全出画的偏移
    if ctx.get("dir", "left") == "left":  # 从左画外滑入（缺 dir 默认 left）
        x = -off // 1 + (W // 2 + off) * e
    else:                                # 从右画外滑入
        x = W + off - (W // 2 + off) * e
    a = int(255 * clamp01(p * 5))        # 快速跟上不透明度
    put(im, lay, int(x), H // 2, a)


# ============ 3. rotate 旋转进场 ============
def fx_rotate(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.80)
    if p <= 0: return
    e = ease_out_back(p)                 # ≙ back.out(1.4) 近似
    ang = -24.0 * (1 - e)
    s = 1.7 - 0.7 * e
    a = int(255 * clamp01((t - ctx["in_at"]) / 0.14))
    lay = scaled(ctx["layer"], s).rotate(ang, expand=True, resample=Image.BICUBIC)
    put(im, lay, W // 2, H // 2, a)


# ============ 4. drop 落下弹跳 ============
def fx_drop(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.95)
    if p <= 0: return
    b = ease_out_bounce(p)               # ≙ bounce.out
    lay = ctx["layer"]
    lw, lh = lay.size
    y = H // 2 - 500 * (1 - b)           # 从上方 500px 落下
    # 落点阴影：随高度下降变宽变实
    d = ImageDraw.Draw(im, "RGBA")
    sw = int(lw * 0.62 * (0.45 + 0.55 * b))
    sh = max(6, int(26 * (0.4 + 0.6 * b)))
    sa = int(110 * b)
    d.ellipse([W // 2 - sw // 2, H // 2 + lh // 2 + 8 - sh // 2,
               W // 2 + sw // 2, H // 2 + lh // 2 + 8 + sh // 2], fill=(20, 22, 26, sa))
    put(im, lay, W // 2, int(y), 255)


FX = {"pop": fx_pop, "slide": fx_slide, "rotate": fx_rotate, "drop": fx_drop}
FX_LABEL = {"pop": "A3 弹入", "slide": "A4 滑入", "rotate": "A7 旋转进场", "drop": "A8 落下弹跳"}
# auto 音效：drop 在首次触地，其余在进场起点
SFX_DEFAULT = {"pop": "kw_spring", "slide": "kw_snap", "rotate": "kw_shine", "drop": "kw_drop"}


def parse_color(s):
    if s in COLORS: return COLORS[s]
    r, g, b = [int(v) for v in s.split(",")]
    return (r, g, b)


def render_fx(out, fx, text, size=200, color="pink", bg="grid", dur=2.4,
              in_at=0.3, sfx=None, sfx_at=None, dir="left", stroke=None,
              label=True):
    """渲一个特效成片。fx ∈ pop/slide/rotate/drop"""
    col = parse_color(color)
    if stroke is None:
        stroke = {"pop": 13, "slide": 10, "rotate": 12, "drop": 14}[fx]
    layer = text_layer(text, size, col, stroke)
    ctx = {"layer": layer, "in_at": in_at, "dir": dir}

    fr_dir = out + "_frames"
    os.makedirs(fr_dir, exist_ok=True)
    total = int(dur * FPS)
    for fr in range(total):
        t = fr / FPS
        im = make_canvas(bg)
        FX[fx](im, t, ctx)
        if label:
            d = ImageDraw.Draw(im, "RGBA")
            tag = FX_LABEL[fx]
            d.rounded_rectangle([40, 40, 40 + 30 + 30 * len(tag), 112], 14, (30, 32, 38, 200))
            d.text((58, 50), tag, font=F(32), fill=(255, 255, 255, 235))
        im.convert("RGB").save(f"{fr_dir}/f{fr:05d}.jpg", quality=93)

    ff = globals()["FF"]               # 模块级 _find_ffmpeg() 已解析
    cmd = [ff, "-y", "-v", "error", "-framerate", str(FPS), "-i", f"{fr_dir}/f%05d.jpg"]
    sfx = sfx or SFX_DEFAULT[fx]
    sp = os.path.join(SFX_DIR, sfx + ".wav")
    if sfx_at == "auto" or sfx_at is None:
        sfx_at = in_at + (0.95 * BOUNCE_TOUCH if fx == "drop" else 0.0)
    if os.path.exists(sp):
        ms = int(sfx_at * 1000)
        cmd += ["-i", sp, "-filter_complex",
                f"[1:a]volume=0.85,adelay={ms}|{ms},apad=pad_dur=2[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}"]
    cmd += ["-vf", f"fps={FPS},format=yuv420p",
            "-c:v", "h264_videotoolbox", "-q:v", "75", out]
    subprocess.run(cmd, check=True)
    for f in os.listdir(fr_dir): os.remove(os.path.join(fr_dir, f))
    os.rmdir(fr_dir)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", required=True, choices=FX.keys())
    ap.add_argument("--text", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=200)
    ap.add_argument("--color", default="pink")
    ap.add_argument("--bg", default="grid", choices=["grid", "black", "green"])
    ap.add_argument("--dur", type=float, default=2.4)
    ap.add_argument("--in-at", type=float, default=0.3)
    ap.add_argument("--sfx", default=None)
    ap.add_argument("--sfx-at", default=None)
    ap.add_argument("--dir", default="left", choices=["left", "right"])
    ap.add_argument("--no-label", action="store_true")
    a = ap.parse_args()
    out = render_fx(a.out, a.fx, a.text, a.size, a.color, a.bg, a.dur,
                    a.in_at, a.sfx, float(a.sfx_at) if a.sfx_at else None,
                    a.dir, label=not a.no_label)
    print(f"→ {out}  ({os.path.getsize(out)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
