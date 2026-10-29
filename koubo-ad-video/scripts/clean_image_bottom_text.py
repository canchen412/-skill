#!/usr/bin/env python3
"""清掉生成图底部的 AI 水印与乱码文字（2026-10-29 米糊返工时新建）。

主用「手工框」：`--box x0,y0,x1,y1`（0~1 相对坐标，可多次传），因为 AI 乱码位置
每次都不一样，自动扫容易误伤台面/食物。
填充方式＝**水平插值**（每行取框左右邻域色线性过渡）＋上下 feather 羽化：
实测对平坦台面/渐变背景几乎无痕；垂直插值在「桶底压着台面」这种场景会出现
横向条纹，已降级为 `_vfill` 兜底。

右下角固定水印框（0.885,0.900,1.0,1.0）默认清，`--no-wm` 关掉。
`--auto` 才启用「扫 y>0.78h 低饱和高亮块」的自动检测（`find_text_blocks`），
默认关——未经逐张验收前不建议开。

用法:
  python3 clean_image_bottom_text.py <img1> [img2 ...] --outdir shots_v4/final \
      [--box 0.395,0.905,0.570,0.978] [--auto] [--no-wm] [--dry]
"""
import sys, os
from PIL import Image
import random

def load(p):
    im = Image.open(p).convert('RGB')
    return im

def find_text_blocks(im, y_start=0.78, blk=8, hit_ratio=0.10):
    """返回相对坐标框列表（8x8 网格合并成行块）"""
    w, h = im.size
    W = 384
    sc = W / w
    small = im.resize((W, max(1, int(h * sc))), Image.BILINEAR)
    sw, sh = small.size
    px = small.load()
    ys = int(sh * y_start)
    rows = {}
    for gy in range(ys, sh, blk):
        cnt = 0
        tot = 0
        for gx in range(0, sw, blk):
            hc = 0
            for y in range(gy, min(gy + blk, sh)):
                for x in range(gx, min(gx + blk, sw)):
                    r, g, b = px[x, y]
                    mx, mn = max(r, g, b), min(r, g, b)
                    if mn > 120 and (mx - mn) < 42:
                        hc += 1
            if hc >= blk * blk * 0.35:
                cnt += 1
            tot += 1
        if tot and cnt > 0:
            rows[gy] = cnt
    # 合并相邻行
    blocks = []
    keys = sorted(rows)
    i = 0
    while i < len(keys):
        y0 = keys[i]
        y1 = y0 + blk
        j = i + 1
        while j < len(keys) and keys[j] <= y1:
            y1 = keys[j] + blk
            j += 1
        blocks.append((y0 / sh, min(1.0, y1 / sh)))
        i = j
    return blocks

def fill_region(im, box, feather=6):
    """水平插值填充：每行取区域左右邻域色均匀过渡（适合平坦台面/渐变背景），
    上下各 feather px 羽化。"""
    w, h = im.size
    x0, y0, x1, y1 = [int(round(v * s)) for v, s in zip(box, (w, h, w, h))]
    x0, x1 = max(0, x0), min(w, x1)
    y0, y1 = max(0, y0), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return 0
    px = im.load()
    gap = 10
    lx = max(0, x0 - gap)
    rx = min(w - 1, x1 + gap)
    n = 0
    span = x1 - x0 + 1
    for x in range(x0, x1):
        t = (x - x0) / span
        for y in range(y0, y1):
            cl = px[lx, y]
            cr = px[rx, y]
            j = random.randint(-1, 1)
            px[x, y] = (max(0, min(255, int(cl[0] + (cr[0] - cl[0]) * t) + j)),
                        max(0, min(255, int(cl[1] + (cr[1] - cl[1]) * t) + j)),
                        max(0, min(255, int(cl[2] + (cr[2] - cl[2]) * t) + j)))
            n += 1
    # 上下边缘羽化（避免硬边）
    base = im.copy()
    pxb = base.load()
    for k in range(feather):
        a = 0.35 + 0.65 * k / feather
        for y in (y0 + k, y1 - 1 - k):
            if y < 0 or y >= h:
                continue
            ref = max(0, y0 - feather - 1) if y < (y0 + y1) // 2 else min(h - 1, y1 + feather)
            for x in range(x0, x1):
                c1 = pxb[x, ref]
                c2 = px[x, y]
                px[x, y] = tuple(int(c2[i] * a + c1[i] * (1 - a)) for i in range(3))
    return n


def _vfill(im, box):
    """兜底：垂直线性插值"""
    w, h = im.size
    x0, y0, x1, y1 = box
    px = im.load()
    pad = 5
    top_y = max(0, y0 - pad)
    bot_y = min(h - 1, y1 + pad)
    n = 0
    for x in range(x0, x1):
        c_top = px[x, top_y]
        c_bot = px[x, bot_y]
        span = bot_y - top_y
        for y in range(y0, y1):
            t = (y - top_y) / span if span else 0.5
            r = c_top[0] + (c_bot[0] - c_top[0]) * t
            g = c_top[1] + (c_bot[1] - c_top[1]) * t
            b = c_top[2] + (c_bot[2] - c_top[2]) * t
            j = random.randint(-2, 2)
            px[x, y] = (max(0, min(255, int(r) + j)),
                        max(0, min(255, int(g) + j)),
                        max(0, min(255, int(b) + j)))
            n += 1
    return n

def main():
    args = sys.argv[1:]
    outdir = None
    boxes = []
    dry = '--dry' in args
    files = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == '--outdir':
            outdir = args[i + 1]; i += 2; continue
        if a == '--box':
            boxes.append(tuple(float(v) for v in args[i + 1].split(','))); i += 2; continue
        if not a.startswith('--'):
            files.append(a)
        i += 1
    if outdir and not dry:
        os.makedirs(outdir, exist_ok=True)
    for f in files:
        im = load(f)
        w, h = im.size
        auto = []
        # 右下角水印固定框（AI生成/WORKBUDDY 两行）
        if '--no-wm' not in args:
            auto.append((0.885, 0.900, 1.0, 1.0))
        # 可选：自动扫底部高亮文字块（默认关，误伤风险见文件头）
        if '--auto' in args:
            for (ya, yb) in find_text_blocks(im):
                auto.append((0.0, max(0.0, ya - 0.006), 1.0, min(1.0, yb + 0.006)))
            if not auto[1:]:
                print(f"{os.path.basename(f)} 自动扫描未命中文字块")
        allb = auto + boxes
        # 去重合并（简单：按 y 排序，重叠则并）
        allb = sorted(set(allb), key=lambda b: b[1])
        merged = []
        for b in allb:
            if merged and b[1] <= merged[-1][3] and b[0] <= merged[-1][2] and b[2] >= merged[-1][0]:
                m = merged[-1]
                merged[-1] = (min(m[0], b[0]), min(m[1], b[1]), max(m[2], b[2]), max(m[3], b[3]))
            else:
                merged.append(b)
        print(f"{os.path.basename(f)} {w}x{h} 清理区: " +
              " | ".join(f"y{b[1]*100:.0f}-{b[3]*100:.0f}%" for b in merged))
        if dry:
            continue
        total = 0
        for b in merged:
            total += fill_region(im, b)
        out = os.path.join(outdir, os.path.basename(f))
        im.save(out)
        print(f"   ✅ {out}  填充 {total} px")
    return 0

sys.exit(main())
