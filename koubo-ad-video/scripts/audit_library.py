# -*- coding: utf-8 -*-
"""分镜图库「使用台账 + 扩充建议」—— 每次出片后跑一次，告诉你该补哪些图。

用户 2026-09-19 要求：
  "我想慢慢扩充分镜图库，不然以后每次生成视频的时候都复用，难免重复。分镜完成后，
   你看看哪些需要复用得多的图片（比如健身撸铁的，表情惊讶或者开心的等），当然新产品
   和新场景更要提醒我生成，提示我，我再确认。"

于是本脚本做两件事：
  1) 记台账：从出片的 config 里提取「这次用了图库里的哪些图、哪几句在用」→ 累积到
     <图库根>/_使用台账.json（跨片累积，越用越准）。
  2) 出建议：--report 输出三类扩充建议
       A 高复用 TOP   —— 用得太频繁的图 → 提示补「同类不同款」，避免每条片都同一张
       B 通用地基缺口 —— 跨品类复用率最高的族（健身动作 / 情绪表情 / 生活场景 …）
                        对照图库现状算缺口，这部分补一次能喂所有品类
       C 本次新缺口   —— 用到了图库外的素材（工程静帧/AI 图/实拍抽帧）、以及首次出片的新产品

⚠️ 本脚本只**建议**，不生成任何东西。用户确认后才走 ImageGen（积分闸门，见 SKILL.md）。

用法:
  python3 audit_library.py --shot config_v4.json        # 记一次台账（出片后台账自增）
  python3 audit_library.py --report                     # 看扩充建议
  python3 audit_library.py --report --lib <图库目录>     # 指定图库根（默认素材盘小绿人库）
  python3 audit_library.py --report --json /tmp/a.json  # 存一份结构化结果
"""
import argparse
import json
import os
import sys
import time
from collections import defaultdict

