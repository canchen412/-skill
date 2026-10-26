#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分镜时长配额规划器 / 校验器  (v2 · 快切图镜版)
=================================================================
规则（2026-09-26 灿爹拍板；2026-09-27 修订为「图片镜可快切」）：

    **分镜数量由「该句配音时长」决定，不由固定条数决定。**

    ★ 图片镜与视频镜用**两套**阈值——图片可以切得快，视频不行（视频自带运动，
      切太短会晕）。这是一切判定的前提。

    图片镜（kind=img，含静图 + 缓动）
      IMG_MIN  1.0s = 绝对下限，<1.0s 才叫真闪屏 → 必须并入相邻镜
      IMG_CUT  1.2s = 多切门槛（配额分母）：一句最多几镜 = floor(句时长 / 1.2)
      IMG_MAX  3.2s = 软上限，超出建议拆（带 motion 放宽 +0.8 → 4.0s）
    视频镜（kind=prod/imgvid/video）
      VID_MIN  1.2s = 绝对下限
      VID_CUT  1.5s = 多切门槛 + 舒适下沿（1.2–1.5s 判「偏短」告警）
      VID_MAX  4.0s = 软上限

    配额表（图片镜口径）：
      <=2.3s → 1 镜        2.4-3.5s → 最多 2 镜      3.6-4.7s → 最多 3 镜
      4.8-9.5s → 最多 4 镜（N_CAP 封顶）            >9.5s → 拆句，不堆第 5 镜
    反向规则：单镜 <IMG_MIN(图片)/<VID_MIN(视频) 撑不起一镜 → 并入相邻镜；
              **1.0–1.2s 的图片快切镜是合法节奏**（判 OK，仅标注「快切」），
              **1.2s 以上的图片镜一律不再算「偏短」**——这是本版的核心放宽。
    特例：黑板宣言镜（「今天我们讲/说/聊，X」）独占 1 镜、不计配额（大字需完整停留）。

用法：
    python3 shot_plan.py --table                      # 打印配额表
    python3 shot_plan.py --config config_A.json       # 校验 config 分镜时长分布
    python3 shot_plan.py --config config_A.json --csv plan.csv
    python3 shot_plan.py --dur 2.0                    # 单句时长 → 建议镜数
