#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百炼 CosyVoice **逐句合成 + 拼接**（替代「整段合成 + 事后切音频补停顿」）。

═══ 为什么必须换这条通路（2026-10-29 第三次返工的血证）═══

同一个模型、同一段文本，在**文本末尾**和**文本中间**的句号处，行完全不同的两套韵律：

    整段合成         句号处只给 25ms 碎片气口（「米糊。外胚」几乎连着读）
    逐句单独合成     句末自带 **380～510ms** 完整收尾停顿

也就是说，模型本来就会好好收句，只是被「一次喂一整段」这个用法压掉了。
于是「补停顿」根本不该在音频上动刀 —— 那样必然切在字上，听感就是
「每句最后一两个字被掐断、卡一下」（灿爹连续两次反馈的原话）。

本条通路的做法：
  ① 每句单独一次 WebSocket 合成，拿到该句**完整的句末收尾**；
  ② 只裁掉段首/段末的**真静音**（-50dB 以下），绝不切到字；
  ③ 句间停顿 = 段末保留 + 段首保留，纯拼接，零切口；
  ④ 逐字时间戳按段累积偏移合并，下游（mk_tokens_cloud / 字幕 / 切镜）零改动。

═══ 已知特性（别再当 bug）═══
  · CosyVoice 合成是**确定性**的：同文本同 seed 逐样本完全一致，重跑等于白跑。
    要换韵律只能换 seed 或换文本切分，不能指望「再生成一次就好了」。
  · SSML 被服务端拒绝：`SSML text is not supported at the moment!`
  · 文本里加空格会让停顿直接消失（实测 2.76s，全片无气口）；换行也不加停顿。
  · 重复标点（"。。。"）确实能拉长停顿，但会把 normalized_text 里的句号变成「…」，
    破坏 mk_tokens_cloud 的逐字对齐 —— 不许用。

用法：
    python tts_split_cloud.py --phrases proj/phrases.txt --out proj/audio/vo \
        --tail 0.34 --head 0.03 --jobs 3
