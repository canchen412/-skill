#!/usr/bin/env python3
"""右下角 AI 平台水印去除（相对框 + 亮灰文字掩膜 inpaint）。
用法: dewm.py <in> <out> [--apply]
不带 --apply 只报告掩膜占比（dry-run）。
"""
import sys, cv2, numpy as np

def main():
    src, dst = sys.argv[1], sys.argv[2]
    apply = "--apply" in sys.argv
    im = cv2.imread(src, cv2.IMREAD_COLOR)
    if im is None:
        print("读取失败", src); return 1
    h, w = im.shape[:2]
    # 右下角相对框（覆盖「AI生成 / WORKBUDD>」两行）
    x0, y0 = int(0.900 * w), int(0.915 * h)
    roi = im[y0:h, x0:w]
    b, g, r = roi[:, :, 0].astype(int), roi[:, :, 1].astype(int), roi[:, :, 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    mask = ((mn > 105) & ((mx - mn) < 30)).astype(np.uint8) * 255
    n = int((mask > 0).sum())
    print(f"{src}  {w}x{h}  框=({x0},{y0})-({w},{h})  掩膜像素 {n} ({n/(roi.shape[0]*roi.shape[1])*100:.2f}% of roi)")
    if not apply:
        print("  [dry-run] 加 --apply 执行"); return 0
    full = np.zeros((h, w), np.uint8)
    full[y0:h, x0:w] = mask
    full = cv2.dilate(full, np.ones((5, 5), np.uint8), iterations=2)
    out = cv2.inpaint(im, full, 4, cv2.INPAINT_TELEA)
    cv2.imwrite(dst, out)
    print(f"  ✅ 已写入 {dst}")
    return 0

sys.exit(main())
