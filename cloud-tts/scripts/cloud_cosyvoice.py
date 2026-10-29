#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""百炼 CosyVoice / Qwen-Audio-TTS 云端配音：创建音色 + 合成一条。

最简用法：
    export DASHSCOPE_API_KEY=sk-xxxx
    python3 cloud_cosyvoice.py --ref 参考.wav --text-file 文案.txt --out ./out/试听

要点：
- 参考音频给**本地文件**即可。SDK 见到 file:// 会自动把它上传到 OSS，再拿
  公网 URL 去创建音色。若报 UploadFileException（Key 无存储权限），
  就改为 --ref-url 直接给一个公网可访问的音频地址。
- 复刻音色**绑定 target_model**：换模型必须用同一段音频重建一次。
- 音色 ID 存进 <out>.voice.json；下次加 --voice-id 直接复用，不重复创建（创建免费但没必要重来）。
- 语速参数各 SDK 版本命名不一致，脚本会依次尝试 speech_rate / rate，都不行就用
  ffmpeg atempo 后处理（音高不变）。
"""
import argparse
import json
import os
import pathlib
import subprocess
import sys


def log(msg):
    print(msg, flush=True)


def die(msg):
    print("[x] " + msg, file=sys.stderr, flush=True)
    sys.exit(1)


def find_ffmpeg():
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, "ffmpeg")
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return "ffmpeg"


def main():
    ap = argparse.ArgumentParser(description="百炼云端配音：建音色 + 合成")
    ap.add_argument("--ref", help="参考音频本地路径")
    ap.add_argument("--ref-url", default="", help="参考音频公网 URL（与 --ref 二选一）")
    ap.add_argument("--text", help="文案字符串")
    ap.add_argument("--text-file", help="文案 txt 路径")
    ap.add_argument("--model", default="cosyvoice-v3.5-plus",
                    help="cosyvoice-v3.5-plus / -flash / qwen-audio-3.0-tts-plus / MiniMax/speech-2.8-hd")
    ap.add_argument("--speed", type=float, default=1.2, help="语速（默认 1.2，用户定稿）")
    ap.add_argument("--prefix", default="candie", help="音色名前缀（字母数字，≤10）")
    ap.add_argument("--voice-id", default="", help="已有音色 ID，给了就跳过创建")
    ap.add_argument("--out", required=True, help="输出前缀（不带扩展名）")
    ap.add_argument("--key", default="", help="DashScope API Key，默认读 DASHSCOPE_API_KEY")
    ap.add_argument("--format", default="WAV_24000HZ_MONO_16BIT",
                    choices=["WAV_24000HZ_MONO_16BIT", "MP3_22050HZ_MONO_256KBPS"])
    a = ap.parse_args()

    key = a.key or os.environ.get("DASHSCOPE_API_KEY", "")
    if not key:
        die("缺 API Key：设环境变量 DASHSCOPE_API_KEY，或加 --key")
    if not key.startswith("sk-"):
        log("[!] 提醒：百炼 Key 一般以 sk- 开头，请确认没粘错")

    try:
        import dashscope
        from dashscope.audio.tts_v2 import AudioFormat, SpeechSynthesizer, VoiceEnrollmentService
    except ImportError:
        die("没装 dashscope。跑：pip install -U dashscope")

    dashscope.api_key = key

    text = a.text or (open(a.text_file, encoding="utf-8").read().strip() if a.text_file else "")
    if not text:
        die("缺文案：用 --text 或 --text-file")
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)

    # ---------- 1) 建音色 ----------
    voice_id = a.voice_id
    if not voice_id:
        if a.ref_url:
            uri = a.ref_url
        elif a.ref:
            rp = os.path.abspath(a.ref)
            if not os.path.isfile(rp):
                die("参考音频不存在：" + rp)
            uri = pathlib.Path(rp).as_uri()   # file:// 由 SDK 自动上传 OSS
        else:
            die("没有 --voice-id 就必须给 --ref 或 --ref-url")
        log("[1/2] 建音色  model=%s  ref=%s" % (a.model, uri.split("/")[-1][:40]))
        svc = VoiceEnrollmentService()
        voice_id = svc.create_voice(target_model=a.model, prefix=a.prefix, url=uri)
        log("      voice_id = %s" % voice_id)
        with open(out + ".voice.json", "w", encoding="utf-8") as fh:
            json.dump({"model": a.model, "voice_id": voice_id, "ref": uri,
                       "prefix": a.prefix}, fh, ensure_ascii=False, indent=1)
        log("      已存 → %s.voice.json" % out)
    else:
        log("[1/2] 复用音色 voice_id=%s" % voice_id)

    # ---------- 2) 合成 ----------
    fmt = getattr(AudioFormat, a.format)
    log("[2/2] 合成 %d 字  语速 %.2f  model=%s" % (len(text), a.speed, a.model))

    audio, used_speed = None, True
    for kw in ("speech_rate", "rate", "speed"):
        try:
            syn = SpeechSynthesizer(model=a.model, voice=voice_id, format=fmt, **{kw: a.speed})
            audio = syn.call(text)
            log("      语速参数用 %s=%.2f" % (kw, a.speed))
            break
        except TypeError:
            continue
        except Exception as exc:                      # 参数不被接受 / 网络错
            log("      %s 失败：%s" % (kw, str(exc)[:160]))
            used_speed = False
            break
    if audio is None and not used_speed:
        syn = SpeechSynthesizer(model=a.model, voice=voice_id, format=fmt)
        audio = syn.call(text)
        log("      语速交给 ffmpeg 后处理")
    if not audio:
        die("合成返回空音频")

    ext = ".wav" if a.format.startswith("WAV") else ".mp3"
    raw = out + ext
    with open(raw, "wb") as fh:
        fh.write(audio)
    log("      → %s  (%d 字节)" % (raw, len(audio)))

    # ---------- 3) 变速 + 转 mp3 ----------
    ff = find_ffmpeg()
    mp3 = out + ".mp3"
    chain = []
    if not used_speed and abs(a.speed - 1.0) > 0.001:
        chain.append("atempo=%.4f" % a.speed)
    cmd = [ff, "-y", "-loglevel", "error", "-i", raw]
    if chain:
        cmd += ["-af", ",".join(chain)]
    cmd += ["-ar", "24000", "-ac", "1", "-b:a", "192k", mp3]
    subprocess.run(cmd, check=True)
    log("      → %s" % mp3)
    log("")
    log("完成。试听：%s" % mp3)


if __name__ == "__main__":
    main()
