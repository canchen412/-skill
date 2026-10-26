#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
卷播音效合成 —— 按参考教程 ref.mp4 的音频结构复刻

实测时间轴（568×320 源，30fps）：
  0.48–0.66s  持续低音铺底（180Hz 为主，whoosh 底噪）
  0.51/0.54/0.57s  三个密集瞬态（卡位 tick）
  0.76–0.86s  第二组 whoosh（360Hz 中频）
  1.47s  重音（160Hz 低频下潜）
  1.68/1.78s  中高频点缀
  2.36s  收尾重击（980Hz 中频炸裂，全片峰值 0.336）
"""
import numpy as np, wave, struct

SR = 44100


def env_adsr(n, a=0.005, d=0.05, s=0.7, r=0.08):
    """ADSR 包络，n 为采样点数"""
    e = np.zeros(n)
    ai = max(1, int(a*SR)); di = max(1, int(d*SR)); ri = max(1, int(r*SR))
    ai = min(ai, n//3); di = min(di, n//3); ri = min(ri, n//3)
    e[:ai] = np.linspace(0, 1, ai)
    e[ai:ai+di] = np.linspace(1, s, di)
    e[ai+di:n-ri] = s
    e[n-ri:] = np.linspace(s, 0, ri)
    return e


def tone(freq, dur, vol=0.3, kind="sine", a=0.004, d=0.06, s=0.6, r=0.10, detune=0.0):
    n = int(dur*SR)
    t = np.arange(n)/SR
    if kind == "sine":
        x = np.sin(2*np.pi*freq*t)
        if detune:
            x += 0.5*np.sin(2*np.pi*freq*(1+detune)*t)
    elif kind == "square":
        x = np.sign(np.sin(2*np.pi*freq*t))
    elif kind == "saw":
        x = 2*((freq*t) % 1.0) - 1
    else:
        x = np.random.randn(n)
    return x*env_adsr(n, a, d, s, r)*vol


def noise(dur, vol=0.2, a=0.002, d=0.05, s=0.4, r=0.08, lp=0.5):
    """噪声层（做 whoosh 气流感）"""
    n = int(dur*SR)
    x = np.random.randn(n)
    if lp < 1.0:
        # 单极点低通
        y = np.zeros(n); prev = 0.0
        for i in range(n):
            prev = prev + lp*(x[i]-prev)
            y[i] = prev
        x = y
    return x*env_adsr(n, a, d, s, r)*vol


def sweep(f0, f1, dur, vol=0.25, a=0.005, d=0.08, s=0.5, r=0.12, noise_mix=0.3):
    """频率扫频 —— whoosh 核心"""
    n = int(dur*SR)
    t = np.arange(n)/SR
    k = (f1-f0)/dur
    phase = 2*np.pi*(f0*t + 0.5*k*t*t)
    x = np.sin(phase)
    if noise_mix:
        nz = noise(dur, vol*noise_mix, a, d, s, r, lp=0.25)
        x = x*(1-noise_mix) + nz/max(1e-9, np.abs(nz).max())*vol*noise_mix
    return x*env_adsr(n, a, d, s, r)*vol


def place(buf, x, t):
    """把 x 叠加到 buf 的 t 秒位置"""
    i = int(t*SR)
    j = min(len(buf), i+len(x))
    if i < len(buf) and j > i:
        buf[i:j] += x[:j-i]


def water_drop(dur=0.55, vol=0.42, f0=1750, f1=430):
    """水滴音：频率快速上滑再急速下滑 + 尾部空腔感
    教程风格：清亮「啵」的一声，适合配合画面定格"""
    n = int(dur*SR)
    t = np.arange(n)/SR
    # 上滑段（前 18%）→ 主下滑段
    k1 = int(0.18*n)
    f_up = f0*1.55
    ph = np.zeros(n)
    # 快速上滑
    ph[:k1] = 2*np.pi*(f0*t[:k1] + 0.5*(f_up-f0)/t[k1]*t[:k1]**2)
    # 主频下坠（指数衰减到 f1）
    tt = t[k1:]
    decay = np.exp(-3.2*tt)
    fcurve = f_up*(f1/f_up)**(np.clip(tt/ (dur-t[k1]), 0, 1))
    ph[k1:] = 2*np.pi*(f_up*tt + np.cumsum(np.diff(fcurve, prepend=f_up))*(1/SR))
    x = np.sin(ph)
    # 尾部加一点「水花」噪声，让它更像水滴
    nz = noise(0.06, vol*0.35, a=0.001, d=0.02, s=0.2, r=0.035, lp=0.8)
    x[:len(nz)] += nz/max(1e-9, np.abs(nz).max())*vol*0.35
    # 整体包络：极快起 + 短衰减
    e = np.exp(-5.0*t)
    e[:int(0.004*SR)] = np.linspace(0, 1, int(0.004*SR))
    return x*e*vol


def build(dur=2.9, seed=7, drop_at=None):
    """按教程结构生成 dur 秒音效轨"""
    np.random.seed(seed)
    n = int(dur*SR)
    buf = np.zeros(n)
    K = dur/2.90          # 时间缩放因子
    KS = 2.90/2.90        # 速度缩放因子（v1 已随 KS 放大）

    # ── 段1 whoosh 铺底：0.46s 前几乎静音，0.48s 陡起（教程 RMS 0.001→0.150）
    bed = sweep(140, 420, 0.64*K, vol=0.17, a=0.02*K, d=0.16*K, s=0.60, r=0.28*K, noise_mix=0.45)
    place(buf, bed, 0.46*K)
    # 0.44s 前的极弱起（教程能量仅 0.001-0.032）
    place(buf, noise(0.44*K, 0.010, a=0.20*K, d=0.15*K, s=0.22, r=0.20*K, lp=0.06), 0.0)

    # ── 三个密集卡位 tick（0.51 / 0.54 / 0.57s，间隔 30ms）
    for i, t in enumerate([0.510, 0.540, 0.570]):
        tt = t*K
        # 180Hz 低频 + 短噪声 click，模拟卡片掠过
        place(buf, tone(180+40*i, 0.07*K, 0.20, "sine", a=0.001, d=0.03*K, s=0.25, r=0.035*K), tt)
        place(buf, noise(0.045*K, 0.10, a=0.001, d=0.015*K, s=0.2, r=0.02*K, lp=0.6), tt)

    # ── 第二组 whoosh（0.76–0.86s，360Hz 中频）
    place(buf, sweep(260, 700, 0.34*K, vol=0.15, a=0.03*K, d=0.10*K, s=0.5, r=0.14*K), 0.72*K)
    place(buf, tone(360, 0.22*K, 0.11, "sine", a=0.008, d=0.08*K, s=0.4, r=0.10*K), 0.78*K)

    # ── 段1→段2 转折重音（1.475s，160Hz 低频下潜）
    place(buf, tone(160, 0.30*K, 0.30, "sine", a=0.002, d=0.12*K, s=0.45, r=0.16*K, detune=0.008), 1.475*K)
    place(buf, noise(0.16*K, 0.10, a=0.001, d=0.05*K, s=0.3, r=0.08*K, lp=0.35), 1.475*K)

    # ── 段2 回弹点缀（1.68 / 1.78s，中高频）
    for t, f in [(1.680, 1400), (1.780, 1900)]:
        place(buf, tone(f, 0.10*K, 0.075, "sine", a=0.002, d=0.04*K, s=0.3, r=0.05*K), t*K)
        place(buf, noise(0.06*K, 0.05, a=0.001, d=0.02*K, s=0.2, r=0.03*K, lp=0.75), t*K)

    # ── 段3 收尾重击（2.36s，980Hz 中频炸裂）—— 全片峰值
    tt = 2.360*K if 2.360*K < dur else dur-0.30
    place(buf, tone(980, 0.42*K, 0.42, "sine", a=0.001, d=0.14*K, s=0.42, r=0.24*K), tt)
    place(buf, tone(1960, 0.20*K, 0.14, "sine", a=0.001, d=0.07*K, s=0.3, r=0.10*K), tt)
    place(buf, noise(0.30*K, 0.20, a=0.001, d=0.09*K, s=0.32, r=0.18*K, lp=0.55), tt)
    # 收尾低频垫底
    place(buf, tone(98, 0.46*K, 0.30, "sine", a=0.002, d=0.16*K, s=0.40, r=0.26*K), tt)

    # 段2 全程微弱反向 whoosh（对应画面倒滑）
    place(buf, sweep(500, 200, 1.25*K, vol=0.055, a=0.15*K, d=0.30*K, s=0.5, r=0.30*K, noise_mix=0.5),
          1.10*K)

    # ── 水滴音：可选，落在 drop_at（秒）——「肌酸」二字处
    if drop_at is not None and drop_at < dur:
        place(buf, water_drop(0.55*K if K > 0.5 else 0.55), drop_at)

    # 归一化到 -1.5dBFS
    peak = np.abs(buf).max()
    if peak > 0: buf = buf/peak*0.84
    return buf


def write_wav(path, data, sr=SR):
    x = np.clip(data, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x*32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    import sys
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 2.9
    out = sys.argv[2] if len(sys.argv) > 2 else "sfx.wav"
    drop_at = float(sys.argv[3]) if len(sys.argv) > 3 else None
    b = build(dur, drop_at=drop_at)
    write_wav(out, b)
    print(f"已生成 {out}  {dur:.2f}s  峰值 {np.abs(b).max():.3f}" + (f"  水滴@{drop_at}s" if drop_at else ""))
