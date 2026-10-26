#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""storyboard_sheet.py —— 渲染前「分镜联络图」（灿爹 2026-10-02 拍板的确认方式）

★口径变更（2026-10-02 灿爹）：
  「以后不要给我出那个确认表了，就直接在生成视频前给我一张这个联络图，
    我就能清晰地看见分镜和文案的对应图了，一目了然，那个表太复杂了。」
  ⇒ P3 集中确认 = **出本图**（一格 = 一镜：画面缩略图 + 文案 + 素材 + 标记）。
  《分镜—文案—素材对应表》退为**内部件**（收尾 A/B 模式的镜边界输入），不再给灿爹看。

★二次口径（2026-10-02 11:1x 灿爹）：
  「直接把重点字和详情卡还有什么音效直接写在对应的图片分镜图片上……放在左下角的小字我看不清，
    全部都放在图片缩略图图片上，然后把对这张图片有什么建议也放在图片上。
    这张图不是拿来用的是拿来看的，写很多字没有问题。」
  ⇒ 改为**每镜一张大卡（cols=1）**，所有标注**叠在画面本体上**、大字号：
     · 顶栏（压在图顶）：#镜号 · 时长 · 起止秒  ｜ 右：视频/新生成/镜像 角标
     · 底部面板（压在图底，半透明黑）：彩底大字 chip【★重点字】【详情图】【音效】【动效】
       + 文案（大字）+ 素材名 + **逐镜建议**（`--advice` JSON，我自己对这镜的看法）
  用 `--cols 2` 可退回小网格总览版。

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  $PY scripts/storyboard_sheet.py --config s1/config.json --out 分镜联络图_待确认.jpg \
        [--advice s1/advice.json] [--brand 爱兰纯] [--title 爱兰纯肌酸 · 261002] \
        [--cols 1] [--cell-w 1100] [--notes 附注.txt]

特性：
  · 图片素材直读缩略；视频素材自动抽**中点帧**（带「视频」角标）
  · src 为 NEW: 开头或文件不存在 → 灰底「待生成」卡 + 描述文字（生图前也能确认）
  · 标注全部源自 config（改 config 即改标注）：keywords/transition_whoosh/swing_shots/
    product_cards/opening_burst/opening_punch/motion/flip/motion_style
  · 中文字体：assets/fonts/新青年体.ttf（回退 STHeiti Medium.ttc）；不使用 emoji（字体无字形）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_BOLD = os.path.join(HERE, "..", "assets", "fonts", "新青年体.ttf")
FONT_REG = os.path.join(HERE, "..", "assets", "fonts", "STHeiti Medium.ttc")
FF = "/Users/mac/.local/bin/ffmpeg"

BG = (16, 17, 20)
CELL = (28, 30, 35)
CARD = (44, 46, 52)
LINE = (58, 62, 70)
FG = (240, 242, 246)
DIM = (168, 175, 186)
ACC = (255, 206, 84)
NEW = (255, 150, 80)
VID = (110, 200, 255)
HOT = (255, 120, 150)

# 彩底 chip 配色（底, 字）
CH_STAR = ((255, 206, 84), (32, 22, 4))     # ★重点字
CH_CARD = ((255, 171, 74), (34, 18, 4))     # 详情图
CH_SFX = ((109, 187, 255), (6, 18, 34))     # 音效
CH_MOT = ((122, 224, 176), (6, 30, 18))     # 动效
PANEL_BG = (6, 7, 10)                       # 叠加面板底色
ADV_COL = (255, 214, 120)                   # 建议文字

CARD_ALIAS = {
    "sgs_report": "SGS报告卡", "sgs": "SGS报告卡",
    "nutrition": "成分表卡", "nutrition_facts": "成分表卡",
    "report": "报告卡", "cert": "认证卡", "lab": "检测卡",
}


