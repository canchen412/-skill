#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""分镜配额预检（不依赖 whisper）：用配音段的真实时长 + 段内字数比例反推每镜时长。

口径与 build_ad_video.py 一致：
  start_i = phrase i 首字起始;  cut_end_i = start_{i+1};  末镜 cut_end = 末字 end + tail
  -> 镜时长 = cut_end_i - start_i（句间静音算在本镜内）
用法: python quota_pre.py s1
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import json, os, sys, subprocess

W = os.environ.get("AD_WORKDIR") or os.getcwd()   # 工程目录：优先 $AD_WORKDIR，否则当前目录
FF = os.environ.get("FFPROBE") or "/Users/mac/.local/bin/ffprobe"
PUNC = set("，。！？、：；—…·「」『』（）()\"'“”‘’ ")
IMG_MIN, IMG_MAX = 1.0, 3.2      # 图片镜: 绝对下限 / 软上限
N_CAP = 4


def pc(s):
    return "".join(c for c in s if c not in PUNC)


def dur(p):
    r = subprocess.run([FF, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", p], capture_output=True, text=True)
    return float(r.stdout.strip())


def main(ver):
    d = f"{W}/{ver}"
    cfg = json.load(open(f"{d}/config.json"))
    phrases = cfg["phrases"]
    segs = json.load(open(f"{W}/audio/seg_{ver}/segments.json"))
    gap = segs.get("seg_gap", 0.22)
    seglist = segs["segments"]

    # 段时长
    for s in seglist:
        s["dur"] = dur(f"{W}/audio/seg_{ver}/seg_{s['i']:02d}.wav")

    # 段文本 -> 覆盖哪些 phrase（贪心拼接）
    texts = [pc(p["text"]) for p in phrases]
    mapping, cur = [], 0
    for s in seglist:
        tgt, acc, start = pc(s["text"]), "", cur
        while cur < len(texts) and len(acc) < len(tgt) + 40:
            acc += texts[cur]; cur += 1
            if acc == tgt:
                break
        mapping.append((start, cur, tgt))
    total_ph = sum(b - a for a, b, _ in mapping)
    if total_ph != len(texts):
        print(f"⚠️  段-phrase 映射未全覆盖：{total_ph}/{len(texts)}，结果仅供参考")

    # 字级时间轴（段内按字数比例 + 段间 gap）
    t = 0.0
    chars = []          # (start, end, char)
    for (a, b, tgt), s in zip(mapping, seglist):
        n = sum(len(texts[i]) for i in range(a, b))
        t0 = t
        for i in range(a, b):
            m = len(texts[i])
            for k in range(m):
                chars.append((t0 + s["dur"] * (sum(len(texts[j]) for j in range(a, i)) + k) / n,
                              t0 + s["dur"] * (sum(len(texts[j]) for j in range(a, i)) + k + 1) / n,
                              texts[i][k]))
        t += s["dur"] + gap                      # 段间静音
    TOTAL = chars[-1][1] + cfg.get("tail", 0.9)

    # 按字数贪心对齐 -> 镜时间轴
    tl, ci = [], 0
    for p in phrases:
        n = len(pc(p["text"]))
        tl.append({"text": p["text"], "start": chars[ci][0], "end": chars[ci + n - 1][1]})
        ci += n
    for i in range(len(tl)):
        tl[i]["cut_end"] = tl[i + 1]["start"] if i + 1 < len(tl) else TOTAL

    print(f"=== {ver} · {len(tl)} 镜 · 全片 {TOTAL:.2f}s（净 {t - gap:.2f}s + 静音）===")
    bad = []
    for i, x in enumerate(tl):
        du = x["cut_end"] - x["start"]
        f = ""
        if du < IMG_MIN:
            f = " ❌ 真闪"; bad.append((i + 1, du, "真闪"))
        elif du > IMG_MAX:
            f = " ⚠️ 偏长"; bad.append((i + 1, du, "偏长"))
        print(f"{i+1:2d} {x['start']:6.2f}-{x['cut_end']:6.2f} {du:5.2f}s  {x['text'][:26]}{f}")
    ds = [x["cut_end"] - x["start"] for x in tl]
    print(f"\n合计 {sum(ds):.2f}s  均 {sum(ds)/len(ds):.2f}s  最短 {min(ds):.2f}s  最长 {max(ds):.2f}s")
    print("问题镜:", bad if bad else "无 ✅")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "s1")
