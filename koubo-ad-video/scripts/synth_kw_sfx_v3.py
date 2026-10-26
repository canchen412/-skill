# -*- coding: utf-8 -*-
"""重点字动效音效 v3：新增 7 款「短促、收尾利落」音色（2026-09-27）。

背景：一条片里 7 个重点字几乎都绑 kw_bloom，听感单调重复。用户要求：
  ① 音色干净——无底噪/杂音/过重混响；② 音高·节奏·质感彼此区分明显；
  ③ 能分别适配「强调 / 弹出 / 快速闪现」等不同动效语气；
  ④ 时长短促、收尾利落，与文字出入场节奏对齐；⑤ 统一响度与格式。

技术口径（与 v2 一致，保证混进来不突兀）：
  * 纯音合成：正弦 + 整数倍泛音，**绝不掺白噪声**（白噪底=用户明确嫌弃的杂音）
  * 无混响（不加任何卷积/延迟尾巴），尾韵一律由指数衰减控制 → 收尾干净
  * 单声道 / 16bit / 44100Hz，峰值归一到 -9 dBFS（与现有库同规格）
  * 末尾 8ms 余弦淡出，杜绝爆音与拖尾咔哒
  * 去直流偏置（减均值），保证无低频漂移

新增 7 款（按「语气 → 音效」分工，见脚本底部 TONE_GUIDE）：
  kw_tick    极短高频「嘀」  0.09s  2.8kHz          → 快速闪现 / 逐字打字机
  kw_snap    木质短「哒」    0.15s  780Hz+瞬态       → 利落落点 / 否定 / 切换
  kw_pluck   明亮弹拨        0.30s  1.2kHz 快衰      → 弹出 / 轻快出现
  kw_bellup  上滑五度双音    0.42s  880→1320Hz       → 强调 / 数据上升
  kw_marker  圆润马林巴      0.44s  660Hz            → 圈选 / 标注 / 温和强调
  kw_drop    下滑「puu」     0.30s  1800→600Hz       → 落定 / 放下 / 转折收束
  kw_spark   三连高频颗粒    0.30s  2.4/3.0/3.6kHz   → 清单 / 数量 / 闪亮点

用法：
  python3 synth_kw_sfx_v3.py                # 生成到技能音效库 assets/sfx/
  python3 synth_kw_sfx_v3.py --out /tmp/x   # 生成到指定目录（试听用）
  python3 synth_kw_sfx_v3.py --audition out.mp4   # 拼一条试听片（含片头标注）
"""
import argparse
import os
import subprocess
import wave

import numpy as np

FS = 44100
TARGET_DBFS = -9.0
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.normpath(os.path.join(HERE, ".."))
SFX_LIB = os.path.join(SKILL, "assets", "sfx")
FFMPEG = "/Users/mac/.local/bin/ffmpeg"


# ---------------- 基础合成 ----------------
def _env(n, attack, decay):
    """起音线性上升 + 之后指数衰减（无混响尾巴）。"""
    t = np.arange(n) / FS
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    return e


def _fade_out(y, ms=8.0):
    """末尾余弦淡出：收尾利落、不爆音。"""
    n = min(int(ms / 1000.0 * FS), len(y))
    if n <= 0:
        return y
    y = y.copy()
    y[-n:] *= np.cos(np.linspace(0, np.pi / 2, n)) ** 2
    return y


def tone(freq, dur, decay, harm=(1.0, 0.28, 0.10), attack=0.004):
    n = int(dur * FS)
    t = np.arange(n) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp > 0:
            y += amp * np.sin(2 * np.pi * freq * (k + 1) * t)
    return y * _env(n, attack, decay)


