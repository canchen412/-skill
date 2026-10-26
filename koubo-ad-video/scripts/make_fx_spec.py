#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fx_spec 生成器（2026-10-09）：文案侧车 .fx.json + vo token 实测 → Remotion fx 层 spec
─────────────────────────────────────────────────────────────────────
decl（与文案同名 .fx.json）结构：
{
 "words":  [{"text":"4个9","x":380,"y":250,"color":"#FFE9C8","anim":"plain",
             "durSec":1.8,"fontSize":110}, ...],
 "counts": [{"text":"50多万单","x":500,"y":430,"target":50,"unit":"万+",
             "label":"单","color":"#F2C14E","durSec":2.6,"barWidth":420}],
 "stamps": [{"text":"每批送检","phrase":7,"x":420,"y":300,"color":"#E8796A",
             "fontSize":130,"rotate":-12,"durSec":2.4}]  # ★盖章（旋转砸下）
}
锚点 = 词首字在 vo token 实测时间轴上的时刻（token 词内字符线性分配，回退 0.06s 起）。
输出 fx_spec_{name}.json = {"kw": [...], "viz": [...]}（totalFrames 由 pipeline 回填；
stamps 归并进 viz 数组，kind=stamp，与 count 同一渲染链）。

★2026-10-14 已删除 slams（PriceSlam 价格锤）：灿爹判定「很 low」。
  价格镜改走 KwOverlay 的 price 变体（重点字 + 中段复顶）或 counts 数字滚动。
