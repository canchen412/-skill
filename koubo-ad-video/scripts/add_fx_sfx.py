#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""动效音效联动（2026-10-27 灿爹要求：重点字/数字动效出现时必须有对应音效）
════════════════════════════════════════════════════════════════════
痛点：整条流水线原先只有 vo + 卷播混音，正片里的 98% 圆环、数字滚动、
      详情卡滑入**全是哑的**——画面在动、耳朵什么都听不到，动效白做。

本脚本把「画面事件」翻译成「音效事件」，再合成一条绝对时间轴音轨：

  画面事件来源
  ├─ fx_spec.viz   ring（圆环）/ count（数字滚动）/ stamp（盖章）
  ├─ fx_spec.kw    重点字弹出（KwOverlay）
  ├─ config.product_cards  详情卡滑入（phrase + in_off）
  └─ config.banner 卷播大字滑入（可选）

  时间锚点一律用**实测**而非估算：
  ├─ ring   落定 = 数字首次达标的帧（spring damping22/stiffness190）
  │         实测 durSec=1.6 时在 +0.767s ⇒ 比例 0.48
  ├─ count  落定比例另测（见 SETTLE_RATIO）
  └─ 卡片   = durations[phrase-1].start + in_off

用法
────────────────────────────────────────────────────────────────
  # 只生成音效轨（不碰画面）
  python3 add_fx_sfx.py --fx-spec fx_spec_sD.json --config sD/config.json \
      --out sD/audio/sfx_fx.wav

  # 生成并直接混进成片
  python3 add_fx_sfx.py --fx-spec ... --config ... --in final_sD.mp4 \
      --out-mp4 final_sfx.mp4

  # 看清单不落盘
  python3 add_fx_sfx.py ... --dry-run
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

FF = shutil.which("ffmpeg") or "/Users/mac/.local/bin/ffmpeg"
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SFX_DIR = f"{SKILL}/assets/sfx"
SR = 48000  # 统一 48k，与人声/成片一致

# ══════════════════════════════════════════════════════════════════
# 音频映射表：画面事件 → 音效
#   sfx          : ★写 build_ad_video 音色池的 **key**（不是文件名），
#                  这样 --emit-config 导出的 sfx_at 能被原生混音直接用
#   settle_ratio : 落定时刻占动效总时长的比例（spring 实测，勿凭感觉改）
#   db           : 相对素材原峰值的增益（素材已归一，正值=更响）
#   fade         : 淡出秒数（防止尾巴压到下一句人声）
#
# ★电平基准（2026-10-27 实测）：人声有效值 ≈ −21dB、峰值 ≈ −6dB。
#   音效峰值落在 −26 ~ −14dB 之间才「听得见又不抢人声」：
#     −26dB 以下 = 白做（实测 −29dB 的起手音完全被盖掉）
#     −14dB 左右 = 重音/CTA
#   起手与落定之间必须留 ≥8dB 落差，才有「冲上去→叮」的层次。
# ══════════════════════════════════════════════════════════════════
RULES = {
    # ── 数字圆环（98% 纯度）：渐强起手（轻）+ 落定清脆叮（重）──
    "ring_start":  {"sfx": "kw_riser", "db": -15.0, "fade": 0.04},
    "ring_settle": {"sfx": "kw_ding",  "db": -6.0,  "fade": 0.10},
    # ── 数字滚动（300mg 这类）：滴答起手 + 上滑五度落定 ──
    "count_start":  {"sfx": "kw_tick",   "db": -16.0, "fade": 0.02},
    "count_settle": {"sfx": "kw_bellup", "db": -7.0,  "fade": 0.08},
    # ── 盖章（旋转砸下）：低频重击，全场最有分量 ──
    "stamp": {"sfx": "kw_thump", "db": -4.0, "fade": 0.10},
    # ── 重点字弹出：短促吸附 ──
    "kw": {"sfx": "kw_pop", "db": -9.0, "fade": 0.03},
    # ── 详情卡滑入：轻「啪」一声，确认它到位 ──
    #   ★2026-10-28 实测：原本 -15dB 混进成片后事件窗 Δ 仅 -0.07dB（等于没有）——
    #     卡片恰好人声满格在说话（"诺特是大牌子"），轻声被彻底吃掉。提到 -8dB
    #     后与「重点字 -9dB」同量级，短促一下不抢词，但耳朵能确认卡片到位。
    "card": {"sfx": "kw_snap", "db": -5.0, "fade": 0.03},
    # ── 价格卡（CTA）：与落定同音色（隔 7s 形成呼应），略低一档 ──
    "card_price": {"sfx": "kw_ding", "db": -10.0, "fade": 0.08},
    # ── 卷播大字滑入：轻，卷播段本身已很满 ──
    "hero_label": {"sfx": "kw_snap", "db": -17.0, "fade": 0.03},
}

