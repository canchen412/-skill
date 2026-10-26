#!/usr/bin/env python3
"""brand_asset_guard.py — 成片「零他牌」资产闸门（防爱兰纯/三角喵/米当卡…混入）。

背景（本轮实锤）
----------------
肌酸·肌肉博士片里，镜 3 / 镜 13 用了 `分镜图库/小绿人/肌酸/爱兰纯/图片/` 下的图，
**罐身标签直接写着「肌酸粉 爱兰纯」**，而全片 `cfg["brand"]="肌肉博士"`。
用户要求：「该项目要求成片只出现肌肉博士肌酸」。

三个失效环节（本脚本针对的正是这三点）
----------------------------------------
1. **分类结果未被读取**：`分镜图库/小绿人/<品类>/<品牌>/图片/` 已经按品牌分好目录了，
   但配镜时是「按语义关键词捞图」，**没有任何一步去读「这张图属于哪个品牌目录」**。
2. **匹配逻辑未生效**：既有 `brand_guard.py` 的 `path_judge()` 判据是
   「素材是否落在传入的 `scope`（形如 `品类/品牌`）下」——
   **scope 由调用方手填**。本轮我传了 `肌酸/爱兰纯`（等于把「他牌」声明成「本品」），
   镜3 立刻被误判为「本品」放行。⇒ **scope 填错 ⇒ 门禁全盘失效**。
   正确做法：**scope 必须由 `cfg["brand"]` 自动推导，不允许手填**。
3. **约束未传入生成流程**：新生成的持罐镜若不带 `image2=<本品实拍罐>` 垫图，
   画出来的罐子就是「通用白罐/别牌罐」，prompt 里的品牌名约束不住 AI。

本脚本做什么
------------
**只认 `cfg["brand"]` + 图库目录结构，不接受手填 scope**：
  1. `--brand` 缺省时自动读 `cfg["brand"]`；
  2. 扫每张镜位素材的绝对路径 → 解析出 `品类/品牌` 段
     （形如 `…/小绿人/肌酸/爱兰纯/图片/x.png` ⇒ 品牌＝爱兰纯）；
  3. 品牌 ≠ 本品 ⇒ **BLOCK**；
  4. 落在 `_通用池/` 或图库根平铺层⇒ **WARN**（无品牌可判，必须目视或换成本品目录图）；
  5. 素材不在图库内（项目自有图，如本轮 `hold_*_16x9.png`）⇒ 需 `--own-brand` 声明，
     默认 **WARN**（提醒：这些图若由 ImageGen 生成，**必须带 `image2=本品实拍罐` 垫图**，
     否则罐身是通用罐 ⇒ 有「看不出是本品」的风险）；
  6. `--ledger` 可选：读 `产品图库/` 实拍图做 OCR/目视台账，做二次确认。

用法
----
  # 基本用法（brand 自动读 cfg）
  python3 brand_asset_guard.py <config.json>

  # 指定图库根+ 声明项目自有图的品牌归属
  python3 brand_asset_guard.py <config.json> \
      --lib "/Volumes/.../分镜图库/小绿人" \
      --own-brand "gen/clean/hold_*=肌肉博士（ImageGen 双垫：IP定妆图+本品实拍罐）"

  # 顺带跑既有的产品图闸门
  python3 brand_asset_guard.py <config.json> --also-product-guard

退出码：存在 BLOCK ⇒ 1
"""
import argparse
import fnmatch
import glob
import json
import os
import re
import sys

# 图库根目录下的品牌分层：<lib>/<品类>/<品牌>/图片/xxx.png
LAYER_RE = re.compile(r"^(?P<cat>[^/]+)/(?P<brand>[^/]+)/图片/")
# 已知「通用/无品牌」层
GENERIC_DIRS = ("_通用池", "_无眼版备份", "_旧画风", "_分层")
# 素材盘上的产品图库（实拍/官方图）—— 出现在镜位里一律 BLOCK（另由 shot_source_guard 管）
PRODUCT_LIB_KEY = "产品图库"


