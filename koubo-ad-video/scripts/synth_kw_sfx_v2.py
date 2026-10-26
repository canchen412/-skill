# -*- coding: utf-8 -*-
"""重点字动效音效 v2：新增 7 款候选音色 + 自动生成试听片（2026-09-18）。

背景：用户反馈「重点字跳出时音效太单一」——全片都被 kw_sfx_default=kw_ding
统一成同一个"叮"。需要更多音色让不同语义的重点词听感有区分。

设计原则（沿用铁律）：
  * 纯音合成：正弦 + 整数倍泛音，**绝不掺白噪声**（用户嫌白噪声底=杂音）
  * 每个音效独立归一到 -9dBFS 峰值，与现有音效库同规格
  * 音色之间要在「起音速度 / 音高走向 / 尾韵长度」三个维度上拉开差距，
    这样同一句换音色能明显听出不同

新增：
  kw_glass   玻璃清响    高频双音+短尾韵      → 数据/纯度/成分
  kw_triple  三连快叮    三音递升快击         → 奖励/福利/价格
  kw_charge  能量蓄满    低频上推+高频渐强收顶 → 能量/续航/爆发
  kw_thump   双段冲击    低频双击+瞬态         → 警告/踩坑/禁忌
  kw_bloom   四音绽放    琶音+末音长尾         → 利益点/结论
  kw_spring  弹性弹入    快上滑+回弹小跳       → 通用"弹出来"
  kw_shine   叮—咚收束   高音短+低音长尾韵      → 背书/收尾/CTA

用法：
  python3 synth_kw_sfx_v2.py                          # 生成到 /tmp/kw_candidates
  python3 synth_kw_sfx_v2.py --out <目录>
  python3 synth_kw_sfx_v2.py --audition <输出.mp4>    # 顺带拼试听片（含现有 5 款对比）
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
FONT = os.path.join(SKILL, "assets", "fonts", "新青年体.ttf")
FFMPEG = "/Users/mac/.local/bin/ffmpeg"


# ---------------- 基础合成 ----------------
def _env(n, attack, decay):
    t = np.arange(n) / FS
    e = np.ones(n)
    na = max(1, int(attack * FS))
    e[:na] = np.linspace(0, 1, na)
    e[na:] = np.exp(-(t[na:] - attack) / max(decay, 1e-3))
    return e


def tone(freq, dur, decay, harm=(1.0, 0.28, 0.10), attack=0.004):
    n = int(dur * FS)
    t = np.arange(n) / FS
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp > 0:
            y += amp * np.sin(2 * np.pi * freq * (k + 1) * t)
    return y * _env(n, attack, decay)


def sweep(f0, f1, dur, decay, harm=(1.0, 0.28, 0.10), attack=0.003,
          amp_ramp=None, hold=0.0):
    """扫频音。amp_ramp=(a0,a1) 时振幅线性渐变；hold>0 时扫完保持末频再衰减。"""
    n_sweep = int(dur * FS)
    n_hold = int(hold * FS)
    n = n_sweep + n_hold
    t = np.arange(n) / FS
    f = np.full(n, f1)
    f[:n_sweep] = f0 + (f1 - f0) * (t[:n_sweep] / dur)
    ph = np.cumsum(2 * np.pi * f / FS)
    y = np.zeros(n)
    for k, amp in enumerate(harm):
        if amp > 0:
            y += amp * np.sin(ph * (k + 1))
    e = np.exp(-t / max(decay, 1e-3))
    na = max(1, int(attack * FS))
    e[:na] *= np.linspace(0, 1, na)
    if amp_ramp:
        a0, a1 = amp_ramp
        e *= np.linspace(a0, a1, n)
    return y * e


def mix_into(base, y, at):
    k = int(at * FS)
    need = k + len(y)
    if need > len(base):
        base = np.pad(base, (0, need - len(base)))
    base[k:k + len(y)] += y
    return base


def echoes(y, taps):
    """前馈回声（纯音尾巴），taps=[(延迟秒, 增益), ...]"""
    out = y.copy()
    base = y.copy()
    for d, g in taps:
        k = int(d * FS)
        if 0 < k < len(y):
            out[k:] += g * base[:len(y) - k]
    return out


def norm(y, dbfs=TARGET_DBFS):
    peak = float(np.max(np.abs(y))) or 1.0
    return y * (10 ** (dbfs / 20.0)) / peak


def save(y, name, outdir):
    os.makedirs(outdir, exist_ok=True)
    y = norm(y)
    p = os.path.join(outdir, name)
    with wave.open(p, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((y * 32767).astype(np.int16).tobytes())
    print(f"  {name:<18} {len(y)/FS:.3f}s  peak={20*np.log10(np.max(np.abs(y))):.1f}dBFS")
    return p


# ---------------- 新增音色 ----------------
def kw_glass():
    """玻璃清响：C7+G7 同击 + 两抽头短尾韵 —— 清亮、有"验货"的干净感。"""
    a = tone(2093.0, 0.55, 0.12, harm=(1.0, 0.20, 0.06))
    b = tone(3136.0, 0.42, 0.09, harm=(1.0, 0.14))
    n = max(len(a), len(b))
    a = np.pad(a, (0, n - len(a)))
    b = np.pad(b, (0, n - len(b)))
    return echoes(a * 0.9 + b * 0.42, [(0.090, 0.30), (0.195, 0.15)])


def kw_triple():
    """三连快叮：1568→2093→2637 三击递升 —— 奖励/福利的三连音。"""
    n = int(0.46 * FS)
    y = np.zeros(n)
    for i, f in enumerate((1568.0, 2093.0, 2637.0)):
        y = mix_into(y, tone(f, 0.16, 0.045, harm=(1.0, 0.18)), 0.085 * i)
    return y


def kw_charge():
    """能量蓄满：90→260Hz 上推 + 1318/1976Hz 渐强，末尾一朵 1318Hz 绽放。"""
    n = int(1.05 * FS)
    y = np.zeros(n)
    y = mix_into(y, sweep(90.0, 260.0, 0.55, 0.40, harm=(1.0, 0.35, 0.12),
                          attack=0.02, amp_ramp=(0.25, 1.0)), 0.0)
    y = mix_into(y, sweep(1318.0, 1976.0, 0.50, 0.55, harm=(1.0, 0.22),
                          attack=0.05, amp_ramp=(0.10, 0.85)), 0.02)
    y = mix_into(y, tone(1318.0, 0.50, 0.16, harm=(1.0, 0.25, 0.08)), 0.55)
    return y


def kw_thump():
    """双段冲击：70Hz 闷击 + 高频瞬态，0.17s 后补一记更低的下踏。"""
    n = int(0.60 * FS)
    y = np.zeros(n)
    body = tone(70.0, 0.32, 0.10, harm=(1.0, 0.45, 0.15, 0.06), attack=0.002)
    y = mix_into(y, body, 0.0)
    y = mix_into(y, tone(2400.0, 0.05, 0.012, harm=(1.0, 0.2), attack=0.0008) * 0.22, 0.0)
    y = mix_into(y, tone(62.0, 0.28, 0.085, harm=(1.0, 0.40, 0.12), attack=0.002), 0.17)
    y = mix_into(y, tone(3100.0, 0.04, 0.010, harm=(1.0,), attack=0.0008) * 0.16, 0.17)
    return y


def kw_bloom():
    """四音绽放：C6-E6-G6-C7 琶音，末音长尾 —— 喜庆、适合"涨/赚/值"。"""
    n = int(0.90 * FS)
    y = np.zeros(n)
    for i, f in enumerate((1046.5, 1318.5, 1568.0, 2093.0)):
        dur, dec = (0.22, 0.075) if i < 3 else (0.62, 0.22)
        y = mix_into(y, tone(f, dur, dec, harm=(1.0, 0.24, 0.08)), 0.075 * i)
    return y


def kw_spring():
    """弹性弹入：300→1150Hz 快上滑（0.10s）+ 回弹小跳 —— "弹出来"的活泼版。"""
    n = int(0.34 * FS)
    y = np.zeros(n)
    y = mix_into(y, sweep(300.0, 1150.0, 0.10, 0.035, harm=(1.0, 0.30, 0.10)), 0.0)
    y = mix_into(y, tone(900.0, 0.07, 0.022, harm=(1.0, 0.22)), 0.105)
    y = mix_into(y, tone(1180.0, 0.10, 0.030, harm=(1.0, 0.20)), 0.155)
    return y


def kw_shine():
    """叮—咚收束：高音短击 + 低八度长尾 + 两抽头回声 —— 有"盖章确认"的收束感。"""
    a = tone(1568.0, 0.16, 0.050, harm=(1.0, 0.22))
    b = tone(784.0, 0.60, 0.20, harm=(1.0, 0.30, 0.10))
    n = max(len(a), len(b))
    a = np.pad(a, (0, n - len(a)))
    b = np.pad(b, (0, n - len(b)))
    return echoes(a * 0.75 + b * 0.95, [(0.125, 0.26), (0.245, 0.13)])


NEW = {
    "kw_glass.wav": (kw_glass, "玻璃清响", "数据 / 纯度 / 成分"),
    "kw_triple.wav": (kw_triple, "三连快叮", "奖励 / 福利 / 价格"),
    "kw_charge.wav": (kw_charge, "能量蓄满", "能量 / 续航 / 爆发"),
    "kw_thump.wav": (kw_thump, "双段冲击", "警告 / 踩坑 / 禁忌"),
    "kw_bloom.wav": (kw_bloom, "四音绽放", "利益点 / 结论"),
    "kw_spring.wav": (kw_spring, "弹性弹入", "通用「弹出来」"),
    "kw_shine.wav": (kw_shine, "叮—咚收束", "背书 / 收尾 / CTA"),
}

# 现有 5 款（试听片里做对比基准）
EXISTING = [
    ("kw_pop.wav", "短促上滑", "「弹出来」默认首选"),
    ("kw_ding.wav", "清脆双音", "当前全片默认音色"),
    ("kw_click.wav", "轻点", "打字机逐字"),
    ("kw_riser.wav", "上升渐强", "概念词 / 描边转实心"),
    ("kw_boom.wav", "综艺咚", "开场点题"),
]


# ---------------- 试听片 ----------------
def wav_read(path):
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float64) / 32767.0
        if w.getnchannels() == 2:
            data = data.reshape(-1, 2).mean(axis=1)
        return data


def build_audition(newdir, out_mp4):
    """拼试听片：每款一屏，PIL 画静帧（标题+中文名+波形）→ concat → 叠播放指示 → 混音。

    注意：全角「｜」在新青年体里缺字（会渲染成方框），标签分隔一律用「·」。
    """
    from PIL import Image, ImageDraw, ImageFont

    items = []
    for name, (fn, cn, use) in NEW.items():
        items.append({"title": name.replace(".wav", ""), "cn": cn, "use": use,
                      "path": os.path.join(newdir, name), "new": True})
    for name, cn, use in EXISTING:
        p = os.path.join(SFX_LIB, name)
        if os.path.exists(p):
            items.append({"title": name.replace(".wav", ""), "cn": cn, "use": use,
                          "path": p, "new": False})
    items = [i for i in items if i["new"]] + [i for i in items if not i["new"]]

    GAP1, GAP2, TAIL = 0.55, 1.45, 0.65
    audio = np.zeros(int(0.3 * FS))
    t_cursor = 0.3
    for it in items:
        y = norm(wav_read(it["path"]))
        audio = mix_into(audio, y, t_cursor + GAP1)
        audio = mix_into(audio, y, t_cursor + GAP2)
        it["y_raw"] = y
        it["t0"] = t_cursor
        it["dur"] = len(y) / FS
        it["t1"] = t_cursor + max(GAP2 + len(y) / FS + TAIL, 2.6)
        t_cursor = it["t1"]
    audio = np.concatenate([audio, np.zeros(int(0.5 * FS))])
    total = len(audio) / FS

    outdir = os.path.dirname(out_mp4)
    os.makedirs(outdir, exist_ok=True)
    tmp_wav = os.path.join(outdir, "_audition_audio.wav")
    with wave.open(tmp_wav, "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(FS)
        w.writeframes((audio * 32767).astype(np.int16).tobytes())

    W, H = 1280, 720
    BG = (20, 23, 28)
    f_big = ImageFont.truetype(FONT, 74)
    f_mid = ImageFont.truetype(FONT, 36)
    f_sm = ImageFont.truetype(FONT, 24)
    f_hd = ImageFont.truetype(FONT, 30)

    frames, listf = [], os.path.join(outdir, "_audition_list.txt")
    for idx, it in enumerate(items):
        img = Image.new("RGB", (W, H), BG)
        d = ImageDraw.Draw(img)
        tag = "新增" if it["new"] else "现有"
        accent = (255, 212, 0) if it["new"] else (154, 164, 178)
        d.text((60, 36), f"重点字音效试听 · 共 {len(items)} 款", font=f_hd, fill=(154, 164, 178))
        d.text((60, 78), f"每款连播 2 次 · 前 {len(NEW)} 款新增，后 {len(EXISTING)} 款现有对比",
               font=f_sm, fill=(107, 118, 136))
        d.text((60, 176), f"{idx+1:02d}  {it['title']}", font=f_big, fill=accent)
        d.text((64, 282), f"{it['cn']} · {it['use']} · {tag}", font=f_mid, fill=(255, 255, 255))
        d.text((64, 334), f"{it['dur']:.2f}s", font=f_sm, fill=(107, 118, 136))
        # 波形（峰值包络）：直观看出起音快慢与尾韵长短
        wx0, wx1, wcy, wh = 64, W - 64, 510, 130
        d.line([(wx0, wcy), (wx1, wcy)], fill=(52, 58, 68), width=2)
        y_abs = np.abs(it["y_raw"])
        cols = wx1 - wx0
        step = max(1, len(y_abs) // cols)
        peaks = [float(y_abs[i * step:(i + 1) * step].max() or 0) for i in range(cols)]
        pmax = max(peaks) or 1.0
        for i, pk in enumerate(peaks):
            hgt = int(pk / pmax * wh)
            d.line([(wx0 + i, wcy - hgt), (wx0 + i, wcy + hgt)], fill=accent, width=1)
        d.text((64, 660), "黄色=新增音色　灰色=现有音色　竖线高度=该时刻音量", font=f_sm, fill=(107, 118, 136))
        fp = os.path.join(outdir, f"_aud_{idx:02d}.png")
        img.save(fp)
        frames.append(fp)
        listf_entries = f"file '{fp}'\nduration {it['t1'] - it['t0']:.3f}\n"
        with open(listf, "a" if idx else "w") as lf:
            lf.write(listf_entries)
    with open(listf, "a") as lf:
        lf.write(f"file '{frames[-1]}'\n")

    vf = []
    for it in items:
        for at in (it["t0"] + GAP1, it["t0"] + GAP2):
            vf.append("drawbox=x=64:y=632:w=min(600\\,120+%.2f*320):h=8:color=0xFFD400@0.9:t=fill:"
                      "enable='between(t,%.3f,%.3f)'" % (it["dur"], at, at + 0.16))
    cmd = [FFMPEG, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf,
           "-i", tmp_wav, "-vf", ",".join(vf),
           "-c:v", "h264_videotoolbox", "-q:v", "70", "-pix_fmt", "yuv420p",
           "-r", "30", "-c:a", "aac", "-b:a", "192k", "-shortest", out_mp4]
    subprocess.run(cmd, check=True)
    for fp in frames:
        os.remove(fp)
    os.remove(listf)
    os.remove(tmp_wav)
    print(f"\n试听片: {out_mp4}  ({total:.1f}s, {len(items)} 款)")
    return items


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/kw_candidates")
    ap.add_argument("--audition", default=None)
    ap.add_argument("--save-selected", default=None,
                    help="逗号分隔的音效名（如 kw_glass,kw_charge），从 --out 复制进 assets/sfx")
    args = ap.parse_args()

    print("生成候选音效：")
    for name, (fn, cn, use) in NEW.items():
        save(fn(), name, args.out)

    if args.save_selected:
        import shutil
        for name in [s.strip() for s in args.save_selected.split(",") if s.strip()]:
            fn = name if name.endswith(".wav") else name + ".wav"
            src = os.path.join(args.out, fn)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(SFX_LIB, fn))
                print(f"  入库: {fn}")
            else:
                print(f"  WARN 不存在: {fn}")

    if args.audition:
        build_audition(args.out, args.audition)


if __name__ == "__main__":
    main()