"""
import argparse
import json
import os
import re

PUNCT = re.compile(r"[，。！？、：；—…·～「」『』《》”“‘’（）()\s]")


def hhmmss(x: str) -> float:
    h, m, s = x.split(":")
    return int(h) * 3600 + float(m) * 60 + float(s)


def char_times(transcription):
    """全片字符级时间轴 [(char, t_start)]，token 内字符线性分配"""
    out = []
    for tk in transcription:
        a = hhmmss(tk["timestamps"]["from"])
        b = hhmmss(tk["timestamps"]["to"])
        chars = [c for c in tk["text"] if not PUNCT.match(c)]
        n = max(1, len(chars))
        for i, c in enumerate(chars):
            out.append((c, a + (b - a) * i / n))
    return out


def find_t0(cts, kw: str, phrase_idx: int = 0, sent_of=None):
    """phrase_idx: 1 起始镜号（0=不限）；sent_of: 字符下标→镜号映射"""
    txt = "".join(c for c, _ in cts)
    k = PUNCT.sub("", kw)
    start = 0
    while True:
        i = txt.find(k, start)
        if i < 0:
            return None
        if phrase_idx <= 0 or sent_of is None or sent_of[i] == phrase_idx:
            return cts[i][1]
        start = i + 1


def bounds_from_chars(sent_of, cts):
    """无 shot_bounds.json 时的兜底：按句内首/末字符时刻反推镜边界"""
    if not sent_of:
        return None
    n = max(sent_of)
    bs = [[None, None] for _ in range(n)]
    for i, (_c, t) in enumerate(cts):
        si = sent_of[i] - 1
        if 0 <= si < n:
            if bs[si][0] is None:
                bs[si][0] = t
            bs[si][1] = t
    return [[b[0] or 0.0, b[1] or 0.0] for b in bs]


TAIL = 0.0      # ★转场保护带：动效必须在「切点 - TAIL」之前结束（见 fit_shot）
LEAD = 0.0      # ★提前入场：锚点词前 LEAD 秒就起（2026-10-22 灿爹：停留太短来不及看清）
MIN_SHOW = 1.6  # ★最短可读时长：不够就把起点前移，宁可早出现也不给一段看不清的


def fit_shot(start, dur, bounds, phrase, min_show=None):
    """★2026-10-21 灿爹点名：重点字/动效**必须跟着所属分镜一起消失**，不许跨镜。

    原话「当重点字和重点字的那一个画面进入下一个分镜的时候，那么这个重点字
    就要一起消失了，而不是在进入下一个分镜的时候这个重点字还在，这样就不好」。

    成因：动效层只拿到配音时间轴（startSec + durSec 绝对值），不知道画面在哪切，
    锚点词落在句尾时 durSec 一撑就淌进下一镜（实测 4 处全跨镜，最长溢出 1.55s）。
    修法：按 decl 的 phrase（镜号）取该镜边界，end 硬压到镜末。
    最后一镜（CTA）不截，跟随片尾自然结束。

    ★2026-10-22 灿爹第二次点名：「停留的时间又太短，都来不及看清，你可以尽可能
    延迟他的时间，例如在说到掉肌肉或者掉脂肪之前提前一点加入动效，也可以在下一个
    分镜来之前的最后一刻再消失」。⇒ 从「按 durSec 截断」改成 **铺满本镜**：
      · 起点 = 锚点 - LEAD（但不早于本镜开头），即「说到那个词之前就出现」
      · 终点 = 镜末 - 1 帧 - TAIL，即「切走前最后一刻才消失」
    这同时让 ShiftBar 的液面走得更慢（travel 跟着 durFrames 走），一举两得。
    """
    if min_show is None:
        min_show = MIN_SHOW
    if not bounds or phrase < 1 or phrase > len(bounds):
        return start, dur
    if phrase == len(bounds):
        return start, dur
    s0, s1 = bounds[phrase - 1]
    # 留 1 帧余量 + 转场保护带：镜间有推镜时，下一镜在「切点 - 转场时长」就已经开始
    # 盖进来了；动效压到切点才消失，就会骑在下一镜画面上（2026-10-21 实测 3.55s
    # 那张卡还浮在跑步镜上）⇒ 至少留 1 帧，TAIL 由调用方按转场时长给（默认已收到 0.08）。
    end_cap = s1 - 0.02 - max(0.0, TAIL)
    new_start = max(s0 + 0.05, start - max(0.0, LEAD))
    if end_cap - new_start < min_show:
        # 铺满还不够看 ⇒ 起点再往前挪，最多挪到本镜开头
        new_start = max(s0 + 0.05, end_cap - min_show)
    span = end_cap - new_start
    if span <= 0.2:
        # 本镜本身就短到撑不出来，退回原区间，只做「不跨镜」的压镜
        end = min(start + dur, end_cap)
        return round(start, 2), round(max(0.25, end - start), 2)
    return round(new_start, 2), round(span, 2)


def css2ass(hx: str) -> str:
    """#RRGGBB → ASS &H00BBGGRR"""
    hx = hx.lstrip("#").upper()
    return f"&H00{hx[4:6]}{hx[2:4]}{hx[0:2]}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mapped", required=True, help="vo_mapped_v2.json")
    ap.add_argument("--decl", required=True, help=".fx.json 侧车")
    ap.add_argument("--out", required=True)
    ap.add_argument("--phrases", help="phrases.txt（镜句文本，用于镜级锚点作用域）")
    ap.add_argument("--bounds", help="shot_bounds.json（镜时间边界 [[s,e],...]，用于把动效压进本镜）")
    ap.add_argument("--tail", type=float, default=0.0,
                    help="★转场保护带（秒）：动效在「切点 - tail」前结束。"
                         "镜间用推镜时必须传转场时长，否则动效会骑在下一镜画面上")
    ap.add_argument("--lead", type=float, default=0.0,
                    help="★提前入场（秒）：锚点在词上，提前这么多秒就开始出，"
                         "配合「铺满本镜」一起用（2026-10-22 灿爹：停留太短来不及看清）")
    a = ap.parse_args()
    globals()["TAIL"] = float(a.tail or 0.0)
    globals()["LEAD"] = float(a.lead or 0.0)

    trans = json.load(open(a.mapped, encoding="utf-8"))["transcription"]
    decl = json.load(open(a.decl, encoding="utf-8"))
    cts = char_times(trans)

    # 镜级作用域：phrases.txt 逐句去标点后与 token 字符流对齐 → 字符下标→镜号
    sent_of = None
    if a.phrases:
        sents = [PUNCT.sub("", s.strip()) for s in open(a.phrases, encoding="utf-8") if s.strip()]
        sent_of = []
        for si, s in enumerate(sents, 1):
            sent_of.extend([si] * len(s))
        n_tok = len(cts)
        if len(sent_of) < n_tok:          # token 字符流略长时尾部归最后一句
            sent_of.extend([len(sents)] * (n_tok - len(sent_of)))
        sent_of = sent_of[:n_tok] or None

    # 镜时间边界：优先 shot_bounds.json（配音侧产出的真边界），否则按句内字符时刻兜底
    bounds = None
    if a.bounds and os.path.exists(a.bounds):
        try:
            bounds = [[float(x[0]), float(x[1])] for x in json.load(open(a.bounds, encoding="utf-8"))]
        except Exception as _e:
            print(f"  ⚠️ 读 bounds 失败（{_e}），退回按字符时刻推算")
    if not bounds:
        bounds = bounds_from_chars(sent_of, cts)
    n_shots = len(bounds) if bounds else 0

    kw, viz = [], []
    for w in decl.get("words", []):
        t0 = find_t0(cts, w["text"], int(w.get("phrase", 0)), sent_of)
        if t0 is None:
            print(f"  ⚠️ fx 词「{w['text']}」在配音里找不到锚点，跳过")
            continue
        e = {"startSec": round(max(0.0, t0 - 0.06), 2),
             "durSec": w.get("durSec", 1.8),
             "text": w["text"], "x": w["x"], "y": w["y"],
             "assColor": css2ass(w.get("color", "#FFE9C8")),
             "anim": w.get("anim", "plain")}
        if w.get("fontSize"):
            e["fontSize"] = w["fontSize"]
        if w.get("dropPx") is not None:
            e["dropPx"] = w["dropPx"]
        # ★2026-10-14 灿爹点名：behind = 重点字 3D 景深（字在人物身后，被遮住一点）。
        #   渲染侧 KwWord：perspective + rotateY/X（朝向）+ translateZ 堆叠副本（厚度）
        #   + blur（失焦，字更远所以虚）；遮挡靠 pipeline 逐帧 Vision 显著性蒙版合成。
        if w.get("behind"):
            e["behind"] = True
            for k in ("behindBlur", "behindYaw", "behindPitch"):
                if w.get(k) is not None:
                    e[k] = w[k]
            if w.get("mask"):                 # 兼容旧的手写椭圆（已废弃，留字段不报错）
                e["mask"] = w["mask"]
        _s0, _d0 = e["startSec"], e["durSec"]
        e["startSec"], e["durSec"] = fit_shot(_s0, _d0, bounds, int(w.get("phrase", 0)))
        if abs(e["durSec"] - _d0) > 0.02:
            print(f"  ⏱ 重点字「{e['text']}」镜{w.get('phrase')}: "
                  f"{_s0}+{_d0}s → {e['startSec']}+{e['durSec']}s（压到镜末）")
        kw.append(e)
    for c in decl.get("counts", []):
        t0 = find_t0(cts, c["text"], int(c.get("phrase", 0)), sent_of)
        if t0 is None:
            print(f"  ⚠️ fx 数字「{c['text']}」找不到锚点，跳过")
            continue
        if c.get("shape") == "ring":
            # ★圆环动画（2026-10-09 灿爹点名「98% 用圆形动画做」）：纯度/占比类用环
            # ★RingGauge 的 value 语义是 0~1（组件内数字 ×100 显示）——
            #   传 98 会显示 9800%（2026-10-09 实测踩坑），这里归一化。
            _val = c["target"]
            viz.append({"kind": "ring", "startSec": round(max(0.0, t0 - 0.06), 2),
                        "durSec": c.get("durSec", 2.8), "x": c["x"], "y": c["y"],
                        "r": c.get("r", 138), "value": (_val / 100 if _val > 1 else _val),
                        "label": c.get("label", ""), "sub": c.get("sub", ""),
                        "color": c.get("color", "#F2C14E")})
            viz[-1]["_p"] = int(c.get("phrase", 0))
        else:
            viz.append({"kind": "count", "startSec": round(max(0.0, t0 - 0.06), 2),
                        "durSec": c.get("durSec", 2.6), "x": c["x"], "y": c["y"],
                        "target": c["target"], "unit": c.get("unit", ""),
                        "label": c.get("label", ""), "color": c.get("color", "#F2C14E"),
                        "barWidth": c.get("barWidth", 420)})
            if c.get("fontSize"):
                viz[-1]["fontSize"] = c["fontSize"]
            # ★2026-10-21：plate 透传（DataViz.CountUp 早就支持暗板，但这里一直没转，
            #   ⇒ 落位到亮背景（窗光/白墙/浅货架）时无法开板，数字发糊）。
            if c.get("plate"):
                viz[-1]["plate"] = True
            viz[-1]["_p"] = int(c.get("phrase", 0))
    # ★2026-10-13 灿爹点名（烟酰胺 v2 镜5）：配方含量一览图表 → CompareBar。
    #   decl: "bars": [{"text":"配方和含量","phrase":5,"x":1470,"y":470,
    #                    "title":"配方 & 含量","durSec":2.4,"barWidth":340,
    #                    "rows":[{"name":"烟酰胺","v":0.9,"color":"#F2C14E","suffix":"16毫克/份"},...]}]
    #   行 suffix 可选：不给沿用旧语义显示 "N.N×"（对比倍数）；给了直接显示原文
    #   （配方/含量类数值不是倍数，不能带 ×）。
    for b in decl.get("bars", []):
        t0 = find_t0(cts, b["text"], int(b.get("phrase", 0)), sent_of)
        if t0 is None:
            print(f"  ⚠️ fx 图表「{b['text']}」找不到锚点，跳过")
            continue
        viz.append({"kind": "bar", "startSec": round(max(0.0, t0 - 0.06), 2),
                    "durSec": b.get("durSec", 2.4), "x": b["x"], "y": b["y"],
                    "title": b.get("title", "数据对比"),
                    "rows": b.get("rows", []),
                    "barWidth": b.get("barWidth", 420)})
        viz[-1]["_p"] = int(b.get("phrase", 0))
    # ★2026-10-21 灿爹点名：「掉肌肉和掉脂肪这种，用动效层的数字滚动/进度条显示，更有高级感」
    #   增减条 shift：条从 from 缩/长到 to（CompareBar 是 0→v 生长，方向相反）。
    #   decl: "shifts":[{"text":"同时掉肌肉和脂肪","phrase":1,"x":470,"y":300,
    #                    "title":"肌肉 / 脂肪","durSec":2.6,
    #                    "rows":[{"name":"肌肉","from":100,"to":62,
    #                             "color":"#FF6F61","tag":"一起掉"},...]}]
    #   ★刻意不标百分比：示意值不是实测数据，只给方向标签。
    for sh in decl.get("shifts", []):
        t0 = find_t0(cts, sh.get("anchor", sh["text"]), int(sh.get("phrase", 0)), sent_of)
        if t0 is None:
            print(f"  ⚠️ fx 增减条「{sh['text']}」找不到锚点，跳过")
            continue
        viz.append({"kind": "shift", "startSec": round(max(0.0, t0 - 0.06), 2),
                    "durSec": sh.get("durSec", 2.6), "x": sh["x"], "y": sh["y"],
                    "title": sh.get("title", ""), "shiftRows": sh.get("rows", []),
                    "barWidth": sh.get("barWidth", 430)})
        viz[-1]["_p"] = int(sh.get("phrase", 0))

    # ★2026-10-09 动效库收编：stamp（盖章）→ viz 同链渲染
    #   ★2026-10-14 PriceSlam（价格锤）判定「很 low」已删除，价格镜改走
    #     KwOverlay 的 price 变体（重点字 + 中段复顶）或 .fx.json 的 counts。
    #   ★anchor 字段：显示文字与配音读法不同时用（如显示「¥19.9」配音读「19块9」）
    for kind, key, dflt in (("stamp", "stamps", {"color": "#E8796A", "fontSize": 130, "durSec": 2.4}),):
        for e in decl.get(key, []):
            t0 = find_t0(cts, e.get("anchor", e["text"]), int(e.get("phrase", 0)), sent_of)
            if t0 is None:
                print(f"  ⚠️ fx {key[:-1]}「{e['text']}」找不到锚点，跳过")
                continue
            viz.append({"kind": kind, "text": e["text"],
                        "startSec": round(max(0.0, t0 - 0.06), 2),
                        "durSec": e.get("durSec", dflt["durSec"]),
                        "x": e["x"], "y": e["y"],
                        "color": e.get("color", dflt["color"]),
                        "fontSize": e.get("fontSize", dflt["fontSize"])})
            if e.get("rotate") is not None:
                viz[-1]["rotate"] = e["rotate"]
            viz[-1]["_p"] = int(e.get("phrase", 0))

    # ★统一压进本镜（不得跨镜）——见 fit_shot 里的灿爹原话
    _warn = []
    for v in viz:
        _p = v.pop("_p", 0)
        _s0, _d0 = v["startSec"], v["durSec"]
        v["startSec"], v["durSec"] = fit_shot(_s0, _d0, bounds, _p)
        if abs(v["durSec"] - _d0) > 0.02:
            _warn.append(f"    · {v.get('text') or v.get('title')} 镜{_p}: {_s0}+{_d0}s → {v['startSec']}+{v['durSec']}s（压到镜末）")
    if _warn:
        print("  ⏱ 动效已压进所属分镜（不跨镜）:")
        print("\n".join(_warn))

    json.dump({"kw": kw, "viz": viz}, open(a.out, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    _by = {}
    for v in viz:
        _by[v["kind"]] = _by.get(v["kind"], 0) + 1
    _s = " + ".join(f"{k}×{n}" for k, n in sorted(_by.items())) or "空"
    print(f"  fx_spec: {len(kw)} 重点字 + viz[{_s}] → {a.out}")


if __name__ == "__main__":
    main()