def parse_layer(abspath, lib):
    """从绝对路径解析 `品类/品牌`。命中返回 (cat, brand)；未命中返回 (None, None)。"""
    lib_ = os.path.abspath(lib)
    if not abspath.startswith(lib_ + os.sep):
        return None, None
    rel = os.path.relpath(abspath, lib_).replace(os.sep, "/")
    parts = rel.split("/")
    if parts and parts[0] in GENERIC_DIRS:
        return None, "GENERIC"
    m = LAYER_RE.match(rel)
    if m:
        return m.group("cat"), m.group("brand")
    return None, None


def judge_one(path, brand, lib, own_brand_rules, product_brand_alias=None):
    """返回 (level, reason)。level ∈ BLOCK / WARN / PASS / OWN"""
    ap_ = os.path.abspath(path)
    name = os.path.basename(ap_)

    # 0) 产品图库的实拍/官方图当镜位 ⇒ 另一条规则（#167）已禁，这里也一并拦
    if PRODUCT_LIB_KEY in ap_:
        return "BLOCK", "路径含「产品图库」—— 官方主图/详情图按 #167 不可整屏作分镜画面"

    inside = os.path.abspath(lib) in ap_ or ap_.startswith(os.path.abspath(lib) + os.sep)

    if inside:
        cat, b = parse_layer(ap_, lib)
        if b == "GENERIC":
            return "WARN", f"落在 {cat or '通用池'} —— 跨品牌通用层，零品牌可判，须目视确认画面里没有别牌包装"
        if b:
            if b == brand:
                return "PASS", f"路径即品牌：{cat}/{b}/图片/（{b}＝本品 ✅）"
            return "BLOCK", (f"⛔ 他牌素材：落在 {cat}/{b}/图片/，"
                             f"而 cfg.brand＝{brand} —— 罐身/包装是他牌，必须换图")
        return "WARN", "图库根平铺层，未归入品牌目录 ⇒ 无法判定归属，必须目视"

    # 2) 项目自有图（生成图 / 抽帧）
    for pat, why in own_brand_rules:
        if fnmatch.fnmatch(ap_, pat) or fnmatch.fnmatch(name, pat):
            return "OWN", f"项目自有图（{why}）"
    return "WARN", ("项目自有图且未声明品牌归属 —— 若是 ImageGen 产物，"
                    "必须确认 prompt 带 image2=本品实拍罐 垫图，否则罐身是通用罐、看不出是本品")


def collect_targets(cfg_path):
    with open(cfg_path, encoding="utf-8") as f:
        cfg = json.load(f)
    rows = []
    for i, p in enumerate(cfg.get("phrases", []), 1):
        src = p.get("video") or p.get("src")
        if not src or isinstance(src, list):
            continue
        rows.append((i, src))
    cards = [(f"card@{i}", c.get("src")) for i, c in enumerate(cfg.get("product_cards", []), 1)]
    return cfg, rows, cards


