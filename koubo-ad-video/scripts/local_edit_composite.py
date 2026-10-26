#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
局部编辑合成器：只把 i2i 生成图里的「短裤」贴回原图，其余像素 100% 保持原图。

用法:
  python make_shorts_composite.py --src 原图.png --gen 生成图.png --out 成品.png \
      [--x0 610 --x1 950 --y0 520 --y1 810] [--th 115] [--dilate 8] [--feather 10] \
      [--wm x0,y0,x1,y1]

原理:
  1. 短裤 = 生成图里的深色区（灰度 < th），限制在身体 x/y 窗口内
  2. 取最大连通域 + 孔洞填充（短裤受暖光反射的亮部/高光不至于破洞）
  3. 膨胀 dilate px（吃掉边缘抗锯齿）+ 高斯羽化 feather px 做 alpha 过渡
  4. out = src*(1-a) + gen*a；a==0 处逐像素等于原图（用 --verify 断言）
"""
import argparse, sys
import cv2, numpy as np


def build_mask(gen, x0, x1, y0, y1, th, dilate, feather, verbose=True,
               src_gray=None, mode='dark', smax=60, vmin=40, vmax=150):
    gen_gray = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY)
    if mode == 'dark':
        # 深色短裤：直接找生成图里的暗色区（会连背景一起抓，仅作兜底）
        m = (gen_gray < th).astype(np.uint8)
    elif mode == 'diff':
        # 两图强差异区（开运算去掉 1px 轮廓线，只留色块）
        assert src_gray is not None, 'diff 模式需要 src_gray'
        d = np.abs(src_gray.astype(np.int16) - gen_gray.astype(np.int16)).astype(np.uint8)
        m = (d > th).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)), iterations=4)
    else:
        # flat（推荐）：灰色/黑色布料三通道近乎相等（max-min 绝对差极小），
        # 而暖棕背景与皮肤的 max-min 都很大 ⇒ 用绝对色差比相对饱和度 S 稳得多
        # （深色背景下 S 是相对值会被放大，背景常被误判为"低饱和"）
        b = gen[:, :, 0].astype(np.int16); g_ = gen[:, :, 1].astype(np.int16)
        r = gen[:, :, 2].astype(np.int16)
        mx = np.maximum(np.maximum(b, g_), r)
        mn = np.minimum(np.minimum(b, g_), r)
        chord = mx - mn
        m = ((chord < smax) & (mx > vmin) & (mx < vmax)).astype(np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15)), iterations=3)
    keep = np.zeros_like(m)
    keep[y0:y1, x0:x1] = 1
    m &= keep

    # 取最大连通域
    n, lab, stats, _ = cv2.connectedComponentsWithStats(m, 8)
    if n <= 1:
        raise SystemExit('❌ 掩膜为空：mode=%s 阈值 %d 在该窗口内没定位到短裤，请调 --th 或窗口' % (mode, th))
    idx = 1 + int(np.argmax(stats[1:, 4]))
    m = (lab == idx).astype(np.uint8)

    # 孔洞填充：保证短裤内部的亮部（暖光反射）被包住
    ff = m.copy()
    h, w = ff.shape
    ff[0, :] = 0; ff[-1, :] = 0; ff[:, 0] = 0; ff[:, -1] = 0
    mask2 = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(ff, mask2, (0, 0), 1)
    holes = (mask2[1:-1, 1:-1] == 0) & (m == 0)
    m = (m | holes.astype(np.uint8)).astype(np.uint8)

    # 膨胀 + 羽化
    if dilate > 0:
        k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate * 2 + 1, dilate * 2 + 1))
        m = cv2.dilate(m, k, iterations=1)
    alpha = m.astype(np.float32)
    if feather > 0:
        alpha = cv2.GaussianBlur(alpha, (0, 0), feather / 2.0)
    alpha = np.clip(alpha, 0, 1)

    if verbose:
        ys, xs = np.where(m > 0)
        print(f'   掩膜：{m.sum():,} px  bbox x[{xs.min()}-{xs.max()}] y[{ys.min()}-{ys.max()}]')
        print(f'   羽化后 alpha>0.5 占比 {100*(alpha>0.5).mean():.2f}%  0<a<1 过渡带 {100*((alpha>0.02)&(alpha<0.98)).mean():.2f}%')
    return alpha


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--src', required=True)
    ap.add_argument('--gen', required=True)
    ap.add_argument('--out', required=True)
    ap.add_argument('--x0', type=int, default=600); ap.add_argument('--x1', type=int, default=960)
    ap.add_argument('--y0', type=int, default=520); ap.add_argument('--y1', type=int, default=810)
    ap.add_argument('--th', type=int, default=115, help='dark 模式=暗色灰度阈值; diff 模式=差异阈值')
    ap.add_argument('--mode', choices=['flat', 'dark', 'diff'], default='flat',
                    help='flat: 三通道近乎相等=灰/黑布料（推荐，深浅色通吃）; dark: 生成图暗区; diff: 两图差异色块')
    ap.add_argument('--smax', type=int, default=18, help='flat: 最大通道差上限（灰布料判据）')
    ap.add_argument('--vmin', type=int, default=40, help='flat: 最亮通道下限（排除暗背景）')
    ap.add_argument('--vmax', type=int, default=165, help='flat: 最亮通道上限（排除白背心/高光）')
    ap.add_argument('--dilate', type=int, default=8)
    ap.add_argument('--feather', type=int, default=10)
    ap.add_argument('--wm', default='', help='水印矩形 x0,y0,x1,y1，用原图同位置像素覆盖')
    ap.add_argument('--same-th', type=int, default=0, help='见 --same-th 说明')
    ap.add_argument('--verify', action='store_true')
    a = ap.parse_args()

    src = cv2.imread(a.src); gen = cv2.imread(a.gen)
    print(f'源图 {src.shape}  生成图 {gen.shape}')
    if gen.shape != src.shape:
        gen = cv2.resize(gen, (src.shape[1], src.shape[0]), interpolation=cv2.INTER_LANCZOS4)
        print('   已把生成图缩放到源图尺寸')

    g = cv2.cvtColor(gen, cv2.COLOR_BGR2GRAY)
    gs = cv2.cvtColor(src, cv2.COLOR_BGR2GRAY)
    alpha = build_mask(gen, a.x0, a.x1, a.y0, a.y1, a.th, a.dilate, a.feather,
                       src_gray=gs, mode=a.mode, smax=a.smax, vmin=a.vmin, vmax=a.vmax)

    # 精修：矩形掩膜会把四角背景也圈进来，但那些像素两图几乎一致
    # ⇒ 差异 ≤ same_th 的一律还原为原图，真正实现「除短裤外一个像素都没动」
    if a.same_th > 0:
        d0 = np.abs(src.astype(np.int16) - gen.astype(np.int16)).max(axis=2)
        keep = (d0 > a.same_th).astype(np.uint8)
        keep = cv2.morphologyEx(keep, cv2.MORPH_CLOSE,
                                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7)), iterations=2)
        before = float(alpha.sum())
        alpha = alpha * keep
        print(f'   精修：掩膜内 {a.same_th} 灰度内近乎一致的像素已还原为原图 '
              f'（占掩膜 {100*(before-alpha.sum())/max(before,1):.1f}%）')
        ys, xs = np.where(alpha > 0.5)
        if len(ys):
            print(f'   精修后核心区 bbox x[{xs.min()}-{xs.max()}] y[{ys.min()}-{ys.max()}]')

    al = alpha[..., None]

    out = (src.astype(np.float32) * (1 - al) + gen.astype(np.float32) * al).round().astype(np.uint8)

    # 水印：用源图同位置像素覆盖（编辑产物右下角必带 AI 水印）
    if a.wm:
        x0, y0, x1, y1 = [int(v) for v in a.wm.split(',')]
        out[y0:y1, x0:x1] = src[y0:y1, x0:x1]
        print(f'   水印区 x[{x0}-{x1}] y[{y0}-{y1}] 已用源图像素覆盖')

    if a.verify:
        untouched = alpha < 1e-6
        same = (out[untouched] == src[untouched]).all()
        d = np.abs(out.astype(np.int16) - src.astype(np.int16)).max(axis=2)
        print(f'   ✅ 蒙版外 {100*untouched.mean():.2f}% 像素与原图逐字节相同: {same}')
        print(f'   变化区平均差 {d[~untouched].mean():.1f} / 最大 {d.max()}')

    cv2.imwrite(a.out, out)
    print(f'✅ 已输出 {a.out}')


if __name__ == '__main__':
    main()
