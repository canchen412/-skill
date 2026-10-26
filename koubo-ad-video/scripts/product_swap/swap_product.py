#!/usr/bin/env python3
# HEIC 罐 → target.png 白瓶位置（完整替换，锐边版）
# 架构：罐体在 3× 超采样尺度上做光照/色彩/锐化/合成 → 一次性 LANCZOS 缩回 → 落回原图
import numpy as np, cv2
from PIL import Image, ImageFilter

TGT, SRC = "target.png", "heif3_raw.png"
A = np.array(Image.open(TGT).convert("RGB")).astype(np.float32)
IMG_H, IMG_W, _ = A.shape

OLD = (443, 261, 555, 573)      # 旧白瓶实测框
CENTER_X, BASE_Y = 500, 577     # 罐中心 x / 罐底 y（坐台面）

# ---------- 源罐：裁边 + 自适应腐蚀清羽化带 ----------
src = Image.open(SRC).convert("RGBA")
SA = np.array(src).astype(np.float32)
ys, xs = np.nonzero(SA[:, :, 3] > 250)
src = src.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
am = np.array(src)[:, :, 3]
solid0 = (am > 250).astype(np.uint8)
er = cv2.erode(solid0, np.ones((5, 5), np.uint8), iterations=3)
cap_h = int(er.shape[0] * 0.22)
cap_m = cv2.erode(solid0[:cap_h], np.ones((3, 3), np.uint8), iterations=1)
er[:cap_h] = cap_m                       # 顶盖弧顶只腐蚀 1 次，保住弧度
er = cv2.GaussianBlur((er * 255).astype(np.uint8), (0, 0), 1.0)
src.putalpha(Image.fromarray(er))
SH0, SW0 = src.size[1], src.size[0]
ASPECT = SW0 / SH0
print(f"源罐 {SW0}x{SH0}  宽高比 {ASPECT:.3f}  腐蚀后不透明 {int((er>0).sum())}px "
      f"({(er>0).sum()/max((solid0>0).sum(),1)*100:.1f}%)")

# ---------- 原瓶柱面实测（供亮度匹配） ----------
core = A[335:555, 446:552]
core_m, core_s = core.mean(), core.std()
print(f"原瓶柱面 均值 {core_m:.0f}  标准差 {core_s:.1f}")

SS = 3   # 超采样倍数


