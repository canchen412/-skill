#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分镜 — 文案 — 素材 对应表 生成器（含匹配度自检 / 可批注 HTML / 批注回填）

=================================================================
用途（2026-09-27 灿爹拍板：正式生成前只做一次集中确认）：
    在「生图 / 渲染」之前，先把每一镜的
        文案原文 / 画面描述 / 素材（图库复用 or 实拍 or 待补生成）/ 匹配度自检
    摊成一张表给灿爹逐条批注，多轮改到通过后再一次性执行到底。

用法：
    # ① 生成可批注 HTML（主交付物，浏览器打开即可逐条批注 + 导出）
    python3 storyboard_match.py --config s1/config.json --html 对应表.html --brand 21金维他
    #    页面上两种确认方式（2026-09-27 新增「全部确认」闸门）：
    #      A「✅ 全部确认，直接执行」＝ 整案放行，未表态的镜按「通过」处理，
    #        并把确认凭证据复制到剪贴板 + 导出 annotations.json → 执行端直接推进后续流程
    #      B 只对「发现问题的镜」下拉决策 + 写批注（其余选项全部保留）→
    #        确认后只复核被改动的那几镜，其余按原方案继续，不必整表重来

    # ② 纯文本版（贴进对话快速过目）
    python3 storyboard_match.py --config s1/config.json --md

    # ③ CSV（给表格软件/剪映对照）
    python3 storyboard_match.py --config s1/config.json --csv plan.csv

    # ④ 回填批注（HTML 里导出的 annotations.json）→ 打印「待执行变更清单」
    python3 storyboard_match.py --config s1/config.json --apply annotations.json

    # ⑤ 画面描述人工补写（可选）：--desc desc.json  {"3":"小绿人指着满桌肉", ...}
