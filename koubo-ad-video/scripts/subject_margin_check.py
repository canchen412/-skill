#!/usr/bin/env python3
"""subject_margin_check.py — 生图交付前「主体完整入画」量化校验（自动拦截，替代纯目检）。

背景
----
SKILL.md #121 铁律第 2 条要求「主体四周留 ≥8% 安全边距、头顶不顶边、脚不切底」，
但配套的 crop16x9_sim.py **只模拟裁切并输出拼图，自身不做任何检测、无退出码**，
于是「校验」退化为「人眼看拼图」——肉眼对「差 2~4% 边距」极不敏感，
实测 4 张里 3 张违规（底 4.3% / 底 2.3% / 顶 6.3%）被放过。

本脚本把规则 2 变成**可执行的量化闸门**：
  1. 按 IP 特征色分割主体（小绿人＝绿皮；也支持非人物图的主体差异分割）
  2. 量出主体 bbox 与四边边距（占画幅百分比）
  3. 判定：任一边 < `--min-margin` ⇒ ❌ 违规，**退出码 1**
  4. 可选输出标注图，人工复核

用法
----
  # 校验一批（违规退出码 1）
  python3 subject_margin_check.py a.png b.png c.png

  # 0.5% 容差 + 输出标注图 + JSON 报告
  python3 subject_margin_check.py *.png --min-margin 0.005 --annot out/ --json report.json

  # 只看最严重项（给生图 prompt 反馈用）
  python3 subject_margin_check.py *.png --json report.json

判定标准（默认 min-margin=0.02 即 2%）
  < 0.02      ❌ 触边/溢出（必须重roll 或改裁切区间）
  0.02~0.08   ⚠️ 小于 #121 要求的 8% 安全边距（建议重 roll）
  ≥ 0.08      ✅ 达标

实现要点
--------
* 主体掩膜：优先「IP 特征色」——小绿人绿皮（G 显著高于 R/B）＋ 3x3 邻域去噪。
  非小绿人画面（产品静物/静物组）自动回退到「前景 vs 四角背景色差」分割。
* 对**已经有暗色边框的图**（中心裁切过的 1536x864）同样有效。
* 不依赖 cv2，只用 numpy + PIL（已在托管 venv 内）。
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

# 小绿人绿皮阈值：G - max(R,B) > TH 视作主体像素
GREEN_TH = 14
# 行/列投影判定「该行/列有主体」的最小占比
PROJ_TH = 0.02
# 邻域去噪：3x3 内主体像素数下限
NEIGH_TH = 5


def _green_mask(arr):
    """小绿人绿皮掩膜 —— 色相 + 饱和度 + 分量优势三重判据。

    ⚠️ 不可用简单的 `G - max(R,B) > 14`：小绿人画面大量使用暖色墙面 / 木台 / 暖光，
    实测 hold_d 用该判据会把 75% 的画面（含天花板、台面）判成"绿皮" ⇒ bbox 铺满全图、
    边距全线报0%。色相限定 [70,165] 才是可靠的"绿"。
    """
    a = arr.astype(np.float32) / 255.0
    r, g, b = a[:, :, 0], a[:, :, 1], a[:, :, 2]
    mx = a.max(axis=2)
    mn = a.min(axis=2)
    sat = np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0.0)
    d = np.maximum(mx - mn, 1e-6)
    hue = np.where(mx == r, ((g - b) / d) % 6,
                   np.where(mx == g, (b - r) / d + 2, (r - g) / d + 4)) * 60.0
    return (hue > 70) & (hue < 165) & (sat > 0.18) & (g >= r) & (g > b) \
        & ((g - np.maximum(r, b)) > 0.04)


def _bg_diff_mask(arr, ref_px=6):
    """通用前景掩膜：与四角参考色差异显著 ⇒ 前景。

    适用于产品静物、静物组等无角色画面。
    """
    h, w = arr.shape[:2]
    corners = np.concatenate([
        arr[:ref_px, :ref_px].reshape(-1, 3),
        arr[:ref_px, -ref_px:].reshape(-1, 3),
        arr[-ref_px:, :ref_px].reshape(-1, 3),
        arr[-ref_px:, -ref_px:].reshape(-1, 3),
    ])
    bg = np.median(corners, axis=0)          # 背景代表色
    d = np.abs(arr.astype(np.int32) - bg).max(axis=2)
    return d > 26


def _denoise(mask):
    """3x3 邻域计数去孤立噪点。"""
    h, w = mask.shape
    p = np.pad(mask.astype(np.int32), 1)
    nb = np.zeros((h, w), np.int32)
    for i in range(3):
        for j in range(3):
            nb += p[i:i + h, j:j + w]
    return (nb >= NEIGH_TH) & mask


def _largest_component(mask):
    """保留最大连通域（4邻接，BFS）——去掉零散噪块与背景误检。

    不依赖 cv2，纯 numpy BFS。主体在构图中通常占画面 5%~60%，
    最大连通域能有效剔除「背景色差被误判成主体」的情况。
    """
    h, w = mask.shape
    lab = np.zeros((h, w), np.int32)
    cur = 0
    best_id = best_size = 0
    ys, xs = np.nonzero(mask)
    for y0, x0 in zip(ys, xs):
        if lab[y0, x0]:
            continue
        cur += 1
        size = 0
        stack = [(y0, x0)]
        lab[y0, x0] = cur
        while stack:
            y, x = stack.pop()
            size += 1
            for ny, nx in ((y - 1, x), (y + 1, x), (y, x - 1), (y, x + 1)):
                if 0 <= ny < h and 0 <= nx < w and mask[ny, nx] and not lab[ny, nx]:
                    lab[ny, nx] = cur
                    stack.append((ny, nx))
        if size > best_size:
            best_size, best_id = size, cur
    return (lab == best_id) if best_id else mask, best_size


def _bbox(mask):
    h, w = mask.shape
    rf = mask.sum(axis=1) / w
    cf = mask.sum(axis=0) / h
    ri = np.where(rf >= PROJ_TH)[0]
    ci = np.where(cf >= PROJ_TH)[0]
    if not len(ri) or not len(ci):
        return None
    return int(ci[0]), int(ri[0]), int(ci[-1]), int(ri[-1])


def check_one(path, min_margin=0.02, safe_margin=0.08, annot_dir=None):
    im = Image.open(path).convert("RGB")
    w, h = im.size
    arr = np.asarray(im)

    mask = _denoise(_green_mask(arr))
    how = "green-skin"
    # ★只有绿色像素占比合理（0.5%~70%）才认定是「小绿人画面」，
    #   否则背景里的绿瓶 / 暖光绿调会被误当主体 ⇒ 触发全边 0% 的误报。
    frac = mask.sum() / (w * h)
    if not (0.005 <= frac <= 0.70):
        mask = _denoise(_bg_diff_mask(arr))
        how = "bg-diff"

    # ★取最大连通域作为主体，去掉零散误检块
    mask, csize = _largest_component(mask)
    # 主体最大连通域应至少占画面 0.4%（1024² 下约 4.2k px），
    # 低于此值视为分割失败（画面可能几乎全是背景）。阈值随分辨率缩放。
    if csize < max(1200, w * h * 0.004):
        return dict(file=os.path.basename(path), size=(w, h), verdict="UNKNOWN",
                    seg=how, area=int(csize),
                    note=f"最大连通域仅 {csize} px（占画面 {csize/(w*h)*100:.2f}%），分割失败")

    bb = _bbox(mask)
    if bb is None:
        return dict(file=os.path.basename(path), size=(w, h), verdict="UNKNOWN",
                    note="未能分割出主体（画面可能几乎全是背景或纯色）")

    left, top, right, bottom = bb
    margins = {
        "top": top / h,
        "bottom": (h - 1 - bottom) / h,
        "left": left / w,
        "right": (w - 1 - right) / w,
    }
    hard = [k for k, v in margins.items() if v < min_margin]
    warn = [k for k, v in margins.items() if min_margin <= v < safe_margin]

    if hard:
        verdict = "FAIL"
    elif warn:
        verdict = "WARN"
    else:
        verdict = "PASS"

    res = dict(file=os.path.basename(path), path=os.path.abspath(path), size=(w, h),
               seg=how, bbox=bb, verdict=verdict,
               margins={k: round(v, 4) for k, v in margins.items()},
               hard_fail=hard, warn=warn)
    res["msg"] = {
        "FAIL": "❌ 触边/溢出：" + ",".join(hard) + " —— 必须重 roll 或改裁切区间",
        "WARN": "⚠️ 小于 8% 安全边距：" + ",".join(warn) + " —— 建议重 roll",
        "PASS": "✅ 主体完整，边距达标",
        "UNKNOWN": "⚠️ " + res.get("note", ""),
    }[verdict]

    if annot_dir:
        os.makedirs(annot_dir, exist_ok=True)
        vis = im.copy()
        d = ImageDraw.Draw(vis)
        d.rectangle([left, top, right, bottom], outline=(255, 0, 0), width=max(3, w // 250))
        for key in ("top", "bottom", "left", "right"):
            col = (255, 0, 0) if key in hard else ((255, 190, 0) if key in warn else (0, 255, 140))
            if key == "top":
                d.line([0, top, w, top], fill=col, width=max(2, w // 300))
            elif key == "bottom":
                d.line([0, bottom, w, bottom], fill=col, width=max(2, w // 300))
            elif key == "left":
                d.line([left, 0, left, h], fill=col, width=max(2, w // 300))
            else:
                d.line([right, 0, right, h], fill=col, width=max(2, w // 300))
        out = os.path.join(annot_dir, os.path.splitext(os.path.basename(path))[0] + "_bbox.jpg")
        vis.save(out, quality=88)
        res["annot"] = out
    return res


def main():
    ap = argparse.ArgumentParser(description="主体完整入画量化校验（#121 铁律第 2 条闸门）")
    ap.add_argument("images", nargs="+")
    ap.add_argument("--min-margin", type=float, default=0.02,
                    help="硬阈值：任一边小于此值判FAIL（默认 0.02=2%%）")
    ap.add_argument("--safe-margin", type=float, default=0.08,
                    help="安全边距：小于此值判 WARN（#121 要求 8%%）")
    ap.add_argument("--annot", default=None, help="标注图输出目录")
    ap.add_argument("--json", dest="json_out", default=None, help="JSON 报告输出路径")
    a = ap.parse_args()

    results = []
    for p in a.images:
        if not os.path.isfile(p):
            results.append(dict(file=os.path.basename(p), verdict="UNKNOWN", note="文件不存在"))
            continue
        try:
            results.append(check_one(p, a.min_margin, a.safe_margin, a.annot))
        except Exception as e:                # noqa: BLE001
            results.append(dict(file=os.path.basename(p), verdict="UNKNOWN", note=f"检测异常 {e}"))

    print(f"主体完整入画校验（硬阈值 {a.min_margin*100:.1f}% / 安全边距 {a.safe_margin*100:.1f}%）")
    print("-" * 78)
    n_fail = n_warn = n_pass = 0
    for r in results:
        v = r.get("verdict")
        n_fail += v == "FAIL"
        n_warn += v == "WARN"
        n_pass += v == "PASS"
        if "margins" in r:
            mg = " ".join(f"{k}{v_*100:5.1f}%" for k, v_ in r["margins"].items())
            print(f"  {r['file'][:44]:46s} {r['size'][0]}x{r['size'][1]}  {mg}")
        print(f"      {r.get('msg','')}")
    print("-" * 78)
    print(f"FAIL {n_fail} / WARN {n_warn} / PASS {n_pass}（共 {len(results)}）")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(dict(results=results, min_margin=a.min_margin,
                           safe_margin=a.safe_margin), f, ensure_ascii=False, indent=2)
        print(f"JSON → {a.json_out}")

    # ★ 退出码：存在 FAIL ⇒ 1（供上层 shell/编排拦截），UNKNOWN 记2 但不阻断
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
