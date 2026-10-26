#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
本地克隆 TTS：给定一段说话人样本 + 文案，克隆音色生成配音。

引擎：Coqui XTTS-v2（免费、本地、支持中文克隆）
用法：
  /tmp/ttsvenv/bin/python tts_clone.py --text "文案..." --speaker sample.wav --out out.mp3
  /tmp/ttsvenv/bin/python tts_clone.py --text-file script.txt --speaker sample.wav --out out.mp3

说明：
  - 长文案按标点自动分句，逐句合成再拼接（避免 XTTS 长文本截断/爆内存）
  - 句间自动补短停顿，节奏更自然
  - 输出 mp3（libmp3lame），可直接喂给 build_ad_video.py
  - 首次运行会下载 XTTS-v2 权重（约 1.8GB，建议设 HF_ENDPOINT=https://hf-mirror.com）
"""
import os
import re
import sys
import argparse
import tempfile
import subprocess

FF = "/Users/mac/.local/bin/ffmpeg"
MODEL = "tts_models/multilingual/multi-dataset/xtts_v2"


def split_sentences(text):
    """按句子边界切分，保留标点；超长句再按逗号切。"""
    # 先按句末标点切
    parts = re.split(r"(?<=[。！？!?；;])", text)
    out = [p.strip() for p in parts if p.strip()]
    res = []
    for p in out:
        if len(p) > 42:
            for s in re.split(r"(?<=[，,、])", p):
                s = s.strip()
                if s:
                    res.append(s)
        else:
            res.append(p)
    return res


def synth_one(tts, sent, speaker_wav, out_wav, lang, speed):
    tts.tts_to_file(
        text=sent,
        speaker_wav=speaker_wav,
        language=lang,
        file_path=out_wav,
        speed=speed,
    )


def main():
    ap = argparse.ArgumentParser(description="本地克隆 TTS 生成配音")
    ap.add_argument("--text", help="直接传文案")
    ap.add_argument("--text-file", help="文案文件路径（utf-8）")
    ap.add_argument("--speaker", required=True, help="说话人样本 wav")
    ap.add_argument("--out", required=True, help="输出音频路径（建议 .mp3）")
    ap.add_argument("--lang", default="zh-cn")
    ap.add_argument("--speed", type=float, default=1.0, help="语速，>1 更快")
    ap.add_argument("--pause-ms", type=int, default=160, help="句间停顿毫秒")
    args = ap.parse_args()

    if args.text:
        text = args.text
    elif args.text_file:
        with open(args.text_file, encoding="utf-8") as f:
            text = f.read()
    else:
        print("ERROR: 需要 --text 或 --text-file", file=sys.stderr)
        sys.exit(1)

    # 延迟导入，避免无 torch 环境下无法查看帮助
    from TTS.api import TTS

    print(f"[tts_clone] 加载模型 {MODEL} ...", flush=True)
    tts = TTS(MODEL).to("cpu")
    print("[tts_clone] 模型就绪", flush=True)

    sents = split_sentences(text)
    print(f"[tts_clone] 拆分为 {len(sents)} 句", flush=True)

    tmp = tempfile.mkdtemp(prefix="ttsclone_")
    wavs = []
    for i, s in enumerate(sents):
        p = os.path.join(tmp, f"{i:03d}.wav")
        synth_one(tts, s, args.speaker, p, args.lang, args.speed)
        wavs.append(p)
        print(f"  [{i+1}/{len(sents)}] {s[:20]}...", flush=True)

    # 句间补停顿后拼接
    concat_list = os.path.join(tmp, "list.txt")
    with open(concat_list, "w") as f:
        for w in wavs:
            f.write(f"file '{w}'\n")
            if args.pause_ms > 0:
                silence = os.path.join(tmp, f"pause_{i}.wav")
                subprocess.run(
                    [FF, "-y", "-f", "lavfi", "-i",
                     f"anullsrc=r=22050:cl=mono:d={args.pause_ms/1000:.3f}",
                     "-q:a", "0", silence],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                f.write(f"file '{silence}'\n")

    ext = os.path.splitext(args.out)[1].lower()
    if ext == ".mp3":
        acodec = "libmp3lame"
    elif ext in (".m4a",):
        acodec = "aac"
    else:
        acodec = "pcm_s16le"

    subprocess.run(
        [FF, "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c:a", acodec, args.out],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"[tts_clone] 完成 -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