=================================================================
"""
import argparse
import csv
import html
import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import shot_plan as sp  # 复用时长配额 / 判定

# ---------------- 常量 ----------------
# ★2026-09-29 修正（SKILL #103）：旧版把「诺特兰德」写死在他品表里 —— 那是「21金维他」
#   项目的口径；换项目后就出事：既漏检真他品（21金维他 / VORSE 根本不在表内），
#   又可能误伤本品。现改为：① 静态兜底表只放「跨项目都算他品」的牌子；
#   ② 运行时从品牌台账动态补全真实出现过的品牌；③ 主推品牌由 --brand 传入，永不算他品。
OTHER_BRANDS_FALLBACK = ["21金维他", "VORSE", "爱兰纯", "米当卡", "三角喵",
                         "英之素", "训练怪兽", "fohea", "燃燃", "拜拜丸", "肌酸"]
LEDGER_PATH = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/_素材品牌台账.json"
try:
    _LEDGER_ITEMS = json.load(open(LEDGER_PATH, encoding="utf-8")).get("items", {})
except Exception:
    _LEDGER_ITEMS = {}


def other_brands(brand=""):
    """他品清单 = 静态兜底 ∪ 台账里真实出现过的品牌（排除主推品牌同前缀）"""
    names = set(OTHER_BRANDS_FALLBACK)
    for _e in _LEDGER_ITEMS.values():
        b = str((_e or {}).get("brand", "") or "").split("-")[0].strip().rstrip("?")
        if not b or b in ("本司IP角色", "待目视", "其他文字", "无品牌", "无品牌-成分表"):
            continue
        if brand and b.startswith(brand):
            continue
        names.add(b)
    return sorted(names)


VIDEO_EXT = (".mp4", ".mov", ".avi", ".mkv")
LIB_MARK = "分镜图库"
PROD_MARK = "产品图库"

# 三段式命名 → 中文类别
CAT_CN = {
    "开场": "开场", "情绪": "情绪/表情", "对比": "对比示意", "生活": "生活场景",
    "餐食": "餐饮食材", "产品": "产品展示", "收尾": "收尾CTA", "展示": "展示",
    "选品": "选品判断", "吃": "进食动作", "科普": "科普示意", "场景": "场景空镜",
    "视频": "视频素材",
}
# 景别 → 镜头运动建议（渲染器 motion 缺省时的兜底）
SHOT_MOTION = {"近景": "缓推近", "特写": "缓推近", "中景": "缓拉远", "全景": "缓拉远",
               "全身": "缓拉远", "中近景": "缓推近"}

MOTION_CN = {"zoom_in": "缓推近", "zoom_out": "缓拉远", "pan_r": "右摇", "pan_l": "左摇",
             "pan_u": "上摇", "pan_d": "下摇", "static": "固定不动", "": ""}


# ---------------- 基础工具 ----------------
def pname(src):
    return os.path.splitext(os.path.basename(src or ""))[0]


def eff_src(p):
    """取「该镜真正读的那个文件路径」。
    ⚠️ 2026-09-27 修 bug：prod/imgvid 镜的 src 是时间区间数组 [起,止]，
    文件路径在 video 字段里；旧代码直接把 src 当字符串用 → TypeError。"""
    k = (p.get("kind") or "img").lower()
    if k in ("prod", "imgvid", "video") and isinstance(p.get("video"), str) and p["video"]:
        return p["video"]
    s = p.get("src")
    return s if isinstance(s, str) else ""


def derive_desc(src, kind, motion, flip):
    """从三段式文件名派生「画面描述」（人物/场景/动作），人工可在 --desc 覆盖。"""
    base = pname(src)
    parts = base.split("_")
    cat = CAT_CN.get(parts[0], parts[0] if parts else "")
    content = parts[1] if len(parts) > 2 else (parts[-1] if len(parts) > 1 else "")
    scale = ""
    for p in parts:
        if p in SHOT_MOTION:
            scale = p
    who = "小绿人" if "男孩" not in base else "动漫男孩"
    if "男孩" in base and "小绿人" in base:
        who = "动漫男孩（小绿人旁观）"
    mv = MOTION_CN.get(motion or "", "") or SHOT_MOTION.get(scale, "缓推近")
    if flip:
        mv += "（水平镜像）"
    k = (kind or "img").lower()
    if k in ("prod", "imgvid"):
        mv = "素材自身运动（prod 恒定放大 1 档）"
    return f"{who}｜{cat}·{content}｜{mv}"


def shot_scale(src):
    for p in pname(src).split("_"):
        if p in SHOT_MOTION:
            return p
    return "中景"


def material_kind(src, kind):
    """素材来源分类 + 是否要花积分（src 必须是「真实文件路径」字符串，见 eff_src）"""
    if not src:
        return "待补生成", "NEW"
    s = str(src)
    if s.startswith("NEW:") or s.startswith("待生成"):
        return "待补生成（AI 生图 ≈5–10 积分/张）", "NEW"
    if LIB_MARK in s:
        return "图库复用（0 积分）", "LIB"
    if PROD_MARK in s or os.path.splitext(s)[1].lower() in VIDEO_EXT:
        return "产品实拍素材（0 积分）", "REAL"
    return "工程/其他来源（需人工确认）", "OTHER"


# ---------------- 匹配度自检 ----------------
def self_check(i, shots, cfg, brand=""):
    """对第 i 镜（0 起）做规则自检，返回 (等级, [问题列表])  level: OK/WARN/FAIL"""
    p, s, e = shots[i]
    d = e - s
    src = eff_src(p)
    kind = (p.get("kind") or "img").lower()
    motion = p.get("motion") or ""
    txt = p.get("text", "")
    issues = []
    level = "OK"

    def bump(lv):
        nonlocal level
        order = {"OK": 0, "WARN": 1, "FAIL": 2}
        if order[lv] > order[level]:
            level = lv

    # A 时长合规（引用 shot_plan 同一套阈值）
    lvl, msg = sp.judge(d, kind, bool(motion), sp.is_declaration(txt))
    if lvl != "OK":
        bump("FAIL" if lvl == "FAIL" else "WARN")
        issues.append(("时长", msg))

    # B 素材存在性
    if src and not str(src).startswith("NEW:") and not os.path.exists(src):
        bump("FAIL")
        issues.append(("素材", "文件不存在，渲染必报错"))
    # C 一镜一图（相邻重复 / 隔一镜重复）
    if i > 0 and eff_src(shots[i - 1][0]) == src and src:
        bump("FAIL")
        issues.append(("重复", "与上一镜同一张图（连续同图=单调，返工实锤）"))
    elif i > 1 and eff_src(shots[i - 2][0]) == src and src:
        bump("WARN")
        issues.append(("重复", "与隔一镜同图，建议换位或换图"))
    # D 他品污染
    #   D1 文件名启发式（只对「文件名字里带品牌」的老式命名有效）
    for b in other_brands(brand):
        if b in pname(src) and brand and b not in brand:
            bump("FAIL")
            issues.append(("他品", f"文件名含「{b}」，疑似他品入镜（必须 Read 目视复核）"))
    #   D2 ★台账精确核验（2026-09-29 新增，补 D1 的洞）
    #      `产品_六瓶配日历_中景.png` 这类中性名 D1 永远命中不了，
    #      只有查 `_素材品牌台账.json` 才知道画面里到底是哪个牌子。
    if src:
        _e = _LEDGER_ITEMS.get(os.path.basename(src))
        if _e:
            _eb = str(_e.get("brand", "") or "")
            if brand and _eb.startswith(brand):
                pass                                    # 本品，放行
            elif any(o in _eb for o in other_brands(brand)):
                bump("FAIL")
                issues.append(("他品", f"⛔台账判定「{_eb}」／{_e.get('product', '')}／"
                                      f"conf={_e.get('confidence', '')} —— 他品入镜，必须换图"))
            elif (not _eb) or ("待目视" in _eb) or ("其他文字" in _eb):
                bump("WARN")
                issues.append(("未定", f"台账为「{_eb or '无记录'}」（OCR 读不出）"
                                      f"—— 含瓶罐的图必须目视复核"))
    #   D3 若台账里查得到该素材、且我们没配图片，直接提示
    if src and not os.path.exists(src):
        bump("FAIL")
        issues.append(("缺失", "素材文件不存在"))
    # E 产品镜：文案点到品牌/品类，画面应有产品
    prod_words = [w for w in ([brand] if brand else []) if w and w in txt]
    if prod_words and "产品" not in pname(src) and "展示" not in pname(src):
        bump("WARN")
        issues.append(("产品", f"文案点到「{prod_words[0]}」，但素材名不像产品合成图（§4：品牌镜必须有产品）"))
    # F 重点字规则（#42：黑板宣言镜及之前禁重点字；KW 大字避主体）
    #   ★豁免（2026-09-27 nt13 灿爹口谕，2026-09-29 补进校验器）：用户显式要求
    #   「片头/钩子镜加人群筛选钩大字」时照加 —— keywords 该项写 "exempt": true
    #   （或 "_exempt"）即豁免 FAIL，改成一条说明性提示。默认仍禁。
    decl_idx = next((k for k, q in enumerate(shots) if sp.is_declaration(q[0]["text"])), None)
    kws = [k for k in (cfg.get("keywords") or []) if k.get("phrase") == i + 1]
    kw_exempt = [k for k in kws if k.get("exempt") or k.get("_exempt")]
    kw_normal = [k for k in kws if k not in kw_exempt]
    if decl_idx is not None and i <= decl_idx and kw_normal:
        bump("FAIL")
        issues.append(("重点字", "黑板宣言镜及之前禁止重点字（#42）"))
    if decl_idx is not None and i <= decl_idx and kw_exempt:
        issues.append(("重点字豁免", "片头人群筛选钩大字（#42 豁免）：用户点名要求，豁免「宣言镜前禁重点字」"))
    for k in kws:
        if not k.get("pos"):
            bump("WARN")
            issues.append(("重点字", f"「{k.get('kw','')}」未指定 pos，大字可能压在主体上（#42 需钉侧边空白带）"))
    # G 静图超长无动效（judge 已判，这里补一句可执行建议）
    if lvl != "OK" and "偏长" in msg and not motion:
        issues.append(("动效", "建议加 motion 缓动（0 积分）或拆镜"))
    if not issues:
        issues.append(("—", "时长/来源/重复/产品/重点字 全部通过（构图与他品仍需人工目视）"))
    return level, issues


# ---------------- 主流程 ----------------
def build_rows(cfg_path, brand="", desc_map=None):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    shots, warn = sp.build_timeline(cfg)
    if warn:
        print("[警告]", warn)
    rows = []
    for i, (p, s, e) in enumerate(shots):
        src = eff_src(p)
        kind = (p.get("kind") or "img").lower()
        motion = p.get("motion") or ""
        flip = bool(p.get("flip"))
        d = e - s
        mkind, mcode = material_kind(src, kind)
        desc = (desc_map or {}).get(str(i + 1)) or (desc_map or {}).get(i + 1) \
            or derive_desc(src, kind, motion, flip)
        level, issues = self_check(i, shots, cfg, brand)
        rows.append({
            "n": i + 1,
            "start": round(s, 2), "end": round(e, 2), "dur": round(d, 2),
            "text": p.get("text", ""),
            "desc": desc,
            "kind": kind,
            "motion": MOTION_CN.get(motion, motion) or SHOT_MOTION.get(shot_scale(src), ""),
            "src": src, "src_name": pname(src),
            "mkind": mkind, "mcode": mcode,
            "level": level,
            "issues": issues,
            "kw": "、".join(k.get("kw", "") for k in (cfg.get("keywords") or [])
                            if k.get("phrase") == i + 1),
        })
    return cfg, rows


def print_md(cfg, rows, brand=""):
    n_lib = sum(1 for r in rows if r["mcode"] == "LIB")
    n_new = sum(1 for r in rows if r["mcode"] == "NEW")
    n_real = sum(1 for r in rows if r["mcode"] == "REAL")
    n_fail = sum(1 for r in rows if r["level"] == "FAIL")
    n_warn = sum(1 for r in rows if r["level"] == "WARN")
    print(f"\n【分镜—文案—素材对应表】共 {len(rows)} 镜｜"
          f"图库复用 {n_lib} ／ 实拍 {n_real} ／ 待补生成 {n_new}｜"
          f"❌{n_fail} ⚠️{n_warn}")
    print("| 镜 | 时间 | 时长 | 文案原文 | 画面描述 | 素材来源 | 素材文件 | 自检 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["level"]]
        why = "；".join(f"{a}:{b}" for a, b in r["issues"] if a != "—")
        print(f"| {r['n']} | {r['start']:.2f}-{r['end']:.2f} | {r['dur']:.2f}s | {r['text']} "
              f"| {r['desc']} | {r['mkind']} | `{r['src_name']}` | {mark}{why} |")
    print()


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["镜号", "起", "止", "时长", "文案原文", "画面描述", "镜头运动",
                    "素材来源", "素材文件", "自检", "自检说明", "重点字", "决策", "批注"])
        for r in rows:
            mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["level"]]
            why = "；".join(f"{a}:{b}" for a, b in r["issues"] if a != "—")
            w.writerow([r["n"], r["start"], r["end"], r["dur"], r["text"], r["desc"],
                        r["motion"], r["mkind"], r["src_name"], mark, why, r["kw"], "", ""])
    print("CSV →", path)


# ---------------- 批注回填 ----------------
def apply_annotations(cfg_path, ann_path, brand=""):
    cfg, rows = build_rows(cfg_path, brand)
    ann = json.load(open(ann_path, encoding="utf-8"))
    mode = (ann.get("mode") or "") if isinstance(ann, dict) else ""
    cf_at = ann.get("confirmed_at") if isinstance(ann, dict) else None
    ed_shots = (ann.get("edited_shots") or []) if isinstance(ann, dict) else []
    if mode:
        label = {"all_confirm": "★ 整案全部确认（未逐镜修改）",
                 "all_confirm_with_edits": "★ 整案已确认（含个别镜修改）",
                 "per_shot": "逐镜批注（未整案确认）"}.get(mode, mode)
        print("\n===== 确认方式 =====")
        print(" ", label, ("｜确认时间 " + str(cf_at)) if cf_at else "")
        if ed_shots:
            print("  用户点名修改的镜：", "、".join(str(x) for x in ed_shots),
                  "→ 只复核这几镜，其余按原方案继续")
        elif mode == "all_confirm":
            print("  → 无逐镜修改，可直接执行到底（生图→写 config→渲染→质检→交付）")
    items = ann.get("rows", ann) if isinstance(ann, dict) else ann
    todo = {"换图": [], "新生成": [], "改描述": [], "合并": [], "删除": [], "通过": [], "批注": []}
    for it in items:
        n = it.get("n")
        st = (it.get("status") or "").strip()
        note = (it.get("note") or "").strip()
        row = next((r for r in rows if r["n"] == n), None)
        if not row:
            continue
        if st.startswith("🔄") or "换" in st:
            todo["换图"].append((n, row["text"], row["src_name"], note))
        elif st.startswith("🆕") or "新" in st:
            todo["新生成"].append((n, row["text"], note))
        elif st.startswith("✏️") or "描述" in st:
            todo["改描述"].append((n, note))
        elif st.startswith("➕") or "合并" in st:
            todo["合并"].append((n, note))
        elif st.startswith("❌") or "删" in st:
            todo["删除"].append((n, row["text"], note))
        else:
            todo["通过"].append(n)
        if note:
            todo["批注"].append((n, st, note))
    print("\n===== 批注回填：待执行变更清单 =====")
    for k, v in todo.items():
        if not v:
            continue
        print(f"\n[{k}] {len(v)} 条")
        for x in v:
            print("   ", x)
    blocked = len(todo["换图"]) + len(todo["新生成"]) + len(todo["合并"]) + len(todo["删除"]) + len(todo["改描述"])
    if blocked == 0:
        print("\n结论：全部通过 → 可以一次性执行到底（生图→写 config→渲染→质检→交付）"
              + ("（整案确认，无改动）" if mode.startswith("all_confirm") else ""))
    else:
        print(f"\n结论：还有 {blocked} 条待改 → 只复核这些镜，其余按原方案继续，"
              f"不必整表重确认")
    return todo


# ---------------- HTML ----------------
HTML_TPL = r"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>__TITLE__</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;--txt:#1c1f23;--sub:#6b7280;--acc:#2563eb;--ok:#16a34a;--warn:#d97706;--fail:#dc2626}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:14px/1.7 -apple-system,"PingFang SC","Microsoft YaHei",sans-serif}
.wrap{max-width:1500px;margin:0 auto;padding:28px 22px 80px}
h1{font-size:22px;margin:0 0 6px}
h2{font-size:18px;margin:34px 0 12px;padding-left:10px;border-left:4px solid var(--acc)}
h3{font-size:15px;margin:18px 0 8px;color:var(--sub)}
.meta{color:var(--sub);font-size:13px;margin-bottom:8px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 18px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;font-size:13px;background:var(--card)}
th,td{border:1px solid var(--line);padding:7px 9px;vertical-align:top;text-align:left}
th{background:#eef2f7;font-weight:600;white-space:nowrap}
tr:nth-child(even) td{background:#fafbfc}
.tag{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;white-space:nowrap}
.t-ok{background:#e7f7ec;color:var(--ok)}.t-warn{background:#fdf3e3;color:var(--warn)}
.t-fail{background:#fdeaea;color:var(--fail)}.t-lib{background:#e8f0fe;color:#1a56db}
.t-new{background:#f3e8ff;color:#7c3aed}.t-real{background:#e6f6f6;color:#0f766e}
.bar{position:sticky;top:0;z-index:9;background:#fff;border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-bottom:14px;display:flex;gap:10px;align-items:center;flex-wrap:wrap;box-shadow:0 2px 8px rgba(0,0,0,.04)}
button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:7px 13px;cursor:pointer;font-size:13px}
button.ghost{background:#fff;color:var(--acc);border:1px solid var(--acc)}
button:hover{opacity:.88}
/* ---------- 「全部确认」主按钮 + 确认横幅（2026-09-27） ---------- */
button.primary{background:var(--ok);font-weight:600;padding:8px 16px;box-shadow:0 2px 6px rgba(22,163,74,.28)}
button.primary:hover{opacity:.92}
.cfbar{display:none;align-items:flex-start;gap:10px;background:#e7f7ec;border:1px solid #a7e0bb;color:#15803d;
       border-radius:10px;padding:10px 14px;margin:-6px 0 14px;font-size:13px;line-height:1.65}
.cfbar.on{display:flex;animation:sbUp .36s cubic-bezier(.22,.9,.3,1) both}
.cfbar .cfdot{font-size:15px;line-height:1.35;flex:none}
.cfbar .cfbody{flex:1;min-width:0}
.cfbar b{font-weight:700}
.cfbar button{margin-left:auto;flex:none;background:#fff;color:inherit;border:1px solid currentColor;
              font-size:12px;padding:5px 10px;opacity:.8}
.cfbar.edited{background:#fdf3e3;border-color:#f0c98a;color:#b45309}
.cf-hint{background:#eef4ff;border:1px dashed #b9cdf7;color:#1e40af;border-radius:8px;
         padding:9px 12px;font-size:12.5px;margin:0 0 14px;line-height:1.6}
.stat{font-size:13px;color:var(--sub)}
select,textarea{width:100%;border:1px solid var(--line);border-radius:6px;padding:5px 7px;font:13px/1.5 inherit;background:#fff;color:var(--txt)}
textarea{min-height:46px;resize:vertical}
td.num{white-space:nowrap;font-variant-numeric:tabular-nums}
.small{font-size:12px;color:var(--sub)}
code{background:#f1f3f5;padding:1px 5px;border-radius:4px;font-size:12px}
ol,ul{margin:8px 0 8px 20px;padding:0}
.pill{display:inline-block;background:#eef2f7;border-radius:6px;padding:2px 8px;margin:2px 4px 2px 0;font-size:12px}

/* ---------- 左侧信息栏（桌面固定侧栏 / 移动端顶部信息区） ---------- */
:root{--sbw:300px}
.layout{display:grid;grid-template-columns:var(--sbw) minmax(0,1fr);gap:18px;align-items:start;
        transition:grid-template-columns .32s cubic-bezier(.4,0,.2,1)}
.side{position:sticky;top:14px;max-height:calc(100vh - 28px);overflow-y:auto;overscroll-behavior:contain;
      animation:sbIn .48s cubic-bezier(.22,.9,.3,1) both}
.side::-webkit-scrollbar{width:6px}.side::-webkit-scrollbar-thumb{background:#d7dbe0;border-radius:3px}
@keyframes sbIn{from{opacity:0;transform:translateX(-18px)}to{opacity:1;transform:none}}
@keyframes sbUp{from{opacity:0;transform:translateY(-8px)}to{opacity:1;transform:none}}
.sb-card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:13px 15px;margin-bottom:12px;
         animation:sbUp .42s cubic-bezier(.22,.9,.3,1) both}
.side .sb-card:nth-child(1){animation-delay:.06s}
.side .sb-card:nth-child(2){animation-delay:.12s}
.side .sb-card:nth-child(3){animation-delay:.18s}
.side .sb-card:nth-child(4){animation-delay:.24s}
.sb-card h3{margin:0 0 8px;font-size:13px;color:var(--txt);letter-spacing:.02em}
.sb-row{display:flex;justify-content:space-between;gap:8px;font-size:12.5px;padding:3px 0;color:var(--sub)}
.sb-row b{color:var(--txt);font-weight:600;font-variant-numeric:tabular-nums}
.sb-bar{height:6px;background:#eef2f7;border-radius:4px;overflow:hidden;margin:7px 0 5px}
.sb-bar i{display:block;height:100%;background:var(--ok);border-radius:4px;
          transform-origin:left center;transform:scaleX(0);transition:transform .35s cubic-bezier(.4,0,.2,1)}
.sb-nav a{display:block;font-size:12.5px;color:var(--sub);text-decoration:none;padding:5px 8px;border-radius:6px;
          border-left:2px solid transparent;transition:transform .18s ease,color .18s ease,background .18s ease,border-color .18s ease}
.sb-nav a:hover{color:var(--acc);background:#f2f6fd;border-left-color:var(--acc);transform:translateX(3px)}
.sb-toggle{width:100%;margin-top:2px;background:#fff;color:var(--sub);border:1px solid var(--line);font-size:12px;padding:6px}
.sb-fab{position:fixed;left:14px;top:14px;z-index:20;display:none;font-size:12px;padding:7px 11px;box-shadow:0 3px 10px rgba(0,0,0,.12)}
body.sb-off .layout{grid-template-columns:0px minmax(0,1fr)}
body.sb-off .side{opacity:0;transform:translateX(-18px);pointer-events:none;
                  transition:opacity .28s ease,transform .28s cubic-bezier(.4,0,.2,1)}
body.sb-off .sb-fab{display:inline-block;animation:sbUp .3s ease both}
.sb-head{display:none}
@media (max-width:980px){
  .wrap{padding:20px 14px 60px}
  .layout{grid-template-columns:minmax(0,1fr);gap:12px}
  .side{position:static;max-height:none;overflow:visible;animation:sbIn .4s ease both}
  .side .side-inner{overflow:hidden;transition:max-height .34s cubic-bezier(.4,0,.2,1),opacity .28s ease;max-height:2200px;opacity:1}
  body.sb-off .side{opacity:1;transform:none;pointer-events:auto}
  body.sb-off .side .side-inner{max-height:0;opacity:0}
  body.sb-off .layout{grid-template-columns:minmax(0,1fr)}
  .sb-head{display:flex;align-items:center;justify-content:space-between;background:var(--card);
           border:1px solid var(--line);border-radius:10px;padding:11px 14px;margin-bottom:10px;cursor:pointer;font-size:13px;font-weight:600}
  .sb-head span{font-size:12px;color:var(--sub);font-weight:400;transition:transform .3s ease}
  body.sb-off .sb-head span{transform:rotate(-90deg)}
  .sb-fab{display:none !important}
}
@media (prefers-reduced-motion:reduce){
  .side,.sb-card,.sb-fab{animation:none !important}
  .layout,.side,.side-inner,.sb-bar i,.sb-nav a,.sb-head span{transition:none !important}
  .sb-nav a:hover{transform:none}
}
</style></head><body><div class="wrap">
<h1>__TITLE__</h1>
<div class="meta">工程 __CFG__ ｜ 生成时间 __TIME__ ｜ 主推品牌 __BRAND__ ｜ 共 __N__ 镜 ｜ 口播 __DUR__s</div>

<div class="layout">
<aside class="side">
  <div class="sb-head" onclick="toggleSb()">本片信息速览 <span>▾</span></div>
  <div class="side-inner">

    <div class="sb-card">
      <h3>本片速览</h3>
      <div class="sb-row"><span>主推品牌</span><b>__BRAND__</b></div>
      <div class="sb-row"><span>分镜数</span><b>__N__ 镜</b></div>
      <div class="sb-row"><span>口播时长</span><b>__DUR__s</b></div>
      <div class="sb-row"><span>平均镜长</span><b>__AVG__s</b></div>
      <div class="sb-row"><span>生成时间</span><b>__TIME__</b></div>
    </div>

    <div class="sb-card">
      <h3>素材构成</h3>
      <div class="sb-row"><span>图库复用（0 积分）</span><b>__SB_LIB__</b></div>
      <div class="sb-row"><span>实拍抽帧（0 积分）</span><b>__SB_REAL__</b></div>
      <div class="sb-row"><span>待补生成</span><b style="color:var(--warn)">__SB_NEW__</b></div>
      <div class="sb-bar"><i id="sbLibBar" style="transform:scaleX(__LIBR__)"></i></div>
      <div class="small">图库复用率 __LIBR_PCT__%</div>
    </div>

    <div class="sb-card">
      <h3>自检结果</h3>
      <div class="sb-row"><span>✅ 通过</span><b style="color:var(--ok)">__SB_OK__</b></div>
      <div class="sb-row"><span>⚠️ 需处置</span><b style="color:var(--warn)">__SB_WARN__</b></div>
      <div class="sb-row"><span>❌ 必改</span><b style="color:var(--fail)">__SB_FAIL__</b></div>
    </div>

    <div class="sb-card">
      <h3>批注进度</h3>
      <div class="sb-bar"><i id="sbProg" style="transform:scaleX(0)"></i></div>
      <div class="sb-row"><span>已通过</span><b id="sbP" style="color:var(--ok)">0</b></div>
      <div class="sb-row"><span>待改</span><b id="sbW" style="color:var(--warn)">0</b></div>
      <div class="sb-row"><span>未表态（按通过处理）</span><b id="sbF" style="color:var(--sub)">__N__</b></div>
      <div class="sb-row"><span>确认方式</span><b id="sbMode" style="color:var(--sub)">逐镜批注中</b></div>
      <button class="primary" style="width:100%;margin-top:8px" onclick="confirmAll()">✅ 全部确认</button>
    </div>

    <div class="sb-card">
      <h3>快速跳转</h3>
      <div class="sb-nav">
        <a href="#s0">怎么用这张表</a>
        <a href="#s1">一、现有流程与问题点</a>
        <a href="#s2">二、优化后流程</a>
        <a href="#s3">三、每轮确认输入输出</a>
        <a href="#s4">四、对应表（可批注）</a>
        <a href="#s5">五、图例与后续</a>
      </div>
    </div>

    <button class="sb-toggle" onclick="toggleSb()">收起侧栏</button>
  </div>
</aside>
<main>
<div class="card" id="s0" style="border-left:4px solid var(--acc)">
<b>这张表怎么用（一次性集中确认 · 两种走法二选一）</b>
<div class="cf-hint">
<b>走法 A｜全部确认（推荐，最省事）</b>：点上方绿色按钮 <b>「✅ 全部确认，直接执行」</b> —— 无需逐镜看，整案按当前方案放行，我立刻推进后续流程（生图→写 config→渲染→质检→交付），中途不再打断你。<br>
<b>走法 B｜只挑有问题的改</b>：保留其余镜为「通过」，只对你要动的镜下拉选决策 + 写批注 —— <b>没表态的镜按「通过」处理</b>，同样不用全部走一遍。改完点「全部确认」或直接把摘要发我，我只复核你改动的那几镜，然后继续。
</div>
<ol>
<li><b>你现在看到的是「生成前」的表</b>——图还没生、片还没渲。逐镜看：文案原文 → 画面描述 → 素材 → 自检结论。</li>
<li>每行右侧选一个决策（✅通过 / 🔄换图 / 🆕新生成 / ✏️改画面描述 / ➕合并相邻镜 / ❌删镜），批注里写具体要求（例：「换小绿人指骨头的图」「这两镜合并」）。</li>
<li>点「全部确认」后会自动把确认凭证据复制到剪贴板、并导出 <code>annotations.json</code>；也可以直接在这里回我一句「全部确认」。</li>
<li><b>确认后才开工</b>：补生图 → 写 config → 渲染 → 质检 → 归档，中途不再打断你。</li>
</ol>
<span class="small">画面描述由素材文件名自动派生（人物/类别·内容/镜头运动），可用 <code>--desc 描述.json</code> 人工覆盖；构图、五官、他品入镜三项脚本判不了，由我 Read 目视后填进自检说明。</span>
<div class="small" style="margin-top:6px">__NOTE__</div>
</div>

<h2 id="s1">一、现有流程与问题点</h2>
<div class="card">
<table>
<tr><th>#</th><th>环节</th><th>产物</th><th>依赖</th><th>当前瓶颈 / 问题点</th><th>返工代价</th></tr>
<tr><td>0</td><td>合规扫描</td><td>红线改写清单</td><td>文案</td><td>✅ 已有闸门，无需改</td><td>低</td></tr>
<tr><td>0.1</td><td>错别字/语义核验</td><td>疑点选项</td><td>文案</td><td>✅ 已有闸门（首句漏「了」后建立）</td><td>低</td></tr>
<tr><td>1</td><td>产品图定位</td><td>产品白底图</td><td>品牌名</td><td>偶发多产品线选错；需逐张目视</td><td>中</td></tr>
<tr><td>2</td><td>配音 + 转写对齐</td><td>vo.mp3 / vo_fixed.json</td><td>文案</td><td>CPU 约 7 分钟（机器时间，不等人）；数字读法需二次修正</td><td>中（改文案=重跑）</td></tr>
<tr><td>3</td><td><b>分镜规划</b></td><td>镜数 + 时长分配</td><td>逐句时长</td><td>⚠️ 镜数配额已有脚本；但<b>配图在规划后直接落地</b>，缺少「先给人看」的环节</td><td><b>高</b></td></tr>
<tr><td>4</td><td><b>配图 / 生图</b></td><td>分镜图</td><td>分镜 + 图库</td><td>❌ <b>最大瓶颈</b>：语义对不上、连续同图、他品入镜、五官长出、构图出框 —— 全部在成片后才被发现</td><td><b>最高（烧积分 + 重渲）</b></td></tr>
<tr><td>5</td><td>写 config</td><td>config.json</td><td>素材 + 时间轴</td><td>重点字/卡片落位靠人工，易漏</td><td>中</td></tr>
<tr><td>6</td><td>渲染 + 质检</td><td>成片 mp4</td><td>config</td><td>渲染 40–48s 不慢；慢的是<b>渲染后逐镜发现素材问题→回头改图→再渲</b></td><td>高</td></tr>
<tr><td>7</td><td>交付/定版</td><td>标题+话题</td><td>成片</td><td>⚠️ 用户在此才第一次看到完整画面，前面所有判断都是我单方面做的</td><td><b>高（一轮一轮返工）</b></td></tr>
</table>
<p class="small">根因归纳：<b>① 素材决策没有「事前可视化」</b>——图选得对不对，只有成片出来才看得见；<b>② 确认点碎</b>（合规、积分、换图、配音读法…分散在全流程，打断 5–8 次）；<b>③ 图库复用靠我在脑子里匹配</b>，没有摊开给用户核。</p>
</div>

<h2 id="s2">二、优化后流程（一次集中确认 → 一次执行到底）</h2>
<div class="card">
<table>
<tr><th>阶段</th><th>做什么</th><th>输入</th><th>输出</th><th>要不要确认</th></tr>
<tr><td>P0 文案闸门</td><td>合规扫描 + 错别字/语义核验</td><td>用户文案</td><td>红线清单 / 疑点选项</td><td>🟡 <b>仅当有问题时</b>（无问题直接过）</td></tr>
<tr><td>P1 配音对齐</td><td>CosyVoice3 合成 → whisper → align_vo → 逐句时长</td><td>定稿文案</td><td>vo.mp3、vo_fixed.json、逐句时长表</td><td>❌ 不打断（数字中文读法按铁律自动处理）</td></tr>
<tr><td>P2 分镜+配图规划</td><td>按时长配额定镜数 → <b>全库语义匹配</b> → 复用/实拍/补生分类 → 自动自检</td><td>逐句时长 + 图库清单</td><td><b>《分镜—文案—素材对应表》</b>（本页）</td><td>❌ 出表不打断</td></tr>
<tr><td>P3 <b>集中确认</b></td><td>用户逐镜批注（通过/换图/新生成/合并/删），可多轮改到通过</td><td>对应表</td><td>批注 JSON / 变更清单</td><td>🔴 <b>唯一必经确认点</b>（含积分预估一起批）</td></tr>
<tr><td>P4 一次执行到底</td><td>补生图（并行）→ P 图修复 → 写 config → 渲染 → 质检 → 归档</td><td>通过的对应表</td><td>成片 mp4 + 质检报告</td><td>❌ 全程不打断</td></tr>
<tr><td>P5 验收</td><td>成片 + 标题话题 + 图库扩充建议</td><td>成片</td><td>定版 / 微调清单</td><td>🟢 只做验收，不重复决策</td></tr>
</table>
<h3>确认点从「5–8 次碎片打断」压到「1 次集中 + 1 次验收」</h3>
<p class="small">原流程里分散的：合规、错别字、积分、换图建议、配音读法、重点字位置 —— 前四者并入 P0/P3 一次说清，后两者按既有铁律自动处理，不再单独发问。</p>
</div>

<h2 id="s3">三、每一轮确认的输入与输出</h2>
<div class="card">
<table>
<tr><th>轮次</th><th>触发条件</th><th>我给你的（输入）</th><th>你给我的（输出）</th><th>通过标准</th></tr>
<tr><td>第 0 轮<br>文案核验</td><td>发现错别字/语义不通/合规红线</td><td>原句 + 我的 N 个改法选项</td><td>选一个改法（或「不改」）</td><td>文案定稿，无红线</td></tr>
<tr><td><b>第 1 轮<br>对应表集中确认</b></td><td>每次出片必经</td><td>本页对应表（含画面描述、素材、自检结论、积分预估）</td><td>逐镜批注：<b>通过 / 换图 / 新生成 / 合并 / 删除</b> + 文字意见</td><td>❌ 清零、⚠️ 有处置说明、补生图积分已点头</td></tr>
<tr><td>第 2…N 轮<br>修订复审</td><td>上一轮有未通过项</td><td>只重出「被改动的那几镜」+ 改动说明</td><td>对改动项再批注</td><td>同上，可循环到全通过</td></tr>
<tr><td>最后 1 轮<br>成片验收</td><td>成片渲染完成</td><td>成片 + 质检结论 + 标题话题</td><td>「定版」或「微调清单」</td><td>定版即归档</td></tr>
</table>
<p class="small">硬规矩：<b>P3 未通过前，一张图都不生成、一次渲染都不跑</b>；P3 通过后，中途不再打断，直到成片交付。</p>
</div>

<h2 id="s4">四、分镜 — 文案 — 素材 对应表（可批注）</h2>
<div class="bar">
  <b>批注区</b>
  <span class="stat" id="stat"></span>
  <button class="primary" onclick="confirmAll()">✅ 全部确认，直接执行</button>
  <button class="ghost" onclick="allPass()">仅批量打勾</button>
  <button class="ghost" onclick="save()">保存批注</button>
  <button class="ghost" onclick="expJSON()">导出批注 JSON</button>
  <button class="ghost" onclick="copyMD()">复制 Markdown 表</button>
  <button class="ghost" onclick="summary()">生成确认摘要</button>
  <span class="small" id="tip">点「全部确认」＝整案放行；只改有问题的镜也可以，未表态按「通过」处理</span>
</div>
<div class="cfbar" id="cfbar"><span class="cfdot">✅</span><div class="cfbody" id="cfinfo"></div>
<button onclick="undoConfirm()">撤销确认</button></div>
<div class="card" style="padding:0;overflow-x:auto">
<table id="tb">
<tr><th>镜</th><th>时间/时长</th><th>文案原文</th><th>画面描述（人物/场景/动作/镜头运动）</th>
<th>素材来源</th><th>素材文件</th><th>匹配度自检</th><th>决策</th><th>我的批注</th></tr>
__ROWS__
</table></div>

<h2 id="s5">五、图例与后续</h2>
<div class="card">
<span class="pill">✅ 通过：时长/来源/重复/产品/重点字规则全过（构图与他品仍需我 Read 目视）</span>
<span class="pill">⚠️ 需处置：不阻断，但要在批注里给方向</span>
<span class="pill">❌ 必改：闪屏、连续同图、文件缺失、他品/重点字违规</span>
<span class="pill">素材来源：图库复用 0 积分 ／ 实拍 0 积分 ／ 待补生成 ≈5–10 积分/张</span>
<p class="small"><b>两种确认方式都算通过</b>：① 点「✅ 全部确认，直接执行」（整案放行，未表态按通过）；② 只改有问题的镜、其余不动（我会自动把未表态视为通过）。改完点「全部确认」或把确认摘要发我 → 我只复核被改动的镜，然后进入生图与渲染，<b>中途不再打断</b>。手工回填时跑 <code>storyboard_match.py --apply annotations.json</code> 出「待执行变更清单」。</p>
</div>
</main>
</div>
</div>
<button class="sb-fab" onclick="toggleSb()">☰ 信息</button>
<script>
const KEY='__KEY__';
const N=__N__;
let CF={on:false,at:''};            /* 整案确认状态：on=已「全部确认」 */
/* 有改动的镜：决策非「通过」或写了批注 → 只复核这几镜 */
function edited(){const a=[];document.querySelectorAll('tr[data-n]').forEach(tr=>{
 const s=tr.querySelector('.st').value,n=(tr.querySelector('.nt').value||'').trim();
 if((s&&s.indexOf('通过')<0)||n)a.push(+tr.dataset.n);});return a;}
function save(){const out=[];document.querySelectorAll('tr[data-n]').forEach(tr=>{
 out.push({n:+tr.dataset.n,status:tr.querySelector('.st').value,note:tr.querySelector('.nt').value});});
 localStorage.setItem(KEY,JSON.stringify({rows:out,cf:CF}));
 document.getElementById('tip').textContent='已保存 '+new Date().toLocaleTimeString();
 upd();}
function load(){try{const d=JSON.parse(localStorage.getItem(KEY)||'{}');(d.rows||[]).forEach(r=>{
 const tr=document.querySelector('tr[data-n="'+r.n+'"]');if(!tr)return;
 tr.querySelector('.st').value=r.status||'';tr.querySelector('.nt').value=r.note||'';});
 CF=d.cf||{on:false,at:''};}catch(e){}upd();}
/* 确认横幅：绿＝全案通过；橙＝已确认但个别镜有修改 */
function renderCf(){const bar=document.getElementById('cfbar'),info=document.getElementById('cfinfo'),
 sm=document.getElementById('sbMode');if(!bar)return;
 if(!CF.on){bar.classList.remove('on','edited');
   if(sm){sm.textContent='逐镜批注中';sm.style.color='var(--sub)';}return;}
 const ed=edited();
 bar.classList.add('on');bar.classList.toggle('edited',ed.length>0);
 if(sm){sm.textContent=ed.length?('已确认（含 '+ed.length+' 处修改）'):'全部确认（直接执行）';
        sm.style.color=ed.length?'var(--warn)':'var(--ok)';}
 info.innerHTML=ed.length
  ?'<b>整案已确认</b>（'+CF.at+'），其中镜 <b>'+ed.join('、')+'</b> 有修改 → 我只复核这几镜，其余按原方案继续'
  :'<b>整案全部确认</b>（'+CF.at+'）｜无逐镜修改 → 可直接推进后续流程：生图 → 写 config → 渲染 → 质检 → 交付';
}
function upd(){let p=0,w=0,f=0;document.querySelectorAll('tr[data-n]').forEach(tr=>{
 const s=tr.querySelector('.st').value;if(s.indexOf('通过')>=0)p++;else if(s.indexOf('换')>=0||s.indexOf('新')>=0||s.indexOf('合并')>=0||s.indexOf('删')>=0)w++;else f++;});
 document.getElementById('stat').innerHTML='共 '+(N)+' 镜 ｜ 已通过 <b style="color:var(--ok)">'+p+'</b> ｜ 待改 <b style="color:var(--warn)">'+w+'</b> ｜ 未表态 <b style="color:var(--sub)">'+f+'</b>';
 const sp=document.getElementById('sbP'),sw=document.getElementById('sbW'),sf=document.getElementById('sbF'),sg=document.getElementById('sbProg');
 if(sp){sp.textContent=p;sw.textContent=w;sf.textContent=f;sg.style.transform='scaleX('+(N?(p/N):0)+')';
   sg.style.background=(f===0&&w===0)?'var(--ok)':'var(--acc)';}
 renderCf();}
function toggleSb(){document.body.classList.toggle('sb-off');
 try{localStorage.setItem(KEY+'_sb',document.body.classList.contains('sb-off')?'1':'0');}catch(e){}}
/* 旧用法：只批量打勾，不落整案确认 */
function allPass(){document.querySelectorAll('tr[data-n] .st').forEach(s=>{if(!s.value)s.value='✅通过';});save();}
/* ★ 整案确认：未表态的镜自动填「通过」，已选的决策/批注一律保留 → 直接放行后续流程 */
function cfText(){const ed=edited();
 return '【对应表'+(ed.length?'确认（含修改）':'全部确认')+'】'+KEY+'｜共 '+(N)+' 镜｜'
  +(ed.length?('修改镜：'+ed.join('、')):'无逐镜修改')+'｜确认时间 '+CF.at+'｜请直接推进后续流程';}
function confirmAll(){
 document.querySelectorAll('tr[data-n] .st').forEach(s=>{if(!s.value)s.value='✅通过';});
 CF={on:true,at:new Date().toLocaleString('zh-CN',{hour12:false})};
 save();
 const ed=edited(),txt=cfText();
 let ok=false;try{navigator.clipboard.writeText(txt);ok=true;}catch(e){}
 try{expJSON();}catch(e){}
 document.getElementById('tip').textContent='已确认 · '+CF.at+(ed.length?(' · 修改镜 '+ed.join('、')):' · 全案通过');
 alert('✅ '+(ed.length?('整案已确认（含 '+ed.length+' 处修改）'):'整案全部确认')+'\n\n'+txt
  +'\n\n'+(ok?'确认凭证已复制到剪贴板 —— 直接粘贴发给我即可。':'（剪贴板不可用，请点「生成确认摘要」复制）')
  +'\n已同时导出 annotations.json。\n点上方横幅「撤销确认」可反悔。');
}
function undoConfirm(){CF={on:false,at:''};save();
 document.getElementById('tip').textContent='已撤销整案确认';}
function rows(){const out=[];document.querySelectorAll('tr[data-n]').forEach(tr=>{
 out.push({n:+tr.dataset.n,dur:tr.dataset.dur,text:tr.dataset.text,src:tr.dataset.src,status:tr.querySelector('.st').value,note:tr.querySelector('.nt').value});});
 return out;}
function expJSON(){const ed=edited();
 const payload={project:KEY,
   mode:CF.on?(ed.length?'all_confirm_with_edits':'all_confirm'):'per_shot',
   confirmed_at:CF.on?CF.at:null,edited_shots:ed,rows:rows()};
 const b=new Blob([JSON.stringify(payload,null,2)],{type:'application/json'});
 const a=document.createElement('a');a.href=URL.createObjectURL(b);a.download='annotations.json';a.click();}
function copyMD(){let s='| 镜 | 时长 | 文案 | 素材 | 决策 | 批注 |\n|---|---|---|---|---|---|\n';
 rows().forEach(r=>{s+='| '+r.n+' | '+r.dur+' | '+r.text+' | '+r.src+' | '+(r.status||'')+' | '+(r.note||'')+' |\n';});
 navigator.clipboard.writeText(s).then(()=>{document.getElementById('tip').textContent='Markdown 已复制';});}
function summary(){const r=rows();const bad=r.filter(x=>x.status&&x.status.indexOf('通过')<0);
 let s=(CF.on?('【整案已确认 · '+CF.at+'】'):'【对应表确认摘要】')+KEY+'\n共 '+r.length+' 镜｜通过 '+(r.length-bad.length)+' 镜｜待改 '+bad.length+' 镜（未表态按通过处理）\n';
 bad.forEach(x=>{s+='  镜'+x.n+'（'+x.text+'）→ '+(x.status||'未表态')+(x.note?'：'+x.note:'')+'\n';});
 navigator.clipboard.writeText(s).then(()=>{document.getElementById('tip').textContent='摘要已复制，直接发我';});
 alert(s);}
document.querySelectorAll('.st,.nt').forEach(e=>e.addEventListener('change',save));
(function(){var off=false;
 try{off=localStorage.getItem(KEY+'_sb')==='1';}
 catch(e){off=window.matchMedia('(max-width:980px)').matches;}
 if(off)document.body.classList.add('sb-off');})();
load();
</script></body></html>"""


