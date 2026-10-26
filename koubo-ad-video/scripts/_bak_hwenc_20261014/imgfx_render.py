#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""imgfx_render.py —— 图片动效库渲染器（灿爹拍板入库 15 款，3D 翻转未收）

对应 `GSAP图片特效总览.html` 的 GSAP 曲线 1:1 翻译成 PIL 逐帧：

  fade_up        淡入上浮      opacity 0→1 + y 70→0（power3.out）         → whoosh
  pop            缩放弹入      scale .35→1（back.out(1.7)）               → pop_in
  clip_wipe      擦除揭示      从左往右裁切露出（power2.inOut）           → whoosh2
  circle_reveal  圆形揭示      圆形 mask 从一点荡开（power2.out）         → kw_bloom
  blur_in        模糊聚焦      blur 18→0 + scale 1.18→1（power2.out）     → float_in
  rotate_in      旋转落位      rotate -24°→0 + scale 1.7→1（back.out(1.4)）→ kw_spring
  breathe        呼吸脉冲(循环) scale 1↔1.07（sine.inOut）                → 无（循环不配音）
  float          悬浮漂浮(循环) y 0↔-14（sine.inOut）                     → 无
  shake          左右摇晃(循环) rotation ±3.2°（0.13s 半周期）           → 无
  glow           辉光闪烁(循环) 金色光晕沿圆角图边呼吸                     → 无
  parallax       双层视差(循环) 前景小图框与背景大图反向漂（sine）         → 无
  glitch         故障风        复制层色偏错位抖 0.05s 步进                 → kw_tick
  sweep          扫光遮罩      软边亮带 mask 从右扫到左（power2.inOut）    → kw_marker
  fade_out_blur  虚化退场      进场→停→blur 14 + scale 1.22 + opacity→0   → kw_glass(退场时刻)
  combo          组合款        图弹入(back.out1.8)＋价格标签 spring 甩入＋角标补刀 → pop_in2 + kw_pop

用法：
  python3 imgfx_render.py --fx pop --img 图片.jpg --out out.mp4 [--bg grid|black|green]
      [--dur 2.4] [--in-at 0.3] [--sfx 名] [--sfx-at auto] [--size 640]
      [--price "¥9.9/瓶" --corner "第二瓶半价"]   # combo 专用
  供 import：render_imgfx(out, fx, img, ...)
