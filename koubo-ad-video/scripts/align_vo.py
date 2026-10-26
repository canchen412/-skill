# -*- coding: utf-8 -*-
"""
口播文案 × whisper 识别结果 的拼音强制对齐。

解决 whisper.cpp 输出的两个老问题：
  1) 同音字 / 繁体字（"撸铁练到底" → "录旅道底"）
  2) 多字节字符被拆坏产生 U+FFFD 乱码段，导致字符数 ≠ 文案字数，逐字贪心对齐整体漂移

做法：把文案和识别文本都转成拼音序列，用 difflib 做序列比对，
     识别里多出来的字（乱码/插入）跳过，文案里没被识别到的字按前后时间线性插值。
产出 fixed.json：字符序列与文案严格一致（每段 ≤4 字，与实际字形一致），
时间取对齐到的识别字时间 —— 可直接喂给 build_ad_video.py 的 tokens 字段。

用法:
  python3 align_vo.py <whisper.json> <phrases.txt> <out_fixed.json>
phrases.txt: 每行一句文案（标点会被忽略，字母数字保留）
"""
import json, sys, difflib, re

try:
    from pypinyin import lazy_pinyin
except ImportError:
    lazy_pinyin = None

PUNCT = "，。！？、：；——…「」《》\"'（）()"

def pchars(t):
    return [c for c in t if c not in PUNCT]

def py(c):
    if lazy_pinyin is None:
        return c
    if re.match(r"[\u4e00-\u9fff]", c):
        r = lazy_pinyin(c)
        return r[0] if r else c
    return c.lower()

def load_heard(path, offset=0.0):
    raw = open(path, "rb").read().decode("utf-8", errors="replace")
    w = json.loads(raw)
    heard = []
    for seg in w["transcription"]:
        s, e = seg["offsets"]["from"] / 1000.0 + offset, seg["offsets"]["to"] / 1000.0 + offset
        t = seg["text"].strip()
        if not t:
            continue
        n = max(1, len(t))
        for i, c in enumerate(t):
            heard.append({
                "c": c if not ("\ufffd" in c) else "?",
                "s": s + (e - s) * i / n,
                "e": s + (e - s) * (i + 1) / n,
            })
    return heard

def align(heard, exp_chars):
    """返回 exp_chars 每个字的时间 [start, end]"""
    hp = [py(h["c"]) for h in heard]
    ep = [py(c) for c in exp_chars]
    sm = difflib.SequenceMatcher(None, hp, ep, autojunk=False)
    times = [None] * len(exp_chars)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for k in range(j2 - j1):
                times[j1 + k] = [heard[i1 + k]["s"], heard[i1 + k]["e"]]
    # 未对齐到的字：按前后已知时间线性插值
    known = [i for i, t in enumerate(times) if t]
    if not known:
        raise SystemExit("对齐失败：没有任何字匹配上")
    for i in range(len(times)):
        if times[i]:
            continue
        prev = max([k for k in known if k < i], default=None)
        nxt = min([k for k in known if k > i], default=None)
        if prev is None:
            times[i] = [times[nxt][0], times[nxt][0]]
        elif nxt is None:
            step = (times[prev][1] - times[prev][0]) if times[prev][1] > times[prev][0] else 0.12
            times[i] = [times[i - 1][1], times[i - 1][1] + step]
        else:
            a, b = times[prev][1], times[nxt][0]
            frac = (i - prev) / (nxt - prev)
            t0 = a + (b - a) * (frac - 1.0 / (nxt - prev))
            t1 = a + (b - a) * frac
            times[i] = [t0, max(t0, t1)]
    # 单调递增修正
    last = 0.0
    for i, t in enumerate(times):
        if t[0] < last:
            t[0] = last
        if t[1] < t[0]:
            t[1] = t[0] + 0.05
        last = t[1]
    return times

def main():
    argv = [a for a in sys.argv[1:] if not a.startswith("--")]
    extras = [a for a in sys.argv[1:] if a.startswith("--extra")]
    wj, pf, out = argv[0], argv[1], argv[2]
    phrases = [l.strip() for l in open(pf, encoding="utf-8") if l.strip()]
    exp_chars, phrase_len = [], []
    for p in phrases:
        cs = pchars(p)
        phrase_len.append(len(cs))
        exp_chars += cs
    heard = load_heard(wj)
    # 分段补录：整轨识别可能漏掉结尾，用 --extra <json>:<该段在原始音频的起点秒> 补进来
    for ex in extras:
        spec = ex.split("=", 1)[1] if "=" in ex else ex[len("--extra"):].lstrip()
        path, _, off = spec.rpartition(":")
        off = float(off)
        cut = off - 0.15
        before = [h for h in heard if h["s"] < cut]
        after = load_heard(path, offset=off)
        heard = before + after
        print(f"补录 {path} @{off}s: 保留 {len(before)} 字 + 补入 {len(after)} 字")
    heard.sort(key=lambda h: h["s"])
    print(f"文案 {len(exp_chars)} 字 / 识别 {len(heard)} 字")
    times = align(heard, exp_chars)

    # 输出 whisper 同格式 JSON：段长 ≤4，text 必须是文案原字，故乱码字还原为文案字
    segs, i = [], 0
    while i < len(exp_chars):
        n = min(4, len(exp_chars) - i)
        segs.append({
            "timestamps": {"from": f"00:00:{times[i][0]:05.2f}", "to": f"00:00:{times[i+n-1][1]:05.2f}"},
            "offsets": {"from": int(times[i][0] * 1000), "to": int(times[i + n - 1][1] * 1000)},
            "text": "".join(exp_chars[i:i + n]),
        })
        i += n
    json.dump({"systeminfo": "pinyin-aligned", "model": {}, "params": {}, "result": {"language": "zh"},
               "transcription": segs}, open(out, "w", encoding="utf-8"), ensure_ascii=False)

    # 逐句打印核对：文案 vs 对齐到的识别字
    print()
    ci = 0
    for k, (p, n) in enumerate(zip(phrases, phrase_len)):
        heard_str = "".join(
            heard[min(range(len(heard)), key=lambda x: abs(heard[x]["s"] - times[j][0]))]["c"]
            for j in range(ci, ci + n))
        flag = "⚠" if any(c == "?" for c in heard_str) else " "
        print(f"[{k+1:2d}] {times[ci][0]:6.2f}-{times[ci+n-1][1]:6.2f} {flag} {p}")
        print(f"          听到: {heard_str}")
        ci += n
    print("\n写出:", out)

if __name__ == "__main__":
    main()