"""
import argparse
import json
import math
import os
import sys

# ---------------- 规则常数（改这里即可全线生效） ----------------
# —— 图片镜 ——
IMG_MIN = 1.0         # 图片镜绝对下限：<此值 = 真闪屏（必改）
IMG_CUT = 1.2         # 图片镜多切门槛 = 配额分母（原 1.5 → 1.2，允许更快节奏）
IMG_FAST_HI = 1.2     # 图片快切区上沿：IMG_MIN–此值 = 合法快切（判 OK + 标注）
IMG_MAX = 3.2         # 图片镜软上限（超过建议拆；带 motion 放宽 MOTION_BONUS）
# —— 视频镜 ——
VID_MIN = 1.2         # 视频镜绝对下限：<此值 = 闪屏
VID_CUT = 1.5         # 视频镜多切门槛 + 舒适下沿（1.2–1.5s 判偏短）
VID_MAX = 4.0         # 视频镜软上限
# —— 通用 ——
MOTION_BONUS = 0.8    # 带缓动/动效的单镜可多撑 0.8s
N_CAP = 4             # 单句镜数封顶（原 3 → 4，配合图片快切）
SPLIT_OVER = 9.5      # 超过此句长 → 建议拆句（而不是堆第 5 镜）
IDEAL_SHOT = 2.2      # 理想单镜时长（用于推荐镜数，原 2.4 → 2.2 偏快）
DECL_MIN = 1.2        # 黑板宣言镜时间下限（大字停留）
PUNCT = "，。！？、：；—…·～「」『』《》\"\"''（）()"


def pchars(t):
    """与 build_ad_video.py 完全一致的口播字符口径（剔除标点与空白）。"""
    return [c for c in t if c not in PUNCT and not c.isspace()]


def is_video_shot(p):
    return (p.get("kind") or "").lower() in ("prod", "imgvid", "video")


# 黑板宣言镜（#40）：独占 1 镜、不计配额，时长下限放宽（大字需完整停留）
DECL_KEYS = ("今天我们讲", "今天我们说", "今天咱们讲", "今天咱们说", "今天我们聊",
             "今天我们聊", "今天聊聊", "今天咱们聊", "今天讲", "今天说")


def is_declaration(text):
    t = (text or "").replace("，", "").replace(",", "").replace(" ", "")
    return any(k.replace("，", "") in t for k in DECL_KEYS)


def has_motion(p):
    return bool(p.get("motion"))


def cut_gate(video=False):
    """多切门槛：图片 1.2s / 视频 1.5s"""
    return VID_CUT if video else IMG_CUT


def shot_cap(video=False, motion=False):
    """单镜软上限：图片 3.2（带 motion 4.0）/ 视频 4.0"""
    if video:
        return VID_MAX
    return IMG_MAX + (MOTION_BONUS if motion else 0.0)


def plan_for(dur, video=False, motion=False):
    """给定句时长 → (最少镜数, 推荐镜数, 最多镜数, 是否需要拆句)

    最少镜数：不让单镜长过软上限（否则闷）
    最多镜数：不让单镜短过多切门槛（否则闪），再按 N_CAP 封顶
    """
    gate = cut_gate(video)
    cap = shot_cap(video, motion)
    n_max = max(1, int(math.floor(dur / gate + 1e-9)))
    n_min = max(1, math.ceil(dur / cap - 1e-6))
    n_rec = max(1, int(dur / IDEAL_SHOT + 0.5))
    n_rec = max(n_min, min(n_rec, n_max))
    need_split = dur > SPLIT_OVER
    return (min(n_min, N_CAP), min(n_rec, N_CAP), min(n_max, N_CAP), need_split)


def judge(dur, kind="img", motion=False, decl=False):
    """单镜时长体检 → (等级, 说明)。等级：OK / WARN / FAIL"""
    video = is_video_shot({"kind": kind})

    if decl:
        if dur < DECL_MIN:
            return "WARN", f"宣言镜偏紧（{dur:.2f}s < {DECL_MIN}s），大字停留不足"
        return "OK", "宣言镜（独占不计配额）"

    if video:
        if dur < VID_MIN:
            return "FAIL", f"闪屏（{dur:.2f}s < 视频镜下限 {VID_MIN}s）→ 并入相邻镜"
        if dur < VID_CUT:
            return "WARN", f"视频镜偏短（{dur:.2f}s < {VID_CUT}s）→ 视频自带运动，切太快会晕"
        if dur > VID_MAX:
            return "WARN", f"视频偏长（{dur:.2f}s > {VID_MAX}s）"
        return "OK", ""

    # ---- 图片镜 ----
    if dur < IMG_MIN:
        return "FAIL", f"闪屏（{dur:.2f}s < 图片镜下限 {IMG_MIN}s）→ 并入相邻镜"
    if dur < IMG_FAST_HI:
        return "OK", f"快切（{dur:.2f}s，图片镜合法快切区 {IMG_MIN}–{IMG_FAST_HI}s）"
    cap = shot_cap(False, motion)
    if dur > cap:
        return "WARN", f"图片偏长（{dur:.2f}s > {cap:.1f}s{'（含动效）' if motion else ''}，建议拆）"
    return "OK", ""


def build_timeline(cfg):
    """复刻渲染器的字符级贪心对齐，返回 ([(phrase, start, end)], 警告)。"""
    w = json.load(open(cfg["tokens"], encoding="utf-8"))
    chars = []
    for seg in w["transcription"]:
        s, e = seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000
        txt = seg["text"].strip()
        n = len(txt)
        for i, c in enumerate(txt):
            chars.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n, c))

    phrases = cfg["phrases"]
    need_total = sum(len(pchars(p["text"])) for p in phrases)
    warn = None
    if need_total != len(chars):
        warn = f"口播字数 {len(chars)} != 文案字数 {need_total}，对齐可能漂移"

    out, ci = [], 0
    for p in phrases:
        need = len(pchars(p["text"]))
        if ci + need > len(chars):
            need = len(chars) - ci
        end = chars[ci + need - 1][1] if need > 0 else chars[ci][0]
        out.append([p, chars[ci][0], end])
        ci += need

    # ★2026-09-29 口径修正（本条让「对应表时长」= 渲染器实际镜头时长）
    #   渲染器 build_ad_video.py L1017：t["cut_end"] = timeline[i+1]["start"]，
    #   即「句间静音算在本镜内」。原实现返回的是「本句末字时刻」，
    #   把 0.3~0.4s 静音漏在时长之外 ⇒ 会把实测 1.30s 的镜误判成 0.96s「闪屏」，
    #   反之也可能把真闪镜误判成合格。现改为与渲染器逐字一致：
    #     end_i = start_(i+1)   （末镜 end = 本句末字 + cfg.tail，与 TOTAL 对齐）
    tail = float(cfg.get("tail", 1.1))
    for i in range(len(out)):
        out[i][2] = out[i + 1][1] if i + 1 < len(out) else out[i][2] + tail
    return [tuple(x) for x in out], warn


def group_rows(tl):
    """按「原句」分组（以 。！？ 结尾为界）"""
    groups, cur = [], []
    for item in tl:
        cur.append(item)
        if item[0]["text"].strip().endswith(("。", "！", "？")):
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    return groups


# ================= 模式 1：打印配额表 =================
def _cap(d, video):
    """某句长在某口径下的最大镜数（与 plan_for 同源，避免表格与判定漂移）"""
    cut = VID_CUT if video else IMG_CUT
    return max(1, min(N_CAP, math.floor(d / cut + 1e-9)))


def build_tier_table():
    """按 IMG_CUT / VID_CUT / N_CAP / SPLIT_OVER 动态推导档位表，
    保证「配额表」与实际判定永不漂移。返回 [(lo, hi, img_max, vid_max)]。"""
    pts = set()
    for cut in (IMG_CUT, VID_CUT):
        k = 1
        while k * cut < SPLIT_OVER - 1e-9:
            pts.add(round(k * cut, 4))
            k += 1
    bnds = sorted(pts)
    raw, lo = [], 0.0
    for b in bnds:
        raw.append((lo, b))
        lo = b
    raw.append((lo, SPLIT_OVER))
    tiers = []
    for (a, b) in raw:
        caps = (_cap(b - 1e-3, False), _cap(b - 1e-3, True))   # 取该段上沿
        if tiers and tiers[-1][2] == caps:
            tiers[-1][1] = b                                     # 合并同容量段
        else:
            tiers.append([a, b, caps])
    return tiers


def print_table():
    print(f"分镜时长配额表 v2（图片镜 下限 {IMG_MIN}s／门槛 {IMG_CUT}s｜"
          f"视频镜 下限 {VID_MIN}s／门槛 {VID_CUT}s｜封顶 {N_CAP} 镜）")
    print("-" * 92)
    print(f"{'该句配音时长':<16}{'图片镜最多':<12}{'视频镜最多':<12}说明")
    print("-" * 92)
    TIER_NOTE = {
        (1, 1): f"短促句/一闪而过，一镜到底（切 2 镜 = 每镜 <{IMG_CUT}s，真闪）",
        (2, 1): f"图片可切 2；视频仍 1（视频自带运动，切太快会晕）",
        (2, 2): "有明确语义分界（动宾/转折/列举）才切",
        (3, 2): "图片可快切到 3；视频建议 2",
        (3, 3): "过渡档（图片未到 4、视频刚到 3）",
        (4, 3): "长句快切档（图片镜为主；切点必须落语义边界）",
        (4, 4): "长句；切点落在标点/语义交界",
    }
    for (a, b, (im, vm)) in build_tier_table():
        top = b if abs(b - SPLIT_OVER) < 1e-9 else b - 0.1   # 末段落点写 9.5，中间段写下一段起点-0.1
        label = f"≤ {top:.1f}s" if a == 0 else f"{a:.1f} – {top:.1f}s"
        print(f"{label:<16}{im:<12}{vm:<12}{TIER_NOTE.get((im, vm), '')}")
    print(f"{'> ' + format(SPLIT_OVER, '.1f') + 's':<16}{'—':<12}{'—':<12}"
          f"拆成两条 phrase（pchars 守恒），不靠堆第 {N_CAP + 1} 镜解决")
    print("-" * 92)
    print("注：「最多」栏＝该档**上限时长**对应的容量；精确查询用 `--dur X`")
    print(f"硬约束：① 图片镜 ≥{IMG_MIN}s、视频镜 ≥{VID_MIN}s 为绝对下限，低于 = 闪屏（必改）")
    print(f"        ② 图片镜 {IMG_MIN}–{IMG_FAST_HI}s = **合法快切区**（判 OK，仅标注）；"
          f"{IMG_FAST_HI}s 以上不再算偏短")
    print(f"        ③ 图片镜 >{IMG_MAX}s（带 motion >{IMG_MAX + MOTION_BONUS}s）、"
          f"视频镜 >{VID_MAX}s → 建议拆")
    print("        ④ 切点必须落在语义边界（标点/动宾交界/转折词前）")
    print("        ⑤ 黑板宣言镜独占 1 镜、不计配额（大字需完整停留）")
    print(f"        ⑥ 单句镜数封顶 {N_CAP} 镜；句长 >{SPLIT_OVER}s → 拆句而不是堆镜")
    print()
    print("时长 → 建议镜数（速查）：")
    for d in [1.0, 1.2, 2.0, 2.3, 2.4, 3.5, 3.6, 4.7, 4.8, 5.9, 6.0, 7.5, 9.0, 9.6, 11.0]:
        n_min, n_rec, n_max, split = plan_for(d, video=False)
        v_min, v_rec, v_max, _ = plan_for(d, video=True)
        tag = "  ⇒ 拆句" if split else ""
        print(f"  {d:>5.1f}s → 图片镜 推荐 {n_rec}（可用 {n_min}–{n_max}）│ "
              f"视频镜 推荐 {v_rec}（可用 {v_min}–{v_max}）{tag}")


# ================= 模式 2：校验 config =================
def check_config(path, csv_path=None):
    cfg = json.load(open(path, encoding="utf-8"))
    tl, warn = build_timeline(cfg)
    if warn:
        print(f"[警告] {warn}")

    total = tl[-1][2]
    print(f"config: {os.path.basename(path)}   镜数 {len(tl)}   "
          f"口播总长 {tl[0][1]:.2f}–{total:.2f}s")
    print("-" * 100)
    print(f"{'#':>3} {'起-止':<16}{'时长':>6}  {'kind':<7}{'判定':<6}{'句子':<30}备注")
    print("-" * 100)

    durs, fails, warns, fast, csv_rows = [], 0, 0, 0, []
    for (p, s, e) in tl:
        d = e - s
        durs.append(d)
        kind = (p.get("kind") or "img").lower()
        lvl, msg = judge(d, kind, has_motion(p), is_declaration(p["text"]))
        if lvl == "FAIL":
            fails += 1
        elif lvl == "WARN":
            warns += 1
        elif msg.startswith("快切"):
            fast += 1
        mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌"}[lvl]
        txt = p["text"]
        idx = len(csv_rows) + 1
        print(f"{idx:>3} {s:6.2f}-{e:6.2f}  {d:6.2f}  {kind:<7}{mark:<6}{txt[:28]:<30}{msg}")
        csv_rows.append([idx, round(s, 2), round(e, 2), round(d, 2), kind,
                         "decl" if is_declaration(txt) else ("motion" if has_motion(p) else ""),
                         lvl, txt])

    print("-" * 100)
    avg = sum(durs) / len(durs)
    print(f"统计：镜数 {len(durs)}   平均镜长 {avg:.2f}s   最短 {min(durs):.2f}s   最长 {max(durs):.2f}s")
    print(f"      闪屏镜(❌) {fails}   偏长/偏短(⚠️) {warns}   图片快切镜(✅ 1.0–1.2s) {fast}")

    print()
    print("原句 → 镜数核对（时长配额）")
    for g in group_rows(tl):
        gs, ge = g[0][1], g[-1][2]
        gd = ge - gs
        actual = len(g)
        head = g[0][0]["text"][:30]
        if actual == 1 and is_declaration(g[0][0]["text"]):
            print(f"  · [{gs:5.2f}-{ge:5.2f}] {gd:5.2f}s  宣言镜（独占 1 镜，不计配额）  {head}")
            continue
        all_video = all(is_video_shot(p) for (p, _, _) in g)
        all_motion = all(has_motion(p) for (p, _, _) in g)
        n_min, n_rec, n_max, split = plan_for(gd, video=all_video, motion=all_motion)
        flag, notes = "✅", []
        if actual > n_max:
            flag = "❌"
            notes.append(f"镜数超配额（该句最多 {n_max} 镜，实际 {actual}）→ 每镜仅 {gd / actual:.2f}s")
        if split:
            flag = "❌" if flag == "❌" else "⚠️"
            notes.append(f"句长 {gd:.2f}s > {SPLIT_OVER}s → 建议拆句")
        # 组内逐镜查偏长（motion 感知），比看平均更准，避免误判
        over = [(i + 1, e - s) for i, (p, s, e) in enumerate(g)
                if (e - s) > shot_cap(is_video_shot(p), has_motion(p))]
        if over:
            flag = "❌" if flag == "❌" else "⚠️"
            notes.append("偏长镜 " + "、".join(f"#{i} {d:.2f}s" for i, d in over) + " → 建议拆")
        if not notes:
            if actual != n_rec:
                flag = "·"
                notes.append(f"可用（推荐 {n_rec} 镜，可在 {n_min}–{n_max} 内按语义调）")
            else:
                notes.append(f"推荐值命中（{n_rec} 镜）")
        print(f"  {flag} [{gs:5.2f}-{ge:5.2f}] {gd:5.2f}s  实际 {actual} 镜 / "
              f"推荐 {n_rec}（{n_min}–{n_max}）  {head}")
        for n in notes:
            print(f"      {n}")

    if csv_path:
        import csv as _csv
        with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
            w = _csv.writer(f)
            w.writerow(["镜号", "起", "止", "时长", "kind", "标记", "判定", "文本"])
            w.writerows(csv_rows)
        print(f"\nCSV 已写出：{csv_path}")

    return fails


def main():
    ap = argparse.ArgumentParser(description="分镜时长配额规划器 / 校验器 (v2 快切图镜版)")
    ap.add_argument("--table", action="store_true", help="打印配额表")
    ap.add_argument("--config", help="校验某条 config.json")
    ap.add_argument("--dur", type=float, help="单句时长 → 建议镜数")
    ap.add_argument("--video", action="store_true", help="--dur 按视频镜口径折算")
    ap.add_argument("--csv", help="导出逐镜 CSV")
    a = ap.parse_args()

    if a.table:
        print_table()
    if a.dur is not None:
        n_min, n_rec, n_max, split = plan_for(a.dur, video=a.video)
        kind = "视频镜" if a.video else "图片镜"
        print(f"{a.dur:.2f}s（{kind}）→ 推荐 {n_rec} 镜（可用 {n_min}–{n_max}）"
              + ("   ⇒ 句长超标，建议拆句" if split else ""))
    if a.config:
        sys.exit(1 if check_config(a.config, a.csv) else 0)
    if not (a.table or a.dur is not None or a.config):
        ap.print_help()


if __name__ == "__main__":
    main()