# 音色 key → 素材文件名（与 build_ad_video 的 sfx_files 池保持一致）
KEY_FILE = {
    "kw_riser": "kw_riser.wav", "kw_ding": "kw_ding.wav",
    "kw_snap": "kw_snap.wav", "kw_pop": "kw_pop.wav",
    "kw_tick": "kw_tick.wav", "kw_bellup": "kw_bellup.wav",
    "kw_thump": "kw_thump.wav", "pop_in": "pop_in.wav",
}

# 落定时刻占比：ring 实测 0.767/1.6 = 0.48（数字首次=98 的第 23 帧）
SETTLE_RATIO = {"ring": 0.48, "count": 0.45, "stamp": 0.0}

SRC_ALIAS = {}

# 同一时刻最小间隔：太密的音效只留最响的那个（避免糊成一团）
MIN_GAP = 0.09


def res(*p):
    return os.path.join(*p)


def load_wav_48k(key, tmpdir):
    """音色 key → 48k 单声道 float32（借 ffmpeg 解码）"""
    fname = KEY_FILE.get(key, key if key.endswith(".wav") else key + ".wav")
    src = SRC_ALIAS.get(fname, res(SFX_DIR, fname))
    if not os.path.exists(src):
        return None
    dst = res(tmpdir, os.path.basename(src).rsplit(".", 1)[0] + "_48k.wav")
    if not os.path.exists(dst):
        # ★用 pcm_s16le：Python 标准库 wave 不认 float32(wFormatTag=3)
        subprocess.run([FF, "-y", "-v", "error", "-i", src,
                        "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", dst],
                       check=True, capture_output=True)
    w = wave.open(dst)
    n = w.getnframes()
    a = np.frombuffer(w.readframes(n), dtype="<i2").astype(np.float32) / 32768.0
    w.close()
    return a


def wav_dur(path):
    w = wave.open(path)
    d = w.getnframes() / w.getframerate()
    w.close()
    return d


def collect_events(spec, config, durations):
    """把画面事件翻译成 [(t, tag, note)]，时间一律绝对秒"""
    ev = []

    # ① 数字/可视化动效
    for v in spec.get("viz", []):
        kind = v.get("kind", "")
        t0 = float(v.get("startSec", 0))
        dur = float(v.get("durSec", 1.6))
        if kind == "ring":
            ev.append((t0, "ring_start", f"圆环起手 {v.get('value', '')}"))
            ev.append((t0 + dur * SETTLE_RATIO["ring"], "ring_settle",
                       f"圆环落定 {v.get('value', '')} {v.get('label', '')}"))
        elif kind == "count":
            ev.append((t0, "count_start", f"数字滚动 {v.get('text', '')}"))
            ev.append((t0 + dur * SETTLE_RATIO["count"], "count_settle",
                       f"数字落定 {v.get('text', '')}"))
        elif kind == "stamp":
            ev.append((t0 + 0.18, "stamp", f"盖章 {v.get('text', '')}"))

    # ② 重点字动效
    for w in spec.get("kw", []):
        ev.append((float(w.get("startSec", 0)), "kw", f"重点字 {w.get('text', '')}"))

    # ③ 详情卡 / 价格卡
    sent = (durations or {}).get("sentences") or []
    for i, c in enumerate(config.get("product_cards") or []):
        ph = int(c.get("phrase") or 0)
        if not (1 <= ph <= len(sent)):
            continue
        t0 = float(sent[ph - 1]["start"]) + float(c.get("in_off", 0.5))
        name = os.path.basename(str(c.get("src", "")))
        is_price = any(k in name for k in ("价格", "price", "价签"))
        ev.append((t0, "card_price" if is_price else "card", f"卡片 {name}"))

    # ③b 动效层详情卡（fx_spec.cards，2026-10-28 新增）
    #     ProductCardFx 的 spring 是 damping11/stiffness170/mass0.9，实测约 0.22s 落定，
    #     「啪」要压在那一下上，早于它就是空放、晚于它就错拍。
    for c in spec.get("cards") or []:
        name = os.path.basename(str(c.get("src", "")))
        is_price = any(k in name for k in ("价格", "price", "价签"))
        ev.append((float(c.get("startSec", 0)) + 0.22,
                   "card_price" if is_price else "card", f"详情卡 {name}"))

    # ④ 卷播大字滑入（clickAt + 0.12s，与 SlideLabel 的 local 起滑一致）
    bl = config.get("banner_label") or {}
    if bl.get("enabled"):
        t = float(bl.get("click_at", 2.72)) + 0.12
        ev.append((t, "hero_label", f"卷播大字「{bl.get('text', '')}」滑入"))

    ev.sort(key=lambda x: x[0])
    return ev


