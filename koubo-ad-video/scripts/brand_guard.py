#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""他品/本品 自动守门（★2026-09-29 建立，补「台账存在却没人查」的洞）

背景：`分镜图库/小绿人/_素材品牌台账.json`（268 条，带 brand/confidence/OCR 证据）
一直存在，但**选材和校验环节都没引用它**；`storyboard_match.py` 的他品检查是
「文件名子串匹配」，对 `产品_六瓶配日历_中景.png` 这种中性名完全无效 ⇒
实测漏进 21金维他 - 液体钙 / VORSE - 液体钙 到他品零容忍的片子里。

本脚本把台账接进流程，对 config 里每个素材逐张判定：
    本品   —— 台账 brand 以 --brand 开头（含「诺特兰德?」这类带问号的，标注为「本品(存疑)」）
    他品   —— 台账 brand 命中 --other 列表（默认覆盖常见他牌）
    未定   —— 待目视 / 其他文字 / 无品牌 / 台账无记录  ⇒ 必须人工目视
    干净   —— 明确标注「本司IP角色 / 无品牌 / 无品牌-成分表」等不含产品包装的图

补充判据（--color）：对「未定」里含瓶罐的图跑**纯白标签占比**检测。
    诺特兰德 = 粉红标签（白标签占比个位数以下）
    21金维他 = 纯白标签（白标签占比 ≈10%）
    实测：4 张 21金维他图 9.94%~11.60%，13 张诺特兰德图 0.13%~5.72% ⇒ 阈值 8% 可分。

用法:
    PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
    $PY brand_guard.py --config s1/config.json s2/config.json --brand 诺特兰德
    $PY brand_guard.py --config s1/config.json --color        # 附带颜色交叉验证
