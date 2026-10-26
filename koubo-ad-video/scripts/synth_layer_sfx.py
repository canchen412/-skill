# -*- coding: utf-8 -*-
"""合成「弹入 / 浮动」分层入场音效。

统一响度铁律：新音效入库前必须与其他转场音效同峰值（-9dBFS），
否则音色判断会被音量污染（详见 references/workflow.md 唿哨改版教训）。

产出（assets/sfx/）：
  pop_in.wav    弹入：下坠 whoosh + 落位钝击（A 方案，主用）
  pop_in2.wav   弹入候选 B：短促弹跳「啵+上扬」
  float_in.wav  浮动：轻柔上浮气声（C 方案，主用）
  float_in2.wav 浮动候选 D：低频轻嗡浮现
"""
import os
import numpy as np
import wave

FS = 44100
TARGET_DBFS = -9.0
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "sfx")
OUT = os.path.normpath(OUT)


def lowpass(x, fc_curve):
    """一阶 IIR 低通，截止频率可随时间变化（fc_curve 与 x 等长）"""
    y = np.zeros_like(x)
    prev = 0.0
    a = 1.0 - np.exp(-2.0 * np.pi * fc_curve / FS)
    for n in range(len(x)):
        prev += a[n] * (x[n] - prev)
        y[n] = prev
    return y


def highpass(x, fc):
    """一阶高通（低切），避免低频闷响"""
    y = np.zeros_like(x)
    prev_x = prev_y = 0.0
    a = np.exp(-2.0 * np.pi * fc / FS)
    for n in range(len(x)):
        prev_y = a * (prev_y + x[n] - prev_x)
        prev_x = x[n]
        y[n] = prev_y
    return y


def env_adsr(n, attack, decay, sustain=0.0, release=0.0):
    """简化包络：attack 线性起，decay 指数落，可加 release 淡出"""
    t = np.arange(n) / FS
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    if release > 0:
        nr = int(release * FS)
        e[-nr:] *= np.linspace(1, 0, nr) ** 1.5
    return e


def fall_whoosh(dur=0.30, f0=900.0, f1=110.0, decay=0.16, seed=7):
    """下坠 whoosh：白噪 + 随时间下降的低通截止"""
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    x = rng.normal(0, 1, n)
    fc = np.geomspace(f0, f1, n)
    y = lowpass(x, fc)
    return y * env_adsr(n, 0.03, decay)


def thud(dur=0.32, freq=88.0, decay=0.075, click=True, seed=11):
    """落位钝击：低频正弦指数衰减 + 极短噪声瞬态"""
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    t = np.arange(n) / FS
    body = np.sin(2 * np.pi * freq * t) * np.exp(-t / decay)
    body += 0.35 * np.sin(2 * np.pi * freq * 1.98 * t) * np.exp(-t / (decay * 0.6))
    if click:
        nc = int(0.012 * FS)
        body[:nc] += rng.normal(0, 0.8, nc) * np.linspace(1, 0, nc)
    return highpass(body, 45)


def rise_air(dur=0.75, f0=380.0, f1=1150.0, seed=23):
    """上浮气声：带通噪声缓升（低通+高通夹出带通）"""
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    x = rng.normal(0, 1, n)
    lp = lowpass(x, np.geomspace(f1, f1 * 1.35, n))
    y = highpass(lp, f0)
    return y * env_adsr(n, 0.18, 0.55, release=0.30)


def hum_rise(dur=0.9, base=224.0):
    """轻嗡浮现：两个低频正弦 + 缓慢包络"""
    n = int(dur * FS)
    t = np.arange(n) / FS
    y = 0.6 * np.sin(2 * np.pi * base * t) + 0.4 * np.sin(2 * np.pi * base * 1.5 * t + 0.6)
    return y * env_adsr(n, 0.25, 0.75, release=0.35)


def place(seg, at, total):
    """把片段放到总长的 at 秒处"""
    out = np.zeros(int(total * FS))
    i = int(at * FS)
    j = min(len(out), i + len(seg))
    out[i:j] += seg[: j - i]
    return out


def norm_db(x, dbfs=TARGET_DBFS):
    peak = np.max(np.abs(x))
    if peak < 1e-9:
        return x
    return x / peak * (10 ** (dbfs / 20.0))


def soft_clip(x, k=1.6):
    """轻度软限幅，压掉合成瞬态毛刺"""
    return np.tanh(x * k) / np.tanh(k)


def save(x, name):
    x = norm_db(soft_clip(x))
    x = np.clip(x, -1, 1)
    pcm = (x * 32767).astype("<i2")
    p = os.path.join(OUT, name)
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes(pcm.tobytes())
    print(f"{name:16s} {len(x)/FS:5.2f}s  峰值 {20*np.log10(np.max(np.abs(x))):.1f} dBFS  -> {p}")


def main():
    os.makedirs(OUT, exist_ok=True)
    # A 弹入：下坠 0.30s 内到落位，落位钝击在 0.17s
    a = place(fall_whoosh(0.30, 900, 110, 0.15), 0.0, 0.50)
    a = place(thud(0.32, 88, 0.075), 0.17, 0.50) + 0.9 * a
    save(a, "pop_in.wav")
    # B 候选：短促「啵」+ 上扬
    b = place(fall_whoosh(0.14, 700, 240, 0.06), 0.0, 0.36)
    n = int(0.22 * FS)
    t = np.arange(n) / FS
    up = np.sin(2 * np.pi * np.geomspace(180, 430, n) * t) * np.exp(-t / 0.055)
    b = place(b, 0, 0.36) + place(up, 0.10, 0.36)
    save(b, "pop_in2.wav")
    # C 浮动：上浮气声 + 极轻垫底
    c = place(rise_air(0.75, 380, 1150), 0.02, 1.0)
    c += 0.25 * place(hum_rise(0.8, 196.0), 0.05, 1.0)
    save(c, "float_in.wav")
    # D 候选：低频轻嗡
    d = place(hum_rise(0.9, 224.0), 0.0, 1.05)
    d += 0.22 * place(rise_air(0.6, 500, 900), 0.1, 1.05)
    save(d, "float_in2.wav")


if __name__ == "__main__":
    main()