def dedupe(events):
    """同一时刻挤多个音效 → 只留增益最大的，避免糊"""
    keep = []
    for e in events:
        if keep and e[0] - keep[-1][0] < MIN_GAP:
            a, b = RULES[keep[-1][1]], RULES[e[1]]
            if b["db"] > a["db"]:
                keep[-1] = e
            continue
        keep.append(e)
    return keep


def build_track(events, total, tmpdir):
    buf = np.zeros(int(total * SR) + SR, dtype=np.float32)
    report = []
    for t, tag, note in events:
        r = RULES[tag]
        a = load_wav_48k(r["sfx"], tmpdir)
        if a is None:
            report.append((t, tag, note, "缺素材"))
            continue
        g = 10 ** (r["db"] / 20.0)
        a = a * g
        # 淡入 2ms 防爆音；淡出按规则
        fi = min(len(a), int(0.002 * SR))
        if fi > 0:
            a[:fi] *= np.linspace(0, 1, fi)
        fo = int(r.get("fade", 0.03) * SR)
        if 0 < fo < len(a):
            a[-fo:] *= np.linspace(1, 0, fo)
        i0 = int(round(t * SR))
        seg = a[: max(0, len(buf) - i0)]
        buf[i0: i0 + len(seg)] += seg
        report.append((t, tag, note, f"{r['sfx']} {r['db']:+.1f}dB"))
    return buf, report


