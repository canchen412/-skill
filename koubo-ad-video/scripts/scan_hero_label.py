#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主图（卷播 Hero）「大字 + 人物遮挡」落位扫描
─────────────────────────────────────────────────────
背景（2026-10-15 烟酰胺21金维他 v1 灿爹需求）：
  「烟酰胺三个大字要在人物的后面，被遮挡一部分，形成 3D 景深；正中间，
   头部遮挡一小部分」

原理：Hero 三层卡（hero_bg → 字层 → hero_fg）中，字的位置由卡内 (labelTop, labelSize)
  决定。本脚本用**真实字体渲染字形蒙版**，与 hero_fg.png 的人物 alpha 求交，
  逐参数输出「字可见率」与「遮挡来自头部的占比」，挑最贴合需求的一组。

卡→屏幕映射（MarqueeClickD 定式，勿改）：
  S = 1920/700；x_screen = 960 + (x_card-350)*S；y_screen = 520 + (y_card-197)*S

用法:
  python3 scan_hero_label.py --fg <cards_dir>/hero_fg.png --font <新青年体.ttf> \
      --text 烟酰胺 --gap 16 [--sizes 90,100,110,120,132] [--tops 0,16,30,45]

输出：表格 + 推荐组合（默认判据：可见率 60~80% 且「头遮占字比」最大）
参考实拍（烟酰胺v1）：size=100/top=16 → 可见 67.8%，遮挡中 51.6% 来自头部 ⇒ 采选。
"""
import argparse
import numpy as np
from PIL import Image, ImageDraw, ImageFont

S = 1920 / 700.0
CX_CARD, CY_CARD = 350.0, 197.0
SCR_CX, SCR_CY = 960.0, 520.0


def card_to_screen(cx, cy):
    return SCR_CX + (cx - CX_CARD) * S, SCR_CY + (cy - CY_CARD) * S


def head_zone(person, frac=0.30):
    """头部＝从头顶到「肩线」之间的人物区域。
    肩线判据：逐行宽度首次超过「头顶窄段宽度中位数 × 1.45」且 > 150px。
    ★2026-10-15 修正：旧版取「上半段最窄那一行」的水平范围，而头顶尖点往往只有
    几像素宽（实测 1920×1080 下 y=38 行宽 9px）⇒ 头部框退化成 48px 细缝，
    **头部遮挡率恒为 0，工具给出的「推荐参数」因此失真**（4 字标题时误推 size70/top30）。
    现改为按肩线划上下界、取该段人物最大外接 x 作水平范围。
    """
    ys, xs = np.where(person)
    if len(ys) == 0:
        return np.zeros_like(person)
    y_top, y_bot = ys.min(), ys.max()
    rows = person.sum(axis=1)
    band = rows[y_top:y_top + max(4, int(0.06 * (y_bot - y_top)))]
    base = np.median(band[band > 0]) if (band > 0).any() else 1
    y_shoulder = y_bot
    for y in range(y_top + 1, y_bot):
        if rows[y] > base * 1.45 and rows[y] > 150:
            y_shoulder = y
            break
    h = np.zeros_like(person)
    seg = person[y_top:max(y_top + 1, y_shoulder)]
    sy, sx = np.where(seg)
    if len(sx) == 0:
        return h
    h[y_top:y_shoulder, max(0, sx.min() - 10):min(person.shape[1], sx.max() + 11)] = True
    return h


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--fg', required=True, help='hero_fg.png（人物 RGBA，含 alpha）')
    ap.add_argument('--font', required=True, help='字体 ttf')
    ap.add_argument('--text', default='烟酰胺')
    ap.add_argument('--gap', type=int, default=16, help='字间距（卡内 px）')
    ap.add_argument('--sizes', default='90,100,110,120,132')
    ap.add_argument('--tops', default='0,16,30,45,60')
    a = ap.parse_args()

    fg = np.array(Image.open(a.fg))
    person = fg[:, :, 3] > 120
    H, W = person.shape
    head = head_zone(person) & person

    sizes = [int(x) for x in a.sizes.split(',')]
    tops = [int(x) for x in a.tops.split(',')]
    text = a.text
    rows = []
    print(f"{'size':>5} {'top':>5} | {'屏幕y区间':>13} | {'字可见%':>7} {'被遮%':>6} | {'头遮占字%':>9} {'头遮占遮挡%':>11}")
    for size in sizes:
        total_w = len(text) * size + (len(text) - 1) * a.gap
        canvas = Image.new('L', (int(total_w) + 8, int(size * 1.25) + 8), 0)
        d = ImageDraw.Draw(canvas)
        font = ImageFont.truetype(a.font, size)
        x = 4
        for ch in text:
            d.text((x, 4), ch, font=font, fill=255)
            x += size + a.gap
        cw, chh = canvas.size
        cm = np.array(canvas)
        for top in tops:
            cx0 = CX_CARD - cw / 2.0
            sx0, sy0 = card_to_screen(cx0, float(top))
            sx1, sy1 = card_to_screen(cx0 + cw, float(top) + chh)
            xs = np.arange(max(0, int(sx0)), min(W, int(sx1)))
            ys = np.arange(max(0, int(sy0)), min(H, int(sy1)))
            if len(xs) == 0 or len(ys) == 0:
                continue
            gx = np.clip(((xs - sx0) / S).astype(int), 0, cw - 1)
            gy = np.clip(((ys - sy0) / S).astype(int), 0, chh - 1)
            glyph = cm[np.ix_(gy, gx)] > 128
            tot = glyph.sum()
            if tot == 0:
                continue
            sub_p = person[np.ix_(ys, xs)]
            sub_h = head[np.ix_(ys, xs)]
            covered = (glyph & sub_p).sum()
            hcov = (glyph & sub_h).sum()
            vis = 100 * (1 - covered / tot)
            hpt = 100 * hcov / tot
            hp = 100 * hcov / covered if covered else 0
            rows.append((size, top, vis, hpt, hp, sy0, sy1))
            print(f"{size:>5} {top:>5} | {sy0:6.0f}~{sy1:6.0f} | {vis:>6.1f}% {100-covered/tot*100:>5.1f}% | {hpt:>8.1f}% {hp:>10.1f}%")

    cand = [r for r in rows if 60 <= r[2] <= 80]
    if cand:
        best = max(cand, key=lambda r: r[4])
        print(f"\n★ 推荐 size={best[0]} top={best[1]}：可见 {best[2]:.1f}%，"
              f"遮挡中 {best[4]:.1f}% 来自头部（屏幕 y {best[5]:.0f}~{best[6]:.0f}）")
        print(f"  → 写 config: \"hero_label_size\": {best[0]}, \"hero_label_top\": {best[1]}")
    else:
        print("\n⚠️ 无 60~80% 可见率的组合，放宽 sizes/tops 重扫")


if __name__ == '__main__':
    main()
