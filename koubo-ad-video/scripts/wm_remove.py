#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wm_remove.py — ImageGen 右下角「AI生成 WORKBUDDY」水印通用清除工具

核心原理
--------
水印是**固定模板的半透明白色叠加**：P = a*W + (1-a)*B
但它**按图像高度等比缩放**（实测：1536x1024 图上 136x68；1536x864 图上 0.844x = 114x57），
且始终**右下角锚定**。所以不能直接拿一个固定尺寸的模板去套。

流程
----
1. 标定：用一对「带水印图 / 同位置已去水印图」反解出 alpha 模板
   a = (P_watermarked - B_clean) / (255 - B_clean)
2. 定位：把模板按多尺度在目标图上做归一化互相关，取峰值
   （搜索前先去掉低频背景，只留文字笔画的高频，否则食物/纹理噪声会压过文字）
3. 修复：用缩放后的 alpha 阈值化 + 膨胀得到**精确文字笔画 mask**
   （用整块矩形 mask 会把周围干净内容一起抹平 —— 这是关键差异），
   再做 cv2.inpaint(TELEA)。
   水印下若是平滑背景（棚拍渐变），改用曲面拟合（--planefit）几乎零痕。

用法
----
python wm_remove.py --img 待处理.png \
    --ref-wm 带水印标定图.png --ref-clean 去水印标定图.png \
    --out 输出.png --th 0.12 --dilate 3 --radius 3

