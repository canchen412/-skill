#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""品牌层二次细分：诺特兰德新款/老款 + 新建 蛋白饮/品酷

起因（2026-09-30 灿爹拍板）：
  ① 「复核的产品是蛋白饮，对应的品牌是品酷」→ 新建 蛋白饮/品酷/ 产品层
  ② 「诺特兰德烟酰胺分两款…要再分一下，诺特兰德新款，诺特兰德老款」
     原 烟酰胺/诺特兰德/ 一锅装 → 拆成两个品牌目录

用法
  $PY pool_resplit.py plan
  $PY pool_resplit.py apply      # 自动备份到 _旧版备份/<日期>_品牌拆分前快照/
  $PY pool_resplit.py rollback
  $PY pool_resplit.py verify
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import shutil
import sys

LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
LEDGER = os.path.join(LIB, "_素材品牌台账.json")
MAP_JSON = os.path.join(LIB, "_迁移映射_品牌拆分260930.json")
SRC_NOTE = "烟酰胺/诺特兰德/图片"

# ---- 判定表（OCR 铁证优先 → 文件名 → 视觉） --------------------------------
# OCR 判据：新款标签「维生素C+维生素E+烟酰胺 +虾青素+葡萄籽提取物片 / 24g(30片)」
#           老款标签「维生素C+维生素E+烟酰胺 咀嚼片 / 48g(0.8g×60片)」
NEW = [  # → 烟酰胺/诺特兰德新款/图片/
    ("产品_举新款粉瓶过顶_健身房_中景.png", "名「新款」+ 大罐"),
    ("产品_仰拍手持新款粉瓶怼镜头_中近景.png", "OCR★虾青素+葡萄籽+24g(30片)"),
    ("产品_倒粉色片剂到手心_近景.png", "OCR★+虾青素+葡萄籽+24g(30片)"),
    ("产品_单手高举粉罐展示_中近景.png", "视觉大罐+标签四行含虾青素"),
    ("产品_双手举新款烟酰胺罐怼镜头_中近景.png", "名「新款」+OCR★24g(30片)"),
    ("产品_双手捧烟酰胺罐_中近景.png", "OCR★虾青素+葡萄籽+30片"),
    ("产品_双臂抱三罐跃起_全身.png", "视觉大罐（相对人≈躯干高）"),
    ("产品_托新款粉瓶竖大拇指_中景.png", "名+OCR★虾青素+葡萄籽+24g(30片)"),
    ("产品_托罐竖拇指推荐_中景.png", "视觉大罐"),
    ("产品_抱一摞烟酰胺罐_中景.png", "OCR★葡萄籽"),
    ("产品_抱新款粉瓶腾空欢跳_中景.png", "名+OCR★葡萄籽"),
    ("产品_指空蔬菜格托瓶_中景.png", "视觉大罐"),
    ("产品_新款粉瓶配果蔬坚果分格盘_中景.png", "名+OCR★虾青素+葡萄籽+24g(30片)"),
    ("产品_比六手势六瓶排开_中景.png", "OCR★虾青素+葡萄籽+24g(30片)"),
    ("产品_比六手势六瓶排开_中景_五瓶原版.png", "OCR★虾青素+葡萄籽+24g(30片)"),
    ("产品_深夜床头水杯新款罐_中景.png", "名「新款」+台账「+虾青素+葡萄籽提取物片」"),
    ("产品_肩扛新款粉瓶悠闲_中景.png", "名「新款」+视觉大罐"),
]
OLD = [  # → 烟酰胺/诺特兰德老款/图片/
    ("产品_健身房近走举瓶_中景.png", "视觉细高瓶+台账「白瓶粉标·维生素C+维生素E+烟酰胺」"),
    ("产品_单指指向老款粉瓶_全身.png", "名+OCR★咀嚼片+48g"),
    ("产品_双手举老款粉瓶到镜头_中近景.png", "名「老款」+视觉细高瓶"),
    ("产品_双手捧老款粉瓶端详_中近景.png", "名+OCR★咀嚼片+48g(0.8g×60片)"),
    ("产品_抱一摞老款粉罐_中景.png", "名「老款」"),
    ("产品_沙漠单膝托瓶逆光_全身.png", "视觉细高瓶+台账「白瓶粉标·维生素C+维生素E+烟酰胺」"),
    ("产品_深夜厨房扶额举瓶_中景.png", "OCR「咀嚼片／净含量46g(0.8g×60片)」（老款标签式样）"),
    ("囤货_五瓶装省钱_中景.png", "OCR★48g+60片"),
    ("展示_营养配比满盘_中景.png", "OCR★咀嚼片+48g+60片"),
    ("生活_睡前卧室端水杯托药片_中景.png", "OCR★咀嚼片+48g"),
    ("花钱_单瓶价签犹豫_中景.png", "OCR★咀嚼片"),
    ("餐食_蔬菜格空配产品_俯拍.png", "OCR★咀嚼片+48g+60片"),
]
# 平铺层里同品牌但一直没归位的（顺手完成上一轮遗留的「品牌层归位」）
NEW_FLAT = [
    ("产品_单指指向新款烟酰胺罐_全身.png", "OCR★葡萄籽"),
    ("产品_新款粉瓶配葡萄籽虾青素_中景.png", "名「新款」+葡萄籽虾青素"),
    ("产品_手持诺特粉瓶配葡萄籽红藻_中景.png", "名+葡萄籽红藻"),
    ("产品_怀抱六瓶新款白瓶腾空欢跳_中景.png", "名「新款」+OCR 烟酰胺字样+视觉大罐"),
]
OLD_FLAT = [
    ("产品_双手捧诺特老款粉瓶_中景.png", "名「老款」"),
]
# 蛋白饮 / 品酷（灿爹复核结论：产品=蛋白饮、品牌=品酷）
#   图库里没有任何「白瓶+蓝标」的品酷图（全库蓝占比扫描证实），
#   所以品酷素材 = 图库里的牛奶/白色乳品瓶类（无他品牌标识）+ 品酷项目产出的分镜图
PINKU = [
    ("展示_端详白瓶_近景.png", "白色乳品瓶·无他品牌标识"),
    ("囤货_抱一摞白瓶_中景.png", "白色乳品瓶·无他品牌标识"),
    ("展示_屈臂举白瓶_中景.png", "白色乳品瓶·无他品牌标识"),
    ("生活_厨房灌牛奶_中景.png", "牛奶元素（蛋白饮主打「配料只有生牛乳」）"),
    ("餐食_牛奶肉盘对比_俯拍.png", "牛奶元素（蛋白 vs 牛奶 对比镜）"),
]
# 品酷项目产出的分镜图（工作区 → 图库，复制入库；项目目录保留作交付留档）
PINKU_PROJ = "/Volumes/陈灿会展/workbuddy外置/2026-09-29-10-17-25/品酷蛋白饮_分镜图"
PINKU_COPY = [
    ("先导片_小绿人手持品酷瓶_中景.png", "品酷项目·先导片"),
    ("产品_直接喝品酷蛋白饮_中景.png", "品酷项目·开盖即饮"),
    ("产品_倒杯品酷蛋白饮_中景.png", "品酷项目·倒杯"),
    ("产品_精致早餐配品酷蛋白饮_中景.png", "品酷项目·早餐场景"),
    ("收尾_小绿人双拇指加品酷瓶_中景.png", "品酷项目·收尾（旧版）"),
    ("收尾_左右双小绿人护品酷瓶_中景.png", "品酷项目·收尾定版"),
]


