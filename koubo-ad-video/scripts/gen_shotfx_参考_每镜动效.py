#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gen_shotfx.py —— 姜黄素VORSE R8 五个动效镜的成片级素材生成（全幅铺满版）

输出（全部 1920x1080@30fps，静音，12s 起供渲染器 trim）：
  gen/镜04_科普猛塞蛋白_fx-fade_up.mp4      淡入上浮
  gen/镜06_科普办公室_fx-blur_in.mp4        模糊聚焦
  gen/镜07_科普玩手机_fx-breathe.mp4        呼吸脉冲（循环）
  gen/镜09_建模3D大字.mp4                   「建模」大字飞入小绿人身后（3D 挤压+透视+摇摆）
  gen/镜14_原料分列_fx-circle_reveal.mp4    圆形揭示（圆圈动效）

复用技能动效库 imgfx_render 的 fx 函数与缓动曲线（GSAP 1:1）。
"""
import os, sys, math, subprocess
sys.path.insert(0, "/Users/mac/.workbuddy/skills/koubo-ad-video/scripts")
import imgfx_render as I
from PIL import Image, ImageDraw, ImageFilter, ImageFont
import numpy as np

FF = "/Users/mac/.local/bin/ffmpeg"
FPS = 30
W, H = 1920, 1080
LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
WORK = "/Users/mac/WorkBuddy/2026-10-10-10-50-03/姜黄素R8_视频"
GEN = os.path.join(WORK, "gen")
os.makedirs(GEN, exist_ok=True)
DUR = 12.0


def cover(path, w=W, h=H):
    """铺满裁切成 1920x1080 RGBA（无圆角，全幅）"""
    im = Image.open(path).convert("RGB")
    iw, ih = im.size
    s = max(w / iw, h / ih)
    nw, nh = int(iw * s + 0.5), int(ih * s + 0.5)
    im = im.resize((nw, nh), Image.LANCZOS)
    x0, y0 = (nw - w) // 2, (nh - h) // 2
    return im.crop((x0, y0, x0 + w, y0 + h)).convert("RGBA")


def encode_pipe(out, dur, frame_iter):
    """帧直灌 ffmpeg stdin（rawvideo），不落帧文件"""
    cmd = [FF, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
           "-vf", f"fps={FPS},format=yuv420p", "-c:v", "h264_videotoolbox", "-q:v", "75", "-t", f"{dur:.3f}", out]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    for rgb in frame_iter:
        proc.stdin.write(rgb)
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {out}")
    print("→", out, f"{os.path.getsize(out)/1e6:.2f} MB")


def iter_frames(total, frame_fn):
    for i in range(total):
        t = i / FPS
        yield frame_fn(t).convert("RGB").tobytes()


def render_fx(fx, img_path, out, in_at=0.25, dur=DUR):
    """全幅版图效：黑底画布 + 全幅图，静音（成片音效由渲染器统一铺）"""
    img = cover(img_path)
    ctx = {"img": img, "in_at": in_at}

    def frame(t):
        canvas = Image.new("RGBA", (W, H), (10, 11, 14, 255))
        I.FX[fx](canvas, t, ctx)
        return canvas

    encode_pipe(out, dur, iter_frames(int(dur * FPS), frame))


# ---------------- 镜9：「建模」3D 大字飞入小绿人身后 ----------------
def person_mask(bg_rgb):
    """绿色小绿人抠像：绿主导掩膜 → 闭运算填孔（白护腕/黑短裤）→ 羽化"""
    a = np.asarray(bg_rgb).astype(int)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    green = (g > r + 18) & (g > b + 18) & (g > 55)
    m = Image.fromarray((green * 255).astype("uint8"), "L")
    # 闭运算：先膨胀再腐蚀，把白护腕/黑短裤等内部孔洞合进来
    m = m.filter(ImageFilter.MaxFilter(41)).filter(ImageFilter.MinFilter(41))
    m = m.filter(ImageFilter.MaxFilter(9))          # 外扩 4px 兜住轮廓边
    m = m.point(lambda v: 255 if v > 110 else 0)
    m = m.filter(ImageFilter.GaussianBlur(2.5))     # 羽化边
    return m


def text3d_layer(text, size):
    """3D 挤压大字：暗金挤压层 + 亮金面 + 描边，再 QUAD 透视（上窄下宽=后仰立体）"""
    font = ImageFont.truetype(I.FONT_PATH, size, index=I.FONT_IDX)
    d0 = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    bbox = d0.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pad, depth = 80, 16
    lw, lh = tw + pad * 2 + depth, th + pad * 2 + depth
    lay = Image.new("RGBA", (lw, lh), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    ox, oy = pad - bbox[0], pad - bbox[1]
    # 挤压层：沿右下方向堆叠暗金色
    for i in range(depth, 0, -1):
        k = i / depth
        col = (int(120 - 40 * k), int(72 - 24 * k), int(8 + 6 * k), 255)
        d.text((ox + i * 1.6, oy + i * 1.6), text, font=font, fill=col)
    # 顶面：亮金 + 深色描边
    d.text((ox, oy), text, font=font, fill=(255, 208, 62, 255),
           stroke_width=6, stroke_fill=(92, 54, 6, 255))
    # 高光：亮面左上再叠一层浅金，错位 2px 制造受光面
    d.text((ox - 2, oy - 2), text, font=font, fill=(255, 236, 150, 160))
    # QUAD 透视：源四边形上边更宽 → 输出上窄下宽（牌匾后仰感）
    src_q = (lw * -0.06, 0, 0, lh, lw, lh, lw * 1.06, 0)   # NW, SW, SE, NE
    lay = lay.transform((lw, lh), Image.QUAD, src_q, resample=Image.BICUBIC)
    return lay


def render_jianmo(img_path, out, in_at=2.6, dur=DUR):
    bg = cover(img_path)
    pm = person_mask(bg)
    fg = bg.copy()
    fg.putalpha(pm)
    bbox = pm.getbbox()  # (l, t, r, b)
    px, pt, ph = (bbox[0] + bbox[2]) // 2, bbox[1], bbox[3] - bbox[1]
    tl = text3d_layer("建模", 330)
    tw, th = tl.size
    tx, ty = px - tw // 2, pt + int(ph * 0.52) - th // 2   # 停在胸口偏下，身体挡住字的中间一条
    # ★灿爹 2026-10-10：大字必须完整在屏内——落点钳到画面安全区（上 24px / 下 24px）
    ty = max(th // 2 + 24, min(ty, H - th // 2 - 24))
    total = int(dur * FPS)

    def frame(t):
        im = bg.copy()
        if t >= in_at:
            p = min(1.0, (t - in_at) / 0.9)
            e = I.ease_out_back(p, 1.4)
            # 飞入：从画外上方斜插进来
            sy = -560 + (ty + 560) * e
            sc = 1.5 - 0.5 * e
            alpha = int(255 * min(1.0, (t - in_at) / 0.18))
            lay = tl
            if abs(sc - 1) > 0.01:
                lay = I.scaled(tl, sc)
            # 落定后 3D 摇摆：轻微左右倾 + 上下浮
            sway = 0.0
            yb = 0
            if p >= 1:
                ph2 = (t - in_at - 0.9)
                sway = 1.4 * math.sin(ph2 * math.pi / 1.6)
                yb = int(6 * math.sin(ph2 * math.pi / 1.6))
            if abs(sway) > 0.05:
                lay = lay.rotate(sway, expand=True, resample=Image.BICUBIC)
            if alpha < 250:
                lay = lay.copy()
                lay.putalpha(lay.getchannel("A").point(lambda v: int(v * alpha / 255)))
            im.alpha_composite(lay, (px - lay.size[0] // 2, int(sy) + yb - lay.size[1] // 2))
        # 人物层压在大字之上 → 「小绿人挡住一点点」
        im.alpha_composite(fg)
        return im

    encode_pipe(out, dur, iter_frames(total, frame))


if __name__ == "__main__":
    jobs = sys.argv[1:] or ["all"]
    if "all" in jobs or "4" in jobs:
        render_fx("fade_up", f"{LIB}/姜黄素/VORSE/图片/科普段_猛塞蛋白堆_抓食送嘴_16x9.png",
                  f"{GEN}/镜04_科普猛塞蛋白_fx-fade_up.mp4", in_at=0.25)
    if "all" in jobs or "6" in jobs:
        render_fx("blur_in", f"{LIB}/姜黄素/VORSE/图片/科普段_深夜办公室扶头_窗外灯火_16x9.png",
                  f"{GEN}/镜06_科普办公室_fx-blur_in.mp4", in_at=0.25)
    if "all" in jobs or "7" in jobs:
        render_fx("breathe", f"{LIB}/姜黄素/VORSE/图片/科普段_凌晨玩手机_躺床刷手机_16x9.png",
                  f"{GEN}/镜07_科普玩手机_fx-breathe.mp4")
    if "all" in jobs or "9" in jobs:
        # ★镜内时点按配音实测校准：P07 全长 2.66s，「建模」镜内 1.46s 起音
        render_jianmo(f"{LIB}/训练_健身房展示体型_全身.png",
                      f"{GEN}/镜09_建模3D大字.mp4", in_at=1.30)
    if "all" in jobs or "14" in jobs:
        render_fx("circle_reveal", f"{LIB}/姜黄素/VORSE/图片/原料_产品居中黑胡椒肉桂分列_16x9.png",
                  f"{GEN}/镜14_原料分列_fx-circle_reveal.mp4", in_at=0.05)
    print("done")