def glide(f0, f1, dur, decay, harm=(1.0, 0.22, 0.06), attack=0.003, curve="exp"):
    """音高滑音（上滑/下滑），相位连续积分，避免咔哒。"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    u = t / max(dur, 1e-6)
    if curve == "exp":
        f = f0 * (f1 / f0) ** u
    else:
        f = f0 + (f1 - f0) * u
    ph = 2 * np.pi * np.cumsum(f) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp > 0:
            y += amp * np.sin(ph * (k + 1))
    return y * _env(n, attack, decay)


# ---------------- 7 款新音色 ----------------
def kw_tick():
    """极短高频「嘀」——几乎无尾韵，专配闪现/逐字出现。"""
    a = tone(2800, 0.09, 0.018, harm=(1.0, 0.30, 0.08), attack=0.001)
    b = tone(4200, 0.05, 0.012, harm=(1.0, 0.10), attack=0.001)
    y = np.zeros(int(0.10 * FS))
    y[:len(a)] += a
    y[:len(b)] += 0.35 * b
    return y


def kw_snap():
    """木质短「哒」——中频主体 + 2.4kHz 瞬态，收得极快。"""
    body = tone(780, 0.15, 0.030, harm=(1.0, 0.22, 0.06), attack=0.001)
    click = tone(2400, 0.045, 0.010, harm=(1.0, 0.35), attack=0.0008)
    y = np.zeros(int(0.16 * FS))
    y[:len(body)] += body
    y[:len(click)] += 0.45 * click
    return y


def kw_pluck():
    """明亮弹拨——起音极快、衰减快，配「弹出」。"""
    y = glide(1240, 1180, 0.30, 0.075, harm=(1.0, 0.30, 0.12, 0.05), attack=0.0012)
    tail = tone(2480, 0.14, 0.045, harm=(1.0, 0.10), attack=0.001)
    y[:len(tail)] += 0.22 * tail
    return y


def kw_bellup():
    """上滑五度双音（880→1320）——音高走向明显，配强调/数据上升。"""
    up = glide(880, 1320, 0.20, 0.075, harm=(1.0, 0.26, 0.08), attack=0.002)
    y = np.zeros(int(0.34 * FS))
    y[:len(up)] += up
    b = tone(1320, 0.20, 0.070, harm=(1.0, 0.22, 0.06), attack=0.003)
    off = int(0.12 * FS)
    y[off:off + len(b)] += 0.9 * b
    return y


def kw_marker():
    """圆润马林巴——中音、泛音柔、无刺激感，配圈选/标注。"""
    y = tone(660, 0.34, 0.115, harm=(1.0, 0.16, 0.05, 0.02), attack=0.003)
    hi = tone(1980, 0.16, 0.060, harm=(1.0, 0.08), attack=0.003)
    y[:len(hi)] += 0.18 * hi
    return y


def kw_drop():
    """下滑「puu」——与 riser 反向，配落定/放下/转折收束。"""
    return glide(1800, 600, 0.30, 0.085, harm=(1.0, 0.18, 0.05), attack=0.002)


def kw_spark():
    """三连高频颗粒——节奏感最强，配清单/数量/闪亮点。"""
    y = np.zeros(int(0.30 * FS))
    for i, (f, off) in enumerate([(2400, 0.0), (3000, 0.09), (3600, 0.18)]):
        g = tone(f, 0.075, 0.022, harm=(1.0, 0.24, 0.06), attack=0.001)
        k = int(off * FS)
        y[k:k + len(g)] += (1.0 - 0.12 * i) * g
    return y


def normalize(y):
    """去直流 → 峰值归一到 -9 dBFS → 末尾淡出 → 软限幅。"""
    y = y - float(np.mean(y))
    pk = float(np.max(np.abs(y)))
    if pk > 0:
        y = y / pk * (10 ** (TARGET_DBFS / 20.0))
    y = _fade_out(y, 10.0)
    y = np.clip(y, -1.0, 1.0)
    return y


def write_wav(path, y):
    y16 = (np.clip(y, -1, 1) * 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(y16.tobytes())


NEW = {
    "kw_tick": (kw_tick, "极短高频嘀 · 快速闪现/逐字"),
    "kw_snap": (kw_snap, "木质短哒 · 利落落点/否定"),
    "kw_pluck": (kw_pluck, "明亮弹拨 · 弹出/轻快出现"),
    "kw_bellup": (kw_bellup, "上滑五度 · 强调/数据上升"),
    "kw_marker": (kw_marker, "圆润马林巴 · 圈选/标注"),
    "kw_drop": (kw_drop, "下滑puu · 落定/转折收束"),
    "kw_spark": (kw_spark, "三连颗粒 · 清单/数量"),
}

TONE_GUIDE = [
    ("快速闪现 / 逐字出现", "kw_tick", "0.09s，2.8kHz，几乎无尾韵"),
    ("利落落点 / 否定 / 切换", "kw_snap", "0.15s，780Hz + 2.4kHz 瞬态"),
    ("弹出 / 轻快出现", "kw_pluck", "0.30s，1.2kHz 弹拨快衰"),
    ("强调 / 数据上升 / 利益点", "kw_bellup", "0.42s，880→1320Hz 上滑"),
    ("圈选 / 标注 / 温和强调", "kw_marker", "0.44s，660Hz 圆润马林巴"),
    ("落定 / 放下 / 转折收束", "kw_drop", "0.30s，1800→600Hz 下滑"),
    ("清单 / 数量 / 闪亮点", "kw_spark", "0.30s，三连高频颗粒"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=SFX_LIB, help="输出目录，默认写入技能音效库")
    ap.add_argument("--audition", default="", help="额外拼一条试听片 mp4")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    print(f"输出目录：{a.out}")
    for name, (fn, desc) in NEW.items():
        y = normalize(fn())
        p = os.path.join(a.out, name + ".wav")
        write_wav(p, y)
        dur = len(y) / FS
        pk = 20 * np.log10(max(float(np.max(np.abs(y))), 1e-9))
        tail = float(np.max(np.abs(y[int(len(y) * 0.92):])))  # 尾段残留（应≈0）
        print(f"  {name}.wav  {dur:.3f}s  峰值 {pk:.1f}dBFS  尾段残留 {tail:.5f}  · {desc}")
    if a.audition:
        build_audition(a.out, a.audition)


def build_audition(out_dir, dst):
    """拼试听片：每个音效前留 0.35s 静音，依次播放。"""
    inputs, filters = [], []
    for i, (name, _) in enumerate(NEW.items()):
        inputs += ["-i", os.path.join(out_dir, name + ".wav")]
        filters.append(f"[{i}:a]adelay={int(350 + i * 900)}|{int(350 + i * 900)}[a{i}]")
    filters.append("".join(f"[a{i}]" for i in range(len(NEW))) +
                   f"amix=inputs={len(NEW)}:normalize=0[out]")
    cmd = [FFMPEG, "-y"] + inputs + ["-filter_complex", ";".join(filters),
                                     "-map", "[out]", "-ar", "44100", dst]
    subprocess.run(cmd, check=False)
    print("试听片 →", dst)


if __name__ == "__main__":
    main()