def build_plan():
    plan, missing = [], []
    for names, dst, tag in ((NEW, "烟酰胺/诺特兰德新款/图片/", "新款"),
                            (OLD, "烟酰胺/诺特兰德老款/图片/", "老款")):
        for n, why in names:
            s = os.path.join(LIB, SRC_NOTE, n)
            (plan if os.path.isfile(s) else missing).append(
                (s, os.path.join(LIB, dst, n), tag, why))
    for names, dst, tag in ((NEW_FLAT, "烟酰胺/诺特兰德新款/图片/", "新款(平铺)"),
                            (OLD_FLAT, "烟酰胺/诺特兰德老款/图片/", "老款(平铺)")):
        for n, why in names:
            s = os.path.join(LIB, n)
            (plan if os.path.isfile(s) else missing).append(
                (s, os.path.join(LIB, dst, n), tag, why))
    for n, why in PINKU:
        s = os.path.join(LIB, n)
        (plan if os.path.isfile(s) else missing).append(
            (s, os.path.join(LIB, "蛋白饮/品酷/图片", n), "品酷", why))
    return plan, missing


def build_copy_plan():
    out = []
    for n, why in PINKU_COPY:
        s = os.path.join(PINKU_PROJ, n)
        d = os.path.join(LIB, "蛋白饮/品酷/图片", n)
        if os.path.isfile(s):
            out.append((s, d, "品酷(项目入库)", why))
    return out