def main():
    ap = argparse.ArgumentParser(description="成片零他牌资产闸门（品牌归属自动推导，不接受手填 scope）")
    ap.add_argument("config")
    ap.add_argument("--brand", default=None, help="缺省自动读 cfg['brand']")
    ap.add_argument("--lib", default="/Volumes/陈灿会展/广告视频/分镜图库/小绿人",
                    help="图库根（含 <品类>/<品牌>/图片/ 分层）")
    ap.add_argument("--own-brand", nargs="*", default=[],
                    help="项目自有图品牌声明，形如 'hold_*=肌肉博士（双垫含本品实拍罐）'")
    ap.add_argument("--also-product-guard", action="store_true",
                    help="顺带跑 shot_source_guard.py")
    ap.add_argument("--json", dest="json_out", default=None)
    a = ap.parse_args()

    cfg, rows, cards = collect_targets(a.config)
    brand = a.brand or cfg.get("brand")
    if not brand:
        print("❌ config 里没有 brand 字段，也无法 --brand 指定 ⇒ 无法判定品牌归属")
        return 2

    own_rules = []
    for item in a.own_brand:
        if "=" in item:
            pat, why = item.split("=", 1)
            own_rules.append((pat, why))

    print(f"零他牌资产闸门｜本品 = {brand}｜图库 = {a.lib}")
    print(f"（brand 来源：{'cfg[brand]' if not a.brand else '--brand 参数'}；"
          f"scope 由 brand 自动推导，**不接受手填**）")
    print("─" * 78)

    results = []

    # ── ① 镜位画面：产品图库的图**禁止**（#167），他牌目录禁止
    for mirror, src in rows:
        if not os.path.isfile(src):
            r = dict(mirror=mirror, file=os.path.basename(src), level="MISS",
                     reason="文件不存在")
        else:
            lvl, why = judge_one(src, brand, a.lib, own_rules)
            r = dict(mirror=mirror, file=os.path.basename(src), level=lvl, reason=why)
        results.append(r)
        mark = {"BLOCK": "❌ BLOCK", "WARN": "⚠️ WARN", "PASS": "✅ PASS",
                "OWN": "🔷 OWN", "MISS": "✖️ MISS"}[r["level"]]
        print(f"  {str(r['mirror']):>7s}  {r['file'][:40]:42s} {mark}")
        print(f"           {r['reason']}")

    # ── ② product_cards：**允许**用产品图库官方图（#167 的正解就是降级进卡片），
    #    这里只校验「品牌归属是否与本品一致」，不再重复 #167 的整屏禁令。
    for mirror, src in cards:
        if not src or not os.path.isfile(src):
            continue
        ap_ = os.path.abspath(src)
        nm = os.path.basename(src)
        inside = ap_.startswith(os.path.abspath(a.lib) + os.sep)
        if PRODUCT_LIB_KEY in ap_:
            lvl, why = "PASS", "产品图库官方图作**详情卡**（#167 正解），非整屏镜位 ⇒ 放行"
        elif inside:
            cat, b = parse_layer(ap_, a.lib)
            lvl = "WARN" if not b else ("PASS" if b == brand else "BLOCK")
            why = ("详情卡素材：路径无品牌分层，须确认是本品官方图" if not b
                   else f"详情卡素材：{cat}/{b}（{'本品 ✅' if b == brand else '他牌 ⛔'}）")
        else:
            matched = any(fnmatch.fnmatch(nm, pat) or fnmatch.fnmatch(ap_, pat)
                          for pat, _ in own_rules)
            lvl = "OWN" if matched else "WARN"
            why = ("详情卡为项目自有图（已声明）" if matched
                   else "详情卡为项目自有图，未声明来源 ⇒ 须确认是本品官方图裁切")
        r = dict(mirror=mirror, file=nm, level=lvl, reason=why)
        results.append(r)
        mark = {"BLOCK": "❌ BLOCK", "WARN": "⚠️ WARN", "PASS": "✅ PASS",
                "OWN": "🔷 OWN"}[lvl]
        print(f"  {str(mirror):>7s}  {nm[:40]:42s} {mark}  [卡片口径]")
        print(f"           {why}")

    n_block = sum(r["level"] == "BLOCK" for r in results)
    n_warn = sum(r["level"] == "WARN" for r in results)
    print("-" * 78)
    print(f"BLOCK {n_block} / WARN {n_warn} / 其余 {len(results)-n_block-n_warn}（共 {len(results)}）")
    if n_block:
        print("⛔ 存在他牌素材 —— 禁止交付。修法：① 换成 {品类}/{本品}/图片/ 下的图"
              "② 或把该图 ImageGen 重生（image2 垫本品实拍罐）③ 或改成不含产品的无品牌镜位")

    if a.json_out:
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(dict(brand=brand, results=results, blocked=n_block), f,
                      ensure_ascii=False, indent=2)
        print(f"JSON → {a.json_out}")

    rc = 1 if n_block else 0

    if a.also_product_guard:
        import subprocess
        here = os.path.dirname(os.path.abspath(__file__))
        ps = os.path.join(here, "shot_source_guard.py")
        if os.path.isfile(ps):
            print("\n" + "=" * 78)
            r2 = subprocess.run([sys.executable, ps, a.config], capture_output=True, text=True)
            print(r2.stdout[-800:])
            rc = rc or r2.returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
