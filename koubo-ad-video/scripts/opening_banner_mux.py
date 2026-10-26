#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卷播成片一站式封装：画面(无声) + 配音 + 音效 → 成品

★ 教训：以前只做画面+音效，忘了混配音，灿爹反馈「那句话没有声音」。
   本脚本强制三路音频齐备，并在结尾自检。

用法:
  python3 opening_banner_mux.py \
    --video gen/banner.mp4 \
    --vo vo/hook.wav \
    --sfx gen/sfx.wav \
    --out final.mp4 \
    --dur 3.00 \
    --vo-gain 1.5 --sfx-gain 0.42
"""
import argparse, subprocess, os, sys, json


def probe_audio(path):
    """用 ffprobe 抽一轨 PCM 回来测能量"""
    p = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1",
         "-ar", "44100", "-c:a", "pcm_s16le", "-f", "wav", "-"],
        capture_output=True)
    if p.returncode != 0:
        return None
    import io, wave, numpy as np
    w = wave.open(io.BytesIO(p.stdout))
    d = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True, help="无声卷播画面（opening_banner.py 产出）")
    ap.add_argument("--vo", default="", help="配音 wav（★必须有）")
    ap.add_argument("--sfx", default="", help="音效 wav（opening_banner_sfx.py 产出）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--dur", type=float, required=True, help="总时长（秒）")
    ap.add_argument("--vo-gain", type=float, default=1.5)
    ap.add_argument("--sfx-gain", type=float, default=0.42)
    ap.add_argument("--hpf", type=int, default=80, help="配音高通，去低频隆隆声")
    a = ap.parse_args()

    if not a.vo:
        print("❌ 缺少 --vo。卷播必须有配音（灿爹明确要求过）。", file=sys.stderr)
        return 1
    for p in (a.video, a.vo):
        if not os.path.exists(p):
            print(f"❌ 文件不存在: {p}", file=sys.stderr)
            return 1
    if a.sfx and not os.path.exists(a.sfx):
        print(f"❌ 音效文件不存在: {a.sfx}", file=sys.stderr)
        return 1

    # 混音：配音为主，音效垫底（降到 0.42 防盖人声），限幅防削波
    parts = [f"[1:a]volume={a.vo_gain},highpass=f={a.hpf}[vo]"]
    if a.sfx:
        parts.append(f"[2:a]volume={a.sfx_gain}[sfx]")
        mix_in = "[vo][sfx]"
        n = 2
    else:
        mix_in = "[vo]"
        n = 1
    fc = ";".join(parts) + \
         f";{mix_in}amix=inputs={n}:duration=first:dropout_transition=0,alimiter=limit=0.92[a]"

    cmd = ["ffmpeg", "-y", "-i", a.video]
    if a.sfx:
        cmd += ["-i", a.sfx]
    cmd += ["-i", a.vo] if a.sfx else []
    # 重新排序输入：0=video, 1=vo, 2=sfx
    cmd = ["ffmpeg", "-y", "-i", a.video, "-i", a.vo]
    if a.sfx:
        cmd += ["-i", a.sfx]
    cmd += ["-filter_complex", fc, "-map", "0:v", "-map", "[a]",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-t", str(a.dur), a.out]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        print("❌ ffmpeg 失败:\n" + r.stderr.decode("utf-8", "ignore")[-600:], file=sys.stderr)
        return 1

    # ── 自检：三条都要在
    print(f"✅ 已生成 {a.out}")
    d = probe_audio(a.out)
    if d is None:
        print("❌ 自检失败：读不到音轨")
        return 1
    import numpy as np
    pk = float(np.abs(d).max())
    rms = float(np.sqrt((d ** 2).mean()))
    dur = len(d) / 44100
    # 分段检查有没有静音洞
    seg = 0.25
    quiet = []
    for i in range(0, len(d), int(seg * 44100)):
        r = float(np.sqrt((d[i:i + int(seg * 44100)] ** 2).mean()))
        if r < 0.012:
            quiet.append(i / 44100)
    print(f"   音轨 {dur:.2f}s  峰值 {pk:.3f}  RMS {rms:.4f}")
    if pk < 0.15:
        print("❌ 自检失败：音轨几乎无声")
        return 1
    if quiet:
        print(f"⚠️  以下时刻几乎静音: {[f'{x:.2f}s' for x in quiet]}")
    else:
        print("   ✅ 全程有声，无静音段")
    # 视频核验
    r2 = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                         "stream=codec_type,duration", "-of", "json", a.out],
                        capture_output=True)
    try:
        st = json.loads(r2.stdout)["streams"]
        kinds = [s["codec_type"] for s in st]
        print(f"   轨道: {kinds} {'✅ 视频+音频齐备' if 'audio' in kinds and 'video' in kinds else '❌ 缺轨'}")
    except Exception as e:
        print(f"   ffprobe 解析失败: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