def cmd_plan():
    plan, missing = build_plan()
    copies = build_copy_plan()
    print(f"方案：迁移 {len(plan)} 个 + 复制入库 {len(copies)} 个\n")
    for tag in ("新款", "老款", "新款(平铺)", "老款(平铺)", "品酷", "品酷(项目入库)"):
        sub = [p for p in (plan + copies) if p[2] == tag]
        if not sub:
            continue
        print(f"── {tag}（{len(sub)}）→ {os.path.dirname(sub[0][1]).replace(LIB + '/', '')}")
        for s, d, _, why in sub:
            print(f"     {os.path.basename(s)[:48]:50s} {why[:44]}")
        print()
    if missing:
        print("⚠️ 源文件不存在（跳过）：")
        for s, d, _, why in missing:
            print("     ", s)
    print(f"合计 新款 {len([p for p in plan if p[2].startswith('新款')])} + "
          f"老款 {len([p for p in plan if p[2].startswith('老款')])} + "
          f"品酷(迁移) {len([p for p in plan if p[2] == '品酷'])} + "
          f"品酷(复制) {len(copies)} = {len(plan) + len(copies)}")


def cmd_apply():
    plan, missing = build_plan()
    copies = build_copy_plan()
    stamp = _dt.datetime.now().strftime("%y%m%d_%H%M")
    bak = os.path.join(LIB, "_旧版备份", f"260930_品牌拆分前快照")
    os.makedirs(bak, exist_ok=True)
    moved = []
    print(f"[1/5] 备份 {len(plan)} 个源文件 → {bak}")
    for s, d, tag, _ in plan:
        b = os.path.join(bak, os.path.relpath(s, LIB))
        os.makedirs(os.path.dirname(b), exist_ok=True)
        if not os.path.exists(b):
            shutil.copy2(s, b)
    print("[2/5] 建目标目录并移动")
    for s, d, tag, _ in plan:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        if os.path.exists(d):
            print("      已存在，跳过：", os.path.basename(d)); continue
        shutil.move(s, d)
        moved.append({"from": s, "to": d, "tag": tag})
    print(f"      已移动 {len(moved)} 个")
    print(f"[3/5] 复制品酷项目分镜图入库（{len(copies)} 个）")
    for s, d, tag, _ in copies:
        os.makedirs(os.path.dirname(d), exist_ok=True)
        shutil.copy2(s, d)
        print("      +", os.path.basename(d))
    json.dump({"time": stamp, "backup": bak, "moves": moved,
               "copies": [{"from": c[0], "to": c[1], "tag": c[2]} for c in copies]},
              open(MAP_JSON, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"      映射 → {MAP_JSON}")
    print("[4/5] 清理已空的原品牌目录")
    old = os.path.join(LIB, "烟酰胺/诺特兰德")
    if os.path.isdir(old) and not [f for f in os.listdir(old)
                                   if not f.startswith("._") and not f.startswith(".")]:
        shutil.rmtree(old); print("      已删除空目录 烟酰胺/诺特兰德/")
    else:
        rest = [f for f in os.listdir(old) if not f.startswith("._")] if os.path.isdir(old) else []
        print("      未删（仍有内容）：", rest)
    print("[5/5] 更新台账 path/scope/brand")
    n = update_ledger(moved + [{"to": c[1], "tag": "品酷"} for c in copies])
    print(f"      台账更新 {n} 条")


def update_ledger(moved):
    led = json.load(open(LEDGER, encoding="utf-8"))
    items = led.get("items", {})
    n = 0
    for m in moved:
        name = os.path.basename(m["to"])
        rel = os.path.relpath(m["to"], LIB).replace(os.sep, "/")
        ent = items.get(name)
        if not ent:
            ent = items[name] = {"brand": "", "product": "", "type": "图片", "note": ""}
        ent["path"] = rel
        parts = rel.split("/")
        ent["scope"] = parts[:2] if len(parts) >= 3 else []
        if m["tag"] == "品酷":
            ent["brand"] = "品酷"
            ent["product"] = "鲜萃蛋白饮"
        elif m["tag"].startswith("新款"):
            ent["brand"] = "诺特兰德新款"
        elif m["tag"].startswith("老款"):
            ent["brand"] = "诺特兰德老款"
        ent["note"] = (str(ent.get("note") or "") +
                       f" ★2026-09-30 品牌拆分：{rel}")
        n += 1
    led["items"] = items
    led["generated"] = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return n


def cmd_rollback():
    mp = json.load(open(MAP_JSON, encoding="utf-8"))
    n = 0
    for m in mp["moves"]:
        if os.path.exists(m["to"]) and not os.path.exists(m["from"]):
            os.makedirs(os.path.dirname(m["from"]), exist_ok=True)
            shutil.move(m["to"], m["from"]); n += 1
    # 台账 path 还原
    led = json.load(open(LEDGER, encoding="utf-8")); items = led["items"]
    for m in mp["moves"]:
        name = os.path.basename(m["from"])
        if name in items:
            items[name].pop("path", None); items[name].pop("scope", None)
    json.dump(led, open(LEDGER, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"已回退 {n} 个文件（备份仍在 {mp['backup']}）")


def cmd_verify():
    led = json.load(open(LEDGER, encoding="utf-8"))
    items = led["items"]
    print("== 品牌层纯度校验 ==")
    for root, dirs, files in os.walk(LIB):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if os.path.basename(root) != "图片":
            continue
        fs = [f for f in files if not f.startswith("._") and not f.startswith(".")]
        if not fs:
            continue
        rel = os.path.relpath(root, LIB)
        print(f"\n  {rel}/  （{len(fs)} 张）")
        brands = {}
        for f in fs:
            b = str(items.get(f, {}).get("brand", "?"))
            brands.setdefault(b, []).append(f)
        for b, names in sorted(brands.items()):
            mark = "✅" if len(brands) == 1 else "⚠️"
            print(f"     {mark} brand={b}  {len(names)} 张")
            if len(brands) > 1:
                for nm in names:
                    print("         -", nm)
    print("\n== 台账 path 与磁盘一致性 ==")
    bad = 0
    for name, e in items.items():
        p = e.get("path")
        if not p:
            continue
        if not os.path.exists(os.path.join(LIB, p)):
            print("  ✗ path 失效：", name, "→", p); bad += 1
    print(f"  失效 {bad} 条")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "plan"
    {"plan": cmd_plan, "apply": cmd_apply,
     "rollback": cmd_rollback, "verify": cmd_verify}[cmd]()
