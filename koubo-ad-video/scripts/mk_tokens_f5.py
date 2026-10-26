#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★F5 配音的 token 时间轴生成（2026-10-09）
输入：f5_batch.py 产出的 durations.json（句级起止）+ phrases.txt（镜位句）
输出：vo_mapped_v2.json（build_ad_video 字幕格式）+ shot_bounds.json（镜界）
原理：句界是 F5 合成的实测值（零推导），句内字符按字数比例分配。
用法: python3 mk_tokens_f5.py --durations durations.json --phrases phrases.txt --out-dir sX/audio
"""
import argparse
import json
import re

P = re.compile('[，。！？、：；—…·～「」『』《》""''（）()\s]')

def ts(x):
    h, m = divmod(x, 3600); m, s = divmod(m, 60)
    return f"{int(h):02d}:{int(m):02d}:{s:05.2f}"

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--durations", required=True)
    ap.add_argument("--phrases", required=True, help="镜位句文件（每行一镜）")
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()

    durs = json.load(open(a.durations, encoding="utf-8"))["sentences"]
    phrases = [l.strip() for l in open(a.phrases, encoding="utf-8") if l.strip()]
    assert len(durs) == len(phrases), f"句数不匹配 {len(durs)}/{len(phrases)}"

    out, shot_bounds = [], []
    for (d, ph) in zip(durs, phrases):
        # 归一化后的发音文本与 ph 字数一致（durations 的 text 是原句）
        lo, hi = d["start"], d["end"]
        chars = P.sub("", ph)
        cn = len(chars)
        assert cn > 0, ph
        span = hi - lo
        i = 0
        while i < cn:
            k = min(4, cn - i)
            # 前 88% 时长均分，尾部 12% 留白（句尾自然衰减，防字幕压音尾）
            eff = span * 0.88
            aa = lo + eff * i / cn
            zz = lo + eff * (i + k) / cn
            zz = max(aa + 0.08, zz)
            out.append({"timestamps": {"from": ts(aa), "to": ts(zz)},
                        "offsets": {"from": int(round(aa * 1000)), "to": int(round(zz * 1000))},
                        "text": chars[i:i + k]})
            i += k
        shot_bounds.append([round(lo, 3), round(hi, 3)])

    doc = {"systeminfo": "f5-aligned", "model": {}, "params": {},
           "result": {"language": "zh"}, "transcription": out}
    json.dump(doc, open(f"{a.out_dir}/vo_mapped_v2.json", "w"), ensure_ascii=False, indent=1)
    json.dump(shot_bounds, open(f"{a.out_dir}/shot_bounds.json", "w"))
    print(f"[mk_f5] {len(phrases)} 镜 / {len(out)} token / 全片 {shot_bounds[-1][1]:.1f}s", flush=True)

if __name__ == "__main__":
    main()
