#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
齿轮卷播音效 —— 转速跟随画面速度实时变化

设计要点（灿爹要求）：
  1. 卷播时是「齿轮咯咯咯吱」的机械咬合声
  2. 齿轮转速 = 画面卷播速度（快 → 齿轮快；减速 → 齿轮同步慢下来）
  3. 定格「咔哒」一声要更高级（金属质感 + 双层瞬态），不是「咚」

用法:
  python3 opening_banner_gear.py 3.00 sfx_gear.wav 2.48
      参数依次为 总时长秒、水滴/定格音落下秒

速度曲线必须与 banner.py 完全一致：
  段1  0 → 1.64s      恒速 v1=95px/帧
  段2  1.64 → 2.48s   线性收到 0（峰值 v2_peak=107.6）
  段3  2.48s → 末     定格（齿轮停转，只留一声咔哒）
"""
import numpy as np, wave, sys

SR = 44100

# ── 与 banner.py 保持一致的节奏参数（3.00s 基准）────────────────
PITCH = 500
V1 = 95.0            # 段1 每帧位移
T1 = 1.64            # 段1 结束
T_FOCUS = 2.72       # ★ 定格帧：灿爹要求减速段更长，从 1.64s 起降速到 2.72s（1.08s 段2）
TOTAL_REF = 3.00
# off_stop：取使段2 峰值最接近 v1 的整节距（与 banner.py 同算法）
def _seg_peak(n_off):
    off1 = V1 * int(T1 * 30)
    return 2.0 * (n_off - off1) / max(1, int(T_FOCUS * 30) - int(T1 * 30))
_c = [k * PITCH for k in range(1, 40) if k * PITCH > V1 * int(T1 * 30)]
OFF_STOP = max(_c, key=lambda o: -abs(_seg_peak(o) - V1))
OFF1 = V1 * int(T1 * 30)
OFF2 = OFF_STOP - OFF1
V2_PEAK = _seg_peak(OFF_STOP)
# ─────────────────────────────────────────────────────────────


def gear_teeth(n_teeth, dur, f0, f1, amp=1.0, bright=0.5, seed=0):
    """一串齿轮咬合「咯咯咯吱」：每个齿是一个短促的带通噪声脉冲，
    带轻微音高抖动，避免机械般的死板。"""
    rng = np.random.RandomState(seed)
    out = []
    for i in range(n_teeth):
        # 每个齿的间隔带 ±4% 抖动（真实齿轮有公差感）
        jitter = 1.0 + rng.uniform(-0.04, 0.04)
        pos = dur * i / max(1, n_teeth - 1) * jitter
        # 齿内音高在 f0→f1 间微滑
        f = f0 + (f1 - f0) * rng.uniform(-1, 1) * 0.15
        out.append((pos, f, amp * rng.uniform(0.85, 1.0), bright))
    return out


def render_click(dur, pos, vol=0.60, seed=1):
    """定格音 = 「当」—— 金属敲钟声，带长回音。

    灿爹要的是「当的一声 + 继续有回音」，不是「咔哒」。
    物理模型：一枚金属棒被敲击 → 基频 + 强非谐性分音，长指数衰减。
    ① 基音 210Hz（钟体的「当」的胸腔感）
    ② 非谐分音 1.52×/2.73×/4.13×（金属板/棒的特征，非整数倍才像金属）
    ③ 短促起振（2ms）+ 长达 1.1s 的回音尾巴
    ④ 轻微音高漂移（真实敲击的 pitch drop）
    """
    rng = np.random.RandomState(seed)
    i = int(pos * SR)
    n = int(1.30 * SR)                      # 总长 1.3s（回音 1.1s+）
    if i >= int(dur * SR):
        return np.zeros(int(dur * SR), np.float32)
    t = np.arange(n) / SR
    x = np.zeros(n)

    # ① 基音 210Hz，带 pitch drop（从 218 → 208）
    f0 = 218.0 - 10.0 * (t / t[-1])
    x += 0.60 * np.sin(2 * np.pi * np.cumsum(f0) / SR)

    # ② 非谐分音（金属特征：倍率不是整数）
    for k_, a_ in ((1.52, 0.34), (2.73, 0.22), (4.13, 0.13), (5.81, 0.07)):
        x += a_ * np.sin(2 * np.pi * 210.0 * k_ * t)

    # ③ 起振：2ms 极快
    atk = int(0.002 * SR)
    env = np.exp(-3.1 * t)                  # 长回音：到 0.1 需 t≈0.74s
    env[:atk] = np.linspace(0, 1, atk)
    # 高频成分衰减更快（真实钟声：亮的部分先没）
    x += 0.16 * np.sin(2 * np.pi * 1850 * t) * np.exp(-11.0 * t)
    x += 0.09 * np.sin(2 * np.pi * 3200 * t) * np.exp(-16.0 * t)

    # ④ 敲击瞬态：极短噪声 click
    cn = int(0.02 * SR)
    nz = rng.randn(cn)
    nz = np.convolve(nz, np.ones(12)/12, mode="same")
    nz = nz / (np.abs(nz).max() + 1e-9)
    x[:cn] += nz * np.exp(-260.0 * t[:cn]) * 0.40

    y = np.zeros(int(dur * SR), np.float32)
    j = min(n, len(y) - i)
    y[i:i+j] += (x * env)[:j] * vol
    return y


def water_whoosh(dur=0.85, vol=0.55, seed=5):
    """水波音：教程 2.35-2.37s 那个 0.30 峰值的声音。
    特征 = 宽带噪声「呼」+ 频率下扫（模拟水面晃动的滤波扫频），
    起音快、尾巴长、无二次峰 → 与定格"锁定音"叠加成"水波涌出"的错觉。
    """
    rng = np.random.RandomState(seed)
    n = int(dur * SR)
    t = np.arange(n) / SR
    # 噪声源：宽带 → 低通逐渐关闭（高频先没，低频后没）= 水波"沉下去"
    nz = rng.randn(n)
    # 简单一阶低通，系数随时间下降 → 滤波从"全通"到"低通"
    y = np.zeros(n)
    z = 0.0
    for i in range(n):
        u = i / n
        a = 0.85 * (1.0 - 0.72 * u)      # 0.85 → 0.24，越到后面越闷
        z = z + a * (nz[i] - z)
        y[i] = z
    y = y / (np.abs(y).max() + 1e-9)
    # 包络：快起 + 长尾（不做二次起振）
    e = np.exp(-4.2 * t)
    e[:int(0.006 * SR)] = np.linspace(0, 1, int(0.006 * SR))
    # 叠一点中频「涌」的抖动
    wob = 1.0 + 0.16 * np.sin(2 * np.pi * 7.5 * t)
    return (y * e * wob * vol).astype(np.float32)


def build(dur=3.00, click_at=None, seed=7, water_at=None):
    n = int(dur * SR)
    buf = np.zeros(n, np.float32)
    KS = dur / TOTAL_REF

    # 逐帧算速度 → 换算齿轮转速
    NF1 = int(T1 * 30 * KS)
    NFF = int(T_FOCUS * 30 * KS)
    n2 = max(1, NFF - NF1)
    v1 = V1                      # px/帧（不随 KS 缩放，见 banner.py）
    v2p = 2.0 * OFF2 / n2

    # 齿轮「每转多少帧咬合一次」：卡节距 500px，速度 95px/帧 →
    # 每 5.26 帧走过一张卡 = 一个卡位周期。设每卡 2 个齿，节奏更密。
    teeth_per_card = 2
    frames_per_tooth = PITCH / v1 / teeth_per_card   # ≈2.63 帧/齿 → 11.4 齿/秒

    # ── 滴答音：按教程实测精读复刻（灿爹认可方向后要求"更参考教程的滴答"）──
    # 教程 0.48-0.70s 密集滴答逐瞬态实测：
    #   峰频  200 / 267 / 334 / 400Hz   ← 低频金属簇（不是单音，是四层叠）
    #   起振  0.1ms    ← 极快，瞬态锐利 = "哒"的手感
    #   衰减  10-13ms  ← ★极短！这是"滴答"质感的核心
    #   频谱  0-300Hz 29-63%，300-800Hz 17-29%，1.5-8kHz 合计 12-27%
    # 我上一版 2900-3800Hz + 42ms → 偏"叮"、尾巴太长，不像滴答。
    teeth_per_card = 4
    phase = 0.0
    # ★ tooth_len 必须 > 衰减尾部，否则信号被截断、听着像"被切掉"。
    #   实测尾部 10-13ms，但窗口取 30ms，让 exp 包络自然衰减到接近 0。
    TOOTH_MS = 30.0
    tooth_len = max(8, int(TOOTH_MS/1000.0 * SR))
    # 低频金属簇的四个频率（实测值，等间距 67Hz）
    # ★ 灿爹要求「再高一点点」→ 整体升调约 1.45 倍（保持等间距比例不变）
    CLUSTER = (420.0, 560.0, 700.0, 840.0)   # 200/267/334/400 × 2.1（灿爹两次要求升调）
    for fi in range(0, NFF + 1):
        tsec = fi / 30.0
        if fi < NF1:
            v = v1
        elif fi < NFF:
            u = (fi - NF1) / n2
            v = v2p * (1.0 - u)
        else:
            v = 0.0
        if v < 0.5:
            if fi >= NFF:
                break
            continue
        dteeth = v / PITCH * teeth_per_card
        prev = phase
        phase += dteeth
        if int(phase) - int(prev) < 1:
            continue
        i = int(tsec * SR)
        if i + tooth_len > len(buf):
            break
        t = np.arange(tooth_len) / SR
        rng = np.random.RandomState(seed + fi)
        # ① 低频金属簇：四层等间距泛音堆叠，音高随速度微升（越转越亮）
        spd = min(1.0, v / v1)
        x = np.zeros(tooth_len)
        for i_, f0 in enumerate(CLUSTER):
            f = f0 * (1.0 + 0.18 * spd)          # 速度↑ → 音高↑（金属感更亮）
            amp = (0.62, 0.48, 0.34, 0.22)[i_]  # 高次泛音递减
            x += amp * np.sin(2 * np.pi * f * t)
        # ② 极短的宽带「咔」（给瞬态锐度），高通后只留很窄一段
        nz = rng.randn(tooth_len)
        nz = np.convolve(nz, np.ones(10)/10, mode="same")   # 10 抽头 → 极偏高频
        nz = nz / (np.abs(nz).max() + 1e-9)
        x = x * 0.90 + nz * 0.16
        # ③ 中高频金属泛音（实测 1.5-8k 合计 12-27%）：三层窄带
        for f_, a_ in ((3800, 0.07), (6600, 0.045), (10500, 0.025)):
            x += a_ * np.sin(2 * np.pi * f_ * t)
        # ④ 包络：0.1ms 起振 + 极快衰减（实测衰减尾部 10-13ms）
        e = np.exp(-160.0 * t) + 0.28 * np.exp(-400.0 * t)
        e[:max(2, int(0.0001 * SR))] = np.linspace(0, 1, max(2, int(0.0001 * SR)))
        amp = 0.52 * spd ** 0.30                  # 慢下来更弱
        buf[i:i+tooth_len] += (x * e * amp).astype(np.float32)

    # 段1 起始的一声「启动咬合」，让开头有实体感
    start = render_click(dur, 0.02, vol=0.30, seed=seed+1)
    buf += start

    # 减速段加一点低频「电机降速」嗡鸣，随速度线性下滑
    for fi in range(NF1, NFF):
        tsec = fi / 30.0
        u = (fi - NF1) / n2
        v = v2p * (1.0 - u)
        i = int(tsec * SR)
        nn = int(0.12 * SR)
        if i + nn > len(buf):
            break
        t = np.arange(nn) / SR
        # 频率随速度降
        f = 90 + 60 * (v / max(1.0, v2p))
        sig = np.sin(2 * np.pi * f * t) * (1 - t[-1]) * 0.05
        e = np.exp(-6 * t)
        buf[i:i+nn] += (sig * e).astype(np.float32)

    # 水波音：落在定格前一瞬（教程是 2.35s 重击，这里放在 click_at 前 0.02s）
    if water_at is not None and water_at < dur:
        wsig = water_whoosh(0.85, vol=0.50, seed=seed+7)
        iw = int(water_at * SR)
        jw = min(len(wsig), len(buf) - iw)
        if jw > 0:
            buf[iw:iw+jw] += wsig[:jw]

    # 定格咔哒（更高级）
    if click_at is not None and click_at < dur:
        buf += render_click(dur, click_at, vol=0.72, seed=seed+99)

    # 归一化
    pk = np.abs(buf).max()
    if pk > 0:
        buf = buf / pk * 0.86
    return buf


def write_wav(path, data, sr=SR):
    x = np.clip(data, -1, 1)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes((x * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 3.00
    out = sys.argv[2] if len(sys.argv) > 2 else "sfx_gear.wav"
    click_at = float(sys.argv[3]) if len(sys.argv) > 3 else None
    water_at = float(sys.argv[4]) if len(sys.argv) > 4 else None
    b = build(dur, click_at=click_at, water_at=water_at)
    write_wav(out, b)
    print(f"已生成 {out}  {dur:.2f}s  峰值 {np.abs(b).max():.3f}")
    print(f"  段1 {OFF1:.0f}px 恒速 95px/帧 | 段2 {OFF2:.0f}px 峰值 {V2_PEAK:.1f} → 0 | 定格 {click_at}s")
