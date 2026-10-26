# -*- coding: utf-8 -*-
"""给分镜图打「背景干净度」分，判断哪些适合抠图分层。

思路：抠图后要用扩散填补背景，背景越平（色彩方差小、边缘少）填补越自然。
指标：
  1. 四边框区域的色彩标准差（越低越平）
  2. 全图高频边缘密度（越低越简洁）
  3. 中心区域与边框的色差（人物突出、背景单纯时差值大）
综合分 0-100，>=60 建议抠图。
"""
import sys, os, glob
import numpy as np
from PIL import Image


def score(path):
    im = Image.open(path).convert("RGB").resize((480, 270))
    a = np.array(im).astype(float)
    # 边框带（上下各 18%、左右各 12%）
    mask = np.zeros((270, 480), bool)
    mask[:48, :] = mask[-48:, :] = True
    mask[:, :58] = mask[:, -58:] = True
    border = a[mask]
    bstd = border.std(axis=0).mean()                     # 边框色彩标准差
    # 边缘密度（Sobel 近似）
    g = np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean()
    # 中心 vs 边框色差
    cen = a[90:190, 160:320].reshape(-1, 3).mean(0)
    bmean = border.mean(0)
    contrast = np.abs(cen - bmean).mean()
    s_bstd = max(0.0, 100 - bstd * 2.2)                  # std 45+ -> 0
    s_edge = max(0.0, 100 - g * 1.6)                     # 边缘均值 60+ -> 0
    s_con = min(100.0, contrast * 1.6)
    total = 0.40 * s_bstd + 0.35 * s_edge + 0.25 * s_con
    return total, bstd, g, contrast


def main(paths):
    rows = []
    for p in paths:
        if os.path.basename(p).startswith("._"):
            continue
        try:
            t, b, g, c = score(p)
        except Exception as e:
            print(f"跳过 {p}: {e}")
            continue
        rows.append((t, os.path.basename(p), b, g, c))
    rows.sort(reverse=True)
    print(f"{'综合分':>6} {'边框std':>7} {'边缘密度':>7} {'中心对比':>7}  文件")
    for t, n, b, g, c in rows:
        flag = "★建议抠图" if t >= 60 else ("可考虑" if t >= 50 else "")
        print(f"{t:6.1f} {b:7.1f} {g:7.1f} {c:7.1f}  {n}  {flag}")


if __name__ == "__main__":
    args = sys.argv[1:]
    files = []
    for a in args:
        if os.path.isdir(a):
            files += glob.glob(os.path.join(a, "*.png")) + glob.glob(os.path.join(a, "*.jpg"))
        else:
            files.append(a)
    main(files)