def build_html(cfg, rows, cfg_path, brand, out_path, note=""):
    trs = []
    for r in rows:
        mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}[r["level"]]
        cls = {"OK": "t-ok", "WARN": "t-warn", "FAIL": "t-fail"}[r["level"]]
        mcls = {"LIB": "t-lib", "NEW": "t-new", "REAL": "t-real"}.get(r["mcode"], "t-lib")
        why = "".join(f"<div>{html.escape(a)}：{html.escape(b)}</div>" for a, b in r["issues"])
        trs.append(
            f'<tr data-n="{r["n"]}" data-text="{html.escape(r["text"])}" data-src="{html.escape(r["src_name"])}" data-dur="{r["dur"]}">'
            f'<td class="num"><b>{r["n"]}</b></td>'
            f'<td class="num">{r["start"]:.2f}–{r["end"]:.2f}<br><span class="small">{r["dur"]:.2f}s</span></td>'
            f'<td>{html.escape(r["text"])}</td>'
            f'<td>{html.escape(r["desc"])}<br><span class="small">镜头运动：{html.escape(r["motion"] or "—")}'
            f'{"｜重点字：" + html.escape(r["kw"]) if r["kw"] else ""}</span></td>'
            f'<td><span class="tag {mcls}">{html.escape(r["mkind"])}</span></td>'
            f'<td class="small">{html.escape(r["src_name"])}</td>'
            f'<td><span class="tag {cls}">{mark} {r["level"]}</span><div class="small">{why}</div></td>'
            f'<td><select class="st"><option value=""></option><option>✅通过</option><option>🔄换图</option>'
            f'<option>🆕新生成</option><option>✏️改画面描述</option><option>➕合并相邻镜</option><option>❌删镜</option></select></td>'
            f'<td><textarea class="nt" placeholder="例：换一张小绿人指骨头的图 / 这两镜合并 / 产品图要真人手持"></textarea></td>'
            f"</tr>")
    total = rows[-1]["end"] if rows else 0
    n = len(rows)
    avg = (total / n) if n else 0
    n_lib = sum(1 for r in rows if r.get("mcode") == "LIB")
    n_real = sum(1 for r in rows if r.get("mcode") == "REAL")
    n_new = sum(1 for r in rows if r.get("mcode") == "NEW")
    libr = (n_lib / n) if n else 0
    n_ok = sum(1 for r in rows if r["level"] == "OK")
    n_warn = sum(1 for r in rows if r["level"] == "WARN")
    n_fail = sum(1 for r in rows if r["level"] == "FAIL")
    key = os.path.basename(os.path.dirname(os.path.abspath(cfg_path))) + "_" + \
        os.path.basename(cfg_path).replace(".json", "")
    doc = (HTML_TPL
           .replace("__TITLE__", html.escape(f"分镜—文案—素材对应表｜{brand or '本项目'}"))
           .replace("__CFG__", html.escape(os.path.abspath(cfg_path)))
           .replace("__TIME__", datetime.now().strftime("%Y-%m-%d %H:%M"))
           .replace("__BRAND__", html.escape(brand or "—"))
           .replace("__N__", str(len(rows)))
           .replace("__DUR__", f"{total:.2f}")
           .replace("__ROWS__", "\n".join(trs))
           .replace("__NOTE__", html.escape(note))
           .replace("__AVG__", f"{avg:.2f}")
           .replace("__SB_LIB__", f"{n_lib} 镜")
           .replace("__SB_REAL__", f"{n_real} 镜")
           .replace("__SB_NEW__", f"{n_new} 镜（≈{n_new * 18} 积分）")
           .replace("__LIBR__", f"{libr:.3f}")
           .replace("__LIBR_PCT__", f"{libr * 100:.0f}")
           .replace("__SB_OK__", f"{n_ok} 镜")
           .replace("__SB_WARN__", f"{n_warn} 镜")
           .replace("__SB_FAIL__", f"{n_fail} 镜")
           .replace("__KEY__", key))
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(doc)
    print("HTML →", out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--brand", default="")
    ap.add_argument("--desc", default="")
    ap.add_argument("--md", action="store_true")
    ap.add_argument("--csv", default="")
    ap.add_argument("--html", default="")
    ap.add_argument("--apply", default="")
    ap.add_argument("--note", default="", help="印在表头下方的说明文字")
    a = ap.parse_args()

    if a.apply:
        apply_annotations(a.config, a.apply, a.brand)
        return

    desc_map = json.load(open(a.desc, encoding="utf-8")) if a.desc else None
    cfg, rows = build_rows(a.config, a.brand, desc_map)
    if a.md or not (a.csv or a.html):
        print_md(cfg, rows, a.brand)
    if a.csv:
        write_csv(rows, a.csv)
    if a.html:
        build_html(cfg, rows, a.config, a.brand, a.html, a.note)


if __name__ == "__main__":
    main()
