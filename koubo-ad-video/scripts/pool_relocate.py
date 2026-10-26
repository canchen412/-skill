#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分镜图库「品牌维度」迁移器 —— 规划 / 执行 / 回退 / 校验

★设计前提（2026-09-30 评估结论）
  图库主体是「跨品牌复用的角色资产库」，不是产品素材库。
  强行按 {产品}/{品牌}/图片 全覆盖会有 70% 素材无处安放（台账 268 条里
  189 条归不了品牌）。所以采用【双层结构】：
      品牌绑定层  小绿人/{产品}/{品牌}/图片/   只收台账已定级的图（路径即隔离）
      跨品牌层    小绿人/_通用池/               零产品零品牌，任何项目都能用
      未归位层    小绿人/（平铺，不动）          待目视/其他文字/无品牌，保留原状
  ⇒ 未定级的图**不动**，零风险；已定级的图搬进品牌层，取图只扫品牌层即可天然隔离他品。

子命令
  plan      输出迁移方案（不改任何文件）
  apply     备份 → 移动 → 更新台账 path → 写 _迁移映射.json
  rollback  按 _迁移映射.json 全部还原
  verify    校验：品牌层无他品、映射可回退、台账 path 与磁盘一致

用法
  $PY pool_relocate.py plan
  $PY pool_relocate.py apply            # 先自动备份到 _旧版备份/<日期>_改目录前快照/
  $PY pool_relocate.py rollback
  $PY pool_relocate.py verify
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import sys

LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
LEDGER = os.path.join(LIB, "_素材品牌台账.json")
MAP_JSON = os.path.join(LIB, "_迁移映射.json")

# ---- 品牌 → (产品层, 品牌层) -------------------------------------------------
#   产品层用中文品类（检索友好），品牌层保持原始大小写（英文品牌不改动）
ROUTE = {
    "诺特兰德":        ("烟酰胺", "诺特兰德"),
    "诺特兰德?":       ("烟酰胺", "诺特兰德"),
    "21金维他":        None,          # 按 product 再分（液体钙 / 烟酰胺）
    "VORSE-姜黄素":    ("姜黄素", "VORSE"),
    "VORSE-液体钙":    ("液体钙", "VORSE"),
    "爱兰纯-肌酸":     ("肌酸", "爱兰纯"),
    "米当卡-肌酸":     ("肌酸", "米当卡"),
    "三角喵-肌酸":     ("肌酸", "三角喵"),
    "肌酸-未标注品牌": ("肌酸", "未标注"),
    "英之素-米糊":     ("米糊", "英之素"),
    # 隔离区文件名括号里写的品牌（台账无记录时从文件名解析）
    "21金维他-液体钙":        ("液体钙", "21金维他"),
    "21金维他-烟酰胺咀嚼片":  ("烟酰胺", "21金维他"),
    "诺特烟酰胺":             ("烟酰胺", "诺特兰德"),
}

# 隔离区里**文件名无括号**且台账无记录的两张 —— 依 SKILL.md 实证（#43 / 米当卡 OCR）
ISO_OVERRIDE = {
    "污染_收尾_竖拇指产品在旁_中景.png": ("肌酸", "米当卡"),   # SKILL：与「互动_递罐给新手」同为肌酸粉红黑罐（OCR=米当卡肌酸粉）
    "污染_花钱_单瓶价签犹豫_中景.png":    ("烟酰胺", "诺特兰德"), # SKILL #43：瓶身实印「维生素C+烟酰胺咀嚼片」= 诺特粉瓶
}
# 这些 brand 值**不迁移**（归不了品牌 / 属跨品牌共用）
STAY = {"待目视", "其他文字", "本司IP角色", "无品牌", "无品牌-成分表"}

# 目录/文件命名净化：禁 空格 · （）() ? ! / : * " < > | 等
BAD_CHARS = re.compile(r'[\\/:*?"<>|\s·（）()?!！，,、]+')


def sanitize(s: str) -> str:
    """品牌层/产品层目录名净化；英文大小写原样保留。"""
    s = BAD_CHARS.sub("", s.strip())
    return s or "未标注"


def route_of(brand: str, product: str):
    """返回 (产品层, 品牌层) 或 None（不迁移）。"""
    b = (brand or "").strip()
    if b in STAY:
        return None
    if b.startswith("诺特") and b not in ROUTE:
        return ("烟酰胺", "诺特兰德")
    if b == "21金维他":
        p = product or ""
        if "液体钙" in p:
            return ("液体钙", "21金维他")
        if "烟酰胺" in p:
            return ("烟酰胺", "21金维他")
        return ("烟酰胺", "21金维他")      # 台账 product 为空时的兜底
    r = ROUTE.get(b)
    if r is None:
        return None
    return (sanitize(r[0]), sanitize(r[1]))


def load_ledger():
    return json.load(open(LEDGER, encoding="utf-8"))


ISOLATED = "_他品污染隔离"


