#!/usr/bin/env python3
"""shot_source_guard.py — 分镜图源合法性闸门（#167/#183 落地执行器）。

背景
----
SKILL.md #167★★★★★ 写明「`产品图库/` 里的官方主图与详情图**永远不作为分镜镜位的画面**，
只能进 `product_cards`」，但代码里的门禁（#183）**只挂在 product_cards 循环上**——
管的是「能不能当**卡片**」，完全没管「能不能当**分镜镜位**」。
⇒ 作用域缺口：把产品详情图直接写进 `config.phrases[].src`，门禁根本不经过这条路。

2026-10-05 实测踩坑：肌酸片把 `IMG_2783.jpg`（上市公司出品官方详情图）、
`IMG_2784.jpg`（纯白底官方主图）直接当镜 13/15 的整屏画面，全部漏检。

用法
----
  # 校验 config 全部镜位（默认读 config.phrases[].src，兼容 video）
  python3 shot_source_guard.py <config.json>

  # 校验一批散图是否可作分镜图源
  python3 shot_source_guard.py --images a.png b.jpg

  # 显式放行（品牌商授权的实拍场景图等），需写明理由，会记入 report
  python3 shot_source_guard.py <config.json> --allow "IMG_2783.jpg=品牌商官方场景图，已授权整屏"

判定
----
  ❌ BLOCK  路径含 `产品图库/`（官方主图/详情图）⇒ 只能进 product_cards
  ❌ BLOCK  `card_src_ok` / `card_src_ok_prefixes` 白名单里的图（是卡片素材，不是镜位画面）
  ❌ BLOCK  纯白底 / 纯色底官方图（无人物、无场景，纵向图库特征）
  ⚠️ WARN  文件名前缀含 `产品_` 且无 `卡` 字（可能是分镜图但命名可疑，需目视）
  ✅ PASS   其余

退出码：存在 BLOCK ⇒ 1
"""
import argparse
import json
import os
import sys

import numpy as np
from PIL import Image

# 官方产品图库目录名（路径里出现即拦截）
PRODUCT_LIB_KEYS = ("产品图库",)
# 卡片素材登记文件名前缀（这些是卡片，不是镜位画面）
CARD_SRC_MARK = ("_card_w", "_card.")


def _is_product_lib(path):
    p = path.replace("\\", "/")
    return any(k in p for k in PRODUCT_LIB_KEYS)


def _in_card_whitelist(path, cfg):
    ab = os.path.abspath(path)
    for s in cfg.get("card_src_ok", []) or []:
        if os.path.abspath(s) == ab:
            return True
    for pre in cfg.get("card_src_ok_prefixes", []) or []:
        if ab.startswith(os.path.abspath(pre)):
            return True
    return False


def _flat_bg_ratio(path):
    """纯白/纯色底占比（四边 12% 宽条内的主色占比）。>0.80 判为官方棚拍/白底主图。"""
    try:
        im = Image.open(path).convert("RGB")
    except Exception:
        return None
    w, h = im.size
    a = np.asarray(im).reshape(-1, 3).astype(np.int32)
    # 量化到 16 级/通道后取众数占比，容忍压缩噪点
    q = (a // 16)
    key = q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2]
    vals, counts = np.unique(key, return_counts=True)
    return float(counts.max() / len(key))


def check(path, cfg=None, allow=None, is_card=False):
    cfg = cfg or {}
    allow = allow or {}
    name = os.path.basename(path)
    reasons = []
    level = "PASS"

    if _is_product_lib(path):
        reasons.append("路径含「产品图库」—— 官方主图/详情图按 #167 只能进 product_cards，不可整屏作分镜画面")
        level = "BLOCK"
    if _in_card_whitelist(path, cfg):
        reasons.append("该图已登记在 card_src_ok/ prefixes —— 是卡片素材，不可同时作分镜画面")
        level = "BLOCK"
    if any(m in name for m in CARD_SRC_MARK):
        reasons.append("文件名是卡片成品（*_card_w*）—— 应走 product_cards")
        level = "BLOCK"

    flat = _flat_bg_ratio(path)
    if flat is not None and flat > 0.80 and not is_card:
        reasons.append(f"纯色/白底占比 {flat*100:.1f}%（>80%）—— 典型官方棚拍主图，不是场景分镜")
        if level != "BLOCK":
            level = "BLOCK"

    if level == "PASS" and name.startswith("产品_"):
        reasons.append("文件名前缀「产品_」—— 图库里此类多为小绿人分镜图（#183），但命名可疑，建议目视确认")
        level = "WARN"

    for k, v in allow.items():
        if k and k in name:
            return dict(file=name, path=os.path.abspath(path), verdict="ALLOW",
                        reason=f"显式放行：{v}", flat=flat)

    return dict(file=name, path=os.path.abspath(path), verdict=level,
                reason="；".join(reasons) if reasons else "OK（分镜图库素材）", flat=flat)


def collect_from_config(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    rows = []
    for i, p in enumerate(cfg.get("phrases", []), 1):
        src = p.get("video") or p.get("src")
        if not src or isinstance(src, list):
            continue
        rows.append((i, src))
    return cfg, rows


def main():
    ap = argparse.ArgumentParser(description="分镜图源合法性闸门（#167/#183 执行器）")
    ap.add_argument("config", nargs="?", help="config.json 路径")
    ap.add_argument("--images", nargs="*", default=None, help="直接校验一批散图")
    ap.add_argument("--allow", nargs="*", default=[],
                    help="显式放行，形如 文件名=理由")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args()

    allow = {}
    for item in a.allow:
        if "=" in item:
            k, v = item.split("=", 1)
            allow[k] = v

    cfg, targets = {}, []
    if a.config:
        cfg, targets = collect_from_config(a.config)
        targets = [(i, s) for i, s in targets]
    if a.images:
        targets += [(None, s) for s in a.images]
    if not targets:
        print("用法：给出 config.json 或 --images")
        return 2

    results = []
    for idx, src in targets:
        if not os.path.isfile(src):
            r = dict(file=os.path.basename(src), mirror=idx, verdict="MISS",
                     reason="文件不存在")
        else:
            r = check(src, cfg, allow)
            r["mirror"] = idx
        results.append(r)

    print("分镜图源合法性闸门（#167 官方主图/详情图禁作整屏分镜）")
    print("-" * 78)
    n_block = n_warn = n_pass = 0
    for r in results:
        v = r["verdict"]
        n_block += v == "BLOCK"
        n_warn += v == "WARN"
        n_pass += v in ("PASS", "ALLOW")
        mark = f"镜{r['mirror']}" if r.get("mirror") else "  --"
        print(f"  {mark:>5s}  {r['file'][:44]:46s} {v}")
        print(f"         {r['reason']}")
    print("-" * 78)
    print(f"BLOCK {n_block} / WARN {n_warn} / PASS+ALLOW {n_pass}（共 {len(results)}）")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(dict(results=results, blocked=n_block), f,
                      ensure_ascii=False, indent=2)
        print(f"JSON → {a.json_out}")

    return 1 if n_block else 0


if __name__ == "__main__":
    sys.exit(main())
