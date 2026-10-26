#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""品牌层台账补录 —— 把「目录即品牌」的信息写回 _素材品牌台账.json

迁移后，品牌层每张图的品牌归属已由**目录路径**确定，但部分图（尤其从
`_他品污染隔离/` 回迁的 17 张）台账里没有条目 ⇒ brand_guard 会判「未定」。
本脚本扫描品牌层，为缺失条目建档，并给已有条目补齐 path / scope。

幂等：只补不覆盖已有 brand（除非 --force）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os

LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
LEDGER = os.path.join(LIB, "_素材品牌台账.json")
SKIP = ("_", ".")

# 品牌层目录 → (brand, confidence) 说明
BRAND_NOTE = {
    "诺特兰德": "粉红标签＋「维生素C+维生素E+烟酰胺」＋粉色 S logo",
    "21金维他": "纯白标签＋黑字「21金维他」；液体钙款另有「钙镁维生素D/凝胶型」",
    "VORSE": "VORSE 标识（姜黄素胶囊 / 液体钙软胶囊 1200mg 两个品类）",
    "爱兰纯": "爱兰纯肌酸粉罐",
    "米当卡": "米当卡肌酸粉（粉红罐）",
    "三角喵": "三角喵肌酸粉",
    "英之素": "英之素碳水能量米糊",
    "未标注": "★带产品但品牌未定 —— 取用前必须目视瓶身确认（跨品牌复用前先定级）",
}


def scan_brand_layer(lib=LIB):
    """返回 [(prod, brand, filename, abs_path)]"""
    out = []
    for prod in sorted(os.listdir(lib)):
        pd = os.path.join(lib, prod)
        if not os.path.isdir(pd) or prod.startswith(SKIP):
            continue
        for br in sorted(os.listdir(pd)):
            bd = os.path.join(pd, br, "图片")
            if not os.path.isdir(bd) or br.startswith("."):
                continue
            for f in sorted(os.listdir(bd)):
                if f.startswith("._"):
                    continue
                out.append((prod, br, f, os.path.join(bd, f)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="覆盖已有 brand")
    ap.add_argument("--lib", default=LIB)
    a = ap.parse_args()

    led = json.load(open(LEDGER, encoding="utf-8"))
    items = led.setdefault("items", {})

    rows = scan_brand_layer(a.lib)
    added = patched = 0
    for prod, br, f, p in rows:
        rel = os.path.relpath(p, a.lib).replace(os.path.sep, "/")
        ent = items.get(f)
        if ent is None:
            ent = {}
            items[f] = ent
            added += 1
        if a.force or not ent.get("brand") or ent.get("brand") in ("待目视", "其他文字"):
            ent["brand"] = br if br != "未标注" else "未标注品牌"
            ent["product"] = f"{prod}（{br}）" if br != "未标注" else f"{prod}（品牌未定）"
            ent["confidence"] = "high" if br != "未标注" else "low"
            ent["ocr"] = ent.get("ocr", "[无文字]")
            patched += 1
        ent["path"] = rel
        ent["scope"] = [prod, br]
        ent["note"] = (ent.get("note", "") + " " if ent.get("note") else "") + \
            f"★2026-09-30 品牌层归位：目录 {prod}/{br}/图片/ 即品牌归属，取图只扫本品牌目录即可天然隔离他品。" \
            + (f"判别特征：{BRAND_NOTE.get(br, '—')}" if br in BRAND_NOTE else "")
        ent.pop("isolated", None)

    # counts 重算
    from collections import Counter
    c = Counter(v.get("brand", "?") for v in items.values())
    led["counts"] = dict(c.most_common())
    led["total"] = len(items)
    led["generated"] = _dt.datetime.now().isoformat(timespec="seconds")
    led.setdefault("structure", {})
    led["structure"]["brand_layer"] = {
        "desc": "{产品}/{品牌}/图片/ —— 已定级素材的路径即品牌，取图只扫当前品牌目录",
        "cross_brand_layer": "_通用池/ —— 零产品零品牌，任何项目可用",
        "ungraded": "平铺根目录 —— 待目视/其他文字/无品牌，未定级，不动",
        "note": "★品牌是「相对当前项目」的概念：做 A 品牌时不扫 B 品牌目录，即天然隔离，无需关键词排除",
    }
    json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"品牌层扫描 {len(rows)} 张 → 新建条目 {added}，补/改 brand {patched}")
    print(f"台账总数 {led['total']}")
    print("brand 分布 top12：")
    for k, v in c.most_common(12):
        print(f"   {v:4d}  {k}")


if __name__ == "__main__":
    main()
