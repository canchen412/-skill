# -*- coding: utf-8 -*-
"""
小绿人素材干净抠图（去背景 / 无白边 / 输出透明 RGBA）。

思路：绿主体色做种子 -> GrabCut 精修 -> 连通域清理 -> 边缘颜色内推(defringe) -> 羽化。
用法: python cutout.py <输入图> <输出png> [--feather 1.2] [--grow 6]
"""
import sys, os
import cv2
import numpy as np


def green_seed(bgr):
    b, g, r = bgr[:, :, 0].astype(np.int16), bgr[:, :, 1].astype(np.int16), bgr[:, :, 2].astype(np.int16)
    mx = np.maximum(r, b)
    m = ((g - mx) > 15) & (g > 40)
    return m.astype(np.uint8) * 255


def biggest_component(mask, keep_ratio=0.02):
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    if n <= 1:
        return mask
    areas = stats[1:, cv2.CC_STAT_AREA]
    order = np.argsort(-areas)
    keep = np.zeros_like(mask)
    for i in order:
        if areas[i] < areas[order[0]] * keep_ratio:
            break
        keep[lab == i + 1] = 255
    return keep


def grabcut(bgr, fg_seed, grow=6, iters=5):
    h, w = bgr.shape[:2]
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * grow + 1, 2 * grow + 1))
    fg = cv2.dilate(fg_seed, k, iterations=1)
    dist_bg = cv2.distanceTransform(255 - fg_seed, cv2.DIST_L2, 5)
    bg = (dist_bg > 45).astype(np.uint8) * 255
    mask = np.full((h, w), cv2.GC_PR_BGD, np.uint8)
    mask[bg > 0] = cv2.GC_BGD
    mask[fg_seed > 0] = cv2.GC_FGD
    mask[(fg > 0) & (fg_seed == 0)] = cv2.GC_PR_FGD
    bgd = np.zeros((1, 65), np.float64)
    fgd = np.zeros((1, 65), np.float64)
    cv2.grabCut(bgr, mask, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    out = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    return out


def defringe(bgr, alpha):
    """把半透明/边缘像素的颜色换成由内部实心色向外扩散的颜色，消除背景色污染（白边/灰边）。"""
    a = alpha.astype(np.float32) / 255.0
    rgb = bgr.astype(np.float32) * (a >= 0.99)[:, :, None]
    w = (a >= 0.99).astype(np.float32)
    solid = (a >= 0.99)
    col = bgr.astype(np.float32).copy()
    for _ in range(8):
        den = cv2.blur(w, (3, 3))
        avg = cv2.blur(rgb, (3, 3)) / np.maximum(den, 1e-6)[:, :, None]
        edge = (~solid) & (a > 0.02) & (den > 1e-6)
        if not edge.any():
            break
        col[edge] = avg[edge]
        rgb[edge] = avg[edge]
        w[edge] = 1.0
        solid = solid | edge
    return np.clip(col, 0, 255).astype(np.uint8)


def absorb(bgr, fg, iters=60, thresh=0.30):
    """把与主体相连的暗色（黑短裤、靴子、描边）顺着连通暗区吸收回前景。"""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    dark = (gray < 88).astype(np.uint8)
    fg = fg.copy()
    k = np.ones((5, 5), np.float32) / 25.0
    for _ in range(iters):
        frac = cv2.filter2D((fg > 0).astype(np.float32), -1, k)
        add = (dark > 0) & (fg == 0) & (frac > thresh)
        if not add.any():
            break
        fg[add] = 255
    return fg


def main():
    src, dst = sys.argv[1], sys.argv[2]
    feather = float(sys.argv[sys.argv.index("--feather") + 1]) if "--feather" in sys.argv else 1.2
    grow = int(sys.argv[sys.argv.index("--grow") + 1]) if "--grow" in sys.argv else 6
    bgr = cv2.imread(src, cv2.IMREAD_COLOR)
    h, w = bgr.shape[:2]

    seed = green_seed(bgr)
    seed = biggest_component(seed)
    seed = cv2.morphologyEx(seed, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    print(f"绿种子占比 {100*(seed>0).mean():.1f}%")

    fg = grabcut(bgr, seed, grow=grow)
    fg = biggest_component(fg, keep_ratio=0.05)
    # 补齐：顺着连通暗区吸收黑短裤/靴子/描边，并剔除暖色背景残留
    n_before = int((fg > 0).sum())
    fg = absorb(bgr, fg)
    print(f"暗区吸收补回 {100*(int((fg>0).sum())-n_before)/max(n_before,1):.1f}%")
    # 填充被轮廓包住的内部孔洞
    ff = fg.copy()
    hmask = np.zeros((fg.shape[0] + 2, fg.shape[1] + 2), np.uint8)
    cv2.floodFill(ff, hmask, (0, 0), 255)
    fg = cv2.bitwise_or(fg, cv2.bitwise_not(ff))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    fg = biggest_component(fg, keep_ratio=0.20)
    # 最后剔除「大面积」暖色背景块（先开运算，保住人物自带的细橙描边，只删成块的背景光晕）
    b_, g_, r_ = bgr[:, :, 0].astype(np.int16), bgr[:, :, 1].astype(np.int16), bgr[:, :, 2].astype(np.int16)
    warm = (((r_ - g_) > 26) & ((r_ - b_) > 26)).astype(np.uint8)
    warm = cv2.morphologyEx(warm, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))   # 把网点阵连成块
    kbig = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
    warm_big = cv2.dilate(cv2.morphologyEx(warm, cv2.MORPH_OPEN, kbig), np.ones((3, 3), np.uint8))
    n_rm = int(((fg > 0) & (warm_big > 0)).sum())
    print(f"剔除大面积暖色块 {n_rm} px")
    fg[warm_big > 0] = 0
    fg = biggest_component(fg, keep_ratio=0.20)
    # 剔除「与图像边界连通的亮色块」（背景白雾/光晕）——人物内部的白护腕是独立块，不会被误删
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    S, V = hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    pale = ((S < 62) & (V > 158)).astype(np.uint8)
    n_lab, lab = cv2.connectedComponents(pale, 4)
    if n_lab > 1:
        border = set(np.unique(np.concatenate([lab[0, :], lab[-1, :], lab[:, 0], lab[:, -1]])))
        border.discard(0)
        if border:
            bg_pale = np.isin(lab, list(border)).astype(np.uint8)
            n_rm2 = int(((fg > 0) & (bg_pale > 0)).sum())
            print(f"剔除连通边界的亮色背景 {n_rm2} px")
            fg[bg_pale > 0] = 0
    fg = biggest_component(fg, keep_ratio=0.20)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    a = fg.astype(np.float32) / 255.0
    a = cv2.GaussianBlur(a, (0, 0), feather)
    a = np.clip((a - 0.18) / 0.62, 0, 1)
    alpha = (a * 255).astype(np.uint8)

    rgb_fixed = defringe(bgr, alpha)
    out = np.dstack([rgb_fixed, alpha])
    cv2.imwrite(dst, out)

    ys, xs = np.where(alpha > 128)
    print(f"bbox x {xs.min()}..{xs.max()}  y {ys.min()}..{ys.max()}  尺寸 {w}x{h}  占比 {100*(alpha>128).mean():.1f}%")

    # 预览：放到三种底色上检查边缘
    for name, bgc in (("white", (255, 255, 255)), ("blue", (216, 232, 245)), ("dark", (30, 34, 40))):
        canvas = np.zeros((h, w, 3), np.uint8)
        canvas[:, :] = bgc
        al = (alpha.astype(np.float32) / 255)[:, :, None]
        canvas = (rgb_fixed * al + canvas * (1 - al)).astype(np.uint8)
        cv2.imwrite(dst.replace(".png", f"_on{name}.png"), canvas)


if __name__ == "__main__":
    main()
