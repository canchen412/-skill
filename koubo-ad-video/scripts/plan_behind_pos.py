#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
plan_behind_pos.py —— behind 重点字的「自动落位」（2026-10-14）
═══════════════════════════════════════════════════════════════
为什么要它
────────
behind 字要「藏到人物后面、露出来一点点」，位置必须压在人物**轮廓边缘**上：
字一半在人物区（被遮），一半在背景（可见）。手写 x/y 完全靠猜 —— 而各版本
镜位不同、运镜还会把人物推走 —— 落偏了就变成「字缺一角」。

做法（在底片渲染之后、fx 层渲染之前跑）：
  从已渲染的底片 base.mp4 取该字出场时刻的帧 → Vision 显著性取人物区 bbox →
  选人物**外侧空间更大**的那一边，把字中心放在「边缘外 0.5 个字宽」处 ⇒
  字恰好跨过轮廓、右/左半身被人物吃掉。y 取人物竖向 30% 处（头肩高度，最出戏）。

用法
────
  plan_behind_pos.py --base <out_base.mp4> --spec <fx_spec_*.json> [--fps 30]
  原地回写 spec（kw[i].x / kw[i].y）。
"""
import argparse
import json
import os
import subprocess

import numpy as np

FF = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")


def _import_seg():
    from make_behind_mask import saliency_map, person_mask_from_map
    return saliency_map, person_mask_from_map


def person_bbox(frame_png, threshold=0.38, feather=0):
    saliency_map, person_mask_from_map = _import_seg()
    a = saliency_map(frame_png)
    m = np.asarray(person_mask_from_map(a, threshold=threshold, dilate=0, feather=feather))
    ys, xs = np.where(m > 128)
    if len(xs) < 500:                      # 显著性太弱：这一镜可能没有明确主体
        return None
    return {"x0": int(xs.min()), "x1": int(xs.max()),
            "y0": int(ys.min()), "y1": int(ys.max())}


def grab_frame(base, t, out_png):
    subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", base,
                    "-frames:v", "1", out_png], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--spec", required=True)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--tmp", default="/tmp/_pbp")
    a = ap.parse_args()

    os.makedirs(a.tmp, exist_ok=True)
    spec = json.load(open(a.spec, encoding="utf-8"))
    changed = 0
    for k in spec.get("kw", []):
        if not k.get("behind"):
            continue
        t = max(0.0, k["startSec"] + 0.55)
        png = os.path.join(a.tmp, f"behind_{k['text']}.png")
        grab_frame(a.base, t, png)
        bb = person_bbox(png)
        if not bb:
            print(f"  ⚠️ 「{k['text']}」该帧无明确主体，保持原坐标")
            continue
        fs = k.get("fontSize", 120)
        w_est = max(120, len(k["text"]) * fs * 0.92)
        h_est = fs * 1.15
        left_room, right_room = bb["x0"], 1920 - bb["x1"]
        side = k.get("personSide")
        if side not in ("left", "right"):
            side = "left" if left_room >= right_room else "right"
        if side == "left":
            cx = bb["x0"] - w_est * 0.48          # 字跨过人物左轮廓
        else:
            cx = bb["x1"] + w_est * 0.48
        cy = bb["y0"] + (bb["y1"] - bb["y0"]) * 0.30
        # 边界保护：字中心至少留出半个身位
        cx = float(np.clip(cx, w_est * 0.42, 1920 - w_est * 0.42))
        cy = float(np.clip(cy, h_est * 0.6, 1080 - h_est * 0.6))
        k["x"], k["y"] = round(cx), round(cy)
        k["_bbox"] = bb
        changed += 1
        print(f"  ⟩「{k['text']}」→ ({int(cx)},{int(cy)}) 人物bbox x{bb['x0']}-{bb['x1']} "
              f"y{bb['y0']}-{bb['y1']} side={side}")
    json.dump(spec, open(a.spec, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"plan_behind_pos ✓ 调整 {changed} 个 behind 字 → {a.spec}")


if __name__ == "__main__":
    main()
