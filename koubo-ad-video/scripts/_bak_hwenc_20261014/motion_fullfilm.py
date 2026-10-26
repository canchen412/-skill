#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motion_fullfilm.py —— 用「既有对应表 + 原片音频」一键出「动效增强版」成片（0 积分）

把《分镜—文案—素材对应表》里记录的 17 镜，逐镜套六层动效重渲、烧中文字幕与原重点字，
再并回原片音轨 ⇒ 一条画面"活"起来的同长成片。

链路：对应表 HTML → 解析(镜号/时间轴/文案/素材/重点字) → 图库+工作区定位图
      → 逐镜六层动效（img_to_motion 内核）→ 帧上烧字幕+重点字 → ffmpeg 直出 → 并音轨

用法：
  $PY motion_fullfilm.py --table 品酷_分镜文案素材对应表.html \
        --out 动效增强版.mp4 [--audio 原片.mp4|音频文件] [--fps 30]
        [--intensity 1.0] [--grain 3.0] [--no-sub] [--crf 18]
        [--only 8]        # 只渲第 8 镜（调试）
        [--lib <图库根>] [--work <工程目录，用来找项目图>]

★成本：0 积分（纯本地 cv2/numpy）。37s 片约 2 分钟渲完。
"""
from __future__ import annotations

import argparse
import html
import math
import os
import re
import subprocess
import sys

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from img_to_motion import (KINDS, build_vignette, cover_to,  # noqa: E402
                           make_dust, render_frame)

W, H = 1920, 1080
FONTS = os.path.expanduser("~/.workbuddy/skills/koubo-ad-video/assets/fonts")
FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")
DEFAULT_LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"


# ---------------------------------------------------------------- 对应表解析
def parse_table(path: str):
    """从《分镜—文案—素材对应表》里抽出 [(no, t0, t1, text, stem, hot)]。"""
    s = open(path, encoding="utf-8", errors="ignore").read()
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", s, flags=re.S)
    out = []
    for r in rows:
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", r, flags=re.S)
        cs = [re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", c))).strip() for c in cells]
        cs = [c for c in cs if c]
        if len(cs) < 5:
            continue
        m_no = re.fullmatch(r"(\d{1,2})", cs[0])
        if not m_no:
            continue
        m_t = re.search(r"(\d+\.\d+)\s*[–\-—~]\s*(\d+\.\d+)", cs[1])
        if not m_t:
            continue
        text = cs[2]
        # 素材文件名：优先第 6 格（镜/时间/文案/画面描述/来源/素材）；末格是决策按钮
        FILE_RE = re.compile(r"^[\u4e00-\u9fa5A-Za-z0-9]+_[\u4e00-\u9fa5A-Za-z0-9_]+$")
        stem = ""
        for c in ([cs[5]] if len(cs) > 5 else []) + cs[3:]:
            cc = re.sub(r"\.(png|jpg|jpeg|webp|PNG|JPG)$", "", c).strip()
            if FILE_RE.match(cc) and len(cc) > 4:
                stem = cc
                break
        hot = ""
        mh = re.search(r"重点字[：:]\s*([^\s｜|]+)", " ".join(cs))
        if mh:
            hot = mh.group(1).strip()
        if stem:
            out.append((int(m_no.group(1)), float(m_t.group(1)), float(m_t.group(2)),
                        text, stem, hot))
    out.sort(key=lambda x: x[0])
    return out


def build_index(lib: str, work: str | None):
    idx = {}
    roots = [lib]
    if work:
        roots.append(work)
    for root in roots:
        for dp, dns, fns in os.walk(root):
            dns[:] = [d for d in dns if not d.startswith(".") and not d.startswith("_旧")]
            for f in fns:
                if f.startswith("._"):
                    continue
                if os.path.splitext(f)[1].lower() not in (".png", ".jpg", ".jpeg", ".webp"):
                    continue
                idx.setdefault(os.path.splitext(f)[0], os.path.join(dp, f))
    return idx


def guess_kind(stem: str) -> str:
    if re.match(r"(产品_|选品_|囤货_|展示_|收尾_)", stem):
        return "product"
    if re.match(r"(餐食_|吃_|生活_厨房|生活_粥)", stem):
        return "food"
    if re.match(r"(训练_|举铁_|跑步_|俯卧撑_|单杠_)", stem):
        return "scene"
    return "person"


# ---------------------------------------------------------------- 字幕 / 重点字
_font_cache: dict = {}


def _font(size: int, hot: bool = False):
    key = (size, hot)
    if key in _font_cache:
        return _font_cache[key]
    paths = ([os.path.join(FONTS, "新青年体.ttf")] if hot
             else [os.path.join(FONTS, "STHeiti Medium.ttc"), os.path.join(FONTS, "新青年体.ttf")])
    f = None
    for p in paths:
        for i in (0, 1, 2):
            try:
                f = ImageFont.truetype(p, size, index=i)
                break
            except Exception:
                continue
        if f:
            break
    f = f or ImageFont.load_default()
    _font_cache[key] = f
    return f


def wrap_cjk(text: str, per_line: int = 17):
    if len(text) <= per_line:
        return [text]
    cands = []
    for p in "，。、；：？！":
        q = text.find(p, max(0, per_line - 9), min(len(text) - 1, per_line + 9))
        if q >= 0:
            cands.append(q)
    bp = min(cands, key=lambda q: abs(q + 1 - per_line)) if cands else per_line
    return [text[:bp + 1], text[bp + 1:]]


def make_sub_layer(text: str, size: int = 70):
    lines = wrap_cjk(text)
    f = _font(size)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    lh = int(size * 1.28)
    y0 = H - 62 - lh * len(lines)
    for i, ln in enumerate(lines):
        bb = d.textbbox((0, 0), ln, font=f, stroke_width=4)
        d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], y0 + i * lh), ln, font=f,
               fill=(255, 255, 255, 255), stroke_width=4, stroke_fill=(0, 0, 0, 255))
    return img


def make_hot_layer(hot: str):
    if not hot:
        return None
    f = _font(168, hot=True)
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    bb = d.textbbox((0, 0), hot, font=f, stroke_width=9)
    d.text(((W - (bb[2] - bb[0])) // 2 - bb[0], 150), hot, font=f,
           fill=(255, 196, 32, 255), stroke_width=9, stroke_fill=(40, 26, 0, 255))
    return img


def relight(im: np.ndarray) -> np.ndarray:
    """统一影调：轻微提对比 + 冷调阴影，抹平不同来源图的色差。"""
    f = im.astype(np.float32) / 255.0
    f = np.clip((f - 0.5) * 1.045 + 0.5 + 0.006, 0, 1)
    f[..., 2] *= 1.012
    f[..., 0] *= 0.994
    return np.clip(f * 255.0, 0, 255).astype(np.uint8)


MOTIONS = ["in", "pan_l", "out", "pan_r"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--audio", default=None, help="原片 mp4 或音频文件；不给则输出无声")
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--work", default=None)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--intensity", type=float, default=1.0)
    ap.add_argument("--grain", type=float, default=3.0)
    ap.add_argument("--crf", type=int, default=18)
    ap.add_argument("--no-sub", action="store_true")
    ap.add_argument("--no-beam", action="store_true", help="关掉光束扫过")
    ap.add_argument("--no-shake", action="store_true", help="关掉手持微晃")
    ap.add_argument("--no-dust", action="store_true", help="关掉漂浮粒子")
    ap.add_argument("--ease", default="out", choices=["out", "inout"])
    ap.add_argument("--breath", type=float, default=0.0, help="极缓慢呼吸幅度（缩放单位，0=关）")
    ap.add_argument("--zoom-span", type=float, default=0.068, help="推拉总行程（缩放单位）")
    ap.add_argument("--focus-every", type=int, default=1,
                    help="每 N 镜做一次「对焦呼吸」：1=每镜都做，2=隔一张做一次，3=隔两张做一次")
    ap.add_argument("--only", type=int, default=0)
    a = ap.parse_args()

    shots = parse_table(a.table)
    if not shots:
        sys.exit("对应表里没解析出任何镜头 —— 检查表格结构")
    print(f"对应表解析：{len(shots)} 镜，总时长 {shots[-1][2]:.2f}s")
    if a.only:
        shots = [s for s in shots if s[0] == a.only]
    idx = build_index(a.lib, a.work)

    vig = build_vignette(W, H, 0.30)
    rng = np.random.default_rng(11)
    tmp = a.out + ".silent.mp4" if a.audio and not a.only else a.out
    cmd = [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(a.fps), "-i", "-", "-an",
           "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf),
           "-pix_fmt", "yuv420p", tmp]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    done = 0
    for si, (no, t0, t1, text, stem, hot) in enumerate(shots, 1):
        path = idx.get(stem)
        if not path:
            sys.exit(f"镜{no} 找不到素材：{stem}")
        src = cv2.imread(path, cv2.IMREAD_COLOR)
        if src is None:
            sys.exit(f"读不到：{path}")
        flip = "镜像" in text or "（水平镜像）" in text
        if flip:
            src = cv2.flip(src, 1)
        base = relight(cover_to(src, W, H))

        n = max(2, int(round((t1 - t0) * a.fps)))
        kind = guess_kind(stem)
        motion = MOTIONS[(no - 1) % 4]
        dust_rgb, beam_amp, beam_rgb = KINDS[kind]
        span = a.zoom_span
        zbase = 1.075
        cfg = dict(intensity=a.intensity, dur=t1 - t0, grain=a.grain,
                   z0=zbase, z1=zbase + span, dx=-0.22, dy=-0.10,
                   dust_rgb=dust_rgb, ease=a.ease, breath=a.breath,
                   focus=(1.0 if (a.focus_every <= 1 or (no - 1) % a.focus_every == 0) else 0.0),
                   beam=(0.0 if a.no_beam or flip else beam_amp), beam_rgb=beam_rgb,
                   shake=(0.0 if a.no_shake else 1.0))
        if motion == "in":
            cfg.update(z0=zbase, z1=zbase + span, dx=-0.22, dy=-0.10)
        elif motion == "out":
            cfg.update(z0=zbase + span, z1=zbase, dx=0.22, dy=0.10)
        elif motion == "pan_l":
            cfg.update(z0=zbase + 0.035, z1=zbase + 0.035 + span * 0.22, dx=0.82, dy=-0.14)
        else:
            cfg.update(z0=zbase + 0.035, z1=zbase + 0.035 + span * 0.22, dx=-0.82, dy=0.14)

        dust = [] if a.no_dust else make_dust(52, W, H, np.random.default_rng(1000 + no))
        sub = None if a.no_sub else make_sub_layer(text)
        hotl = None if a.no_sub else make_hot_layer(hot)
        hot_n = min(n, int(0.55 * a.fps)) if hotl else 0

        for i in range(n):
            fr = render_frame(i, n, base, W, H, cfg, dust, vig, rng)
            if sub is not None:
                im = Image.fromarray(cv2.cvtColor(fr, cv2.COLOR_BGR2RGB)).convert("RGBA")
                if hotl is not None and i < hot_n:
                    k = min(1.0, i / max(1, 0.22 * a.fps))
                    sc = 0.72 + 0.28 * (1 - (1 - k) ** 3)
                    hw, hh = int(W * sc), int(H * sc)
                    hl = hotl.resize((hw, hh), Image.LANCZOS)
                    al = int(255 * min(1.0, k * 1.4))
                    hl.putalpha(hl.getchannel("A").point(lambda v: v * al // 255))
                    im.alpha_composite(hl, ((W - hw) // 2, int(150 - (hh - H) * 0.16)))
                im.alpha_composite(sub)
                fr = cv2.cvtColor(np.asarray(im.convert("RGB")), cv2.COLOR_RGB2BGR)
            p.stdin.write(fr.tobytes())
        done += n
        _f = "对焦" if cfg["focus"] > 0 else "  — "
        print(f"  镜{no:2d} {t0:5.2f}–{t1:5.2f} ({t1-t0:4.2f}s) {motion:5s} {kind:7s} {_f} "
              f"{'★'+hot if hot else '':8s} {text[:16]}", flush=True)

    p.stdin.close()
    p.wait()
    if p.returncode != 0:
        sys.exit("ffmpeg 渲染失败")
    print(f"✅ 画面 {done} 帧 ≈ {done/a.fps:.2f}s → {tmp}")
    if a.audio and not a.only:
        subprocess.run([FFMPEG, "-v", "error", "-y", "-i", tmp, "-i", a.audio,
                        "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-shortest", a.out], check=True)
        os.remove(tmp)
        print(f"✅ 已并入音轨 → {a.out}")


if __name__ == "__main__":
    main()