def font(path, size):
    for p in (path, FONT_REG, FONT_BOLD):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def ctx_wrap(draw, text, fnt, max_w, max_lines=None):
    """按宽度折行；max_lines 限行数，超出末行加…"""
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=fnt) <= max_w:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
        if max_lines and len(lines) >= max_lines:
            break
    if cur and (not max_lines or len(lines) < max_lines):
        lines.append(cur)
    if max_lines and lines:
        # 若还有剩余字符，末行加省略号
        joined = "".join(lines)
        if len(joined) < len(text):
            while lines[-1] and draw.textlength(lines[-1] + "…", font=fnt) > max_w:
                lines[-1] = lines[-1][:-1]
            lines[-1] += "…"
    return lines


def clip(draw, text, fnt, max_w):
    if draw.textlength(text, font=fnt) <= max_w:
        return text
    while text and draw.textlength(text + "…", font=fnt) > max_w:
        text = text[:-1]
    return text + "…"


def blend(sheet, box, color, alpha):
    """在 RGBA 图上叠一块半透明色"""
    x0, y0, x1, y1 = (int(v) for v in box)
    x0 = max(0, x0); y0 = max(0, y0)
    x1 = min(sheet.size[0], x1); y1 = min(sheet.size[1], y1)
    if x1 <= x0 or y1 <= y0:
        return
    ov = Image.new("RGBA", (x1 - x0, y1 - y0), tuple(color) + (alpha,))
    sheet.paste(Image.alpha_composite(sheet.crop((x0, y0, x1, y1)), ov), (x0, y0))


def card_short(src):
    b = os.path.basename(str(src))
    b = re.sub(r"\.(png|jpe?g|webp)$", "", b, flags=re.I)
    b = re.sub(r"(_card)?_w\d+$", "", b, flags=re.I)
    b = re.sub(r"_card$", "", b, flags=re.I)
    return CARD_ALIAS.get(b.lower(), b.replace("_", " "))


def mid_frame(video, out, w, trim=None):
    dur = None
    try:
        r = subprocess.run([FF.replace("ffmpeg", "ffprobe"), "-v", "error",
                            "-select_streams", "v:0", "-show_entries", "format=duration",
                            "-of", "csv=p=0", video], capture_output=True, text=True)
        dur = float(r.stdout.strip())
    except Exception:
        pass
    t = (dur or 2.0) / 2
    if trim and isinstance(trim, (list, tuple)) and len(trim) == 2:
        t = (float(trim[0]) + float(trim[1])) / 2
    subprocess.run([FF, "-v", "error", "-ss", f"{t:.2f}", "-i", video, "-frames:v", "1",
                    "-vf", f"scale={w}:-1", "-q:v", "3", "-y", out], check=False)
    return os.path.exists(out)


def resolve(p):
    """返回 (素材路径, 是否视频, 是否待生成, 裁切区间)。prod 镜素材在 video 字段、src 是裁切秒区间。"""
    kind = p.get("kind", "img")
    if kind == "prod":
        v = str(p.get("video", ""))
        trim = p.get("src") if isinstance(p.get("src"), (list, tuple)) else None
        return v, True, (not v or not os.path.isfile(v)), trim
    src = str(p.get("src", ""))
    is_new = src.startswith("NEW:") or not os.path.isfile(src)
    is_vid = kind in ("vid", "imgvid") or src.lower().endswith((".mp4", ".mov"))
    return src, is_vid, is_new, None


def cover(im, w, h):
    im = im.convert("RGB")
    iw, ih = im.size
    sc = max(w / iw, h / ih)
    im = im.resize((max(1, int(iw * sc)), max(1, int(ih * sc))), Image.LANCZOS)
    iw, ih = im.size
    l = (iw - w) // 2
    t = int((ih - h) * 0.42)          # 偏上取景，避免切头
    return im.crop((l, t, l + w, t + h))


