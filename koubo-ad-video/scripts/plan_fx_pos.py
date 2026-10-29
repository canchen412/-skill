#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
plan_fx_pos.py —— 重点字/数字动效的「避让主体」自动落位（2026-10-28）
════════════════════════════════════════════════════════════════
灿爹原话：「重点字不要挡住人物的主体」「remotion 动效也不要挡住人物的主体」。

做法：
  ① Vision 显著性取该镜主体 bbox（复用 plan_behind_pos.person_bbox）
  ② bbox 外扩 PAD 作为禁放区
  ③ 网格搜索字块中心：块必须完整落在安全框内（左右 60 / 上 140 / 下 820）
  ④ 打分 = 块内 Sobel 边缘密度（低=平=不压细节）＋ 离主体越近越好（有关联）
  ⑤ 回写 decl 的 x/y

用法：
  plan_fx_pos.py --decl copy.fx.json --shots v1.shot_map_clean.json [--draw out/sheet.jpg]
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

W, H = 1920, 1080
PAD = 34          # bbox 外扩（禁放区）
MARGIN_X = 60
TOP = 140
BOTTOM = 820      # 字幕带上沿留白
CN, EN = 1.0, 0.58


def word_box(text, fs):
    """按字数估块宽高（技能定式：Σ(中文1.0/数字字母0.58)×字号×1.16）"""
    w = sum(CN if ord(c) > 127 else EN for c in text) * fs * 1.16
    return w, fs * 1.15


def count_box(fontSize, barWidth, num_text, label):
    w = max(barWidth * 1.5, len(num_text) * fontSize * 0.58 * 1.7)
    lab_w = sum(CN if ord(c) > 127 else EN for c in label) * fontSize * 0.37 * 1.1
    return max(w, lab_w), fontSize * 1.15 + fontSize * 0.5


def edge_map(path):
    im = Image.open(path).convert("L").resize((W, H), Image.LANCZOS)
    a = np.asarray(im, dtype=np.float32) / 255.0
    gx = np.abs(np.diff(a, axis=1, prepend=a[:, :1]))
    gy = np.abs(np.diff(a, axis=0, prepend=a[:1, :]))
    g = gx + gy
    # 积分图加速块均值
    return g, np.pad(g, ((1, 0), (1, 0))).cumsum(0).cumsum(1)


def box_mean(ii, x0, y0, x1, y1):
    x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
    s = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    return s / max(1, (x1 - x0) * (y1 - y0))


def solve(img_path, bw, bh):
    from plan_behind_pos import person_bbox
    im = Image.open(img_path).convert("RGB").resize((W, H), Image.LANCZOS)
    tmp = "/tmp/_pfx_frame.png"
    im.save(tmp)
    bb = person_bbox(tmp)
    _, ii = edge_map(img_path)
    if bb:
        bx0, by0 = bb["x0"] - PAD, bb["y0"] - PAD
        bx1, by1 = bb["x1"] + PAD, bb["y1"] + PAD
    else:
        bx0 = by0 = bx1 = by1 = 0
    hw, hh = bw / 2, bh / 2
    best = None
    for cy in range(int(TOP + hh), int(BOTTOM - hh) + 1, 24):
        for cx in range(int(MARGIN_X + hw), int(W - MARGIN_X - hw) + 1, 32):
            x0, y0, x1, y1 = cx - hw, cy - hh, cx + hw, cy + hh
            # 与主体禁放区相交 ⇒ 弃
            if bb and not (x1 < bx0 or x0 > bx1 or y1 < by0 or y0 > by1):
                continue
            dens = box_mean(ii, x0, y0, x1, y1)
            if bb:
                dx = max(0, max(bx0 - x1, x0 - bx1))
                dy = max(0, max(by0 - y1, y0 - by1))
                dist = (dx * dx + dy * dy) ** 0.5
            else:
                dist = 0
            # 密度主权重；离主体太远扣分（动效要贴着主体起说明作用）
            score = dens * 1000 + dist * 0.045
            if best is None or score < best[0]:
                best = (score, cx, cy, dens, dist)
    if best is None:
        return None, bb
    _, cx, cy, dens, dist = best
    return {"x": int(cx), "y": int(cy), "dens": round(float(dens), 4),
            "dist": int(dist), "bw": int(bw), "bh": int(bh)}, bb


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--decl", required=True)
    ap.add_argument("--fx", required=True, help="copy.fx.json（原始声明，含 phrase/字号）")
    ap.add_argument("--shots", required=True, help="shot_map_clean.json")
    ap.add_argument("--draw", default="")
    a = ap.parse_args()

    shots = json.load(open(a.shots, encoding="utf-8"))
    src = json.load(open(a.fx, encoding="utf-8"))
    decl = json.load(open(a.decl, encoding="utf-8"))

    jobs = []
    for i, w in enumerate(src.get("words", [])):
        fs = w.get("fontSize") or 120
        bw, bh = word_box(w["text"], fs)
        jobs.append(("words", i, w["text"], w.get("phrase"), bw, bh))
    for i, c in enumerate(src.get("counts", [])):
        bw, bh = count_box(c.get("fontSize") or 108, c.get("barWidth") or 420,
                           str(c["target"]), c.get("label", ""))
        jobs.append(("counts", i, c["text"], c.get("phrase"), bw, bh))

    draw = None
    if a.draw:
        draw = Image.new("RGB", (W, H * len(jobs)), (12, 12, 16))
        d = ImageDraw.Draw(draw)
        try:
            f = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Unicode.ttf", 30)
        except Exception:
            f = ImageFont.load_default()

    for k, (kind, i, text, phrase, bw, bh) in enumerate(jobs):
        img = shots[phrase - 1] if phrase and phrase <= len(shots) else shots[0]
        pos, bb = solve(img, bw, bh)
        if not pos:
            print(f"  ⚠️ 「{text}」镜{phrase} 找不到可放位置（块 {bw:.0f}x{bh:.0f} 太大）")
            continue
        decl[kind][i]["x"] = pos["x"]
        decl[kind][i]["y"] = pos["y"]
        print(f"  镜{phrase:02d} {kind:6s} 「{text}」→ ({pos['x']},{pos['y']}) "
              f"块{pos['bw']}x{pos['bh']} 平整度{pos['dens']:.4f} 离主体{pos['dist']}px"
              f"{'  ⚠️主体未识别' if not bb else ''}")
        if draw:
            th = Image.open(img).convert("RGB").resize((W, H), Image.LANCZOS)
            draw.paste(th, (0, k * H))
            d.rectangle([pos["x"] - bw / 2, k * H + pos["y"] - bh / 2,
                         pos["x"] + bw / 2, k * H + pos["y"] + bh / 2],
                        outline=(0, 255, 120), width=5)
            if bb:
                d.rectangle([bb["x0"], k * H + bb["y0"], bb["x1"], k * H + bb["y1"]],
                            outline=(255, 70, 70), width=4)
            d.text((22, k * H + 18), f"{text}  镜{phrase}", fill=(255, 220, 60), font=f)

    json.dump(decl, open(a.decl, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("已回写", a.decl)
    if draw:
        draw.resize((W // 2, H * len(jobs) // 2), Image.LANCZOS).save(a.draw, quality=88)
        print("验收图", a.draw)


if __name__ == "__main__":
    main()
