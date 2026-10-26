#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
blackboard_swap.py —— 黑板宣言镜「换字」合成（v2 · 2026-09-28 增补干净底图模式 A）

场景：文案是「今天我们讲烟酰胺」，但库里只有「今天我们讲姜黄素」的黑板宣言镜
      （小绿人指黑板 + 大字）。**别重新生成** —— AI 重生成整张图，黑板质感 / 木框 /
      粉笔字 / 小绿人姿态全都会漂，与全片其它素材画风对不上。用合成。

★ 模式 A（首选，2026-09-28 22:0x 灿爹返工后定）：--clean-base 干净底图直接叠字
  前提：库里存在**同一构图的「无字干净底图」**，例如
        `分镜图库/小绿人/_通用池/场景空镜/场景_教室黑板宣言_小绿人指黑板_中景.png`（1536x864，与含字成品同源）。
  做法：从同源含字图（--glyph-src）用**亮度掩膜**抠出大字（保留 alpha 抗锯齿边缘）→ 平移到新位置 → 叠在干净底图上。
  **为什么不再用 inpaint**：抹字会在木框 / 粉笔痕等纹理处留下可见修复带。实测翻车（2026-09-28）：
  inpaint 掩膜吃进左侧木框 → 木框在 y≈226 被切断 + 左侧一条**竖直亮带**，灿爹判「黑板边框有 P 图痕迹」。
  干净底图法从源头就没有需要修复的像素 ⇒ 零痕迹、零积分。

★ 模式 B（库里没有干净底图时才用）：--decl + --decl-bbox 抹字，--src + --src-bbox 贴字
  ① cv2.inpaint(TELEA, r) 抹掉底图上的原大字 —— **mask 左边界必须避开木质边框**，否则吃出竖直亮带；
  ② 从字源图裁出目标大字（外扩 --pad 保留描边/辉光）；
  ③ 等比缩放到 --target-w；
  ④ alpha 用**「黄 或 白」双掩膜** + 膨胀 + 高斯羽化
     —— **别用灰度阈值**：浅色/白底背景会被几乎全选（实测白底商品图 97.9% vs 双掩膜 78.6%）；
  ⑤ 按 --center 做 alpha 叠加。

用法 A（首选 · 2026-09-28 金维他烟酰胺条实测值，字位中心已右移到 437,332）：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/blackboard_swap.py
  B="/Volumes/陈灿会展/广告视频/分镜图库/小绿人"

  $PY $S \
    --clean-base "$B/_通用池/场景空镜/场景_教室黑板宣言_小绿人指黑板_中景.png" \
    --glyph-src  "$B/开场_黑板宣言_烟酰胺大字_小绿人指黑板_中景.png" \
    --glyph-bbox 95,200,700,470 \
    --dx 135 --dy 0 \
    --out "$B/开场_黑板宣言_烟酰胺大字_小绿人指黑板_中景.png" \
    --preview /tmp/bb_preview.png

  （--dx/--dy 是相对原字位置的平移量；也可改用 --center 437,332 直接指定新中心。）

用法 B（无干净底图时 · 2026-09-28 白天首次实测值）：
  $PY $S \
    --decl "$B/开场_黑板宣言_姜黄素大字_小绿人指黑板_中景.png" \
    --src  "$B/开场_黑板正对_烟酰胺大字_中景.png" \
    --decl-bbox 100,225,625,455 \
    --src-bbox  371,208,1147,487 \
    --target-w 376 --center 437,332 \
    --out ./s1/gen/开场_黑板宣言_烟酰胺大字_小绿人指黑板_中景.png \
    --preview /tmp/swap_preview.png

