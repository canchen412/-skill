# -*- coding: utf-8 -*-
"""
把两个「小绿人」透明素材合成到带盖瓶产品图左右两侧，输出替换后的分镜图。

- 左右大体对称（两个人物中心关于画布中心镜像），但用的是两张不同素材（非镜像复制）
- 统一光照方向（左上主光）、色温、饱和度，人物脚下加与地面接触的软投影
输出: 1536x864 PNG
"""
import cv2
import numpy as np
from PIL import Image, ImageFilter

BASE = "/tmp/adwork/gen_bottle/bottle_final.png"
L_PNG = "/tmp/adwork/lastframe/cand_L1.png"     # 托手介绍（朝右，指向产品）
R_PNG = "/tmp/adwork/lastframe/cand_R1.png"     # 两桶组合装（翻转后朝左，竖拇指点赞）
OUT = "/tmp/adwork/lastframe/lastframe_v1.png"

# 画面基准：产品瓶 x 625..912（中心 768）y 60..845
GROUND = 838.0
CX = 768.0


def load_fig(path, target_h, flip=False):
    im = Image.open(path).convert("RGBA")
    a = np.array(im)[:, :, 3]
    ys, xs = np.where(a > 128)
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    im = im.crop(box)
    if flip:
        im = im.transpose(Image.FLIP_LEFT_RIGHT)
    s = target_h / im.height
    im = im.resize((max(1, int(round(im.width * s))), int(round(target_h))), Image.LANCZOS)
    return im


def color_unify(im_rgba, warm_shift=-5, cool_shift=7, sat=0.96, contrast=1.04, lift=0.0):
    """统一色温/饱和/对比：把素材的暖调往冷调拉，贴合浅蓝棚拍环境。"""
    arr = np.array(im_rgba).astype(np.float32)
    rgb, a = arr[:, :, :3], arr[:, :, 3:]
    rgb[:, :, 0] = rgb[:, :, 0] + warm_shift          # R 降
    rgb[:, :, 2] = np.minimum(255, rgb[:, :, 2] + cool_shift)   # B 升
    # 饱和度
    g = rgb.mean(axis=2, keepdims=True)
    rgb = g + (rgb - g) * sat
    # 对比（围绕 128）
    rgb = 128 + (rgb - 128) * contrast + lift
    arr[:, :, :3] = np.clip(rgb, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def directional_light(im_rgba, strength=0.07):
    """按左上主光给人物加一个极轻的明暗渐变（左亮右暗），与底座光向一致。"""
    arr = np.array(im_rgba).astype(np.float32)
    w = arr.shape[1]
    grad = np.linspace(1.0 + strength, 1.0 - strength, w, dtype=np.float32)[None, :, None]
    arr[:, :, :3] = np.clip(arr[:, :, :3] * grad, 0, 255)
    return Image.fromarray(arr.astype(np.uint8), "RGBA")


def fix_rim_hue(im_rgba, to_hue=90, hmin=22, hmax=52, ring=9, full=False):
    """把「黄色描边」统一成青绿色描边，与左侧素材/本片其他帧的冷调描边一致。
    full=True 时全图处理（不只外缘环）。"""
    arr = np.array(im_rgba)
    a = (arr[:, :, 3] > 128).astype(np.uint8)
    if full:
        region = a > 0
    else:
        inner = cv2.erode(a, np.ones((ring, ring), np.uint8))
        region = (a > 0) & (inner == 0)
    hsv = cv2.cvtColor(arr[:, :, :3], cv2.COLOR_RGB2HSV)
    H, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    m = region & (H >= hmin) & (H <= hmax) & (S > 75) & (V > 105)
    hsv[:, :, 0][m] = to_hue
    arr[:, :, :3] = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    return Image.fromarray(arr, "RGBA")


def soft_shadow(canvas, cx, ground_y, width, strength=0.30, core=0.26, off=(14, 6)):
    """在地面接触处画椭圆软投影（两层：外围大而淡 + 核心小而实），向右下偏移（左上主光）。"""
    H, W, _ = canvas.shape
    layer = np.zeros((H, W), np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    for (ax, ay, sof, s) in ((width * 0.42, width * 0.078, 28, strength),
                             (width * 0.25, width * 0.045, 12, core)):
        d = ((xx - (cx + off[0])) / ax) ** 2 + ((yy - (ground_y + off[1])) / ay) ** 2
        m = np.clip(1.0 - d, 0, 1) ** 0.65
        m = cv2.GaussianBlur(m, (0, 0), sof)
        layer = np.maximum(layer, m * s)
    sh = np.array([96, 112, 138], np.float32)      # 冷调地面阴影色
    a = layer[:, :, None]
    return canvas * (1 - a) + sh[None, None, :] * a


def main():
    canvas = np.array(Image.open(BASE).convert("RGB")).astype(np.float32)
    H, W = canvas.shape[:2]

    # 左：托手介绍（真·全身，脚踩地）；右：两桶组合装（翻转朝左，弓步近景，膝盖以下出画）
    figL = load_fig(L_PNG, 490, flip=False)
    figR = load_fig(R_PNG, 450, flip=True)
    figL = fix_rim_hue(directional_light(color_unify(figL, warm_shift=-4, cool_shift=7, sat=0.98, contrast=1.02, lift=6)),
                       to_hue=88, hmin=24, hmax=54)
    figR = fix_rim_hue(directional_light(color_unify(figR, warm_shift=-9, cool_shift=10, sat=0.92, contrast=1.04, lift=2)),
                       to_hue=88, hmin=18, hmax=44)

    posL = (int(CX - 500 - figL.width // 2), int(GROUND - figL.height + 14))
    posR = (int(CX + 500 - figR.width // 2), int(900 - figR.height))

    # 左人物脚在画内 -> 明确接地投影；右人物膝盖以下出画 -> 底缘一层柔和落地暗部
    canvas = soft_shadow(canvas, posL[0] + figL.width / 2, GROUND + 16, figL.width * 1.24,
                         strength=0.50, core=0.42, off=(20, 8))
    canvas = soft_shadow(canvas, posR[0] + figR.width / 2, 886, figR.width * 2.0,
                         strength=0.26, core=0.18, off=(14, 6))

    for fig, (px, py) in ((figL, posL), (figR, posR)):
        fa = np.array(fig).astype(np.float32)
        fh, fw = fa.shape[:2]
        x0, y0 = max(0, px), max(0, py)
        x1, y1 = min(W, px + fw), min(H, py + fh)
        sx0, sy0 = x0 - px, y0 - py
        sub = fa[sy0:sy0 + (y1 - y0), sx0:sx0 + (x1 - x0)]
        al = (sub[:, :, 3:4] / 255.0)
        canvas[y0:y1, x0:x1] = canvas[y0:y1, x0:x1] * (1 - al) + sub[:, :, :3] * al

    out = Image.fromarray(np.clip(canvas, 0, 255).astype(np.uint8), "RGB")
    out.save(OUT)
    print("saved", OUT, out.size, " L:", posL, figL.size, " R:", posR, figR.size)


if __name__ == "__main__":
    main()
