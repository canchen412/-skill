#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""给卷播画面混入音轨（★原版丢音效的根因修复）。

原版 gen/片头卷播_B_定格咚.mp4 只有视频轨 → build_ad_video.py 的 own_audio:true
空接，「突然加速到暂停」从未进成片。这里把音效+配音真正烧成一条带音轨的 mp4。

音效层：
  ① 突然加速到暂停（卷播滚动）→ 压到卷播段时长
  ② 定格咚 第3版（咚＋低频下潜）→ 落在 focus-at
  ③ 钩子配音（seg_01_fast，1.35 倍速）
"""
import json, os, subprocess, sys, wave
import numpy as np

# 工作目录：argv[8] > 环境变量 KOUBO_WD > 当前目录（★别写死某个项目，换片就跑不了）
W = sys.argv[8] if len(sys.argv) > 8 else (os.environ.get("KOUBO_WD") or os.getcwd())
FF = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")
SR = 48000
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 6.84
FOCUS = float(sys.argv[2]) if len(sys.argv) > 2 else 4.30
HOOK_SEG = sys.argv[3] if len(sys.argv) > 3 else ""   # 留空 = 直接用 argv[7] 的全局 vo
DONG_SRC = sys.argv[5] if len(sys.argv) > 5 else f"{W}/gen/定格咚_B_trim.mp3"
# ★ 灿爹「音效不是昨天确认好的吗？怎么又改了？」（2026-10-07）
#   定格咚**必须用昨天定稿那份** gen/定格咚_B_trim.mp3（1.60s，已调好音色/动态）。
#   别退回~/Downloads/咚的一声综艺开头.mp3 原素材（5.67s，质心 1428Hz vs 定稿 1969Hz，偏闷）。
#   卷播滚动同样优先用昨天定稿的 gen/卷播滚动_B.mp3。
ROLL_SRC = sys.argv[4] if len(sys.argv) > 4 else f"{W}/gen/卷播滚动_B.mp3"
# 版本号参数化（argv[6]，默认 B9）：读 gen/卷播_<TAG>_画面.mp4 → gen/片头卷播_<TAG>_带音轨.mp4
VID_TAG = sys.argv[6] if len(sys.argv) > 6 else "B9"
# ★ argv[7] 全局配音源（A 条=gen/vo_with_pause_s1.mp3，B 条=s2/audio/vo.mp3）
VO_SRC = sys.argv[7] if len(sys.argv) > 7 else f"{W}/s2/audio/vo.mp3"


def rd(p, sr=SR):
    out = f"/tmp/_ab_{abs(hash(p))}.wav"
    subprocess.run([FF, "-y", "-v", "error", "-i", p, "-ar", str(sr), "-ac", "1",
                    "-c:a", "pcm_s16le", out], check=True)
    with wave.open(out, "rb") as w:
        a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    return a


N = int(DUR * SR)
mix = np.zeros(N, dtype=np.float32)

# ③ 配音：**先算人声包络**，给音效做闪避（ducking）
# ★ 2026-10-07 灿爹「前面的配音声音比后面小很多」根因：
#   滚动音效 1.45 倍常驻整个 0→focus 区间，而整段混合又按**峰值归一到 0.92**
#   ⇒ 峰值由音效决定 ⇒ 人声被整体反推下去、再被音效掩蔽，听着像"配音变小"。
#   修法：① 音效被人声包络闪避（人声一起来就压下去，人声空隙里抬起）；
#        ② 音效基础增益从 1.45 降到 0.90；③ 人声增益 1.08 → 1.25。
vo_full = rd(VO_SRC)[:N]
if len(vo_full) < N:
    vo_full = np.pad(vo_full, (0, N - len(vo_full)))
_k = max(1, int(0.05 * SR))
_env = np.convolve(np.abs(vo_full), np.ones(_k) / _k, mode="same")
_lvl = _env / max(float(_env.max()), 1e-9)
# 门限：包络 <0.05 视为没人声（不压），>0.35 压到底（DUCK_DEPTH）
DUCK_DEPTH = 0.60
duck = 1.0 - DUCK_DEPTH * np.clip((_lvl - 0.05) / 0.30, 0.0, 1.0)
_k2 = max(1, int(0.03 * SR))
duck = np.convolve(duck, np.ones(_k2) / _k2, mode="same").astype(np.float32)
print(f"[ab] 人声闪避：最深 -{DUCK_DEPTH*100:.0f}%（人声间隙保留音效）", flush=True)

# ① 卷播滚动音效：拉伸/压缩到 0 → focus 区间（并被人声闪避）
roll = rd(ROLL_SRC)
tgt = int(FOCUS * SR)
idx = np.linspace(0, len(roll) - 1, tgt)
r = np.interp(idx, np.arange(len(roll)), roll).astype(np.float32)
mix[:tgt] += r * 0.90 * duck[:tgt]

# ② 定格咚：落在 focus-at。
#★ 用昨天定稿素材的**完整有效段**（含调好的动态），只压掉尾部静音/混响余韵，
#   不做重新归一化（会把昨天调好的音色动态覆盖掉 → 变闷）。
dong = rd(DONG_SRC)
env_w = int(0.02 * SR)
d_n = len(dong) // env_w
denv = np.array([np.sqrt((dong[i*env_w:(i+1)*env_w] ** 2).mean()) for i in range(d_n)])
nz = np.where(denv > denv.max() * 0.04)[0]
# 尾部留 0.30s 让混响自然衰减（昨天的 trim 已含恰当尾巴）
tail_keep = int(round(0.30 * SR))
cut = min(len(dong), (nz[-1] + 1) * env_w + tail_keep) if len(nz) else len(dong)
d = dong[:cut]
d = d / max(float(np.abs(d).max()), 1e-9) * 0.72   # 仅做峰值对齐，不改包络形状
s0 = int(FOCUS * SR)
n = min(len(d), N - s0)
mix[s0:s0 + n] += d[:n]

# ③ 配音：★卷播现在吃掉镜1+镜2+镜3，所以要带全局 vo 的前 DUR 秒
#    （钩子 + 0.22s 停顿 + seg_02 前半段）。成片侧 own_audio 会静音同区间全局 vo，
#    这里必须自带，否则镜2/镜3 会没声音。
mix += vo_full * 1.25

pk = float(np.abs(mix).max())
mix = mix * (0.92 / pk)
print(f"[ab] 峰值 {pk:.3f} → 0.920  (无削波)", flush=True)

out = f"{W}/gen/banner_B2_audio.wav"
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes())

# 混成最终卷播 mp4（视频直接 copy，不重编码）
fin = f"{W}/gen/片头卷播_{VID_TAG}_带音轨.mp4"
subprocess.run([FF, "-y", "-v", "error", "-i", f"{W}/gen/卷播_{VID_TAG}_画面.mp4",
                "-i", out, "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-t", f"{DUR}", "-movflags", "+faststart", fin], check=True)
subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                "stream=index,codec_type,codec_name,duration", "-of", "default=nw=1", fin])
print(f"[ab] → {fin}", flush=True)
print("[ab] DONE", flush=True)