退出码：0 = 无他品；1 = 存在他品（可用于流水线卡口）
"""
import argparse
import glob
import json
import os
import sys

DEFAULT_LEDGER = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/_素材品牌台账.json"
DEFAULT_LIB = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"

# 常见他品品牌（按需扩展；★不要把这些写成「本品」——本品由 --brand 动态指定）
OTHER_BRANDS = ["21金维他", "VORSE", "爱兰纯", "米当卡", "三角喵", "英之素",
                "训练怪兽", "fohea", "燃燃", "拜拜丸", "肌酸"]

CLEAN_BRANDS = ("本司IP角色", "无品牌", "无品牌-成分表", "纯餐盘", "场景空镜")


def load_ledger(path):
    if not os.path.exists(path):
        sys.exit(f"❌ 台账不存在：{path}\n   请先跑 pool_ocr.py + 人工确认生成品牌台账")
    return json.load(open(path, encoding="utf-8"))


def white_label_ratio(path):
    """纯白标签像素占比（%）。21金维他≈10%，诺特兰德多在这个数以下。"""
    try:
        import numpy as np
        from PIL import Image
    except ImportError:
        return None
    im = Image.open(path).convert("RGB")
    a = np.asarray(im, dtype=np.float32)
    R, G, B = a[..., 0], a[..., 1], a[..., 2]
    white = ((R > 208) & (G > 208) & (B > 208) &
             (np.abs(R - B) < 14) & (np.abs(R - G) < 14))
    return float(white.mean() * 100)


def judge(name, entry, brand):
    """返回 (等级, 说明)"""
    # ① 文件名前缀硬拦（最外层保险：即使台账丢了也挡得住）
    #    `_他品污染隔离/` 里的图一律带 `污染_` 前缀，规范见 SKILL §0.5 规则③
    if name.startswith("污染_"):
        return "他品", f"⛔ 文件名带「污染_」前缀 —— 隔离区素材，永不复用（{name}）"
    if entry is None:
        return "未定", "台账无记录 → 必须目视确认"
    b = str(entry.get("brand", "") or "")
    conf = str(entry.get("confidence", "") or "")
    prod = str(entry.get("product", "") or "")

    # ② 台账里已被标记隔离的，直接按他品处理
    if entry.get("isolated"):
        return "他品", f"⛔ 台账已标 isolated（{b} / {prod}）—— 隔离区素材，永不复用"

    if any(c in b for c in CLEAN_BRANDS):
        return "干净", f"{b}（无产品包装）"
    if b.startswith(brand):
        if "?" in b:
            return "本品(存疑)", f"{b} / {prod} / conf={conf}（台账未完全确定，建议目视）"
        return "本品", f"{b} / {prod} / conf={conf}"
    for o in OTHER_BRANDS:
        if o in b:
            return "他品", f"⛔ {b} / {prod} / conf={conf}"
    if "待目视" in b:
        return "未定", f"{b} / {prod} —— OCR 读不出，含瓶罐必须目视"
    return "未定", f"{b} / {prod} / conf={conf}"


def resolve(lib, name, idx):
    """★2026-09-30：素材已分层到 {产品}/{品牌}/图片/，老写法 os.path.join(lib, name) 会找不到。
    优先用台账 path 字段，其次按品牌层/通用池/平铺层依次探测。"""
    ent = idx.get(name) or {}
    p = ent.get("path")
    if p:
        cand = os.path.join(lib, p)
        if os.path.exists(cand):
            return cand
    for sub in ("", "_通用池"):
        cand = os.path.join(lib, sub, name)
        if os.path.exists(cand):
            return cand
    for dp, dns, fns in os.walk(lib):
        dns[:] = [d for d in dns if not d.startswith(".")]
        if name in fns:
            return os.path.join(dp, name)
    return os.path.join(lib, name)


def path_judge(path, lib, scope):
    """★2026-09-30 新增「路径即隔离」判定（目录改造后的一等公民）。

    scope 形如 "烟酰胺/诺特兰德"；不给 scope 则退化为台账判定。
    五档结论：
      本品   —— 落在 {lib}/{scope}/图片/ 下（目录已保证是本品牌）
      干净   —— 落在 _通用池/ 下（零产品零品牌，任何项目可用）
      他品   —— 落在**别的** {产品}/{品牌}/图片/ 下 ⇒ 目录层面已天然隔离，无需关键词排除
      未定   —— 仍在平铺根目录（未定级）⇒ 必须目视
      越界   —— 落在库外（如项目分镜图目录）⇒ 不受图库品牌约束，按未定处理
    """
    if not scope:
        return None, ""
    ap_ = os.path.abspath(path)
    lib_ = os.path.abspath(lib)
    if not ap_.startswith(lib_ + os.sep):
        return "越界", f"不在图库内（图库外素材，按项目自有素材处理）"
    rel = os.path.relpath(ap_, lib_).replace(os.path.sep, "/")
    parts = rel.split("/")
    if parts[0] == "_通用池":
        return "干净", "跨品牌通用层（零产品零品牌，任何项目可用）"
    if len(parts) >= 3 and parts[2] == "图片":
        own = "/".join(parts[0:2])
        if own == scope:
            return "本品", f"路径即品牌：{own}/图片/（目录已保证，无需关键词排除）"
        return "他品", f"⛔ 落在别家品牌目录 {own}/图片/ —— 当前 scope={scope}，路径层面已隔离"
    if len(parts) <= 2:
        return "未定", f"平铺根目录、未归入品牌层 ⇒ 仍是未定级素材，必须目视（{rel}）"
    return "未定", f"未识别的层级 {rel} ⇒ 必须目视"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", nargs="+", required=True)
    ap.add_argument("--brand", default="诺特兰德", help="本品品牌前缀（按项目传）")
    ap.add_argument("--scope", default=None,
                    help="★目录改造后的一等参数：当前项目的「产品/品牌」路径，如 烟酰胺/诺特兰德。"
                         "给了它就启用「路径即隔离」，落在别家品牌目录的图直接判他品")
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--color", action="store_true", help="对未定项跑纯白标签颜色检测")
    ap.add_argument("--white-th", type=float, default=8.0, help="白标签占比报警阈值(%%)")
    a = ap.parse_args()

    led = load_ledger(a.ledger)
    idx = led["items"]
    print(f"台账：{a.ledger}")
    print(f"       {len(idx)} 条 ｜ 本品口径 = brand 以「{a.brand}」开头")
    if a.scope:
        print(f"       ★路径即隔离 scope = {a.scope}  （只认 {a.scope}/图片/ 与 _通用池/）")
    print(f"       台账生成时间 {led.get('generated', '—')}\n")

    n_bad = 0
    for cf in a.config:
        cfg = json.load(open(cf, encoding="utf-8"))
        print("=" * 78)
        print(f"config: {cf}   共 {len(cfg['phrases'])} 镜")
        print("=" * 78)
        buckets = {}
        for i, p in enumerate(cfg["phrases"], 1):
            # ★2026-09-30 修：视频镜（prod/imgvid）的 src 是 [起,止] 时间区间数组，
            #   真实文件路径在 video 字段 —— 旧代码直接 basename(src) 会 TypeError（与 #56 同源）。
            path = p.get("video") if p.get("kind") in ("prod", "imgvid") else p.get("src")
            if not isinstance(path, str):
                path = p.get("src") if isinstance(p.get("src"), str) else ""
            if not path:
                continue
            name = os.path.basename(path)
            real = resolve(a.lib, name, idx)
            plvl, pwhy = path_judge(real, a.lib, a.scope)
            if plvl in ("他品", "本品", "干净"):
                # 路径已定性：直接用路径结论，台账仅作附注
                ent = idx.get(name) or {}
                lvl, why = plvl, pwhy + (f" ｜ 台账 {ent.get('brand', '无记录')}/{ent.get('product', '—')}" if ent else "")
                if plvl == "本品" and ent and not str(ent.get("brand", "")).startswith(a.brand):
                    lvl, why = "本品(存疑)", why + f" ⚠️ 目录说本品、台账说 {ent.get('brand')} —— 以目视为准"
            else:
                lvl, why = judge(name, idx.get(name), a.brand)
                if pwhy:
                    why = f"{pwhy} ｜ {why}"
            buckets.setdefault(lvl, []).append((i, name, why))
            if lvl == "他品":
                n_bad += 1

        for lvl in ["他品", "本品(存疑)", "未定", "本品", "干净"]:
            rows = buckets.get(lvl, [])
            if not rows:
                continue
            mark = {"他品": "⛔❌", "本品(存疑)": "⚠️", "未定": "⚠️",
                    "本品": "✅", "干净": "✅"}[lvl]
            print(f"\n  {mark} {lvl}（{len(rows)} 张）")
            for i, name, why in rows:
                extra = ""
                if a.color and lvl in ("未定", "本品(存疑)") and \
                        any(t in name for t in ("产品_", "瓶", "罐", "六瓶", "胶囊")):
                    p = resolve(a.lib, name, idx)
                    if os.path.exists(p):
                        r = white_label_ratio(p)
                        if r is not None:
                            flag = "  ⛔纯白标签特征(疑21金维他)" if r >= a.white_th else ""
                            extra = f"   [白标签 {r:.2f}%]{flag}"
                            if r >= a.white_th:
                                n_bad += 1
                print(f"     镜{i:>2}  {name}{extra}")
                print(f"            {why}")

    print("\n" + "=" * 78)
    if n_bad:
        print(f"❌ 结论：发现 {n_bad} 处他品/疑他品 —— 必须换图后重渲，禁止交付")
        sys.exit(1)
    print("✅ 结论：本品口径下无他品；「未定」项仍需人工目视（尤其含瓶罐的图）")
    sys.exit(0)


if __name__ == "__main__":
    main()