换别的字同样适用：底图换任意「黑板 + 原大字」宣言镜，字源换任意「黑板 + 目标大字」
（例如库里那批 `开场_黑板正对_<产品>大字_中景.png`）。
"""
import argparse
import sys

import cv2
import numpy as np


def parse_bbox(s, name):
    try:
        x0, y0, x1, y1 = (int(v) for v in s.split(","))
    except Exception:
        sys.exit(f"--{name} 格式应为 x0,y0,x1,y1，收到：{s}")
    if not (x0 < x1 and y0 < y1):
        sys.exit(f"--{name} 需满足 x0<x1 且 y0<y1，收到：{s}")
    return x0, y0, x1, y1


def parse_xy(s, name):
    try:
        x, y = (int(v) for v in s.split(","))
    except Exception:
        sys.exit(f"--{name} 格式应为 x,y，收到：{s}")
    return x, y


def alpha_diag(mask255):
    """诊断 alpha 掩膜是否可信（模式 B 用）。

    ★ 判据不是「覆盖率」——大字本身就占 59%（实测三个超粗黄字），50% 阈值是误报来源。
      真正能区分「字形」与「整块背景误判」的是形状特征：
        · 连通域个数   字形 ≈ 字数（2~5）；       背景误判 ≈ 1~2 个
        · 最大域占比   字形 ~20%；                背景误判 >65%
        · 四角覆盖数   字形 0~1/4；               背景误判 3~4/4
      实测参照（开场_黑板正对_烟酰胺大字 裁字区 376x152）：
        双掩膜   覆盖 59.0%  4 域  最大 20.5%  四角 0/4  → 正常
        灰度>100 覆盖 60.8%  6 域  最大 21.2%  四角 0/4  → 深色底上两者接近
        灰度>100 在白底商品图上 覆盖 97.9%          → 灾难（背景全被选中）
    """
    n, lab, stats, _ = cv2.connectedComponentsWithStats((mask255 > 0).astype(np.uint8), 8)
    areas = sorted(stats[1:, 4], reverse=True) if n > 1 else []
    tot = mask255.shape[0] * mask255.shape[1]
    cs = 20
    corners = [mask255[:cs, :cs].mean(), mask255[:cs, -cs:].mean(),
               mask255[-cs:, :cs].mean(), mask255[-cs:, -cs:].mean()]
    return {
        "cov": mask255.mean() / 255 * 100,
        "ncomp": max(0, n - 1),
        "maxcomp": (areas[0] / tot * 100) if areas else 0.0,
        "corners": sum(1 for c in corners if c > 127),
    }


def alpha_verdict(d):
    """返回 (是否可疑, 原因列表)。"""
    warn = []
    if d["cov"] > 92:
        warn.append(f"覆盖率 {d['cov']:.0f}% 几近全画面")
    if d["maxcomp"] > 65 and d["ncomp"] <= 3:
        warn.append(f"单个连通域占 {d['maxcomp']:.0f}%（疑似整块背景，非字形）")
    if d["corners"] >= 3:
        warn.append(f"四角 {d['corners']}/4 被判为前景（字形不该贴满四角）")
    return bool(warn), warn


def _write_preview(path, canvas, ref_img, box, tag_before, tag_after):
    """输出「上=修改前 / 白线 / 下=修改后」的局部对比预览。"""
    x0, y0, x1, y1 = box
    before = ref_img[y0:y1, x0:x1]
    after = canvas[y0:y1, x0:x1]
    sep = np.full((6, after.shape[1], 3), 255, np.uint8)
    cv2.imwrite(path, np.vstack([before, sep, after]))
    print(f"预览（上={tag_before} / 白线 / 下={tag_after}）-> {path}  (x[{x0},{x1}] y[{y0},{y1}])")


# --------------------------------------------------------------------------- #
# 模式 A：干净底图直接叠字（首选）
# --------------------------------------------------------------------------- #
def run_clean_base(args):
    base = cv2.imread(args.clean_base)
    if base is None:
        sys.exit(f"读不到干净底图：{args.clean_base}")
    glyph_src = args.glyph_src or args.decl
    if not glyph_src:
        sys.exit("模式 A 需要 --glyph-src（或 --decl）指定「含目标大字的同源图」")
    A = cv2.imread(glyph_src)
    if A is None:
        sys.exit(f"读不到抠字源图：{glyph_src}")

    H, W = base.shape[:2]
    if A.shape[:2] != (H, W):
        print(f"⚠️ 尺寸不一致：底图 {W}x{H} vs 抠字源 {A.shape[1]}x{A.shape[0]}"
              f" —— 非同源图会导致字位错位，务必确认两张图是同构图")

    # ① 亮度掩膜抠字：字是「高亮黄/白」（实测字 V=100，R255 G214-238 B90-170），
    #    而干净底图/木框/贴片 V<=31 ⇒ 分离干净。cv2 通道顺序是 BGR。
    R = A[:, :, 2].astype(int)
    G = A[:, :, 1].astype(int)
    B = A[:, :, 0].astype(int)
    mask = ((R > 170) & (G > 130) & (B < 215)).astype(np.uint8)
    if args.glyph_bbox:
        gx0, gy0, gx1, gy1 = parse_bbox(args.glyph_bbox, "glyph-bbox")
        outside = np.ones((H, W), np.uint8)
        outside[gy0:gy1, gx0:gx1] = 0
        mask[outside > 0] = 0          # 只保留搜索区内的候选
        print(f"抠字搜索区 x[{gx0},{gx1}] y[{gy0},{gy1}]")

    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros_like(mask)
    comps = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if area >= args.min_area:
            keep[lab == i] = 1
            comps.append((area, x, y, w, h))
    comps.sort(reverse=True)
    print(f"保留连通块 {len(comps)} 个（面积>= {args.min_area}）：{comps[:6]}")
    if not comps:
        sys.exit("抠字失败：连通块 0 个 —— 检查 --glyph-bbox 是否圈住大字 / 判据是否适用（试试用模式 B）")

    ys, xs = np.where(keep > 0)
    bx0, bx1, by0, by1 = int(xs.min()), int(xs.max()), int(ys.min()), int(ys.max())
    old_cx, old_cy = (bx0 + bx1) // 2, (by0 + by1) // 2
    print(f"原字 bbox x[{bx0},{bx1}] y[{by0},{by1}] ({bx1-bx0+1}x{by1-by0+1})  中心 ({old_cx},{old_cy})")

    # ② 软 alpha（保留抗锯齿边缘）
    lum = A.max(axis=2).astype(np.float32)
    alpha = np.clip((lum - 150.0) / 90.0, 0.0, 1.0)
    alpha = alpha * cv2.dilate(keep, np.ones((3, 3), np.uint8)).astype(np.float32)
    alpha = cv2.GaussianBlur(alpha, (3, 3), 0)
    pad = 3
    box = np.zeros((H, W), np.float32)
    box[max(0, by0 - pad):by1 + pad + 1, max(0, bx0 - pad):bx1 + pad + 1] = 1.0
    alpha = alpha * box

    # ③ 平移量：--center 优先，其次 --dx/--dy
    if args.center:
        cx, cy = parse_xy(args.center, "center")
        dx, dy = cx - old_cx, cy - old_cy
        print(f"落点中心指定 ({cx},{cy}) ⇒ 平移 dx={dx} dy={dy}")
    else:
        dx, dy = args.dx, args.dy
        cx, cy = old_cx + dx, old_cy + dy
        print(f"平移 dx={dx} dy={dy} ⇒ 新中心 ({cx},{cy})")

    # ④ 合成
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    glyph = np.dstack([A, (alpha * 255.0).astype(np.uint8)]).astype(np.uint8)
    glyph = cv2.warpAffine(glyph, M, (W, H), flags=cv2.INTER_LANCZOS4,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0, 0))
    ga = glyph[:, :, 3:4].astype(np.float32) / 255.0
    out = base.astype(np.float32) * (1 - ga) + glyph[:, :, :3].astype(np.float32) * ga
    out = np.clip(out, 0, 255).astype(np.uint8)

    nys, nxs = np.where(cv2.warpAffine(alpha, M, (W, H)) > 0.5)
    ncx, ncy = (int(nxs.min()) + int(nxs.max())) // 2, (int(nys.min()) + int(nys.max())) // 2
    print(f"新字 bbox x[{nxs.min()},{nxs.max()}] y[{nys.min()},{nys.max()}]  中心 ({ncx},{ncy})"
          f"  水平中心占画面 {ncx/W:.3f}（原 {old_cx/W:.3f}）")
    if nxs.min() < 0 or nxs.max() >= W or nys.min() < 0 or nys.max() >= H:
        print("⚠️ 新字有像素被裁出画面外，检查 --dx/--dy 或 --center")

    if not cv2.imwrite(args.out, out):
        sys.exit(f"写不出去：{args.out}")
    print(f"已写出 -> {args.out}")
    print("★ 验收两步：① 目视木框/黑板纹理是否完整无修复带；② 确认字未压小绿人手指与身体。")

    if args.preview:
        if args.preview_bbox:
            pv = parse_bbox(args.preview_bbox, "preview-bbox")
        else:
            pv = (max(0, min(bx0, nxs.min()) - 80), max(0, min(by0, nys.min()) - 80),
                  min(W, max(bx1, nxs.max()) + 80), min(H, max(by1, nys.max()) + 80))
        _write_preview(args.preview, out, A, pv, "抠字源（改前）", "合成（改后）")


# --------------------------------------------------------------------------- #
# 模式 B：inpaint 抹字 + 贴字（无干净底图时）
# --------------------------------------------------------------------------- #
def run_inpaint(args):
    if not (args.decl and args.decl_bbox and args.src and args.src_bbox):
        sys.exit("模式 B 需要 --decl / --decl-bbox / --src / --src-bbox；"
                 "若有干净底图请改用 --clean-base")
    if not args.center:
        sys.exit("模式 B 需要 --center（落点中心；本产品线实测推荐 437,332）")

    decl = cv2.imread(args.decl)
    src = cv2.imread(args.src)
    if decl is None:
        sys.exit(f"读不到底图：{args.decl}")
    if src is None:
        sys.exit(f"读不到字源图：{args.src}")

    dx0, dy0, dx1, dy1 = parse_bbox(args.decl_bbox, "decl-bbox")
    sx0, sy0, sx1, sy1 = parse_bbox(args.src_bbox, "src-bbox")
    cx, cy = parse_xy(args.center, "center")

    H, W = decl.shape[:2]
    if dx1 > W or dy1 > H:
        sys.exit(f"--decl-bbox 超出底图范围（底图 {W}x{H}）")

    # ① 抹掉原字
    mask = np.zeros((H, W), np.uint8)
    mask[dy0:dy1, dx0:dx1] = 255
    cleaned = cv2.inpaint(decl, mask, args.radius, cv2.INPAINT_TELEA)

    # ② 裁字源
    p = args.pad
    sy0p, sy1p = max(0, sy0 - p), min(src.shape[0], sy1 + p)
    sx0p, sx1p = max(0, sx0 - p), min(src.shape[1], sx1 + p)
    patch = src[sy0p:sy1p, sx0p:sx1p].copy()

    # ③ 缩放到目标宽
    scale = args.target_w / float(patch.shape[1])
    tw, th = max(1, int(round(patch.shape[1] * scale))), max(1, int(round(patch.shape[0] * scale)))
    patch_r = cv2.resize(patch, (tw, th), interpolation=cv2.INTER_AREA)

    # ④ 双掩膜 alpha（黄 or 白）
    #    ★ 为什么不用灰度阈值：浅色/白底背景上灰度法会几乎全选（实测白底商品图 97.9%，
    #      双掩膜 78.6%），把整片背景贴上去；深色黑板上两法只差 ~1.7 个百分点（多选的是
    #      黑板反光），所以「黑板上用灰度法」不算致命 —— 但它不可移植，一律用双掩膜。
    b = patch_r[:, :, 0].astype(int)
    g = patch_r[:, :, 1].astype(int)
    r = patch_r[:, :, 2].astype(int)
    yellow = (r > 150) & ((r - b) > 55)
    white = (r > 195) & (g > 190) & (b > 165)
    raw = ((yellow | white).astype(np.uint8)) * 255
    d = alpha_diag(raw)
    a = cv2.dilate(raw, np.ones((3, 3), np.uint8), iterations=1)
    a = cv2.GaussianBlur(a, (5, 5), 0).astype(np.float32) / 255.0
    a3 = np.dstack([a] * 3)

    # ⑤ 叠加
    x0 = cx - tw // 2
    y0 = cy - th // 2
    x1 = x0 + tw
    y1 = y0 + th
    if x0 < 0 or y0 < 0 or x1 > W or y1 > H:
        sys.exit(f"字贴出界：落点 ({cx},{cy}) 尺寸 {tw}x{th} 于 {W}x{H} —— 调整 --center/--target-w")
    roi = cleaned[y0:y1, x0:x1].astype(np.float32)
    merged = cleaned.copy()
    merged[y0:y1, x0:x1] = (roi * (1 - a3) + patch_r.astype(np.float32) * a3).clip(0, 255).astype(np.uint8)

    ok = cv2.imwrite(args.out, merged)
    if not ok:
        sys.exit(f"写不出去：{args.out}")
    print(f"底图 {decl.shape[1]}x{decl.shape[0]}  字源 {src.shape[1]}x{src.shape[0]}")
    print(f"原字区 x[{dx0},{dx1}] y[{dy0},{dy1}]  → inpaint r={args.radius}")
    print(f"字源区 x[{sx0},{sx1}] y[{sy0},{sy1}]  → 缩放 {patch.shape[1]}→{tw} 宽（+pad {p}）")
    print(f"alpha 掩膜（膨胀前）：覆盖 {d['cov']:.1f}% / 连通域 {d['ncomp']} 个 / "
          f"最大域 {d['maxcomp']:.1f}% / 四角 {d['corners']}·4")
    sus, why = alpha_verdict(d)
    if sus:
        print("⚠️ 掩膜可疑：" + "；".join(why) + " —— 检查是否用错字源区（选到了背景）")
    else:
        print("掩膜形态正常（字形特征：多连通域、最大域小、四角未被覆盖）")
    print(f"落点中心 ({cx},{cy})  ->  {args.out}")

    if args.preview:
        if args.preview_bbox:
            px0, py0, px1, py1 = parse_bbox(args.preview_bbox, "preview-bbox")
        else:
            px0, py0 = max(0, dx0 - 80), max(0, dy0 - 80)
            px1, py1 = min(W, dx1 + 80), min(H, dy1 + 80)
        _write_preview(args.preview, merged, decl, (px0, py0, px1, py1), "原图（改前）", "合成（改后）")


def main():
    ap = argparse.ArgumentParser(
        description="黑板宣言镜换字合成（A: 干净底图叠字「首选」 / B: inpaint 抹字贴字）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    # --- 模式 A ---
    ap.add_argument("--clean-base", help="【模式A】无字干净底图路径（提供则走模式 A）")
    ap.add_argument("--glyph-src", help="【模式A】抠字源图（含目标大字的同源图；缺省回落到 --decl）")
    ap.add_argument("--glyph-bbox", help="【模式A】抠字搜索区 x0,y0,x1,y1（建议给，避免误抓远处亮点）")
    ap.add_argument("--dx", type=int, default=0, help="【模式A】相对原字位置的水平平移（默认 0）")
    ap.add_argument("--dy", type=int, default=0, help="【模式A】相对原字位置的垂直平移（默认 0）")
    ap.add_argument("--min-area", type=int, default=300, help="【模式A】连通块最小面积（默认 300）")
    # --- 模式 B ---
    ap.add_argument("--decl", help="【模式B】宣言底图（含原大字）；模式 A 下作为 --glyph-src 的回落值")
    ap.add_argument("--src", help="【模式B】字源图（含目标大字）")
    ap.add_argument("--decl-bbox", help="【模式B】底图上要抹掉的原字区 x0,y0,x1,y1")
    ap.add_argument("--src-bbox", help="【模式B】字源图上目标字区 x0,y0,x1,y1")
    ap.add_argument("--target-w", type=int, default=376, help="【模式B】贴上后的目标宽度（默认 376）")
    ap.add_argument("--pad", type=int, default=30, help="【模式B】字源裁切外扩，保留描边/辉光（默认 30）")
    ap.add_argument("--radius", type=int, default=14, help="【模式B】inpaint 半径（默认 14）")
    # --- 公共 ---
    ap.add_argument("--center", help="落点中心 x,y（模式A 可省，用 --dx/--dy；本产品线实测推荐 437,332）")
    ap.add_argument("--out", required=True, help="输出 PNG")
    ap.add_argument("--preview", help="可选：输出对比裁图（上=改前 / 下=改后），供肉眼验收")
    ap.add_argument("--preview-bbox", help="对比裁图区 x0,y0,x1,y1（默认在原/新字区四周扩 80px）")
    args = ap.parse_args()

    if args.clean_base:
        run_clean_base(args)
    else:
        run_inpaint(args)


if __name__ == "__main__":
    main()