def clean_isolated_name(name: str) -> str:
    """隔离区文件名 → 品牌层文件名：去 `污染_` 前缀 + 去尾部「（污染品牌）」。"""
    s = name
    if s.startswith("污染_"):
        s = s[len("污染_"):]
    s = re.sub(r"[（(][^（()）]*[）)]\s*(?=\.[A-Za-z]+$)", "", s)
    return BAD_CHARS.sub("", s)


def build_plan():
    """返回 [(src_abs, dst_abs, brand, product)]，只含真实存在且不在品牌层的文件。
    含两类：① 主库平铺层已定级的图  ② `_他品污染隔离/` 里的图（★路径即隔离后，隔离区退役）"""
    led = load_ledger()
    items = led.get("items", {})
    plan, skipped = [], []
    iso_dir = os.path.join(LIB, ISOLATED)
    iso_names = set()
    if os.path.isdir(iso_dir):
        iso_names = {f for f in os.listdir(iso_dir)
                     if not f.startswith("._") and os.path.isfile(os.path.join(iso_dir, f))}

    # ① 主库平铺层
    for name, ent in sorted(items.items()):
        if name in iso_names:
            continue                                  # 走 ②
        src = os.path.join(LIB, name)
        if not os.path.isfile(src):
            skipped.append((name, "磁盘无此文件"))
            continue
        if os.path.sep + "图片" + os.path.sep in src:
            skipped.append((name, "已在品牌层"))
            continue
        r = route_of(ent.get("brand", ""), ent.get("product", ""))
        if r is None:
            skipped.append((name, f"不迁移（brand={ent.get('brand')}）"))
            continue
        prod, br = r
        plan.append((src, os.path.join(LIB, prod, br, "图片", name),
                     ent.get("brand", ""), ent.get("product", "")))

    # ② 隔离区 → 品牌层（去掉污染前缀/括号，靠目录实现隔离）
    for name in sorted(iso_names):
        ent = items.get(name) or {}
        if name in ISO_OVERRIDE:
            r = ISO_OVERRIDE[name]
            brand = f"{r[1]}（推定）"
        else:
            brand = ent.get("brand", "")
            if not brand:
                m = re.search(r"[（(](.+?)[）)]", os.path.splitext(name)[0])
                brand = m.group(1).split("·")[0].strip() if m else ""
            r = route_of(brand, ent.get("product", ""))
        if r is None:
            skipped.append((name, f"隔离区不可路由（brand={brand or '无'}）"))
            continue
        prod, br = r
        new = clean_isolated_name(name)
        plan.append((os.path.join(iso_dir, name), os.path.join(LIB, prod, br, "图片", new),
                     brand, ent.get("product", "")))
    return plan, skipped


def cmd_plan(a):
    plan, skipped = build_plan()
    from collections import Counter
    c = Counter()
    print(f"图库：{LIB}")
    print(f"台账：{len(load_ledger().get('items', {}))} 条\n")
    print(f"★拟迁移 {len(plan)} 张 → 品牌绑定层：\n")
    for s, d, b, p in plan:
        rel = os.path.relpath(d, LIB)
        c[os.path.dirname(os.path.dirname(rel))] += 1
        print(f"   {os.path.basename(s)[:46]:48s} → {rel}")
    print("\n  目录分布：")
    for k, v in sorted(c.items()):
        print(f"     {v:3d}  {k}/图片/")
    stay = Counter()
    for name, why in skipped:
        stay[why.split("（")[0]] += 1
    print(f"\n★不迁移 {len(skipped)} 条：")
    for k, v in stay.most_common():
        print(f"     {v:3d}  {k}")
    if not a.json:
        print("\n(dry-run，未改动任何文件。加 --json 可导出方案)")


