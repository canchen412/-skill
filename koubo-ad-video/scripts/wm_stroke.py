#!/usr/bin/env python3
"""小云雀 AI 图片水印 —— 精确笔画掩膜去除（2026-09-29）

背景：candie_pool.py 的 clean_image_wm 用「整框填充」（flat-fill / exemplar / 整框 inpaint），
      在平滑渐变或带纹理的背景上会留下一块边界可见的色块（实测 6+ 张穿帮）。
      ⇒ 改为「水印模板 → 精确笔画掩膜 → inpaint」，只填笔画、保住周边内容。

用法：
  python wm_fix.py --src 原图.JPEG --tpl wm_alpha_tpl.npy --out 输出.jpg \
      [--th 0.08] [--dilate 3] [--radius 3] [--min-long 2400] [--no-enhance] [--verify]
"""
import cv2
import numpy as np
import argparse
import os

# 水印相对位置（含余量的搜索框）—— 与 _视频水印备忘.txt 一致
WM_RECT = (0.834, 0.884, 0.995, 0.981)


def build_mask(shape, tpl, th=0.08, dilate=3):
    """把标定模板缩放到目标图分辨率，二值化为笔画掩膜。"""
    H, W = shape[:2]
    x0, y0 = int(W * WM_RECT[0]), int(H * WM_RECT[1])
    x1, y1 = int(W * WM_RECT[2]), int(H * WM_RECT[3])
    t = cv2.resize(tpl, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
    m = (t > th).astype(np.uint8) * 255
    if dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1,) * 2)
        m = cv2.dilate(m, k)
    full = np.zeros((H, W), np.uint8)
    full[y0:y1, x0:x1] = m
    return full, (x0, y0, x1, y1)


def remove_wm(arr, tpl, th=0.08, dilate=3, radius=3, method=cv2.INPAINT_TELEA):
    mask, rect = build_mask(arr.shape, tpl, th, dilate)
    out = cv2.inpaint(arr, mask, radius, method)
    return out, mask, rect


def white_px(roi):
    a = roi.astype(np.int16)
    mn = a.min(axis=2)
    mx = a.max(axis=2)
    return int(((mn > 135) & ((mx - mn) < 52)).sum())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--tpl', default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                  'wm_tpl_xiaoyunque.npy'),
                    help='标定好的 alpha 模板 .npy（默认用同目录的小云雀模板）')
    ap.add_argument('--out', required=True)
    ap.add_argument('--th', type=float, default=0.08)
    ap.add_argument('--dilate', type=int, default=3)
    ap.add_argument('--radius', type=int, default=3)
    ap.add_argument('--method', choices=['telea', 'ns'], default='telea')
    ap.add_argument('--min-long', type=int, default=0, help='长边不足则 Lanczos4 超分到此值')
    ap.add_argument('--sharpen', type=float, default=0.0)
    ap.add_argument('--quality', type=int, default=95)
    ap.add_argument('--verify', action='store_true')
    ap.add_argument('--preview', help='另存 ROI 前后对比图')
    a = ap.parse_args()

    tpl = np.load(a.tpl)
    img = cv2.imread(a.src, cv2.IMREAD_COLOR)
    if img is None:
        raise SystemExit('读不到 %s' % a.src)
    h0, w0 = img.shape[:2]

    before = white_px(img[int(h0 * WM_RECT[1]):int(h0 * WM_RECT[3]),
                         int(w0 * WM_RECT[0]):int(w0 * WM_RECT[2])])

    m = cv2.INPAINT_NS if a.method == 'ns' else cv2.INPAINT_TELEA
    out, mask, (x0, y0, x1, y1) = remove_wm(img, tpl, a.th, a.dilate, a.radius, m)

    if a.min_long and max(out.shape[:2]) < a.min_long:
        f = a.min_long / max(out.shape[:2])
        out = cv2.resize(out, (int(round(out.shape[1] * f)), int(round(out.shape[0] * f))),
                         interpolation=cv2.INTER_LANCZOS4)
    if a.sharpen > 0:
        blur = cv2.GaussianBlur(out, (0, 0), 1.2)
        out = cv2.addWeighted(out, 1 + a.sharpen, blur, -a.sharpen, 0)

    ok, buf = cv2.imencode(os.path.splitext(a.out)[1] or '.jpg', out,
                           [int(cv2.IMWRITE_JPEG_QUALITY), a.quality])
    if not ok:
        raise SystemExit('编码失败')
    buf.tofile(a.out)

    h1, w1 = out.shape[:2]
    bx0, by0, bx1, by1 = int(w1 * WM_RECT[0]), int(h1 * WM_RECT[1]), int(w1 * WM_RECT[2]), int(h1 * WM_RECT[3])
    after = white_px(out[by0:by1, bx0:bx1])
    cov = mask.mean() / 255 * 100

    print('源 %dx%d → 出 %dx%d' % (w0, h0, w1, h1))
    print('  笔画掩膜覆盖全图 %.3f%%  水印区白像素 %d → %d' % (cov, before, after))
    if a.verify:
        verdict = '✅ 干净' if (after <= max(12, before * 0.05)) else '⚠️ 仍有残留'
        print('  残留自检: %s' % verdict)

    if a.preview:
        pa = img[y0:y1, x0:x1]
        pb = out[int(h1 * WM_RECT[1]):int(h1 * WM_RECT[3]), int(w1 * WM_RECT[0]):int(w1 * WM_RECT[2])]
        pb = cv2.resize(pb, (pa.shape[1], pa.shape[0]))
        gap = np.full((pa.shape[0], 8, 3), 255, np.uint8)
        cv2.imwrite(a.preview, np.hstack([pa, gap, pb]))
        print('  预览已存 %s（左=原图 右=修后）' % a.preview)


if __name__ == '__main__':
    main()
