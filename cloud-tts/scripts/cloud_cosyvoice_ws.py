#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百炼 CosyVoice 云端配音（原生 WebSocket 通路）—— 合成 + 逐字时间戳一次拿全。

为什么不用 dashscope SDK 的 SpeechSynthesizer：
    SDK 只回音频，**不暴露词级时间戳**（speech_synthesizer.py 里那两个
    timestamp 只是首包延迟统计）。而词级时间戳正是能替掉本地 whisper
    逐字对齐的关键产物，所以走原生 WS 协议。

协议要点（2026-10-16 实测通过，cosyvoice-v3.5-plus）：
    run-task      → parameters 里带 word_timestamp_enabled: true
    continue-task → input.text
    finish-task
    事件：task-started / result-generated / task-finished / task-failed
    · 音频走**二进制帧**（不是 base64），必须单独收集，别当 JSON 解析
    · result-generated 的 payload.output.sentence.words 即逐字时间戳，
      字段 {text, begin_index, end_index, begin_time, end_time}，单位毫秒
    · 文字被**规范化后再对齐**：output.normalized_text 里「19块9」变
      「十九块九」，begin_index 是按规范化文本计的 —— 做字幕要用
      normalized_text，别用原文案去索引。

用法：
    export DASHSCOPE_API_KEY=sk-xxxx
    python3 cloud_cosyvoice_ws.py --voice-id cosyvoice-v3.5-plus-bailian-xxx \\
        --model cosyvoice-v3.5-plus --text-file 文案.txt --rate 1.0 --out ./out/试听