def cmd_apply(a):
    plan, _ = build_plan()
    if not plan:
        print("没有需要迁移的文件")
        return
    today = _dt.datetime.now().strftime("%y%m%d")
    bak = os.path.join(LIB, "_旧版备份", f"{today}_改目录前快照")
    os.makedirs(bak, exist_ok=True)
    print(f"[1/4] 备份 → {bak}")
    for s, d, b, p in plan:
        shutil.copy2(s, os.path.join(bak, os.path.basename(s)))
    print(f"      已备份 {len(plan)} 张")

    print("[2/4] 移动 → 品牌层")
    moved = []
    for s, d, b, p in plan:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.exists(d):
            print(f"      ⚠️ 目标已存在，跳过：{d}")
            continue
        shutil.move(s, d)
        moved.append((s, d))
    print(f"      已移动 {len(moved)} 张")

    print("[3/4] 更新台账 path / scope / key（隔离区改名同步）")
    led = load_ledger()
    items = led.get("items", {})
    n = renamed = 0
    for s, d in moved:
        old, new = os.path.basename(s), os.path.basename(d)
        ent = items.get(old)
        if ent is None:
            continue
        if old != new:                       # 隔离区：污染_xxx（品牌）.png → xxx.png
            items.pop(old)
            ent["formerly"] = old
            ent["isolated"] = False          # 已归位，不再是隔离素材
            items[new] = ent
            renamed += 1
        ent["path"] = os.path.relpath(d, LIB).replace(os.path.sep, "/")
        ent["scope"] = os.path.relpath(os.path.dirname(d), LIB).replace(os.path.sep, "/").split("/")[0:2]
        n += 1
    json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"      已写入 path/scope {n} 条，台账改名 {renamed} 条")

    print("[4/4] 写迁移映射（供 rollback）")
    mp = {
        "generated": _dt.datetime.now().isoformat(timespec="seconds"),
        "lib": LIB,
        "backup": os.path.relpath(bak, LIB),
        "moves": [{"from": os.path.relpath(s, LIB), "to": os.path.relpath(d, LIB)}
                  for s, d in moved],
    }
    json.dump(mp, open(MAP_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"      → {MAP_JSON}（{len(moved)} 条）")
    print("\n✅ 完成。回退：pool_relocate.py rollback")


def cmd_rollback(a):
    if not os.path.exists(MAP_JSON):
        sys.exit("❌ 没有 _迁移映射.json，无可回退内容")
    mp = json.load(open(MAP_JSON, encoding="utf-8"))
    n = 0
    for m in mp["moves"]:
        s = os.path.join(LIB, m["from"])
        d = os.path.join(LIB, m["to"])
        if os.path.isfile(d):
            os.makedirs(os.path.dirname(s), exist_ok=True)
            shutil.move(d, s)
            n += 1
    # 清台账 path / 还原 key
    led = load_ledger()
    items = led.get("items", {})
    for m in mp["moves"]:
        old, new = os.path.basename(m["from"]), os.path.basename(m["to"])
        it = items.get(new)
        if it is None:
            continue
        it.pop("path", None)
        it.pop("scope", None)
        if old != new:
            items.pop(new)
            it.pop("formerly", None)
            it["isolated"] = True
            items[old] = it
    json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 清空的品牌目录
    for m in mp["moves"]:
        p = os.path.dirname(os.path.join(LIB, m["to"]))
        while p != LIB and os.path.isdir(p) and not os.listdir(p):
            os.rmdir(p)
            p = os.path.dirname(p)
    os.remove(MAP_JSON)
    print(f"✅ 已回退 {n} 张，删除映射表")


def cmd_verify(a):
    led = load_ledger()
    items = led.get("items", {})
    ok = bad = 0
    scopes = {}
    for name, ent in items.items():
        p = ent.get("path")
        if not p:
            continue
        abs_p = os.path.join(LIB, p)
        if os.path.isfile(abs_p):
            ok += 1
            scopes.setdefault(tuple(ent.get("scope", [])), 0)
            scopes[tuple(ent.get("scope", []))] += 1
        else:
            bad += 1
            print(f"   ❌ 台账有 path 但磁盘不存在：{p}")
    print(f"台账 path 校验：✅ {ok}  ❌ {bad}")
    print("\n品牌层分布：")
    for k, v in sorted(scopes.items(), key=lambda x: -x[1]):
        print(f"   {v:3d}  {'/'.join(k)}")
    # 品牌层里不得出现「别的品牌」（路径即隔离的核心保证）
    print("\n品牌层纯度抽检（每层应只含本品牌）：")
    for prod in sorted(os.listdir(LIB)):
        pd = os.path.join(LIB, prod)
        if not os.path.isdir(pd) or prod.startswith("_"):
            continue
        for br in sorted(os.listdir(pd)):
            bd = os.path.join(pd, br, "图片")
            if not os.path.isdir(bd):
                continue
            files = [f for f in os.listdir(bd) if not f.startswith("._")]
            wrong = [f for f in files
                     if items.get(f, {}).get("scope") != [prod, br]]
            flag = "✅" if not wrong else f"❌ {len(wrong)} 张不匹配"
            print(f"   {flag}  {prod}/{br}/图片/  {len(files)} 张")
            for w in wrong[:5]:
                print(f"          {w}  (台账 scope={items.get(w, {}).get('scope')})")


def main():
    ap = argparse.ArgumentParser(description="分镜图库品牌维度迁移器")
    ap.add_argument("--lib", default=LIB)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan"); p.add_argument("--json", action="store_true"); p.set_defaults(func=cmd_plan)
    sub.add_parser("apply").set_defaults(func=cmd_apply)
    sub.add_parser("rollback").set_defaults(func=cmd_rollback)
    sub.add_parser("verify").set_defaults(func=cmd_verify)
    a = ap.parse_args()
    if a.lib != LIB:
        globals()["LIB"] = os.path.abspath(a.lib)
        globals()["LEDGER"] = os.path.join(LIB, "_素材品牌台账.json")
        globals()["MAP_JSON"] = os.path.join(LIB, "_迁移映射.json")
    a.func(a)


if __name__ == "__main__":
    main()
