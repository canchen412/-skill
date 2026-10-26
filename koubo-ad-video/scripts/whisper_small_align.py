#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""whisper small 逐词转写 → whisper.cpp `-oj` 格式（喂给 align_vo.py）

为什么不用 whisper.cpp base：arm64 上 base 对 CosyVoice 输出尾段会幻觉（重复乱码 +
时间轴超出音频时长），align_vo 兜不住、末镜时间全错。openai-whisper `small`
（cosyvoice venv 里已装、权重已缓存）逐词时间戳完整，同音字靠 align_vo 拼音比对兜住。

用法：
    /Users/mac/.workbuddy/models/cosyvoice/venv/bin/python \
        scripts/whisper_small_align.py audio/vo.wav -o audio/vo_for_align.json
（不传 -o 则默认 <输入>.for_align.json；需要 ffmpeg 转 16k 单声道）
"""
import argparse
import json
import os
import subprocess
import sys

FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def to16k(src, dst):
    subprocess.run([FFMPEG, "-y", "-i", src, "-ar", "16000", "-ac", "1", dst],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("wav")
    ap.add_argument("-o", "--out", default="")
    a = ap.parse_args()

    out = a.out or os.path.splitext(a.wav)[0] + ".for_align.json"
    w16 = os.path.splitext(a.wav)[0] + "_16k.wav"
    if not os.path.exists(w16):
        to16k(a.wav, w16)

    import whisper  # cosyvoice venv 里的 openai-whisper
    model = whisper.load_model("small")
    r = model.transcribe(w16, language="zh", word_timestamps=True,
                         condition_on_previous_text=True)

    segs = []
    for seg in r.get("segments", []):
        for w in seg.get("words", []):
            txt = w.get("word", "").strip()
            if not txt:
                continue
            segs.append({
                "offsets": {"from": int(w["start"] * 1000), "to": int(w["end"] * 1000)},
                "text": txt,
            })
    json.dump({"systeminfo": "whisper-small-word", "model": {"name": "small"},
               "params": {"language": "zh"}, "result": {"language": "zh"},
               "transcription": segs},
              open(out, "w", encoding="utf-8"), ensure_ascii=False)
    n = sum(len(s["text"]) for s in segs)
    print(f"逐词 {len(segs)} 段 / {n} 字，末字 {segs[-1]['offsets']['to']/1000:.2f}s -> {out}")


if __name__ == "__main__":
    main()
