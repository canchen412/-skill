#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""卷播接入成片前的自动闸门（2026-10-07 定稿轮 7 条规则的**代码化**）。

用法：
    python scripts/opening_banner/precheck.py <config.json> [--hero <主图绝对路径>] [--workdir <dir>]

检查项（FAIL 必须改，WARN 需人工确认）：
  ①  卷播素材存在、有 aac 音轨（无音轨 ⇒ own_audio 空接、音效全丢）
  ②  卷播素材没有自带字幕轨（否则与渲染器全局字幕叠成双层）
  ③  cover_until == src[1]（否则轮播被截断或顶开下一镜）
  ④  opening_burst == false（轮播开头只留轮播音效）
  ⑤  trigger_sfx 不含卷播镜（钩子句已进卷播自带音轨）
  ⑥  keywords / transition_whoosh / swing_shots 无落在卷播段内的残留条目
  ⑦  product_cards：workdir/cards/cards.json 里已有圆角成品（不是裸原图）
  ⑧  主图没有被后续分镜复用（--hero 与 phrases[].src 比对）
  ⑨  字数守恒提示 + 「未顶开下一镜」自查命令（人工确认项）
"""
import json, os, subprocess, sys

FFPROBE = os.environ.get("FFPROBE", "ffprobe")


def probe(path):
    if not os.path.exists(path):
        return None
    p = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name", "-of", "json", path],
                       capture_output=True, text=True)
    try:
        return json.loads(p.stdout or "{}").get("streams", [])
    except Exception:
        return []


def main():
    cfg_path = sys.argv[1]
    hero = None
    workdir = None
    a = sys.argv[2:]
    for i, v in enumerate(a):
        if v == "--hero" and i + 1 < len(a):
            hero = a[i + 1]
        if v in ("--workdir", "-w") and i + 1 < len(a):
            workdir = a[i + 1]
    cfg = json.load(open(cfg_path))
    if workdir is None:
        workdir = os.path.dirname(os.path.abspath(cfg_path))

    phs = cfg.get("phrases", [])
    # 找卷播镜：有 video 且 own_audio
    i0 = None
    for i, p in enumerate(phs):
        if p.get("video") and p.get("own_audio"):
            i0 = i
            break
    if i0 is None:
        print("❌ 没找到卷播镜（phrases 里没有 video + own_audio 同时存在的条目）")
        return 1
    p0 = phs[i0]
    vid = p0["video"]
    src = p0.get("src", [0.0, 0.0])
    dur = float(src[1]) - float(src[0])

    fails, warns, oks = [], [], []
    print(f"卷播镜 = phrases[{i0}]   时长 {dur:.2f}s   {os.path.basename(vid)}")

    # ① ② 素材音轨 / 字幕轨
    st = probe(vid)
    if st is None:
        fails.append(f"① 卷播素材不存在：{vid}")
    else:
        has_a = any(s.get("codec_type") == "audio" for s in st)
        has_s = any(s.get("codec_type") == "subtitle" for s in st)
        (oks if has_a else fails).append(
            f"① 卷播音轨 {'存在（aac）' if has_a else '★缺失 ⇒ own_audio 空接、音效全丢'}")
        if has_s:
            fails.append("② 卷播素材带字幕轨 ⇒ 与渲染器全局字幕叠成双层（用 --no-sub 重渲）")
        else:
            oks.append("② 卷播素材无字幕轨（不会双层）")

    # ③ cover_until
    cu = float(p0.get("cover_until", 0) or 0)
    if abs(cu - float(src[1])) > 0.02:
        warns.append(f"③ cover_until {cu:.2f} ≠ src[1] {float(src[1]):.2f}（应相等，否则轮播被截/顶开）")
    else:
        oks.append(f"③ cover_until {cu:.2f} == src[1] ✓")

    # ④ opening_burst
    if cfg.get("opening_burst", True) is not False:
        fails.append("④ opening_burst 未关闭 ⇒ 开场冲击音效会盖掉卷播滚动音（应 false）")
    else:
        oks.append("④ opening_burst=false ✓")

    # ⑤ trigger_sfx
    tsfx = cfg.get("trigger_sfx", []) or []
    bad = [t for t in tsfx if isinstance(t, dict) and int(t.get("phrase", 0)) - 1 <= i0]
    if bad:
        fails.append(f"⑤ trigger_sfx 有落在卷播镜的条目 {bad} ⇒ 钩子句已进卷播音轨，外部不许再触发")
    else:
        oks.append(f"⑤ trigger_sfx 不含卷播镜 ✓（共 {len(tsfx)} 条）")

    # ⑥ keywords(1起始) / whoosh(0起始) / swing(0起始)
    kw_bad = [int(k["phrase"]) for k in cfg.get("keywords", [])
              if int(k.get("phrase", 0)) - 1 <= i0]
    wo_bad = [i for i in cfg.get("transition_whoosh", []) if i <= i0]
    sw_bad = [i for i in cfg.get("swing_shots", []) if i <= i0 or i < 0]
    if kw_bad:
        fails.append(f"⑥ keywords 落在卷播段内（phrase={sorted(set(kw_bad))}，1起始）⇒ 轮播内不许有大字/重点字音效")
    if wo_bad:
        fails.append(f"⑥ transition_whoosh 落在卷播段内 {wo_bad}（0起始）⇒ 剔除")
    if sw_bad:
        fails.append(f"⑥ swing_shots 非法 {sw_bad}（0起始，且不许为负）⇒ 剔除")
    if not (kw_bad or wo_bad or sw_bad):
        oks.append("⑥ keywords / whoosh / swing 均不落在卷播段内 ✓")

    # ⑦ product_cards 圆角成品
    pcs = cfg.get("product_cards", []) or []
    cj = os.path.join(workdir, "cards", "cards.json")
    have = {}
    if os.path.exists(cj):
        try:
            have = json.load(open(cj))
        except Exception:
            have = {}
    miss = [pc.get("src") for pc in pcs if pc.get("src") not in have]
    if pcs and miss:
        fails.append(f"⑦ {len(miss)} 张详情卡没有圆角成品（workdir={workdir}）⇒ 先跑 make_cards.py --style card")
    elif pcs:
        oks.append(f"⑦ {len(pcs)} 张详情卡均有圆角成品 ✓")
    else:
        warns.append("⑦ 本片没有 product_cards（确认是否漏配）")

    # ⑧ 主图复用
    if hero:
        hb = os.path.basename(hero)
        dup = [i for i, p in enumerate(phs)
               if i != i0 and p.get("src") and os.path.basename(str(p["src"])) == hb]
        if dup:
            fails.append(f"⑧ 主图 {hb} 被 phrases{dup} 复用 ⇒ 主图本身就是一个分镜，后面不许再用")
        else:
            oks.append(f"⑧ 主图 {hb} 未被后续分镜复用 ✓")
    else:
        warns.append("⑧ 未传 --hero ⇒ 无法查「主图被复用」（灿爹 2026-10-07 明确要求）")

    # ⑨ 人工项
    warns.append(f"⑨ 人工确认：渲染日志必须只有「未顶开下一镜」→ grep 顶开 <workdir>/filter_debug.txt")
    warns.append(f"⑨ 人工确认：卷播段配音字数守恒（合并几句后 text 必须逐字一致）")

    print("\n--- PASS ---")
    for s in oks:
        print("  ✅", s)
    if warns:
        print("\n--- WARN（人工确认）---")
        for s in warns:
            print("  ⚠️ ", s)
    if fails:
        print("\n--- FAIL（必须改）---")
        for s in fails:
            print("  ❌", s)
    print(f"\n结论：{'❌ 有 FAIL，不许渲染' if fails else '✅ 可渲染（WARN 需扫一眼）'}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