产出：<out>.wav / <out>.words.json / <out>.mp3 / <out>.srt（与整段版同名同格式）
"""
import argparse
import io
import json
import os
import re
import sys
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

CLOUD_SCRIPTS = os.path.expanduser("~/.workbuddy/skills/cloud-tts/scripts")
sys.path.insert(0, CLOUD_SCRIPTS)
from cloud_cosyvoice_ws import synth, ms_to_srt          # noqa: E402

SECRETS_ENV = os.path.expanduser("~/.workbuddy/secrets/dashscope.env")


def load_env(path=SECRETS_ENV):
    if not os.path.exists(path):
        return
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        ln = re.sub(r"^export\s+", "", ln)
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v:
            os.environ.setdefault(k, v)


def wav_to_np(b):
    with wave.open(io.BytesIO(b), "rb") as w:
        assert w.getsampwidth() == 2, "只支持 16bit"
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    return a.astype(np.float32) / 32768.0, sr


def np_to_wav_bytes(a, sr):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes())
    return buf.getvalue()


def trim_silence(a, sr, head_keep, tail_keep, thr_db=-50.0, hop=0.005):
    """裁掉首尾**真静音**（低于 thr_db 的连续段），各保留 head_keep / tail_keep。

    只动 -50dB 以下的样本，绝不碰发音 —— 这是「不卡」的底线。
    返回 (新音频, 头部裁掉秒数)
    """
    if len(a) == 0:
        return a, 0.0
    n = int(sr * hop)
    m = len(a) // n
    if m < 4:
        return a, 0.0
    db = 20 * np.log10(np.sqrt((a[:m * n].reshape(m, n) ** 2).mean(axis=1)) + 1e-9)
    # 段首
    i = 0
    while i < m and db[i] < thr_db:
        i += 1
    head_sil = i * hop
    cut_head = max(0.0, head_sil - head_keep)
    # 段末
    j = m - 1
    while j >= 0 and db[j] < thr_db:
        j -= 1
    tail_sil = (m - 1 - j) * hop
    cut_tail = max(0.0, tail_sil - tail_keep)
    s = int(cut_head * sr)
    e = len(a) - int(cut_tail * sr)
    if e <= s + int(0.05 * sr):
        return a, 0.0
    return a[s:e], cut_head


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phrases", required=True, help="phrases.txt，一行一句")
    ap.add_argument("--out", required=True, help="输出前缀，如 proj/audio/vo")
    ap.add_argument("--tail", type=float, default=0.34, help="句末保留静音秒（句间停顿主要来源）")
    ap.add_argument("--head", type=float, default=0.03, help="句首保留静音秒（避免起音被吃掉）")
    ap.add_argument("--jobs", type=int, default=3, help="并发合成数")
    ap.add_argument("--model", default=None)
    ap.add_argument("--voice-id", default=None)
    ap.add_argument("--rate", type=float, default=None)
    ap.add_argument("--sample-rate", type=int, default=24000)
    a = ap.parse_args()

    load_env()
    key = os.environ.get("DASHSCOPE_API_KEY") or ""
    if not key:
        sys.exit("缺 DASHSCOPE_API_KEY（配 ~/.workbuddy/secrets/dashscope.env）")
    model = a.model or os.environ.get("COSYVOICE_MODEL", "cosyvoice-v3.5-plus")
    voice = a.voice_id or os.environ.get("COSYVOICE_VOICE_ID", "")
    rate = a.rate or float(os.environ.get("COSYVOICE_RATE", "1.0"))
    if not voice:
        sys.exit("缺 COSYVOICE_VOICE_ID")

    phrases = [ln.strip() for ln in open(a.phrases, encoding="utf-8") if ln.strip()]
    if not phrases:
        sys.exit("phrases 为空")
    print("逐句合成 %d 句  model=%s rate=%.2f jobs=%d" % (len(phrases), model, rate, a.jobs))

    def one(idx_txt):
        idx, txt = idx_txt
        words, norm, b = synth(key, model, voice, txt, rate, a.sample_rate,
                               "/dev/null", quiet=True)
        return idx, words, norm, b

    results = [None] * len(phrases)
    with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
        for idx, words, norm, b in ex.map(one, list(enumerate(phrases))):
            results[idx] = (words, norm, b)
            print("  [%d/%d] %d字 音频%d字节" % (idx + 1, len(phrases), len(phrases[idx]), len(b)),
                  flush=True)

    pieces, all_words, cursor, sr = [], [], 0.0, a.sample_rate
    for idx, (words, norm, b) in enumerate(results):
        audio, sr = wav_to_np(b)
        audio, cut_head = trim_silence(audio, sr, a.head, a.tail)
        off = cursor - cut_head
        for w in words:
            all_words.append({
                "text": w["text"],
                "begin_index": w.get("begin_index"),
                "end_index": w.get("end_index"),
                "begin_time": int(round((w["begin_time"] / 1000.0 + off) * 1000)),
                "end_time": int(round((w["end_time"] / 1000.0 + off) * 1000)),
            })
        pieces.append(audio)
        cursor = off + len(audio) / sr + cut_head  # = 上一段结束时刻

    full = np.concatenate(pieces)
    dur = len(full) / sr
    print("拼接完成 %.2fs（%d 段）" % (dur, len(phrases)))

    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out + ".wav", "wb") as fh:
        fh.write(np_to_wav_bytes(full, sr))

    meta = {
        "model": model, "voice_id": voice, "rate": rate,
        "mode": "split_sentence",
        "char_count": sum(len(p) for p in phrases),
        "duration_sec": round(dur, 3),
        "tail_keep": a.tail, "head_keep": a.head,
        "original_text": "".join(phrases),
        "normalized_text": "".join(r[1] for r in results if r[1]),
        "words": all_words,
    }
    with open(out + ".words.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)

    import subprocess
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out + ".wav",
                    "-ar", str(sr), "-ac", "1", "-b:a", "192k", out + ".mp3"], check=True)

    lines, cur = [], []
    for i, w in enumerate(all_words):
        cur.append(w)
        nxt = all_words[i + 1] if i + 1 < len(all_words) else None
        if (not nxt) or re.search(r"[。！？；，、：,.!?;:]$", w["text"]) \
                or (nxt and len("".join(x["text"] for x in cur)) >= 16):
            lines.append(cur); cur = []
    if cur:
        lines.append(cur)
    with open(out + ".srt", "w", encoding="utf-8") as fh:
        for i, ln in enumerate(lines, 1):
            fh.write("%d\n%s --> %s\n%s\n\n" % (
                i, ms_to_srt(ln[0]["begin_time"]), ms_to_srt(ln[-1]["end_time"]),
                "".join(x["text"] for x in ln)))
    print("完成：%s.wav / .words.json / .mp3 / .srt" % out)


if __name__ == "__main__":
    main()