产出：<out>.wav / <out>.mp3 / <out>.words.json / <out>.srt
"""
import argparse
import base64
import json
import os
import re
import subprocess
import sys
import uuid

import websocket  # websocket-client

WS_URL = os.environ.get("DASHSCOPE_WS_URL",
                        "wss://dashscope.aliyuncs.com/api-ws/v1/inference")


def die(msg):
    print("[x] " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def ms_to_srt(t):
    h, t = divmod(t, 3600000)
    m, t = divmod(t, 60000)
    s, ms = divmod(t, 1000)
    return "%02d:%02d:%02d,%03d" % (h, m, s, ms)


def synth(key, model, voice, text, rate, sample_rate, out_wav, quiet=False):
    """返回 (words列表, normalized_text, 音频字节)"""
    tid = uuid.uuid4().hex
    ws = websocket.create_connection(
        WS_URL, header={"Authorization": "Bearer " + key}, timeout=120)

    def send(action, payload):
        ws.send(json.dumps({
            "header": {"action": action, "task_id": tid, "streaming": "duplex"},
            "payload": payload,
        }, ensure_ascii=False))

    send("run-task", {
        "model": model, "task_group": "audio", "task": "tts",
        "function": "SpeechSynthesizer", "input": {},
        "parameters": {
            "voice": voice, "volume": 50, "text_type": "PlainText",
            "sample_rate": sample_rate, "rate": rate, "format": "wav",
            "pitch": 1.0, "seed": 0, "type": 0,
            "word_timestamp_enabled": True,      # ★ 关键开关
        },
    })

    audio = bytearray()
    started = pushed = finished = False
    best_words, normalized = [], ""
    err = None

    while not finished:
        raw = ws.recv()
        if isinstance(raw, bytes):           # ★ 音频是二进制帧
            audio.extend(raw)
            continue
        msg = json.loads(raw)
        ev = msg.get("header", {}).get("event", "")
        if ev == "task-started" and not started:
            started = True
            if not quiet:
                print("[ws] task-started")
            send("continue-task", {
                "model": model, "task_group": "audio", "task": "tts",
                "function": "SpeechSynthesizer", "input": {"text": text},
            })
            pushed = True
            send("finish-task", {"input": {}})
        elif ev == "result-generated":
            out_ = msg.get("payload", {}).get("output", {}) or {}
            sent = out_.get("sentence") or {}
            w = sent.get("words") or []
            if len(w) >= len(best_words):     # 流式更新，取最完整的一份
                best_words, normalized = w, out_.get("normalized_text", normalized)
        elif ev == "task-finished":
            finished = True
        elif ev == "task-failed":
            err = msg
            finished = True
    ws.close()

    if err:
        die("合成失败：" + json.dumps(err, ensure_ascii=False)[:600])
    if not audio:
        die("合成返回空音频")
    return best_words, normalized, bytes(audio)


def main():
    ap = argparse.ArgumentParser(
        description="CosyVoice 云端配音（WS 通路，带逐字时间戳）")
    ap.add_argument("--text")
    ap.add_argument("--text-file")
    ap.add_argument("--model", default=os.environ.get("COSYVOICE_MODEL", "cosyvoice-v3.5-plus"))
    ap.add_argument("--voice-id", default=os.environ.get("COSYVOICE_VOICE_ID", ""),
                    help="音色 ID。★默认读环境变量 COSYVOICE_VOICE_ID"
                         "（定稿音色已固化在 ~/.workbuddy/secrets/dashscope.env）")
    ap.add_argument("--rate", type=float,
                    default=float(os.environ.get("COSYVOICE_RATE", "1.1") or 1.1),
                    help="语速。★ 2026-10-16 实测：云端 rate=1.1 → 33.63s，"
                         "与本地 F5 的 33.86s 相差 0.7%%；rate=1.0 慢 8%%，"
                         "rate=1.2（F5 时代的补偿值）快 15%%。默认 1.1")
    ap.add_argument("--sample-rate", type=int, default=24000)
    ap.add_argument("--out", required=True, help="输出前缀（不带扩展名）")
    ap.add_argument("--key", default="", help="默认读 DASHSCOPE_API_KEY")
    a = ap.parse_args()

    if not a.voice_id:
        die("缺音色 ID：设 COSYVOICE_VOICE_ID 或加 --voice-id")

    key = a.key or os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        die("缺 API Key：设 DASHSCOPE_API_KEY 或加 --key")

    text = a.text or (open(a.text_file, encoding="utf-8").read().strip()
                      if a.text_file else "")
    if not text:
        die("缺文案：--text 或 --text-file")

    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    print("[1/3] 合成 model=%s rate=%.2f 字数=%d" % (a.model, a.rate, len(text)))
    words, normalized, audio = synth(key, a.model, a.voice_id, text,
                                     a.rate, a.sample_rate, out + ".wav")
    with open(out + ".wav", "wb") as fh:
        fh.write(audio)
    dur = None
    try:
        dur = float(subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", out + ".wav"]).decode().strip())
    except Exception:
        pass
    print("      → %s.wav (%d 字节%s)" % (out, len(audio),
          ", %.2fs" % dur if dur else ""))

    print("[2/3] 时间戳 词数=%d" % len(words))
    meta = {"model": a.model, "voice_id": a.voice_id, "rate": a.rate,
            "char_count": len(text), "duration_sec": dur,
            "chars_per_sec": round(len(text) / dur, 2) if dur else None,
            "original_text": text, "normalized_text": normalized,
            "words": words}
    with open(out + ".words.json", "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    print("      → %s.words.json" % out)
    if words:
        print("      首词 %s@%dms  末词 %s@%dms"
              % (words[0]["text"], words[0]["begin_time"],
                 words[-1]["text"], words[-1]["end_time"]))
    if normalized and normalized != text:
        print("      ⚠ 文字被规范化（索引请按 normalized_text）：%s"
              % normalized[:60])

    print("[3/3] 转 mp3 + 出 srt")
    mp3 = out + ".mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", out + ".wav",
                    "-ar", "24000", "-ac", "1", "-b:a", "192k", mp3], check=True)
    print("      → %s" % mp3)

    if words:
        # 按标点把逐字聚合成行，避免一行一字不可读
        lines, cur = [], []
        for i, w in enumerate(words):
            cur.append(w)
            nxt = words[i + 1] if i + 1 < len(words) else None
            if (not nxt) or re.search(r"[。！？；，、：,.!?;:]$", w["text"]) \
                    or (nxt and len("".join(x["text"] for x in cur)) >= 16):
                lines.append(cur)
                cur = []
        if cur:
            lines.append(cur)
        with open(out + ".srt", "w", encoding="utf-8") as fh:
            for idx, ln in enumerate(lines, 1):
                fh.write("%d\n%s --> %s\n%s\n\n" % (
                    idx, ms_to_srt(ln[0]["begin_time"]), ms_to_srt(ln[-1]["end_time"]),
                    "".join(x["text"] for x in ln)))
        print("      → %s.srt（%d 行）" % (out, len(lines)))

    print("\n完成。试听：%s" % mp3)


if __name__ == "__main__":
    main()
