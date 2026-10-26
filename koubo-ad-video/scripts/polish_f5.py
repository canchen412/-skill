#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
F5-TTS 产物精修：去首字幽灵 + 尾部能量裁剪（2026-10-09 定型）
─────────────────────────────────────────────────────────────
背景：F5 zero-shot 会在生成开头泄露参考音频首词（稳定 1 字，随机内容）。
修法：whisper.cpp -ml 1 拿字级时间戳 → 找正文首字起点 → 精确裁剪；
      尾部按能量包络裁到语音结束 +0.12s。

用法: python3 polish_f5.py in.wav out.wav "正文前6字"
      正文前6字用于在字级转写里定位正文起点（跳过泄露字）。
"""
import re, subprocess, sys, wave
import numpy as np

W = "/Users/mac/.workbuddy/models/whisper.cpp"
FF = "/Users/mac/.local/bin/ffmpeg"
SR = 24000

def _hanpy(s):
    """汉字 → 无调拼音串（非汉字剔除）。用于拼音空间匹配：
    多音字替换（囤→屯）后 whisper 可能转出任意同音字，字面匹配必失败，
    拼音空间匹配稳。"""
    try:
        from pypinyin import lazy_pinyin
        return "".join(lazy_pinyin(re.sub(r"[^\u4e00-\u9fff]", "", s)))
    except Exception:
        return None


def main():
    src, dst, head = sys.argv[1], sys.argv[2], sys.argv[3]
    # 1. 字级转写
    jbase = "/tmp/_pol_json"
    subprocess.run([f"{W}/build/bin/whisper-cli", "-m", f"{W}/models/ggml-base.bin",
                    "-f", src, "-l", "zh", "-ml", "1", "-oj", "-of", jbase],
                   capture_output=True, text=True)
    import json
    segs = json.load(open(jbase + ".json"))["transcription"]
    toks = [(s["timestamps"]["from"], s["text"].strip()) for s in segs]
    # 2. 定位正文首字起点：跳过泄露字，找到与 head 前缀对齐的位置
    #    ★拼音空间匹配（2026-10-13）：多音字替换后 whisper 可能转出任意同音字
    #    （屯→吞/托…），字面匹配必失败 → 优先在拼音空间找，失败再回退字面。
    start = None
    joined = "".join(t[1] for t in toks)
    head4 = head[:min(4, len(head))]
    py_all = _hanpy(joined)
    py_head = _hanpy(head4)
    # ★首字保护：转写开头就是正文前4字 → 没有泄露字，一刀都不裁（261016 灿爹报首字被吃）
    if py_all and py_head and py_all.startswith(py_head):
        start = 0.0
    elif py_all and py_head:
        # 逐 token 累积拼音，找「累积拼音串后缀起点」处 head 拼音为前缀的 token
        acc = ""
        starts = []
        for _, txt in toks:
            starts.append(len(acc))
            acc += _hanpy(txt) or ""
        pos = acc.find(py_head)
        if pos >= 0:
            for i, s in enumerate(starts):
                if s >= pos:                # 第一个拼音起点 ≥ 匹配位的 token
                    start = toks[i][0]
                    break
    if start is None:
        for i in range(len(toks)):          # 回退：字面匹配
            seq = "".join(t[1] for t in toks[i:])
            if seq.startswith(head4):
                start = toks[i][0]          # "HH:MM:SS.mmm"
                break
    if start is None:
        print("⚠️ 未找到正文起点，保留原样"); start = "00:00:00.000"
    if isinstance(start, (int, float)):
        start_s = float(start)
    else:
        h, m, s = start.split(":"); start_s = int(h)*3600 + int(m)*60 + float(s.replace(",", "."))
    start_s = max(0.0, start_s - 0.12)  # ★261016：前移量 0.03→0.12，否则首字声母（如"脸"）被切掉
    # 3. 裁剪 + 尾部能量裁剪
    subprocess.run([FF, "-y", "-v", "error", "-ss", str(start_s), "-i", src,
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", "/tmp/_pol.wav"], check=True)
    with wave.open("/tmp/_pol.wav", "rb") as w:
        sr = w.getframerate(); a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    win = int(0.05 * sr); n = len(a) // win
    rms = np.sqrt((a[:n*win].reshape(n, win) ** 2).mean(axis=1)) if n else np.array([0])
    thr = max(rms.max() * 0.02, 0.004)
    active = np.where(rms > thr)[0]
    if len(active):
        end = min(len(a), int((active[-1] + 1) * win) + int(0.12 * sr))
        a = a[:end]
    with wave.open(dst, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(a.tobytes())
    print(f"✓ {dst}  裁头 {start_s:.2f}s  成品 {len(a)/sr:.2f}s")

if __name__ == "__main__":
    main()
