#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
BGM 预处理：把「前段小声、后段大声」的成品音乐拉平成等响背景乐，并整体压到人声之下。

为什么需要：
  流媒体/短视频下载来的 BGM 往往有两类毛病——
  ① 动态大：前奏 -17dB、入歌后 -9dB，直接混音就会"后面突然盖住配音"；
  ② 峰值顶满：原曲 max_volume ≈ 0dB，混音里几乎没有余量。
  这两点跟"配音优先"的口播片需求完全冲突，所以在**入库时一次性离线处理**，
  渲染时直接读这条拉平过的 wav，既省渲染时间，也避免每次渲染重复跑 dynaudnorm。

处理链（本地 ffmpeg，0 成本）：
  aformat  → acompressor  (压掉主动态)
           → dynaudnorm   (逐窗归一，把弱前奏抬起来 / 强段压下来 → 段落等响)
           → equalizer    (2.5kHz / 800Hz 给人声让路，BGM 不必变小声也"躲"得开)
           → loudnorm     (整段响度统一到 target LUFS)
           → alimiter     (锁死峰值，给混音留余量)

用法:
  python3 prep_bgm.py 源BGM.mov                        # 输出到 assets/bgm/default_bgm.wav
  python3 prep_bgm.py 源.mov -o out.wav --target -22
  python3 prep_bgm.py 源.mov --profile gentle|flat|soft
"""
import argparse
import os
import re
import subprocess
import sys

FF = "/Users/mac/.local/bin/ffmpeg"
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.join(HERE, os.pardir, "assets", "bgm", "default_bgm.wav")

# 三种处理力度。
#   flat  : 最狠，段落差能压到 3dB 内（推荐：前奏弱、副歌强的成品 BGM）
#   gentle: 温和，保留一点自然起伏
#   soft  : 只做响度统一 + 峰值限制，不动动态（BGM 本身动态小时用）
PROFILES = {
    "flat": {
        "comp": "acompressor=threshold=-20dB:ratio=3:attack=15:release=250:makeup=3",
        "dyn": "dynaudnorm=f=300:g=11:p=0.78:m=14",
        "eq": "equalizer=f=2500:t=q:w=1.4:g=-3,equalizer=f=800:t=q:w=1.0:g=-1.5",
        "lra": 5,
    },
    "gentle": {
        "comp": "acompressor=threshold=-18dB:ratio=2.5:attack=20:release=300:makeup=2",
        "dyn": "dynaudnorm=f=500:g=15:p=0.85:m=8",
        "eq": "equalizer=f=2500:t=q:w=1.2:g=-2",
        "lra": 7,
    },
    "soft": {
        "comp": None,
        "dyn": None,
        "eq": "equalizer=f=2500:t=q:w=1.2:g=-2",
        "lra": 11,
    },
}


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def seg_mean(path, seg=5.0, upto=None):
    """返回 [(起点, mean_volume_dB), ...]，用于看段落响度是否被拉平。"""
    out = []
    t, dur = 0.0, upto
    while dur is None or t + 1e-6 < dur:
        if dur is not None and t + seg > dur:
            break
        r = run([FF, "-hide_banner", "-ss", f"{t}", "-t", f"{seg}", "-i", path,
                 "-af", "volumedetect", "-f", "null", "-"])
        m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
        if not m:
            break
        out.append((t, float(m.group(1))))
        t += seg
    return out


def astats(path):
    r = run([FF, "-hide_banner", "-i", path, "-af", "astats", "-f", "null", "-"])
    peak = re.search(r"Peak level dB:\s*(-?[\d.]+)", r.stderr)
    rms = re.search(r"RMS level dB:\s*(-?[\d.]+)", r.stderr)
    return (float(peak.group(1)) if peak else None,
            float(rms.group(1)) if rms else None)


def lufs(path):
    r = run([FF, "-hide_banner", "-i", path,
             "-af", "loudnorm=I=-23:TP=-2:print_format=summary", "-f", "null", "-"])
    g = lambda k: (float(re.search(rf"{k}:\s*(-?[\d.]+)", r.stderr).group(1))
                   if re.search(rf"{k}:\s*(-?[\d.]+)", r.stderr) else None)
    return {"I": g("Input Integrated"), "TP": g("Input True Peak"),
            "LRA": g("Input LRA")}


def build_chain(profile, target):
    p = PROFILES[profile]
    parts = ["aformat=channel_layouts=stereo:sample_rates=44100"]
    if p["comp"]:
        parts.append(p["comp"])
    if p["dyn"]:
        parts.append(p["dyn"])
    if p["eq"]:
        parts.append(p["eq"])
    parts.append(f"loudnorm=I={target}:TP=-4:LRA={p['lra']}")
    parts.append("alimiter=limit=0.85")
    # loudnorm 内部按 192kHz 处理，不重采样会把 wav 撑成 4 倍大小
    parts.append("aresample=44100")
    return ",".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    ap.add_argument("--target", type=float, default=-22.0, help="目标整体响度 LUFS（越小越安静）")
    ap.add_argument("--profile", default="flat", choices=list(PROFILES))
    ap.add_argument("--limit", type=float, default=75.0,
                    help="只处理前 N 秒（默认 75s，口播片足够；0=整段）")
    ap.add_argument("--before", type=float, default=None, help="只报前 N 秒的分段（默认整段抽样）")
    a = ap.parse_args()

    print(f"源文件: {a.src}")
    b = seg_mean(a.src, 5.0)
    if b:
        vals = [v for _, v in b]
        print(f"  处理前: 段落 mean {min(vals):.1f} ~ {max(vals):.1f} dB  "
              f"动态 {max(vals) - min(vals):.1f} dB")
        print("  前 8 段:", " ".join(f"{t:.0f}s:{v:.1f}" for t, v in b[:8]))

    chain = build_chain(a.profile, a.target)
    out = os.path.abspath(a.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(f"处理链[{a.profile}]: {chain}")
    cmd = [FF, "-y", "-v", "error"]
    if a.limit and a.limit > 0:
        cmd += ["-t", f"{a.limit}"]
    cmd += ["-i", a.src, "-vn", "-af", chain, "-c:a", "pcm_s16le", out]
    r = run(cmd)
    if r.returncode != 0:
        print("失败:", r.stderr[-800:]); sys.exit(1)

    a2 = seg_mean(out, 5.0)
    vals = [v for _, v in a2]
    pk, rms = astats(out)
    L = lufs(out)
    print(f"  处理后: 段落 mean {min(vals):.1f} ~ {max(vals):.1f} dB  "
          f"动态 {max(vals) - min(vals):.1f} dB")
    print("  前 8 段:", " ".join(f"{t:.0f}s:{v:.1f}" for t, v in a2[:8]))
    print(f"  峰值 {pk:.1f} dB / RMS {rms:.1f} dB / 整体 {L['I']} LUFS "
          f"(TP {L['TP']} LRA {L['LRA']})")
    print(f"  → {out}  ({os.path.getsize(out) / 1e6:.1f} MB)")


if __name__ == "__main__":
    main()