def write_wav(path, buf):
    n = len(buf)
    peak = float(np.abs(buf).max())
    if peak > 0.99:
        buf = buf * (0.99 / peak)
    pcm = np.clip(buf, -1, 1)
    w = wave.open(path, "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((pcm * 32767).astype("<i2").tobytes())
    w.close()
    return peak


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fx-spec", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--durations", default="", help="默认读 config 同目录 audio/durations.json")
    ap.add_argument("--out", default="", help="输出音效轨 wav")
    ap.add_argument("--total", type=float, default=0, help="总时长，缺省取视频时长")
    ap.add_argument("--in", dest="src_video", default="", help="要混入的视频")
    ap.add_argument("--out-mp4", default="")
    # ★2026-10-28：音效轨已自动补偿到峰值 -3dBFS，这里只做「还想更响/更轻」的手调
    ap.add_argument("--sfx-gain", type=float, default=0.0,
                    help="音效整体额外增益 dB（正=更响；自动补偿之后叠加，默认 0）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--emit-config", default="",
                    help="导出 {sfx_at, sfx_volume} 补丁 json，喂给 build_ad_video 一次成型")
    a = ap.parse_args()

    spec = json.load(open(a.fx_spec, encoding="utf-8"))
    config = json.load(open(a.config, encoding="utf-8"))
    cfg_dir = os.path.dirname(os.path.abspath(a.config))
    dur_path = a.durations or res(cfg_dir, "audio", "durations.json")
    durations = json.load(open(dur_path, encoding="utf-8")) if os.path.exists(dur_path) else {}

    total = a.total
    if not total and a.src_video:
        p = subprocess.run([FF.replace("ffmpeg", "ffprobe"), "-v", "error",
                            "-show_entries", "format=duration", "-of", "csv=p=0", a.src_video],
                           capture_output=True, text=True)
        total = float(p.stdout.strip() or 0)
    if not total:
        s = (durations.get("sentences") or [])
        total = float(s[-1]["end"]) if s else 30.0

    events = dedupe(collect_events(spec, config, durations))

    print(f"动效音效清单（总长 {total:.2f}s，共 {len(events)} 个事件）")
    print(f"{'时刻':>8}  {'类型':<12} 说明")
    for t, tag, note in events:
        r = RULES[tag]
        print(f"{t:8.2f}s  {tag:<12} {note}  →  {r['sfx']} {r['db']:+.0f}dB")

    if a.emit_config:
        # db → 线性 volume（build_ad_video 的 sfx_volume 是线性增益）
        patch = {"sfx_at": [[round(t, 2), RULES[tag]["sfx"]] for t, tag, _ in events],
                 "sfx_volume": {}}
        for _, tag, _ in events:
            r = RULES[tag]
            patch["sfx_volume"][r["sfx"]] = round(10 ** (r["db"] / 20.0), 3)
        with open(a.emit_config, "w", encoding="utf-8") as f:
            json.dump(patch, f, ensure_ascii=False, indent=1)
        print(f"\n✅ 已导出 config 补丁 {a.emit_config}")
        print("   合并进 config.json 后重跑 build_ad_video 即可一次成型（无二次编码）")

    if a.dry_run:
        return

    tmpdir = tempfile.mkdtemp(prefix="fxsx_")
    try:
        buf, report = build_track(events, total, tmpdir)
        out = a.out or res(cfg_dir, "audio", "sfx_fx.wav")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        pk = write_wav(out, buf)
        print(f"\n✅ 音效轨 {out}  （峰值 {pk:.3f}，{wav_dur(out):.2f}s）")

        if a.src_video and a.out_mp4:
            # ★2026-10-28 修「音效进了成片但听不见」：
            #   各音色源（kw_pop / kw_snap / kw_ding…）自身电平参差，实测峰值只有
            #   -18~-25 dBFS；再按 -9dB / -5dB 衰减后落到 -27dB 以下，比人声(-13dB RMS)
            #   低 15dB 以上 ⇒ 音轨里确实有、耳朵里没有（差值法测 Δ 仅 +0.06dB）。
            #   这里先按**目标响度**算一个补偿增益（默认抬到峰值 -3dBFS），再叠用户给的
            #   --sfx-gain 微调。不改各事件的相对强弱关系，只把整轨抬到听得见的量级。
            _g = float(getattr(a, "sfx_gain", 0.0) or 0.0)
            _pk = max(pk, 1e-6)
            _auto = max(0.0, 20 * np.log10(0.70 / _pk)) if _pk < 0.70 else 0.0
            _tg = _auto + _g
            _vol = f"volume={_tg:.2f}dB," if abs(_tg) > 0.05 else ""
            if abs(_tg) > 0.05:
                print(f"   音效补偿增益 {_tg:+.1f}dB"
                      f"（原峰 {20 * np.log10(_pk):.1f}dBFS → 目标 -3.1dBFS"
                      + (f"，叠加手调 {_g:+.1f}dB" if abs(_g) > 0.05 else "") + "）")
            sh([FF, "-y", "-v", "error", "-i", a.src_video, "-i", out,
                "-filter_complex",
                # 人声主导：音效轨已按 dB 控好，amix normalize=0 保持原始电平，
                # 出口再软限幅防削波（人声 -13 LUFS 与音效叠加余量足够）
                f"[1:a]{_vol}asetpts=PTS-STARTPTS[sx];"
                f"[0:a][sx]amix=inputs=2:normalize=0:duration=first,"
                # ★latency=1 必须写：alimiter 是 look-ahead 限幅器，默认会**整条音频
                #   延后 attack 时长**（实测 attack=5 ⇒ 晚 238 采样 ≈ 4.96ms）。
                #   画面不会跟着延，于是口型/字幕与声音错开。latency=1 让它自己扣回。
                f"alimiter=limit=0.92:attack=5:release=60:level=disabled:latency=1[a]",
                "-map", "0:v", "-map", "[a]",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", a.out_mp4])
            print(f"✅ 混音成片 {a.out_mp4}")
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def sh(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(r.stderr[-1200:])
        raise SystemExit(f"失败：{cmd[0]}")


if __name__ == "__main__":
    main()
