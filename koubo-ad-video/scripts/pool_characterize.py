#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""不看图也能推断景别/构图：找「绿色小绿人」主体 → 高度占比、左右位置、是否双人。"""
import os, sys, json
import numpy as np, cv2
from PIL import Image

Image.MAX_IMAGE_PIXELS = None


def green_mask(small):
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    H, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    return (((H >= 30) & (H <= 95) & (S >= 55) & (V >= 35)) |
            ((H >= 25) & (H <= 100) & (S >= 110) & (V >= 40))).astype(np.uint8)


def analyze(path, W=900):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    small = np.array(im.resize((W, int(W * h / w)), Image.LANCZOS))
    sh, sw, _ = small.shape
    m = green_mask(small)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, lab, stats, cent = cv2.connectedComponentsWithStats(m, 8)
    comps = [(stats[i, cv2.CC_STAT_AREA], i) for i in range(1, n) if stats[i, cv2.CC_STAT_AREA] > 0.004 * sh * sw]
    comps.sort(reverse=True)
    info = {"file": os.path.basename(path), "ratio_wh": round(w / h, 3), "n_subjects": len(comps)}
    boxes = []
    for area, i in comps[:3]:
        x, y = stats[i, cv2.CC_STAT_LEFT], stats[i, cv2.CC_STAT_TOP]
        bw, bh = stats[i, cv2.CC_STAT_WIDTH], stats[i, cv2.CC_STAT_HEIGHT]
        boxes.append({"h_pct": round(bh / sh, 3), "w_pct": round(bw / sw, 3),
                      "cx": round((x + bw / 2) / sw, 3), "top": round(y / sh, 3),
                      "bottom": round((y + bh) / sh, 3), "area_pct": round(area / (sh * sw), 3)})
        b = boxes[-1]
        # 景别推断
        if b["h_pct"] >= 0.78:
            b["景别猜测"] = "全身"
        elif b["h_pct"] >= 0.50:
            b["景别猜测"] = "中景"
        elif b["h_pct"] >= 0.30:
            b["景别猜测"] = "近景"
        else:
            b["景别猜测"] = "特写"
        b["左右"] = "偏左" if b["cx"] < 0.42 else ("偏右" if b["cx"] > 0.58 else "居中")
        b["是否贴画底"] = str(b["bottom"] >= 0.95)
    info["subjects"] = boxes
    # 深色大块（黑板/夜景）占比
    gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
    info["暗部占比"] = round(float((gray < 60).mean()), 3)
    info["亮部占比"] = round(float((gray > 200).mean()), 3)
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    info["饱和度均值"] = round(float(hsv[:, :, 1].mean()), 1)
    return info


if __name__ == "__main__":
    rows = []
    for p in sys.argv[1:]:
        d = analyze(p)
        rows.append(d)
        s = d["subjects"][0] if d["subjects"] else {}
        print(f"{d['file']:32s} 主体数={d['n_subjects']} "
              f"高占比={s.get('h_pct','-'):<6} 底={s.get('bottom','-'):<6} "
              f"cx={s.get('cx','-'):<6} 景别={s.get('景别猜测','-'):<4} {s.get('左右','-')}  "
              f"暗={d['暗部占比']} 亮={d['亮部占比']} 饱和={d['饱和度均值']}")
    json.dump(rows, open("/tmp/adwork/ingest/subjects.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