"""
import os, sys, math, shutil, argparse, subprocess

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageChops
except ImportError:
    raise SystemExit(
        "[动效库] 缺少 Pillow：请用带 Pillow 的 python 运行。\n"
        "  本机可用: /Users/mac/.workbuddy/binaries/python/envs/default/bin/python3\n"
        "  或安装:   python3 -m pip install pillow")

W, H, FPS = 1920, 1080, 30
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

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
FONT_IDX = 2

PINK = (255, 45, 105)
WHITE = (255, 255, 255)
YELLOW = (255, 225, 77)
BG_GRID = (208, 212, 218)
GRID = (197, 201, 208)
GREEN = (0, 230, 80)

_fc = {}
def F(sz):
    if sz not in _fc: _fc[sz] = ImageFont.truetype(FONT_PATH, sz, index=FONT_IDX)
    return _fc[sz]

def clamp01(x): return max(0.0, min(1.0, x))
def ease_out(x):
    x = clamp01(x); return 1 - (1 - x) ** 3
def ease_in_out(x):
    x = clamp01(x); return x * x * (3 - 2 * x)
def ease_out_back(x, k=1.70158):
    x = clamp01(x); c1, c3 = k, k + 1
    return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2


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


def round_img(img, radius=24, border=0, border_fill=(255, 255, 255)):
    """图转圆角（可选白边），返回 RGBA 图层"""
    img = img.convert("RGBA")
    if border:
        pad = Image.new("RGBA", (img.size[0] + border * 2, img.size[1] + border * 2), (0, 0, 0, 0))
        d = ImageDraw.Draw(pad)
        d.rounded_rectangle([0, 0, pad.size[0] - 1, pad.size[1] - 1], radius + border, border_fill + (255,))
        pad.alpha_composite(img, (border, border))
        img = pad
    mask = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(mask)
    d.rounded_rectangle([0, 0, img.size[0] - 1, img.size[1] - 1], radius, 255)
    img.putalpha(Image.composite(mask, Image.new("L", img.size, 0), img.getchannel("A").point(lambda v: 255 if v > 8 else 0)))
    return img


def prep_img(path, size):
    """图片 fit 到 size=(w,h)，圆角处理，返回 RGBA"""
    im = Image.open(path).convert("RGB")
    im.thumbnail((size[0], size[1]), Image.LANCZOS)
    return round_img(im, radius=26)


def put(im, layer, cx, cy, alpha=255):
    if alpha <= 2: return
    lay = layer
    if alpha < 250:
        lay = lay.copy()
        lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha / 255)))
    im.alpha_composite(lay, (int(cx - lay.size[0] / 2), int(cy - lay.size[1] / 2)))


def scaled(layer, s):
    s = max(0.02, s)
    return layer.resize((max(2, int(layer.size[0] * s)), max(2, int(layer.size[1] * s))), Image.LANCZOS)


def glow_layer(layer, radius=20, color=(255, 200, 87), strength=1.0):
    """沿图层 alpha 生成金色辉光层（垫图后）"""
    a = layer.getchannel("A").filter(ImageFilter.GaussianBlur(radius))
    gl = Image.new("RGBA", layer.size, color + (0,))
    gl.putalpha(a.point(lambda v: int(v * strength)))
    return gl


# ============ 进场类 ============
def fx_fade_up(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.80)
    if p <= 0: return
    e = ease_out(p)
    put(im, ctx["img"], W // 2, H // 2 + int(70 * (1 - e)), int(255 * e))

def fx_pop(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.75)
    if p <= 0: return
    put(im, scaled(ctx["img"], ease_out_back(p, 1.7)), W // 2, H // 2,
        int(255 * clamp01((t - ctx["in_at"]) / 0.10)))

def fx_clip_wipe(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 1.0)
    if p <= 0: return
    img = ctx["img"]
    vis = int(img.size[0] * ease_in_out(p))
    lay = img.crop((0, 0, max(2, vis), img.size[1]))
    im.alpha_composite(lay, (int(W // 2 - img.size[0] / 2), int(H // 2 - img.size[1] / 2)))

def fx_circle_reveal(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 1.1)
    if p <= 0: return
    img = ctx["img"]
    iw, ih = img.size
    mask = Image.new("L", (iw, ih), 0)
    d = ImageDraw.Draw(mask)
    r = ease_out(p) * (math.hypot(iw, ih) / 2 * 0.95)
    d.ellipse([iw / 2 - r, ih / 2 - r, iw / 2 + r, ih / 2 + r], fill=255)
    lay = img.copy(); lay.putalpha(mask)
    im.alpha_composite(lay, (int(W // 2 - iw / 2), int(H // 2 - ih / 2)))

def fx_blur_in(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 1.0)
    if p <= 0: return
    e = ease_out(p)
    lay = ctx["img"]
    b = 18 * (1 - e)
    if b > 0.4: lay = lay.filter(ImageFilter.GaussianBlur(b))
    put(im, scaled(lay, 1.18 - 0.18 * e), W // 2, H // 2, 255)

def fx_rotate_in(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 0.80)
    if p <= 0: return
    e = ease_out_back(p, 1.4)
    ang = -24.0 * (1 - e)
    lay = scaled(ctx["img"], 1.7 - 0.7 * e).rotate(ang, expand=True, resample=Image.BICUBIC)
    put(im, lay, W // 2, H // 2, int(255 * clamp01((t - ctx["in_at"]) / 0.14)))

# ============ 强调循环类（t=0 即动） ============
def fx_breathe(im, t, ctx):
    k = 0.5 - 0.5 * math.cos(t * math.pi / 1.1)     # sine.inOut 往复
    put(im, scaled(ctx["img"], 1 + 0.07 * k), W // 2, H // 2, 255)

def fx_float(im, t, ctx):
    k = 0.5 - 0.5 * math.cos(t * math.pi / 1.7)
    put(im, ctx["img"], W // 2, H // 2 - int(14 * k), 255)

def fx_shake(im, t, ctx):
    ang = 3.2 * math.sin(t * math.pi / 0.13)
    lay = ctx["img"].rotate(ang, expand=True, resample=Image.BICUBIC)
    put(im, lay, W // 2, H // 2, 255)

def fx_glow(im, t, ctx):
    k = 0.5 - 0.5 * math.cos(t * math.pi / 0.9)
    img = ctx["img"]
    gl = glow_layer(img, radius=22, strength=0.25 + 0.75 * k)
    im.alpha_composite(gl, (int(W // 2 - img.size[0] / 2), int(H // 2 - img.size[1] / 2)))
    bright = scaled(img, 1 + 0.02 * k)
    put(im, bright, W // 2, H // 2, 255)

# ============ 高级类 ============
def fx_parallax(im, t, ctx):
    ph = t * math.pi / 2.6                           # 2.6s 半周期往复
    k = math.sin(ph)
    fg = ctx["img"]
    # 背景：缺 img_bg 时自动用前景图放大模糊当背景（自愈，不炸）
    bg = ctx.get("img_bg")
    if bg is None:
        big = fg.resize((int(fg.size[0] * 1.6), int(fg.size[1] * 1.6)), Image.LANCZOS)
        bg = big.filter(ImageFilter.GaussianBlur(10))
    bw, bh = bg.size
    ox = int(-40 * k + (W - bw) / 2); oy = (H - bh) // 2
    im.alpha_composite(bg, (max(0, ox), max(0, oy)))
    d = ImageDraw.Draw(im, "RGBA")
    d.rectangle([0, 0, W, H], fill=(10, 12, 16, 110))
    # 前景小图框：圆角+投影 正向漂
    fg = ctx["img"]
    fw, fh = fg.size
    fx0 = W // 2 + int(72 * k) - fw // 2
    fy0 = H // 2 - fh // 2
    sh = Image.new("RGBA", (fw + 60, fh + 60), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    sd.rounded_rectangle([30, 40, fw + 30, fh + 40], 30, (0, 0, 0, 150))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    im.alpha_composite(sh, (fx0 - 30, fy0 - 30))
    im.alpha_composite(fg, (fx0, fy0))

def fx_glitch(im, t, ctx):
    img = ctx["img"]
    put(im, img, W // 2, H // 2, 255)
    # 故障窗：1.1~1.7s 每 0.05s 步进抖
    if not (1.1 <= t < 1.7): return
    step = int((t - 1.1) / 0.05) % 3
    dx = [10, -8, 6][step]
    # 通道错位（R↔B 模拟色相偏移）+ 半透明叠加
    r, g, b, _ = img.split()
    layer = Image.merge("RGBA", (b, g, r, img.getchannel("A").point(lambda v: int(v * (0.5 if step != 1 else 0.85)))))
    im.alpha_composite(layer, (W // 2 - img.size[0] // 2 + dx, H // 2 - img.size[1] // 2))
    if step == 0:
        d = ImageDraw.Draw(im, "RGBA")
        d.rectangle([0, H // 2 - 60 + step * 20, W, H // 2 - 40 + step * 20], fill=(120, 220, 255, 40))

def fx_sweep(im, t, ctx):
    p = clamp01((t - ctx["in_at"]) / 1.3)
    img = ctx["img"]
    iw, ih = img.size
    if p <= 0:
        put(im, img, W // 2, H // 2, 255); return
    if p >= 1:
        put(im, img, W // 2, H // 2, 255); return
    # mask：2.5 倍宽，44%~56% 一条软亮带（带中心图 25% 不透明 = 75% 透明）
    mw = int(iw * 2.5)
    band = Image.new("L", (mw, 1), 255)
    bd = band.load()
    for x in range(mw):
        u = x / mw
        if 0.44 <= u <= 0.56:
            bd[x, 0] = int(255 * (0.25 + 0.75 * abs(u - 0.5) / 0.06))
    band_col = band.resize((mw, ih))
    off = int((mw - iw) * (1 - ease_in_out(p)))     # 从右扫到左
    mask = band_col.crop((off, 0, off + iw, ih))
    lay = img.copy()
    lay.putalpha(ImageChops.multiply(img.getchannel("A"), mask))
    im.alpha_composite(lay, (int(W // 2 - iw / 2), int(H // 2 - ih / 2)))

def fx_fade_out_blur(im, t, ctx):
    p_in = clamp01((t - ctx["in_at"]) / 0.50)
    if p_in <= 0: return
    p_out = clamp01((t - 1.35) / 0.60)
    e_in = ease_out(p_in)
    s = 0.8 + 0.2 * e_in + 0.22 * ease_out(p_out)
    a = int(255 * e_in * (1 - ease_out(p_out)))
    b = 14 * ease_out(p_out)
    lay = ctx["img"]
    if b > 0.4: lay = lay.filter(ImageFilter.GaussianBlur(b))
    put(im, scaled(lay, s), W // 2, H // 2, a)

def fx_combo(im, t, ctx):
    img = ctx["img"]
    iw, ih = img.size
    cx, cy = W // 2 - 120, H // 2
    # 图 back.out(1.8) 弹入 @0.3
    p_img = clamp01((t - 0.3) / 0.7)
    if p_img > 0:
        s_img = ease_out_back(p_img, 1.8)
        a_img = int(255 * clamp01((t - 0.3) / 0.12))
        # 1.8s 后呼吸一下（0.92 yoyo）
        k = 0.0
        if t > 1.9:
            k = math.sin(clamp01((t - 1.9) / 0.5) * math.pi)
        put(im, scaled(img, s_img * (1 - 0.06 * k)), cx, cy, a_img)
    # 价格标签 spring 甩入 @0.85
    p_price = clamp01((t - 0.85) / 0.5)
    if p_price > 0:
        e = ease_out_back(p_price, 2.0)
        lay = text_layer(ctx.get("price", "¥9.9/瓶"), 110, PINK, 8)
        lay = scaled(lay, 1.0)
        lay = lay.rotate(10 * (1 - e), expand=True, resample=Image.BICUBIC)
        put(im, lay, W - 380 + int(150 * (1 - e)), 300, 255)
    # 角标补刀 @1.15
    p_c = clamp01((t - 1.15) / 0.45)
    if p_c > 0:
        e = ease_out_back(p_c, 1.8)
        lay = text_layer(ctx.get("corner", "第二瓶半价"), 72, (10, 50, 32), 0, shadow=False)
        d = ImageDraw.Draw(im, "RGBA")
        pw, ph = lay.size
        bx, by = W - pw - 70, 820
        d.rounded_rectangle([bx - 24, by - 18 + int(60 * (1 - e)), bx + pw - 24 + 48, by + ph - 18 + int(60 * (1 - e))],
                            14, (61, 220, 151, 240))
        im.alpha_composite(lay, (bx, by + int(60 * (1 - e))))


def text_layer(text, size, color, stroke, stroke_fill=(16, 16, 20, 255), shadow=True, soff=10):
    f = F(size)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    b = probe.textbbox((0, 0), text, font=f)
    tw, th = b[2] - b[0], b[3] - b[1]
    pad = stroke * 4 + 50
    layer = Image.new("RGBA", (tw + pad * 2, th + pad * 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    tx, ty = pad - b[0], pad - b[1]
    if shadow:
        d.text((tx + soff, ty + int(soff * 1.2)), text, font=f,
               fill=(40, 40, 50, 110), stroke_width=stroke, stroke_fill=(40, 40, 50, 110))
    d.text((tx, ty), text, font=f, fill=color + (255,), stroke_width=stroke, stroke_fill=stroke_fill)
    return layer


FX = {k[3:] if k.startswith("fx_") else k: v for k, v in list(globals().items()) if k.startswith("fx_")}
FX_LABEL = {
    "fade_up": "① 淡入上浮", "pop": "② 缩放弹入", "clip_wipe": "③ 擦除揭示",
    "circle_reveal": "④ 圆形揭示", "blur_in": "⑤ 模糊聚焦", "rotate_in": "⑥ 旋转落位",
    "breathe": "⑦ 呼吸脉冲", "float": "⑧ 悬浮漂浮", "shake": "⑨ 左右摇晃",
    "glow": "⑩ 辉光闪烁", "parallax": "⑫ 双层视差", "glitch": "⑬ 故障风",
    "sweep": "⑭ 扫光遮罩", "fade_out_blur": "⑮ 虚化退场", "combo": "⑯ 图片+价格标签",
}
SFX_DEFAULT = {
    "fade_up": ("whoosh", "in"), "pop": ("pop_in", "in"), "clip_wipe": ("whoosh2", "in"),
    "circle_reveal": ("kw_bloom", "in"), "blur_in": ("float_in", "in"), "rotate_in": ("kw_spring", "in"),
    "breathe": (None, None), "float": (None, None), "shake": (None, None), "glow": (None, None),
    "parallax": (None, None),
    "glitch": ("kw_tick", 1.1), "sweep": ("kw_marker", "in"),
    "fade_out_blur": ("kw_glass", 1.35), "combo": ("pop_in2", 0.3),
}


def render_imgfx(out, fx, img_path, size=640, bg="grid", dur=2.4, in_at=0.3,
                 sfx=None, sfx_at=None, price="¥9.9/瓶", corner="第二瓶半价",
                 label=True):
    src = Image.open(img_path).convert("RGB")
    img = prep_img(img_path, (int(size * 1.5), size))
    ctx = {"img": img, "in_at": in_at, "price": price, "corner": corner}
    if fx == "parallax":
        bgim = src.copy()
        bgim.thumbnail((W + 200, H + 200), Image.LANCZOS)
        ctx["img_bg"] = round_img(bgim, radius=0)

    fr_dir = out + "_frames"
    os.makedirs(fr_dir, exist_ok=True)
    total = int(dur * FPS)
    for fr in range(total):
        t = fr / FPS
        im = make_canvas(bg)
        FX[fx](im, t, ctx)
        if label:
            tag = FX_LABEL[fx]
            d = ImageDraw.Draw(im, "RGBA")
            d.rounded_rectangle([40, 40, 40 + 30 + 26 * len(tag), 106], 14, (30, 32, 38, 200))
            d.text((56, 48), tag, font=F(30), fill=(255, 255, 255, 235))
        im.convert("RGB").save(f"{fr_dir}/f{fr:05d}.jpg", quality=93)

    ff = globals()["FF"]               # 模块级 _find_ffmpeg() 已解析
    cmd = [ff, "-y", "-v", "error", "-framerate", str(FPS), "-i", f"{fr_dir}/f%05d.jpg"]
    if fx == "combo":
        # 双音效：图弹入 pop_in2@0.3 + 价格标签 kw_pop@0.85
        chains, n = [], 0
        for nm, at, vol in [("pop_in2", 0.3, 0.85), ("kw_pop", 0.85, 0.9)]:
            sp = os.path.join(SFX_DIR, nm + ".wav")
            if os.path.exists(sp):
                cmd += ["-i", sp]
                ms = int(at * 1000)
                chains.append(f"[{n+1}:a]volume={vol},adelay={ms}|{ms},apad=pad_dur=2[ax{n}]")
                n += 1
        if n:
            fc = ";".join(chains) + ";" + "".join(f"[ax{i}]" for i in range(n)) + \
                 f"amix=inputs={n}:duration=longest:normalize=0,apad=pad_dur=2[a]"
            cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
                    "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}"]
    else:
        sfx_name, sfx_mode = SFX_DEFAULT[fx]
        sfx_name = sfx or sfx_name
        if sfx_name:
            sp = os.path.join(SFX_DIR, sfx_name + ".wav")
            if sfx_at is None:
                sfx_at = in_at if (sfx_mode == "in") else float(sfx_mode)
            if os.path.exists(sp):
                ms = int(float(sfx_at) * 1000)
                cmd += ["-i", sp, "-filter_complex",
                        f"[1:a]volume=0.85,adelay={ms}|{ms},apad=pad_dur=2[a]",
                        "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-t", f"{dur:.3f}"]
    cmd += ["-vf", f"fps={FPS},format=yuv420p", "-c:v", "libx264", "-preset", "medium", "-crf", "18", out]
    subprocess.run(cmd, check=True)
    for f in os.listdir(fr_dir): os.remove(os.path.join(fr_dir, f))
    os.rmdir(fr_dir)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx", required=True, choices=FX_LABEL.keys())
    ap.add_argument("--img", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--size", type=int, default=640)
    ap.add_argument("--bg", default="grid", choices=["grid", "black", "green"])
    ap.add_argument("--dur", type=float, default=2.4)
    ap.add_argument("--in-at", type=float, default=0.3)
    ap.add_argument("--sfx", default=None)
    ap.add_argument("--sfx-at", default=None)
    ap.add_argument("--price", default="¥9.9/瓶")
    ap.add_argument("--corner", default="第二瓶半价")
    ap.add_argument("--no-label", action="store_true")
    a = ap.parse_args()
    out = render_imgfx(a.out, a.fx, a.img, a.size, a.bg, a.dur, a.in_at,
                       a.sfx, float(a.sfx_at) if a.sfx_at else None,
                       a.price, a.corner, label=not a.no_label)
    print(f"→ {out}  ({os.path.getsize(out)/1e6:.2f} MB)")


if __name__ == "__main__":
    main()