def collect_marks(cfg, n):
    """按镜号(1-based)归集：动效 / 音效 / 详情图 / 重点字。"""
    mot = {i: [] for i in range(1, n + 1)}
    sfx = {i: [] for i in range(1, n + 1)}
    cds = {i: [] for i in range(1, n + 1)}
    star = {i: [] for i in range(1, n + 1)}
    phrases = cfg.get("phrases", [])

    for i, p in enumerate(phrases, 1):
        if p.get("motion"):
            mot[i].append(str(p["motion"]))
        if p.get("flip"):
            mot[i].append("镜像")
        if p.get("fg") and p.get("bg"):
            mot[i].append("分层入场")
    # 镜1 默认开场 punch 推近（渲染器：i==0 且 opening_punch 为真）
    if n >= 1 and cfg.get("opening_punch", True) and not phrases[0].get("motion"):
        mot[1].insert(0, "开场punch")

    for i in cfg.get("swing_shots") or []:          # 0-based：入场甩动（视觉，无独立音效）
        j = int(i) + 1
        if 1 <= j <= n and "甩镜" not in mot[j]:
            mot[j].append("甩镜")

    for i in cfg.get("transition_whoosh") or []:    # 0-based：转场唿哨（镜起点）
        j = int(i) + 1
        if 1 <= j <= n:
            sfx[j].append("转场唿哨")

    for k in cfg.get("keywords") or []:
        j = int(k.get("phrase", 0))
        if 1 <= j <= n:
            star[j].append(str(k.get("kw", "")))
            if k.get("sfx"):
                sfx[j].append(str(k["sfx"]))

    for c in cfg.get("product_cards") or []:
        j = int(c.get("phrase", 0))
        span = int(c.get("span", 1) or 1)
        for t in range(j, min(j + span, n + 1)):
            if 1 <= t <= n:
                cds[t].append(card_short(c.get("src")))

    # 开场音效：opening_burst（叠在正片前几秒画面上）优先，关掉才用 opening_punch_sfx
    bst = cfg.get("opening_burst", True)
    if n >= 1 and str(bst).lower() not in ("false", "none", "off", "no", "0"):
        if isinstance(bst, dict):
            ps = str(bst.get("preset", "") or "随机")
        elif isinstance(bst, str) and bst.lower() not in ("true", "yes", "on", ""):
            ps = bst
        else:
            ps = "随机"
        sfx[1].insert(0, f"开场冲击·{ps}")
    elif n >= 1 and cfg.get("opening_punch", True) and cfg.get("opening_punch_sfx", True):
        sfx[1].insert(0, "开场重击")

    return mot, sfx, cds, star


def chip_row(dr, x, y, items, max_w, fnt, h=38, pad=14, gap=12):
    """画一排彩底 chip，超宽自动换行；返回最后一行底部 y"""
    cx = x
    for label, (bgc, fgc) in items:
        w = dr.textlength(label, font=fnt) + pad * 2
        if cx + w > x + max_w and cx > x:
            cx, y = x, y + h + 8
        dr.rounded_rectangle([cx, y, cx + w, y + h], 9, fill=bgc)
        dr.text((cx + pad, y + h / 2), label, font=fnt, fill=fgc, anchor="lm")
        cx += w + gap
    return y + h


