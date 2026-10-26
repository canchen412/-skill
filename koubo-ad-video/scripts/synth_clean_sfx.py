# -*- coding: utf-8 -*-
"""合成「干净 / 纯洁」风格的浮动入场音效候选（零噪声成分）。

背景：原 float_in 主体是带通白噪声（上浮气声），用户反馈"听起来像杂音"。
本脚本改用**纯音合成**（正弦 + 整数倍泛音），完全没有噪声成分。

统一响度铁律：入库前归一到 -9dBFS，与其他转场音效同峰值，
否则音色判断会被音量污染。

产出（assets/sfx/）：
  float_cleanA.wav  水晶点   单音高音铃击，最快最干净
  float_cleanB.wav  风铃浮现 三音轻叠，长尾柔和
  float_cleanC.wav  纯净上浮 正弦 392→784Hz 平滑滑音，无噪声
  float_cleanD.wav  晨露琶音 四音上行琶音，钟琴质感
"""
import os
import numpy as np
import wave

FS = 44100
TARGET_DBFS = -9.0
OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sfx"))


def tone(freq, dur, decay, harm=(1.0, 0.30, 0.12, 0.05), attack=0.004, phase=0.0):
    """纯音：基频 + 整数倍泛音（整数倍=乐音，不产生金属噪声感）"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp <= 0:
            continue
        y += amp * np.sin(2 * np.pi * freq * (k + 1) * t + phase * (k + 1))
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    return y * e


def glide(f0, f1, dur, decay, harm=(1.0, 0.18), attack=0.10, release=0.30):
    """平滑滑音：频率几何插值上滑，纯音无噪声"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    f = np.geomspace(f0, f1, n)
    ph = 2 * np.pi * np.cumsum(f) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        y += amp * np.sin((k + 1) * ph)
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    if release > 0:
        nr = int(release * FS)
        e[-nr:] *= np.linspace(1, 0, nr) ** 1.5
    return y * e


def place(seg, at, total):
    out = np.zeros(int(total * FS))
    i = int(at * FS)
    j = min(len(out), i + len(seg))
    out[i:j] += seg[: j - i]
    return out


def norm_db(x, dbfs=TARGET_DBFS):
    peak = np.max(np.abs(x))
    return x / peak * (10 ** (dbfs / 20.0)) if peak > 1e-9 else x


def soft_clip(x, k=1.5):
    return np.tanh(x * k) / np.tanh(k)


def save(x, name):
    x = np.clip(norm_db(soft_clip(x)), -1, 1)
    p = os.path.join(OUT, name)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((x * 32767).astype("<i2").tobytes())
    print(f"{name:18s} {len(x)/FS:5.2f}s  峰值 {20*np.log10(np.max(np.abs(x))):.1f} dBFS")


def main():
    os.makedirs(OUT, exist_ok=True)

    # A 水晶点：单音高音铃击（G6 1568Hz），极短起音、0.28s 衰减 —— 最干净利落
    a = place(tone(1568.0, 0.55, 0.28, harm=(1.0, 0.28, 0.10, 0.04), attack=0.003), 0.0, 0.60)
    a += 0.18 * place(tone(784.0, 0.55, 0.34, harm=(1.0, 0.20), attack=0.004), 0.0, 0.60)
    save(a, "float_cleanA.wav")

    # B 风铃浮现：C6/E6/G6 三音错开 60ms，长尾柔和
    b = np.zeros(int(1.10 * FS))
    for i, f in enumerate((1046.5, 1318.5, 1568.0)):
        b += place(tone(f, 1.0, 0.62, harm=(1.0, 0.26, 0.08), attack=0.006), i * 0.06, 1.10)
    save(b, "float_cleanB.wav")

    # C 纯净上浮：392→784Hz 平滑滑音，完全无噪声（原气声的直接替代）
    c = place(glide(392.0, 784.0, 1.00, 0.55, harm=(1.0, 0.16), attack=0.22, release=0.30), 0.0, 1.05)
    save(c, "float_cleanC.wav")

    # D 晨露琶音：C5-E5-G5-C6 四音上行，钟琴质感
    d = np.zeros(int(1.00 * FS))
    for i, f in enumerate((523.3, 659.3, 784.0, 1046.5)):
        d += place(tone(f, 0.62, 0.34, harm=(1.0, 0.24, 0.07), attack=0.004), i * 0.085, 1.00)
    save(d, "float_cleanD.wav")


if __name__ == "__main__":
    main()