def compose(width, out_name):
    h = int(round(width / ASPECT))
    w = width
    left = int(round(CENTER_X - width / 2))
    top = int(round(BASE_Y - h))
    assert left <= OLD[0] - 4 and left + w >= OLD[2] + 4, "未横向覆盖旧瓶"
    assert top <= OLD[1] - 4, "未纵向覆盖旧瓶（旧瓶盖会露出）"

    SW, SH = w * SS, h * SS          # 超采样工作尺寸（SW/SH 不与 IMG_W/H 混淆）

    # ---------- 1. 取罐并放大 ----------
    jar = src.resize((SW, SH), Image.LANCZOS)
    J = np.array(jar).astype(np.float32)
    Ja = np.array(jar)[:, :, 3].astype(np.float32) / 255.0
    rgb = J[:, :, :3]
    g = rgb.mean(2)

    # ---------- 2. 亮度：均值 + 标准差双对齐到原瓶柱面 ----------
    sel = Ja > 0.5
    gmin, gmax = np.percentile(g[sel], 3), np.percentile(g[sel], 97)
    src_m, src_s = g[sel].mean(), max(g[sel].std(), 1e-3)
    tgt_g = core_m + (g - src_m) * (core_s / src_s)
    ratio = np.clip(tgt_g / np.maximum(g, 1e-3), 0.45, 1.80)
    out = rgb * ratio[:, :, None]

    # ---------- 3. 柱面光场（横向最亮偏右 0.62；纵向上亮下暗） ----------
    xn = (np.arange(SW) + 0.5) / SW
    lat = np.sin(np.pi * np.clip(xn, 0, 1) ** 0.80)
    lat /= lat.max()
    ver = np.linspace(1.0, 0.84, SH).reshape(-1, 1)
    L = lat.reshape(1, -1) * ver
    Ln = (L - L.min()) / (L.max() - L.min() + 1e-6)
    out *= (0.82 + 0.30 * Ln)[:, :, None]

    # 盖顶「暗部补偿」提亮（不加常量，保住明暗结构）
    capf = np.clip(1.0 - np.arange(SH) / (SH * 0.20), 0, 1).reshape(-1, 1)
    need = np.clip(168.0 - out.mean(2), 0, None) * capf
    out += need[:, :, None] * np.array([1.0, 0.86, 0.70], np.float32).reshape(1, 1, 3)

    # ---------- 4. 环境光：暖金染 + 右侧受光 + 左缘反弹 ----------
    warm_ramp = np.clip((xn - 0.35) / 0.65, 0, 1).reshape(1, SW, 1)
    out *= np.array([1.055, 0.985, 0.918], np.float32).reshape(1, 1, 3)
    out += warm_ramp * np.array([20.0, 11.0, 2.0], np.float32).reshape(1, 1, 3)
    leftf = np.clip(1.0 - xn / 0.16, 0, 1).reshape(1, SW, 1)
    out += leftf * np.array([13.0, 8.0, 2.0], np.float32).reshape(1, 1, 3)

    # ---------- 5. 锐化（超采样尺度上做，只锐罐体） ----------
    def usm(img, sigma, amt, only_darker=False):
        b = cv2.GaussianBlur(img, (0, 0), sigma)
        d = img - b
        if only_darker:
            # ★只保留「比邻域暗」的细节（USM 的暗侧），亮侧一律不补 ⇒ 不会出白边过冲
            d = np.minimum(d, 0.0)
        return np.clip(img + d * amt, 0, 255)
    out = usm(out, 2.6 * SS, 0.34, only_darker=True)          # 整体：只锐暗部
    band = (Ja > 0.02) & (Ja < 0.985)               # 半透明过渡带最易糊
    out[band] = usm(out, 1.2 * SS, 0.70, only_darker=True)[band]  # 边缘带补锐（暗侧）

    # ---------- 6. 接地处理 ----------
    foot = max(7, int(SH * 0.075))
    ramp_f = np.linspace(1.0, 0.28, foot).reshape(-1, 1, 1)
    out[SH - foot:, :, :] *= ramp_f                 # 底部暗脚
    nb = min(5, SH)
    tbl = np.array([36.0, 33.0, 27.0], np.float32).reshape(1, 1, 3)
    wmix = np.linspace(0.0, 0.55, nb).reshape(-1, 1, 1)
    out[SH - nb:, :, :] = out[SH - nb:, :, :] * (1 - wmix) + tbl * wmix

    # ---------- 6.5 动漫白描边（★最后画，不被接地暗脚/锐化吃掉） ----------
    # 原图实测人物轮廓线 2~3px（关节 4~5px）⇒ 罐体取 3px（比人物略粗，因罐体大）
    STROKE = int(2.5 * SS)
    solid_px = (Ja > 0.5).astype(np.uint8)
    # ① 闭运算补平轮廓的小内缩（源图左缘中段有 1~2px 凹口，直接膨胀会留下淡断）
    kc = np.ones((SS * 3 | 1, SS * 3 | 1), np.uint8)
    filled = cv2.morphologyEx(solid_px, cv2.MORPH_CLOSE, kc, iterations=2)
    ksz = STROKE | 1
    dil = cv2.dilate(filled, np.ones((ksz, ksz), np.uint8), iterations=1)
    stroke = (dil - filled).astype(np.float32)
    # 左右不对称：右侧（受光侧）收窄到 0.72 倍，左侧留全宽
    xw = np.ones(SW, np.float32)
    xn2 = np.arange(SW) / SW
    xw = np.where(xn2 > 0.72, 0.72 + (1 - 0.72) * (xn2 - 0.72) / 0.28 * 0.55, 1.0)
    stroke = stroke * xw.reshape(1, -1)
    stroke = cv2.GaussianBlur(stroke, (0, 0), SS * 0.6)  # 外缘 ~1px 羽化
    stroke = np.clip(stroke, 0, 1)
    # 描边色：不是纯白 —— 顶部偏亮白、底部随接地变暗（跟场景光走）
    sv = np.linspace(1.0, 0.58, SH).reshape(-1, 1, 1)
    scol = np.array([236.0, 232.0, 226.0], np.float32).reshape(1, 1, 3) * sv
    # 描边底部再压暗（贴台面那侧本就该暗）
    lowf = np.clip((np.arange(SH) - SH * 0.72) / (SH * 0.28), 0, 1).reshape(-1, 1, 1)
    scol *= (1.0 - lowf * 0.62)
    out = out * (1 - stroke[:, :, None]) + scol * stroke[:, :, None]

    # ---------- 7. alpha（0.6px 羽化，比原来 1.4px 锐一半） ----------
    am2 = np.array(Image.fromarray((Ja * 255).astype(np.uint8))
                   .filter(ImageFilter.GaussianBlur(0.6))).astype(np.float32) / 255.0
    am2 = np.clip(am2 + stroke, 0, 1)   # 描边也是实体，一起落画面
    am3 = am2[:, :, None]

    # ---------- 8. 阴影（全图 1×，光从右上 ⇒ 影向左下） ----------
    res = A.copy()
    shadow = np.zeros((IMG_H, IMG_W), np.float32)
    sw2 = int(width * 1.15)
    x0, x1 = max(0, int(CENTER_X - sw2 * 0.66)), min(IMG_W, int(CENTER_X - sw2 * 0.66) + sw2)
    y0, y1 = max(0, BASE_Y - 6), min(IMG_H, BASE_Y + int(h * 0.16))
    if x1 > x0 and y1 > y0:
        n = x1 - x0
        prof = np.ones(n, np.float32)
        a = max(1, int(n * 0.30)); prof[:a] = np.linspace(0.05, 1, a)
        b = max(1, int(n * 0.10)); prof[-b:] = np.linspace(1, 0.25, b)
        shadow[y0:y1, x0:x1] = np.linspace(1.0, 0.0, y1 - y0).reshape(-1, 1) * prof.reshape(1, -1)
    shadow = cv2.GaussianBlur(shadow, (0, 0), 9)

    contact = np.zeros((IMG_H, IMG_W), np.float32)
    cx0, cx1 = max(0, int(CENTER_X - width * 0.56)), min(IMG_W, int(CENTER_X + width * 0.56))
    cy0, cy1 = max(0, BASE_Y - 3), min(IMG_H, BASE_Y + int(h * 0.045))
    if cy1 > cy0 and cx1 > cx0:
        n = cx1 - cx0
        prof = np.ones(n, np.float32)
        a = max(1, int(n * 0.16)); prof[:a] = np.linspace(0, 1, a)
        b = max(1, int(n * 0.16)); prof[-b:] = np.linspace(1, 0, b)
        contact[cy0:cy1, cx0:cx1] = np.linspace(1.0, 0.0, cy1 - cy0).reshape(-1, 1) * prof.reshape(1, -1)
    contact = cv2.GaussianBlur(contact, (0, 0), 4)
    shadow = np.clip(shadow + contact * 0.75, 0, 1)
    for c in range(3):
        res[:, :, c] *= (1.0 - shadow * 0.70)

    # ---------- 9. 超采样合成 → 一次性缩回 ----------
    bg_ss = cv2.resize(res[top:top + h, left:left + w], (SW, SH), interpolation=cv2.INTER_LANCZOS4)
    comp = bg_ss * (1 - am3) + out * am3
    comp = np.array(Image.fromarray(np.clip(comp, 0, 255).astype(np.uint8))
                    .resize((w, h), Image.LANCZOS)).astype(np.float32)
    # 缩回后微锐（补高频），只作用在罐体覆盖区
    cb = cv2.GaussianBlur(comp, (0, 0), 0.85)
    cd = comp - cb
    comp = np.clip(comp + np.minimum(cd, 0.0) * 0.55, 0, 255)  # 只锐暗侧

    res[top:top + h, left:left + w] = comp

    # ---------- 10. 直出 PNG（★不经 JPEG，避免压缩瑕疵） ----------
    Image.fromarray(np.clip(res, 0, 255).astype(np.uint8)).save(out_name)
    print(f"  {out_name}: 框 x[{left},{left+w-1}] y[{top},{top+h-1}]  {w}x{h}")


print("\n=== 渲染（3× 超采样 + 锐边 + 3px 动漫白描边） ===")
compose(196, "罐_描边196.png")
compose(214, "罐_描边214.png")

o = Image.open(TGT).convert("RGB").crop((330, 150, 700, 660))
a = Image.open("罐_描边196.png").crop((330, 150, 700, 660))
b = Image.open("罐_描边214.png").crop((330, 150, 700, 660))
sh2 = Image.new("RGB", (o.width * 3 + 20, o.height), (20, 20, 20))
for i, im in enumerate([o, a, b]):
    sh2.paste(im, (i * (o.width + 10), 0))
sh2.save("对比_描边三联.png")
print("对比图: 对比_描边三联.png")