def chip_outline(dr, x, y, label, fnt, color, h=30, pad=12):
    w = dr.textlength(label, font=fnt) + pad * 2
    dr.rounded_rectangle([x, y, x + w, y + h], 7, outline=color, width=2)
    dr.text((x + pad, y + h / 2), label, font=fnt, fill=color, anchor="lm")
    return x + w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--brand", default="")
    ap.add_argument("--title", default="")
    ap.add_argument("--advice", default=None, help='逐镜建议 JSON：{"1":"...","12":"..."}')
    ap.add_argument("--cols", type=int, default=1, help="1=每镜一张大卡（默认，字最大）；2/3/4=网格总览")
    ap.add_argument("--cell-w", type=int, default=1100)
    ap.add_argument("--notes", default=None, help="额外汇总行（每行一条，追加到页脚）")
    ap.add_argument("--tmp", default=None, help="视频抽帧缓存目录")
    a = ap.parse_args()

    big = a.cols == 1
    cfg = json.load(open(a.config, encoding="utf-8"))
    phrases = cfg["phrases"]
    workdir = a.tmp or os.path.join(os.path.dirname(os.path.abspath(a.config)), "_sheet_frames")
    os.makedirs(workdir, exist_ok=True)

    advice = {}
    if a.advice and os.path.isfile(a.advice):
        try:
            advice = {str(k): str(v) for k, v in json.load(open(a.advice, encoding="utf-8")).items()}
        except Exception as e:
            print("WARN advice 解析失败:", e)

    # 时间轴：优先用 tokens（真实配音），否则按字数估
    try:
        sys.path.insert(0, HERE)
        import shot_plan as sp
        tl, _ = sp.build_timeline(cfg)
    except Exception:
        tl = None

    n = len(phrases)
    mot, sfx, cds, star = collect_marks(cfg, n)

    cw = a.cell_w
    ch = int(cw * 9 / 16)
    TOP_H = 40
    PANEL_H = 250 if big else 0
    ROW_GAP = 16
    cols = a.cols
    rows = (n + cols - 1) // cols
    info_h = 0 if big else 108
    row_h = ch + info_h + ROW_GAP

    notes = []
    if a.notes and os.path.isfile(a.notes):
        notes = [l.strip() for l in open(a.notes, encoding="utf-8") if l.strip()]

    # ── 页脚汇总 ──
    res = [resolve(p) for p in phrases]
    n_new = sum(1 for (_, _, is_new, _) in res if is_new)
    n_vid = sum(1 for (_, is_vid, _, _) in res if is_vid)
    n_flip = sum(1 for p in phrases if p.get("flip"))

    foot = []
    kws = cfg.get("keywords") or []
    if kws:
        foot.append("重点字 %d 个：" % len(kws) + " / ".join(
            f"镜{k['phrase']}「{k['kw']}」" + (f"＋{k['sfx']}" if k.get("sfx") else "") for k in kws))
    pcs = cfg.get("product_cards") or []
    if pcs:
        parts = []
        for c in pcs:
            j = int(c.get("phrase", 0)); span = int(c.get("span", 1) or 1)
            parts.append(f"{'镜%d' % j if span <= 1 else '镜%d–%d' % (j, j + span - 1)} {card_short(c.get('src'))}")
        foot.append("详情图 %d 张：" % len(pcs) + "、".join(parts))
    if cfg.get("transition_whoosh"):
        foot.append("转场唿哨(1起)：" + "、".join(f"镜{int(i) + 1}" for i in cfg["transition_whoosh"]))
    if cfg.get("swing_shots"):
        foot.append("入场甩镜(1起)：" + "、".join(f"镜{int(i) + 1}" for i in cfg["swing_shots"]))
    ms = cfg.get("motion_style") or {}
    if ms:
        bits = [f"缓动 {ms.get('ease', '—')}"]
        for k2, lab in (("breath", "呼吸"), ("shake", "抖动"), ("grain", "颗粒"), ("intensity", "强度")):
            if ms.get(k2):
                bits.append(f"{lab} {ms[k2]}")
        if ms.get("vignette"):
            bits.append("暗角")
        foot.append("全片动效：" + " · ".join(bits))
    sb = []
    if cfg.get("sfx", True):
        sb.append("音效开")
    if cfg.get("sfx_gap_prominent"):
        sb.append(f"响亮事件互斥 {cfg['sfx_gap_prominent']}s")
    if cfg.get("sfx_gap_whoosh"):
        sb.append(f"唿哨间距 {cfg['sfx_gap_whoosh']}s")
    if cfg.get("sfx_min_gap"):
        sb.append(f"普通间距 {cfg['sfx_min_gap']}s")
    if sb:
        foot.append("音效规则：" + " · ".join(sb))
    shl = cfg.get("sub_highlights") or []
    if shl:
        foot.append("字幕高亮：" + "/".join(str(s.get("kw", "")) for s in shl))
    if n_new:
        foot.append("待生成 / 待补：" + "、".join(
            f"镜{i}" for i, (_, _, is_new, _) in enumerate(res, 1) if is_new))
    if n_flip:
        foot.append("镜像镜（须过 flip_guard）：" + "、".join(
            str(i) for i, p in enumerate(phrases, 1) if p.get("flip")))
    foot += notes

    W = cols * (cw + 14) + 14
    head_h = 100
    foot_h = 30 + 30 * max(1, len(foot))
    H = head_h + rows * row_h + foot_h + 10
    sheet = Image.new("RGBA", (W, H), BG + (255,))
    dr = ImageDraw.Draw(sheet)

    f_title = font(FONT_BOLD, 34)
    f_sub = font(FONT_REG, 20)
    f_meta = font(FONT_BOLD, 25)
    f_meta2 = font(FONT_REG, 20)
    f_chip = font(FONT_BOLD, 22)
    f_badge = font(FONT_REG, 19)
    f_txt = font(FONT_REG, 26)
    f_src = font(FONT_REG, 19)
    f_adv = font(FONT_REG, 21)
    f_foot = font(FONT_REG, 19)

    # ── 页首 ──
    brand = a.brand or str(cfg.get("brand", "") or "")
    title = a.title or ((brand + " · 分镜联络图") if brand else "分镜联络图")
    dr.text((16, 14), title, font=f_title, fill=FG)
    total = f"{(tl[-1][2] - tl[0][1]) if tl else 0:.1f}s" if tl else "—"
    dr.text((16, 60), f"{n} 镜 · 配音总长 {total} · 视频镜 {n_vid} · 新生成 {n_new} · "
                      f"重点字 {len(kws)} · 详情图 {len(pcs)}" + (f" · 品牌 {brand}" if brand else "") +
                      "　（每格＝一镜，标注直接压在画面上）",
            font=f_sub, fill=DIM)
    dr.line([(0, head_h - 10), (W, head_h - 10)], fill=LINE, width=1)

    # ── 每格 ──
    for i, p in enumerate(phrases, 1):
        r, c = divmod(i - 1, cols)
        x0 = 14 + c * (cw + 14)
        y0 = head_h + r * row_h
        box_h = ch + info_h
        dr.rounded_rectangle([x0, y0, x0 + cw, y0 + box_h], 10, fill=CELL)

        src, is_vid, is_new, trim = resolve(p)
        thumb = None
        if not is_new:
            if is_vid:
                f = os.path.join(workdir, f"shot{i:02d}.jpg")
                if mid_frame(src, f, cw, trim):
                    thumb = cover(Image.open(f), cw, ch)
            else:
                try:
                    thumb = cover(Image.open(src), cw, ch)
                except Exception:
                    thumb = None

        if thumb is not None:
            sheet.paste(thumb, (x0, y0))
        else:
            dr.rectangle([x0, y0, x0 + cw, y0 + ch], fill=CARD)
            desc = src[4:].replace("-", " ").replace("_", " ") if src.startswith("NEW:") else "素材待补"
            dr.text((x0 + 24, y0 + 20), "待生成", font=font(FONT_BOLD, 34), fill=NEW)
            yy = y0 + 70
            for ln in ctx_wrap(dr, desc, font(FONT_REG, 20), cw - 48, 4):
                dr.text((x0 + 24, yy), ln, font=font(FONT_REG, 20), fill=FG)
                yy += 28

        # ── 顶部叠加条：镜号 / 时长 / 起止秒 + 类型角标 ──
        blend(sheet, (x0, y0, x0 + cw, y0 + TOP_H), PANEL_BG, 205)
        dur = f"{tl[i-1][2] - tl[i-1][1]:.2f}s" if tl and i - 1 < len(tl) else ""
        rng = f"{tl[i-1][1]:.2f}–{tl[i-1][2]:.2f}s" if tl and i - 1 < len(tl) else ""
        head_txt = f"#{i}　{dur}　{rng.replace('–', '-')}"
        dr.text((x0 + 18, y0 + TOP_H / 2), head_txt, font=f_meta, fill=FG, anchor="lm")
        bx = x0 + cw - 18
        for label, col in reversed(
            ([("镜像", HOT)] if p.get("flip") else []) +
            ([("新生成", NEW)] if is_new else []) +
            ([("视频", VID)] if is_vid else [])
        ):
            w = dr.textlength(label, font=f_badge) + 24
            bx -= w
            chip_outline(dr, bx, y0 + (TOP_H - 30) // 2, label, f_badge, col)
            bx -= 8

        if big:
            # ── 底部叠加面板 ──
            py = y0 + ch - PANEL_H
            blend(sheet, (x0, py, x0 + cw, y0 + ch), PANEL_BG, 222)
            inner_w = cw - 36
            # chips 行
            items = []
            if star.get(i):
                items.append(("重点字　" + "·".join(star[i]), CH_STAR))   # 新青年体无★字形，去星号
            if cds.get(i):
                items.append(("详情图　" + "·".join(cds[i]), CH_CARD))
            if sfx.get(i):
                items.append(("音效　" + "·".join(sfx[i]), CH_SFX))
            items.append(("动效　" + ("·".join(mot[i]) if mot.get(i) else "无"), CH_MOT))
            cy = py + 14
            chip_row(dr, x0 + 18, cy, items, inner_w, f_chip)
            # 文案（最大 2 行）
            ty = py + 14 + 38 + 16
            for k, ln in enumerate(ctx_wrap(dr, p.get("text", ""), f_txt, inner_w, 2)):
                dr.text((x0 + 18, ty + k * 32), ln, font=f_txt, fill=FG)
            # 素材
            base = os.path.basename(src)
            if is_new and src.startswith("NEW:"):
                base = "待生成：" + src[4:]
            sy = ty + 2 * 32 + 6
            dr.text((x0 + 18, sy), clip(dr, "素材　" + base, f_src, inner_w), font=f_src, fill=DIM)
            # 建议
            adv = advice.get(str(i), "")
            ay = sy + 26
            if adv:
                for k, ln in enumerate(ctx_wrap(dr, "建议　" + adv, f_adv, inner_w, 2)):
                    dr.text((x0 + 18, ay + k * 27), ln, font=f_adv, fill=ADV_COL)
        else:
            # 网格模式的简版信息条
            iy = y0 + ch + 8
            dur2 = dur
            dr.text((x0 + 10, iy), f"#{i}", font=font(FONT_BOLD, 19), fill=ACC)
            dr.text((x0 + 40, iy + 2), dur2, font=font(FONT_REG, 14), fill=DIM)
            yy = iy + 26
            for ln in ctx_wrap(dr, p.get("text", ""), font(FONT_REG, 19), cw - 20, 2):
                dr.text((x0 + 10, yy), ln, font=font(FONT_REG, 19), fill=FG)
                yy += 23
            dr.text((x0 + 10, y0 + ch + 108 - 18), clip(dr, os.path.basename(src), f_src, cw - 20),
                    font=font(FONT_REG, 14), fill=DIM)

    # ── 页脚 ──
    fy = head_h + rows * row_h + 6
    dr.line([(14, fy - 8), (W - 14, fy - 8)], fill=LINE, width=1)
    yy = fy
    for ln in foot:
        dr.text((16, yy), "· " + clip(dr, ln, f_foot, W - 36), font=f_foot, fill=DIM)
        yy += 30
    if not foot:
        dr.text((16, yy), "· （无附注）", font=f_foot, fill=DIM)

    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    sheet.convert("RGB").save(a.out, quality=90)
    print(f"{n} 镜 → {a.out}  {sheet.size[0]}x{sheet.size[1]}  视频镜 {n_vid} · 新生成 {n_new} · 建议 {len(advice)} 条")
    for ln in foot:
        print("   ·", ln)


if __name__ == "__main__":
    main()