# 背景是平滑渐变（棚拍/纯色）时更优：
python wm_remove.py ... --planefit --ring 26
"""
import argparse
import numpy as np
import cv2


def calibrate(ref_wm, ref_clean):
    """反解 alpha 模板，自动定位水印 bbox。"""
    d = np.abs(ref_wm.astype(np.int16) - ref_clean.astype(np.int16)).max(axis=2)
    ys, xs = np.where(d > 0)
    if len(ys) == 0:
        raise SystemExit('❌ 标定失败：两张参考图完全一致，没有可标定的水印区')
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    S = ref_wm[y0:y1, x0:x1].astype(np.float64)
    T = ref_clean[y0:y1, x0:x1].astype(np.float64)
    a = np.clip((S - T) / np.clip(255.0 - T, 1, None), 0, 0.97).mean(axis=2)
    a = cv2.GaussianBlur(a, (3, 3), 0.6)
    a = a / max(a.max(), 1e-6)
    print('标定：水印框 x[%d-%d] y[%d-%d] 尺寸 %dx%d  alpha峰值 %.3f'
          % (x0, x1, y0, y1, x1 - x0, y1 - y0, a.max()))
    return a


def locate(img, tpl, search_frac=0.16, scales=np.arange(0.55, 1.45, 0.02)):
    """多尺度互相关定位水印（右下角区域搜索）。"""
    h, w = img.shape[:2]
    th, tw = tpl.shape
    sy, sx = int(h * search_frac), int(w * search_frac)
    y0, x0 = h - sy, w - sx
    g = cv2.cvtColor(img[y0:h, x0:w], cv2.COLOR_BGR2GRAY).astype(np.float32)
    g = g - cv2.GaussianBlur(g, (0, 0), 18)          # 去低频，只留笔画高频
    best = None
    for sc in scales:
        tw2, th2 = int(tw * sc), int(th * sc)
        if tw2 < 12 or th2 < 8 or tw2 >= sx or th2 >= sy:
            continue
        t = cv2.resize(tpl, (tw2, th2), interpolation=cv2.INTER_AREA).astype(np.float32) * 255
        r = cv2.matchTemplate(g, t, cv2.TM_CCOEFF_NORMED)
        _, mx, _, ml = cv2.minMaxLoc(r)
        if best is None or mx > best[0]:
            best = (mx, sc, (x0 + ml[0], y0 + ml[1]), tw2, th2)
    if best is None:
        raise SystemExit('❌ 定位失败：搜索窗口太小，调大 --search-frac')
    mx, sc, (bx, by), tw2, th2 = best
    print('定位：相关系数 %.3f  缩放 %.2f  水印框 x[%d-%d] y[%d-%d]'
          % (mx, sc, bx, bx + tw2, by, by + th2))
    return sc, (bx, by), (tw2, th2)


def fit_inpaint(img, alpha_t, box, th, dilate, radius):
    """精确文字 mask + inpaint。"""
    h, w = img.shape[:2]
    bx, by = box
    th2, tw2 = alpha_t.shape
    m = np.zeros((h, w), np.uint8)
    sub = (alpha_t > th).astype(np.uint8) * 255
    if dilate > 0:
        sub = cv2.dilate(sub, np.ones((dilate * 2 + 1,) * 2, np.uint8))
    m[by:by + th2, bx:bx + tw2] = sub
    cov = sub.mean() / 255 * 100
    out = cv2.inpaint(img, m, radius, cv2.INPAINT_TELEA)
    print('修复：mask 覆盖水印框 %.1f%%  inpaint radius=%d' % (cov, radius))
    return out


def planefit(img, alpha_t, box, th, ring, seed=0):
    """水印区落在平滑渐变背景上时：用上下/左右环带拟合平面填充，几乎零痕。"""
    h, w = img.shape[:2]
    bx, by = box
    th2, tw2 = alpha_t.shape
    sub = (alpha_t > th).astype(np.uint8)
    ys, xs = np.where(sub > 0)
    if len(ys) == 0:
        return img
    y0, y1 = by + ys.min(), by + ys.max() + 1
    x0, x1 = bx + xs.min(), bx + xs.max() + 1

    def plane(region, ox, oy):
        hh, ww = region.shape[:2]
        yy, xx = np.mgrid[0:hh, 0:ww]
        A = np.stack([(xx + ox).ravel(), (yy + oy).ravel(), np.ones(hh * ww)], axis=1).astype(np.float64)
        return np.array([np.linalg.lstsq(A, region[:, :, c].ravel().astype(np.float64), rcond=None)[0]
                         for c in range(3)])

    rt = img[max(0, y0 - ring):y0, x0:x1]
    rl = img[y0:y1, max(0, x0 - ring):x0]
    coefs = []
    if rt.size:
        coefs.append((plane(rt, x0, y0 - rt.shape[0]), 'top'))
    if rl.size:
        coefs.append((plane(rl, x0 - rl.shape[1], y0), 'left'))
    if not coefs:
        raise SystemExit('❌ 曲面拟合失败：水印区紧贴两条边界，改用 inpaint 模式')
    yy, xx = np.mgrid[y0:y1, x0:x1]
    A = np.stack([xx.ravel(), yy.ravel(), np.ones(xx.size)], axis=1).astype(np.float64)
    num = np.zeros((y1 - y0, x1 - x0, 3))
    den = np.zeros((y1 - y0, x1 - x0, 1))
    for coef, kind in coefs:
        p = (A @ coef.T).reshape(y1 - y0, x1 - x0, 3)
        if kind == 'top':
            wgt = np.clip(1 - (yy - y0) / max(y1 - y0, 1), 0, 1)[..., None]
        else:
            wgt = np.clip(1 - (xx - x0) / max(x1 - x0, 1), 0, 1)[..., None]
        num += p * wgt
        den += wgt
    out = img.copy()
    out[y0:y1, x0:x1] = np.clip(num / np.maximum(den, 1e-9), 0, 255).astype(np.uint8)
    print('修复：曲面拟合 环带=%d  区域 x[%d-%d] y[%d-%d]' % (ring, x0, x1, y0, y1))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--img', required=True, help='待处理图')
    ap.add_argument('--ref-wm', required=True, help='带水印的标定图')
    ap.add_argument('--ref-clean', required=True, help='同图去水印后的干净版')
    ap.add_argument('--out', required=True)
    ap.add_argument('--th', type=float, default=0.12, help='alpha 阈值，越大 mask 越紧')
    ap.add_argument('--dilate', type=int, default=3, help='mask 膨胀半径(px)，兜住 1-2px 定位误差')
    ap.add_argument('--radius', type=int, default=3, help='inpaint 半径')
    ap.add_argument('--planefit', action='store_true', help='改用曲面拟合（平滑渐变背景）')
    ap.add_argument('--ring', type=int, default=26, help='曲面拟合环带宽度')
    ap.add_argument('--search-frac', type=float, default=0.16)
    a = ap.parse_args()

    img = cv2.imread(a.img)
    ref_wm = cv2.imread(a.ref_wm)
    ref_clean = cv2.imread(a.ref_clean)
    if img is None or ref_wm is None or ref_clean is None:
        raise SystemExit('❌ 读图失败，检查路径')
    tpl = calibrate(ref_wm, ref_clean)
    sc, box, (tw2, th2) = locate(img, tpl, a.search_frac)
    at = cv2.resize(tpl, (tw2, th2), interpolation=cv2.INTER_AREA)
    if a.planefit:
        out = planefit(img, at, box, a.th, a.ring)
    else:
        out = fit_inpaint(img, at, box, a.th, a.dilate, a.radius)
    cv2.imwrite(a.out, out)
    d = np.abs(img.astype(np.int16) - out.astype(np.int16)).max(axis=2)
    ys, xs = np.where(d > 0)
    if len(ys):
        print('改动像素 %d (占 %.3f%%)  bbox x[%d-%d] y[%d-%d]'
              % (d.astype(bool).sum(), 100 * (d > 0).mean(), xs.min(), xs.max(), ys.min(), ys.max()))
    print('✅ 已写出', a.out)


if __name__ == '__main__':
    main()