# 分镜图库根目录：环境变量 AD_SHOT_LIB 或 --lib 覆盖（命令行优先级最高）
DEFAULT_LIB = os.environ.get("AD_SHOT_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
LEDGER_NAME = "_使用台账.json"

# 积分口径（2026-09-19 实测值，用于选项里的费用估算；实际以平台扣费为准）
IMG_LO, IMG_HI = 5, 10      # ImageGen 单张静态图
VID_LO, VID_HI = 25, 50     # VideoGen 5s 片段（720P；1080P 约 50–100）

# ---------------------------------------------------------------- 语义族
# 第一段（下划线前）→ 族。命名规范见图库 _取名指南.txt
FAMILY_OF = {
    "举铁": "健身动作", "俯卧撑": "健身动作", "深蹲": "健身动作", "硬拉": "健身动作",
    "单杠": "健身动作", "跑步": "健身动作", "拉伸": "健身动作", "苦练": "健身动作",
    "展示": "产品展示", "产品": "产品展示", "囤货": "产品展示",
    "讲课": "科普讲解", "科普": "科普讲解", "升级": "科普讲解", "保障": "科普讲解",
    "特性": "科普讲解", "适用": "科普讲解", "选品": "科普讲解",
    "避坑": "避坑对比", "对比": "避坑对比",
    "生活": "生活场景", "花钱": "生活场景", "互动": "生活场景",
    "收尾": "收尾CTA", "视频": "动态素材",
}

# ---------------------------------------------------------------- 通用地基清单
# 跨品类复用率最高的一批镜头：任何品类（肌酸/米糊/鱼油/褪黑素…）都用得上。
# 补这里的图 = 一次投入、所有片子受益，优先级最高。
# 字段：(族, 名称, 文件名匹配词, 建议张数, 说明)。匹配词命中文件名（不含扩展名）即计数。
BASELINE = [
    ("健身动作", "举铁",     ["举铁"], 3, "杠铃过头 / 卧推 / 硬拉 / 哑铃弯举 —— 每个动作给 2 个景别"),
    ("健身动作", "俯卧撑",   ["俯卧撑"], 2, "地面力竭 / 标准支撑 —— 承接「练不动/没感觉」"),
    ("健身动作", "深蹲",     ["深蹲"], 2, "杠铃深蹲 / 徒手深蹲 —— 腿部动作，现在偏上肢"),
    ("健身动作", "硬拉",     ["硬拉"], 2, "硬拉起杠 —— 力量感最强的动作"),
    ("健身动作", "跑步",     ["跑步", "晨跑"], 2, "跑步机 / 户外 —— 通用运动感"),
    ("健身动作", "单杠",     ["单杠", "引体"], 3, "引体向上 / 悬垂 —— 已有 2 张，可补角度"),
    ("情绪表情", "惊讶",     ["惊讶", "震惊", "瞪眼"], 3, "★用户点名：瞪眼张嘴特写 —— 承接「你知道吗」"),
    ("情绪表情", "开心",     ["咧嘴", "大笑", "笑", "得意"], 3, "★用户点名：咧嘴笑特写 —— 承接「选对了真香」"),
    ("情绪表情", "疑惑",     ["疑惑", "困惑", "皱眉", "歪头"], 2, "皱眉歪头 —— 承接「怎么选不踩坑」"),
    ("情绪表情", "力竭",     ["力竭", "咬牙", "满头汗", "脱力"], 2, "咬牙皱眉满头汗 —— 承接「喝了没感觉」"),
    ("生活场景", "照镜子",   ["镜前", "镜子", "照镜"], 2, "镜前看身材 —— 体型类品类必用"),
    ("生活场景", "疲惫",     ["疲惫", "熬夜", "犯困", "加班", "打哈欠"], 3, "加班/熬夜/犯困 —— 褪黑素/鱼油/维生素必用"),
    ("生活场景", "吃饭",     ["吃饭", "正餐", "外卖", "做饭", "碗饭"], 2, "正餐/外卖 —— 蛋白粉/米糊/代餐必用"),
    ("生活场景", "通勤",     ["通勤", "地铁", "上班", "办公室"], 2, "地铁/办公室/健身房门口 —— 场景过渡"),
    ("避坑对比", "翻配料表", ["配料", "成分表"], 1, "翻配料表摇头 —— 承接「配料表干干净净」"),
]


def list_lib(lib):
    """图库素材清单 {相对路径: (字节, mtime)}。_ 开头的是元数据/备份，不算素材。"""
    out = {}
    for root, dirs, files in os.walk(lib):
        dirs[:] = [d for d in dirs if not d.startswith("_") and not d.startswith(".")]
        for fn in files:
            if fn.startswith("_") or fn.startswith("."):
                continue
            if fn.lower().endswith((".png", ".jpg", ".jpeg", ".heic", ".mov", ".mp4")):
                p = os.path.join(root, fn)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                out[os.path.relpath(p, lib)] = (st.st_size, st.st_mtime)
    return out


def family_of(name):
    key = os.path.splitext(os.path.basename(name))[0].split("_")[0]
    return FAMILY_OF.get(key, "其他"), key


def load_ledger(path):
    if os.path.exists(path):
        try:
            return json.load(open(path, encoding="utf-8"))
        except Exception as e:
            print(f"WARN 台账损坏，重建: {e}")
    return {"version": 1, "usage": {}}


def record_shot(cfg_path, lib, ledger_path):
    """从出片 config 提取图库使用情况 → 累加台账。"""
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    proj = os.path.basename(os.path.dirname(os.path.abspath(cfg_path)))
    today = time.strftime("%y%m%d")
    led = load_ledger(ledger_path)
    usage = led.setdefault("usage", {})

    lib_abs = os.path.abspath(lib)
    hits, outside = [], []

    def take(src, phrase_txt, kind):
        if not src or not isinstance(src, str):
            return                      # 视频镜的 "src" 是 [起,止] 时间区间，不是路径
        ap = os.path.abspath(src)
        if ap.startswith(lib_abs + os.sep):
            rel = os.path.relpath(ap, lib_abs)
            e = usage.setdefault(rel, {"n": 0, "uses": 0, "first": today, "last": today,
                                       "projects": [], "phrases": []})
            e.setdefault("projects", [])
            e["uses"] = int(e.get("uses") or 0) + 1
            # n = 用过的**项目数**（按片去重）→ 同一 config 重跑不会虚增；重复跑只补 uses
            if proj not in e["projects"]:
                e["projects"].append(proj)
            e["n"] = len(e["projects"])
            e["last"] = today
            e.setdefault("phrases", [])
            if phrase_txt and phrase_txt not in e["phrases"]:
                e["phrases"] = (e["phrases"] + [phrase_txt])[-4:]
            hits.append(rel)
        else:
            outside.append((kind, os.path.basename(src), phrase_txt))

    for ph in cfg.get("phrases", []):
        take(ph.get("src"), ph.get("text", ""), "画面")
        take(ph.get("video"), ph.get("text", ""), "视频")
        for ov in (ph.get("over") or []):
            if isinstance(ov, dict):
                take(ov.get("src"), ph.get("text", ""), "贴图")
    for cd in cfg.get("product_cards", []):
        take(cd.get("src"), "", "产品素材")

    led["updated"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(led, open(ledger_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"台账收录 {len(hits)} 条图库素材（累计 {len(usage)} 张）→ {ledger_path}")
    other = [o for o in outside if o[0] != "产品素材"]
    prod = [o for o in outside if o[0] == "产品素材"]
    if other:
        print(f"另有 {len(other)} 处用了图库外素材（画面缺口，建议生成动漫版入库）：")
        for k, n, t in other[:8]:
            print(f"  [{k}] {n}  ← 「{t[:22]}」")
    pn = sorted({n for _, n, _ in prod})
    if pn:
        print(f"产品素材 {len(pn)} 张（产品图放产品目录=正常；若要跨片复用可整理进产品图库）：")
        for n in pn[:6]:
            print(f"  · {n}")
    return hits, outside


def report(lib, ledger_path, out_json=None):
    lib_files = list_lib(lib)
    led = load_ledger(ledger_path)
    usage = led.get("usage", {})
    fam_count, fam_used = defaultdict(int), defaultdict(int)
    for rel in lib_files:
        fam_count[family_of(rel)[0]] += 1
    for rel, e in usage.items():
        if rel in lib_files:
            fam_used[family_of(rel)[0]] += 1

    print("=" * 66)
    print(f"分镜图库扩充建议    {lib}")
    print(f"现有素材 {len(lib_files)} 张 | 台账记录 {len(usage)} 张 | 台账更新 {led.get('updated', '—')}")
    print("=" * 66)

    # A. 高复用 TOP
    print("\n【A】高复用 TOP —— 用得越频繁，越该补「同类不同款」")
    hot = sorted([(e.get("n", 0), rel, e) for rel, e in usage.items() if e.get("n", 0) >= 2 and rel in lib_files],
                 reverse=True)
    if not hot:
        print("  暂无（每条片用过的图都还只出现 1 次；多出几条片后这里会准起来）")
    for n, rel, e in hot[:12]:
        fam, key = family_of(rel)
        fam_n = len([r for r in lib_files if family_of(r)[1] == key])
        flag = " ← 该动作只有 %d 张，建议补同动作不同景别/角度" % fam_n if fam_n <= 2 else ""
        print(f"  {n} 次  {rel}{flag}")
        if e.get("phrases"):
            print(f"          最近用在：「{e['phrases'][-1][:26]}」")

    # B. 通用地基缺口
    print("\n【B】通用地基缺口 —— 跨品类复用率最高的族，补一次全品类受益")
    names = [os.path.splitext(r)[0] for r in lib_files]
    gaps = []
    for fam, key, kws, want, desc in BASELINE:
        have = len([n for n in names if any(k in n for k in kws)])
        if have < want:
            gaps.append({"family": fam, "name": key, "have": have, "want": want, "desc": desc})
            print(f"  [{fam}] {key:<5} 现有 {have} / 建议 {want}   {desc}")
    if not gaps:
        print("  地基已齐，无缺口 👍")
    print("  —— 图库族分布：" + "、".join(
        f"{k}{v}张" for k, v in sorted(fam_count.items(), key=lambda x: -x[1])))

    # C. 新产品 / 新场景
    print("\n【C】本次新缺口 —— 出片时用了图库外素材的位置")
    lo = led.get("last_outside") or {}
    if lo.get("items"):
        print(f"  最近一次出片（{lo.get('project', '?')} · {lo.get('date', '?')}）有 "
              f"{len(lo['items'])} 处用了图库外素材：")
        seen = set()
        for it in lo["items"]:
            tag = f"{it['kind']}·{it['name']}"
            if tag in seen:
                continue
            seen.add(tag)
            print(f"    [{it['kind']}] {it['name']}   ← 「{it.get('phrase', '')[:24]}」")
        print("  → 这些位置建议生成「动漫版」入库，下次同类文案可直接复用。")
    else:
        print("  本次分镜的主画面全部来自图库 ✅（没有用到图库外素材）")
    print("  新产品首片请优先补 3 类：① 白底完整包装（做贴纸切入的源图）"
          " ② 角色手持本品 ③ 本品 + 使用场景。")

    print("\n⚠️ 以上只是建议清单，未消耗任何积分。")

    # ★ 扩充选项菜单（2026-09-19 用户拍板：给 A/B/C/D 四个选项，带优劣与积分，用户来选）
    pick = None
    if gaps:
        emo = [g for g in gaps if g["family"] == "情绪表情"]
        pick = (emo or gaps)[0]
    elif hot:
        pick = {"family": family_of(hot[0][1])[0], "name": family_of(hot[0][1])[1],
                "have": 1, "want": 2,
                "desc": "本片又在用这张，补「同类不同款/不同景别」防观众看腻"}
    n_multi = min(3, len(gaps)) if gaps else (1 if hot else 0)
    if pick:
        one = f"[{pick['family']}] {pick['name']}"
        have_want = f"（现有 {pick.get('have','?')}/{pick.get('want','?')}）"
        rest = [g for g in (gaps or []) if g is not pick][:max(0, n_multi - 1)]
        multi_list = "、".join([f"{pick['family']}·{pick['name']}"] +
                               [f"{g['family']}·{g['name']}" for g in rest])
        print("\n📋 本次图库扩充选项（A/B/C/D 由你选，默认 B —— 都只是方案，未消耗积分）")
        print(f"  A. 不补          0 积分")
        print(f"     优：不花钱、出片最快；劣：图库原地踏步，同一批图反复复用，观众迟早看腻")
        print(f"  B. 补 1 张【推荐】 约 {IMG_LO}–{IMG_HI} 积分")
        print(f"     {one} {have_want} —— {pick['desc']}")
        print(f"     优：成本最低、稳步扩充，一次只花一杯咖啡钱；劣：其他缺口仍在，下次再补")
        if n_multi:
            print(f"  C. 为匹配画面多补 {n_multi} 张  约 {n_multi*IMG_LO}–{n_multi*IMG_HI} 积分")
            print(f"     覆盖：{multi_list}")
            print(f"     优：本片分镜可挑的图明显变多，画面重复率立刻下降；"
                  f"劣：一次性花销，且每张都要 Read 核验（可能出废片返工）")
        print(f"  D. 某片段生成视频（动态）  约 {VID_LO}–{VID_HI} 积分（720P 5s）/"
              f" {VID_HI}–{VID_HI*2} 积分（1080P 5s）")
        print(f"     建议给：开场 3–5 秒或最强卖点那一镜（静止图 + Ken Burns 撑不住的地方）")
        print(f"     优：动感最强，完播/停留明显更好；劣：积分最高、渲染慢，"
              f"且可能要重 roll 才出满意的一版")
        print("  → 回 A/B/C/D（或指定具体补哪张/哪段），我再出「提示词 + 积分」方案，你点头才生成。")
    else:
        print("\n📋 本次图库没有明显缺口，可以不补（选 A 即可）。"
              f"若想提升效果，可考虑 D：把开场/卖点镜做成动态视频（约 {VID_LO}–{VID_HI} 积分）。")
    if out_json:
        json.dump({"lib": lib, "hot": [(n, r) for n, r, _ in hot],
                   "gaps": gaps, "family_count": dict(fam_count)},
                  open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"（结构化结果 → {out_json}）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", default=DEFAULT_LIB, help="图库根目录")
    ap.add_argument("--shot", help="出片 config 路径：把这次用到的图库素材记入台账")
    ap.add_argument("--report", action="store_true", help="输出扩充建议")
    ap.add_argument("--json", dest="out_json", help="结构化结果输出路径")
    args = ap.parse_args()

    if not os.path.isdir(args.lib):
        sys.exit(f"图库目录不存在（素材盘没挂载？）：{args.lib}")
    ledger_path = os.path.join(args.lib, LEDGER_NAME)

    if args.shot:
        if not os.path.exists(args.shot):
            sys.exit(f"config 不存在：{args.shot}")
        record_shot(args.shot, args.lib, ledger_path)
    if args.report or not args.shot:
        report(args.lib, ledger_path, args.out_json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
