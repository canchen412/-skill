#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""【参考实现·欧扎克261018】v2 局部重合成：只换「卷播字层」与「数字动效层」，底片/配音/BGM 全部复用。

为什么不整跑 pipeline：整跑会重渲底片 ⇒ BGM 曲池随机换曲 + sub_style 随机换款，
同一条片两次渲出来不一样。本片底片（out_base.mp4）已经定稿，只重做 3 步：
  ① fx 序列 overlay 回底片            → base_fx.mp4
  ② 卷播段（新字层）+ 混音 wav concat  → final.mp4
  ③ 轮播段字幕二次烧录 + 封面帧 + 交付规格 → final_cov.mp4

用法：python _scripts/rebuild_v2.py [--from 1]
"""
import glob
import os
import shutil
import subprocess
import sys

FF = "/Users/mac/.local/bin/ffmpeg"
FFPROBE = "/Users/mac/.local/bin/ffprobe"
PY = "/Users/mac/.workbuddy/binaries/python/envs/default/bin/python"
ROOT = os.environ.get("KOUBO_PROJECT", "/Users/mac/WorkBuddy/2026-10-10-10-50-03/欧扎克麦片_视频")
# ★改成自己的工程目录，或 export KOUBO_PROJECT=<工程根>
W = f"{ROOT}/ws"
S = f"{W}/v1"
R = f"{S}/render"
SKILL = "/Users/mac/.workbuddy/skills/koubo-ad-video"
CUT = 2.94
FONTS = f"{SKILL}/assets/fonts"


def sh(cmd):
    print("  $", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd))[:160], flush=True)
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stdout[-1500:])
        print(r.stderr[-1500:])
        raise SystemExit(f"FAILED: {cmd[0]}")
    return r


def step(msg):
    print(f"\n══ {msg}", flush=True)


def dur(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                        "-of", "csv=p=0", path], capture_output=True, text=True)
    return float(r.stdout.strip())


# ── 1. fx 序列 overlay ───────────────────────────────────────────
step("1/6 fx 层 overlay")
files = sorted(glob.glob(f"{W}/rem3d/out/fx_v1/element-*.png"))
if not files:
    raise SystemExit("fx 序列为空")
pad = len(os.path.basename(files[0]).split("-")[1].split(".")[0])
print(f"  序列 {len(files)} 帧，pad=%d" % pad, flush=True)
assert os.path.exists(f"{R}/out_base.mp4"), "缺底片 out_base.mp4"
sh([FF, "-y", "-v", "error", "-framerate", "30",
    "-i", f"{W}/rem3d/out/fx_v1/element-%0{pad}d.png",
    "-i", f"{R}/out_base.mp4",
    "-filter_complex", "[1:v][0:v]overlay=0:0:eof_action=endall:format=auto[v]",
    "-map", "[v]", "-map", "1:a", "-c:v", "h264_videotoolbox", "-q:v", "75",
    "-c:a", "copy", f"{R}/base_fx.mp4"])
print(f"  base_fx.mp4 {os.path.getsize(f'{R}/base_fx.mp4')/1048576:.1f}MB", flush=True)

# ── 2. 卷播段混音（复用定式函数）────────────────────────────────
step("2/6 卷播混音")
sys.path.insert(0, f"{SKILL}/scripts")
import importlib.util  # noqa: E402

spec = importlib.util.spec_from_file_location("pl", f"{SKILL}/scripts/pipeline_run.py")
pl = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pl)
import json  # noqa: E402
C = json.load(open(f"{W}/config.json", encoding="utf-8"))
os.makedirs(f"{W}/banner", exist_ok=True)
wav = f"{W}/banner/banner_final_v1.wav"
pl.banner_mix(S, f"{R}/base_fx.mp4", CUT, 1.44, 1.84, wav,
              C["sfx_scroll"], C["sfx_dong"],
              sfx_as_is=bool(C.get("sfx_scroll_as_is")),
              sfx_gain=float(C.get("sfx_scroll_gain", 0.72)))
print(f"  banner wav {dur(wav):.2f}s", flush=True)

# ── 3. concat 卷播 + 底片 ───────────────────────────────────────
step("3/6 拼接")
sh([FF, "-y", "-v", "error",
    "-i", f"{W}/rem3d/out/banner_v1.mp4", "-i", wav,
    "-ss", str(CUT), "-i", f"{R}/base_fx.mp4",
    "-filter_complex",
    "[0:v]fps=30,settb=AVTB[v0];[1:a]aresample=48000,asettb=AVTB[a0];"
    "[2:v]fps=30,settb=AVTB[v1];[2:a]aresample=48000,asettb=AVTB[a1];"
    "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
    "-map", "[v]", "-map", "[a]", "-c:v", "h264_videotoolbox", "-q:v", "75",
    "-c:a", "aac", "-b:a", "128k", f"{R}/final.mp4"])
print(f"  final.mp4 {dur(f'{R}/final.mp4'):.2f}s", flush=True)

# ── 4. 轮播段字幕二次烧录 ───────────────────────────────────────
step("4/6 轮播段字幕")
ass = f"{S}/banner_subs.ass"
assert os.path.exists(ass), "缺 banner_subs.ass"
sh([FF, "-y", "-v", "error", "-i", f"{R}/final.mp4",
    "-vf", f"subtitles={ass}:fontsdir={FONTS}",
    "-c:v", "h264_videotoolbox", "-q:v", "75", "-c:a", "copy",
    f"{R}/final_bsub.mp4"])
shutil.move(f"{R}/final_bsub.mp4", f"{R}/final.mp4")
print("  ✓", flush=True)

# ── 5. 封面帧 + 交付规格 ────────────────────────────────────────
step("5/6 封面帧 + 交付规格（CRF21 / faststart / alimiter）")
cover = f"{SKILL}/assets/cover/cover_frame_1080p.jpg"
sh([FF, "-y", "-v", "error",
    "-loop", "1", "-t", "0.034", "-r", "30", "-i", cover,
    "-i", f"{R}/final.mp4",
    "-filter_complex",
    "[0:v]settb=AVTB,fps=30[c];[1:v]settb=AVTB,fps=30[f];[c][f]concat=n=2:v=1:a=0[v]",
    "-map", "[v]", "-map", "1:a",
    "-c:v", "libx264", "-crf", str(C.get("deliver_crf", 21)),
    "-preset", "medium", "-profile:v", "high", "-level", "4.0",
    "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    "-af", "alimiter=limit=0.78:attack=5:release=60:level=disabled",
    "-c:a", "aac", "-b:a", "128k", f"{R}/final_cov.mp4"])
shutil.move(f"{R}/final_cov.mp4", f"{R}/final.mp4")
print(f"  ✓ {dur(f'{R}/final.mp4'):.2f}s "
      f"{os.path.getsize(f'{R}/final.mp4')/1048576:.1f}MB", flush=True)

# ── 6. 漂浮粒子 ────────────────────────────────────────────────
step("6/6 漂浮粒子")
shutil.copy2(f"{R}/final.mp4", f"{R}/final_nodust.mp4")
sh([PY, f"{SKILL}/scripts/add_dust_to_video.py",
    "--src", f"{R}/final.mp4", "--out", f"{R}/final_dust.mp4",
    "--count", str(C.get("dust_count", 88)),
    "--strength", str(C.get("dust_strength", 1.9)),
    "--size", str(C.get("dust_size", 1.5)),
    "--crf", str(C.get("deliver_crf", 21)),
    "--skip-first", "1"])
shutil.move(f"{R}/final_dust.mp4", f"{R}/final.mp4")
print(f"\n══ 完成 {dur(f'{R}/final.mp4'):.2f}s "
      f"{os.path.getsize(f'{R}/final.mp4')/1048576:.1f}MB", flush=True)
