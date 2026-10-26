# -*- coding: utf-8 -*-
"""合成「重点词弹出动效」专用音效（纯音，无噪声成分）。

背景：用户要求——文案说到重点词时在画面空白处动效弹出，并配一个与"弹出"
动作匹配的音效；且不能与转场唿哨听感混淆。

设计：
  kw_pop.wav   短促上滑音，440→880Hz，0.16s —— 最像"弹出来"，默认首选
  kw_ding.wav  清脆双音（C6+G6），0.45s，比 kw_pop 明亮，用于第二个重点词

铁律：纯音(基频+整数倍泛音)，绝不掺噪声；归一到 -9dBFS 与其他音效同峰值。
"""
import os
import numpy as np
import wave

FS = 44100
TARGET_DBFS = -9.0
OUT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sfx"))


def tone(freq, dur, decay, harm=(1.0, 0.30, 0.12, 0.05), attack=0.004):
    n = int(dur * FS)
    t = np.arange(n) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp <= 0:
            continue
        y += amp * np.sin(2 * np.pi * freq * (k + 1) * t)
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    return y * e


def sweep(f0, f1, dur, decay, harm=(1.0, 0.30, 0.12), attack=0.003):
    """上滑音：瞬时角频率线性插值，听感=向上弹出"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    # 相位 = 2π∫f(t)dt，f 线性从 f0 到 f1
    f = f0 + (f1 - f0) * (t / dur)
    phase = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / dur)
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp <= 0:
            continue
        y += amp * np.sin(phase * (k + 1))
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    del f
    return y * e


def norm(y, dbfs=TARGET_DBFS):
    peak = float(np.max(np.abs(y))) or 1.0
    return y * (10 ** (dbfs / 20.0)) / peak


def save(y, name):
    p = os.path.join(OUT, name)
    y = norm(y)
    with wave.open(p, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    print(f"{name}: {len(y)/FS:.3f}s peak={20*np.log10(np.max(np.abs(y))):.1f}dBFS")


def boom(dur=1.9, sweep_t=0.30, f_hi=130.0, f_lo=48.0):
    """综艺开场「咚」——带回响的高级撞击音（用户点名用于"今天我们讲，肌酸"）。

    构成:
      1) 低频下扫主体 130→48Hz（0.3s 内扫完），指数衰减 0.42s —— "咚"的肉感
      2) 极短金属瞬态 1320/1980/2640Hz（衰减 12~20ms，低幅度）—— 击打的实体感
      3) 6 抽头前馈回声（75ms~950ms，增益 0.50→0.07）—— 综艺棚混响尾韵
    仍遵守「纯音」铁律：不用噪声，只叠正弦与回声。
    """
    n = int(dur * FS)
    t = np.arange(n) / FS
    f = np.where(t < sweep_t, f_hi + (f_lo - f_hi) * (t / sweep_t), f_lo)
    ph = np.cumsum(2 * np.pi * f / FS)
    body = np.sin(ph) + 0.42 * np.sin(2 * ph) + 0.16 * np.sin(3 * ph)
    env = np.exp(-t / 0.42)
    na = max(1, int(0.004 * FS))
    env[:na] *= np.linspace(0, 1, na)
    y = body * env
    for fr, amp, dec in ((1320.0, 0.14, 0.020), (1980.0, 0.09, 0.016), (2640.0, 0.05, 0.012)):
        y += amp * np.sin(2 * np.pi * fr * t) * np.exp(-t / dec)
    base = y.copy()                      # 用副本做回声源，避免就地累加变成不稳定的 IIR
    for d, g in ((0.075, 0.50), (0.16, 0.36), (0.29, 0.26),
                 (0.45, 0.18), (0.68, 0.12), (0.95, 0.07)):
        k = int(d * FS)
        if k < n:
            y[k:] += g * base[:n - k]
    return y


def main():
    os.makedirs(OUT, exist_ok=True)
    # 1) 弹出音：短上滑，最贴合 \fscx 30->126 的回弹动效
    save(sweep(440, 880, 0.16, 0.045), "kw_pop.wav")
    # 2) 清脆双击：两个八度叠音，明亮但不刺耳
    a = tone(1046.5, 0.45, 0.16, harm=(1.0, 0.28, 0.10, 0.04))
    b = tone(1568.0, 0.38, 0.13, harm=(1.0, 0.20, 0.06))
    if len(b) < len(a):
        b = np.pad(b, (0, len(a) - len(b)))
    save(a * 0.85 + b * 0.45, "kw_ding.wav")
    # 3) 轻点：极短高频 tick，配「逐字打字机」的每一字
    save(tone(2600, 0.055, 0.012, harm=(1.0, 0.22, 0.06), attack=0.0012) * 0.9
         + tone(1300, 0.055, 0.010, harm=(1.0, 0.15), attack=0.0012) * 0.35, "kw_click.wav")
    # 4) 上升音：400→1800Hz 振幅渐强 0.5s，配「描边转实心」的高级感概念词
    n = int(0.50 * FS)
    t = np.arange(n) / FS
    f0, f1 = 400.0, 1800.0
    ph = 2 * np.pi * (f0 * t + 0.5 * (f1 - f0) * t * t / 0.50)
    y = np.sin(ph) + 0.24 * np.sin(2 * ph) + 0.08 * np.sin(3 * ph)
    env = np.linspace(0.15, 1.0, n) ** 1.6
    rel = max(1, int(0.09 * FS))
    env[-rel:] *= np.linspace(1.0, 0.0, rel)
    save(y * env, "kw_riser.wav")
    # 5) 综艺「咚」：低频撞击 + 棚混响尾，用于开场点题词（如"今天我们讲，肌酸"）
    save(boom(), "kw_boom.wav")


if __name__ == "__main__":
    main()
