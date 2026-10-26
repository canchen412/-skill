#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""小绿人 IP「补眼睛」工具（0 积分，纯本地像素操作）。

依据定妆图实测的 IP 眼睛比例，在 generated 分镜图上自动定位绿色头部并绘制
纯黑竖椭圆眼洞（带 1.5px 羽化边），还原 IP 标志性黑眼。

用法:
    python add_eyes.py <图1> [图2 ...] [-o 输出目录] [--apply]
默认 dry-run：只打印检测结果与坐标，不写文件；加 --apply 才落盘。
"""
import sys, os, json
from collections import deque
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

# —— 定妆图实测比例（相对头部绿色包围盒 718,106-802,234；眼心 732/773,174）——
EYE_CY = 0.527      # 眼睛中心在头部高度的位置
EYE_DX = 0.241      # 两眼各自偏离头部中心 = 0.241 * 头宽（间距 0.482 头宽）
EYE_RX = 0.147      # 眼椭圆半宽 / 头宽
EYE_RY = 0.132      # 眼椭圆半高 / 头高
EYE_DX = 0.241      # 两眼各偏离眼中点 = 0.241 * 头宽（间距 0.482 头宽）
# IP 是 3/4 侧脸：两眼中点比头心偏向"脸朝向"那一侧 9.4% 头宽（定妆图实测）
# 朝左 = 定妆图原姿态（眼中点 0.406）；朝右 = 镜像（0.594）；正面 = 0.5
EYE_SHIFT = {"left": -0.094, "right": +0.094, "front": 0.0}
EYE_COLOR = (11, 11, 11)


def load_rgb(path):
    im = Image.open(path).convert("RGBA")
    a = np.array(im.split()[-1]).astype(np.float32) / 255.0
    rgb = np.array(im.convert("RGB")).astype(np.float32)
    a3 = a[:, :, None]
    rgb = rgb * a3 + 255.0 * (1 - a3)      # 透明底按白底合成
    return rgb.astype(np.uint8), (a > 0.5)


def green_mask(rgb):
    R = rgb[:, :, 0].astype(np.int16)
    G = rgb[:, :, 1].astype(np.int16)
    B = rgb[:, :, 2].astype(np.int16)
    return (G > R + 20) & (G > B + 20) & (G > 60)


def all_ccs(mask, down=2, min_area=150):
    m = mask[::down, ::down]
    h, w = m.shape
    lab = np.zeros((h, w), np.int32)
    comps = []
    for y in range(h):
        for x in range(w):
            if m[y, x] and lab[y, x] == 0:
                q = deque([(y, x)])
                lab[y, x] = 1
                pix = []
                while q:
                    cy, cx = q.popleft()
                    pix.append((cy, cx))
                    for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        ny, nx = cy + dy, cx + dx
                        if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = 1
                            q.append((ny, nx))
                if len(pix) >= min_area:
                    comps.append(pix)
    return comps, down


def pick_head_cc(comps, down):
    """头 = 最靠上的实质绿色块（同高度带内取最大）；返回 (cc, bbox)。"""
    if not comps:
        return None, None
    info = []
    for pix in comps:
        ys = [p[0] * down for p in pix]
        xs = [p[1] * down for p in pix]
        info.append((min(ys), max(ys), min(xs), max(xs), len(pix), pix))
    top = min(i[0] for i in info)
    band = [i for i in info if i[0] <= top + 0.06 * 1024]
    band.sort(key=lambda i: -i[4])
    y0, y1, x0, x1, n, pix = band[0]
    return pix, (x0, y0, x1, y1)


def row_profile(cc, down, H, W):
    """返回 [(y, minx, maxx)]，每行绿色范围（还原到原图坐标）。"""
    rows = {}
    for cy, cx in cc:
        y = cy * down
        x = cx * down
        d = rows.setdefault(y, [x, x])
        d[0] = min(d[0], x)
        d[1] = max(d[1], x)
    return [(y, v[0], v[1]) for y, v in sorted(rows.items())]


def detect_head(prof):
    """按行宽剖面找头：头顶 -> 峰值 -> 颈(局部极小) -> 肩(骤增)。返回 (x0,y0,x1,y1)。"""
    ys = [p[0] for p in prof]
    exts = np.array([p[2] - p[1] + 1 for p in prof], dtype=float)
    top = ys[0]
    k = 9
    if len(exts) > k:
        sm = np.convolve(exts, np.ones(k) / k, mode="same")
    else:
        sm = exts
    n = len(sm)
    # 1) 头部峰值：前 45% 行内的最大宽度
    head_end_guess = max(5, int(n * 0.45))
    peak_i = int(np.argmax(sm[:head_end_guess]))
    base = sm[peak_i]
    # 2) 颈部：峰值之后宽度回落到 < 0.85*base 的第一个位置，并取其后局部极小
    neck_i = None
    for i in range(peak_i + 1, n):
        if sm[i] < base * 0.85:
            j = i
            while j + 1 < n and sm[j + 1] <= sm[j] + 1:
                j += 1
                if sm[j] > base * 1.25:
                    break
            neck_i = min(j, n - 1)
            break
    # 3) 若没找到脖子（如半身裁切），按头高 ≈ 头宽 * 1.3 估算
    if neck_i is None:
        est_h = base * 1.3
        neck_i = n - 1
        for i in range(peak_i, n):
            if ys[i] - top >= est_h:
                neck_i = i
                break
    sel = prof[:neck_i + 1]
    xs0 = min(p[1] for p in sel)
    xs1 = max(p[2] for p in sel)
    y0 = top
    y1 = ys[neck_i]
    return int(xs0), int(y0), int(xs1), int(y1)


def eye_boxes(head, facing="front"):
    x0, y0, x1, y1 = head
    w = x1 - x0 + 1
    h = y1 - y0 + 1
    cxm = (x0 + x1) / 2.0 + EYE_SHIFT.get(facing, 0.0) * w
    cy = y0 + EYE_CY * h
    dx = EYE_DX * w
    rx = EYE_RX * w
    ry = EYE_RY * h
    return [(cxm - dx, cy, rx, ry), (cxm + dx, cy, rx, ry)], (w, h)


def verify(rgb, gm, eyes):
    """校验：眼睛覆盖区域及其外环应大部分是绿色（说明贴在了头上）。"""
    out = []
    H, W = gm.shape
    for (cx, cy, rx, ry) in eyes:
        r = max(rx, ry) * 1.9
        y0 = max(0, int(cy - r)); y1 = min(H, int(cy + r))
        x0 = max(0, int(cx - r)); x1 = min(W, int(cx + r))
        if y1 <= y0 or x1 <= x0:
            out.append(0.0); continue
        patch = gm[y0:y1, x0:x1]
        out.append(float(patch.mean()))
    return out


def draw_eyes(path, eyes, out_path):
    im = Image.open(path).convert("RGB")
    W, H = im.size
    layer = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(layer)
    for (cx, cy, rx, ry) in eyes:
        d.ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    layer = layer.filter(ImageFilter.GaussianBlur(1.5))
    black = Image.new("RGB", (W, H), EYE_COLOR)
    im = Image.composite(black, im, layer)
    im.save(out_path)


def main():
    skip = set()
    for flag in ("-o", "--json", "--facing"):
        if flag in sys.argv:
            i = sys.argv.index(flag)
            skip.add(i)
            skip.add(i + 1)
    args = [a for i, a in enumerate(sys.argv[1:], start=1)
            if not a.startswith("-") and i not in skip]
    apply = "--apply" in sys.argv
    outdir = None
    if "-o" in sys.argv:
        outdir = sys.argv[sys.argv.index("-o") + 1]
    # 人工指定：--json cfg.json  格式 {"文件名.png": {"head":[x0,y0,x1,y1], "facing":"left|right|front"}}
    # 全局朝向：--facing left|right|front（默认 front，即两眼按头心对称）
    manual = {}
    if "--json" in sys.argv:
        manual = json.load(open(sys.argv[sys.argv.index("--json") + 1]))
    gfacing = "front"
    if "--facing" in sys.argv:
        gfacing = sys.argv[sys.argv.index("--facing") + 1]
    report = {}
    for p in args:
        rgb, _ = load_rgb(p)
        gm = green_mask(rgb)
        comps, down = all_ccs(gm)
        name = os.path.basename(p)
        if not comps:
            print(f"[FAIL] {name}: 找不到绿色区域")
            continue
        if name in manual and "head" in manual[name]:
            head = tuple(manual[name]["head"])
            print(f"[人工] {name} 指定头box={head}")
        else:
            cc, bbox = pick_head_cc(comps, down)
            h_cc = bbox[3] - bbox[1] + 1
            w_cc = bbox[2] - bbox[0] + 1
            if h_cc / max(1, w_cc) > 2.2:
                # 整块是全身 -> 用行宽剖面切出头部
                prof = row_profile(cc, down, *gm.shape)
                head = detect_head(prof)
            else:
                head = bbox
        facing = manual.get(name, {}).get("facing", gfacing)
        eyes, (hw, hh) = eye_boxes(head, facing)
        cov = verify(rgb, gm, eyes)
        ok = all(c > 0.55 for c in cov)
        name = os.path.basename(p)
        print(f"{'OK ' if ok else '?? '}{name}\n   头box={head} 头宽高={hw}x{hh} 朝向={facing} "
              f"眼={[ (round(e[0]),round(e[1]),round(e[2]),round(e[3])) for e in eyes]} 绿覆盖率={[round(c,2) for c in cov]}")
        report[name] = {"head": head, "eyes": eyes, "cov": cov, "ok": ok}
        if apply and ok:
            out = os.path.join(outdir, name) if outdir else p
            os.makedirs(os.path.dirname(out), exist_ok=True)
            draw_eyes(p, eyes, out)
            print(f"   已写出: {out}")
        elif apply and not ok:
            print(f"   [SKIP] 覆盖率不足，未修改（需人工指定坐标）")
    if report and not apply:
        print("\n(dry-run) 确认无误后加 --apply 落盘")


if __name__ == "__main__":
    main()
