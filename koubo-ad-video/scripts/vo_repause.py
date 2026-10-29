# -*- coding: utf-8 -*-
"""vo_repause.py —— 配音标点补停顿（0 积分，确定性音频手术）

★ 背景（2026-10-29 灿爹返工）：
  CosyVoice 整段合成时，**逗号/句号的停顿全靠模型预测**，实测本片 20~24 个标点里
  90% 的 gap = 0ms（字与字首尾相接），听感就是「米糊外胚」连成一串，
  「今天我们讲，米糊。」后面几乎没有分段。用户要求：凡标点处该停的都要停。

★ 为什么不重新合成：
  ① 重合成不保证停顿一定出来（同一文本两次合成停顿位置都可能不同）；
  ② 要花积分；
  ③ 会让后面已经配好的分镜、重点字、卡片全部对不上。
  本脚本直接在成品配音上插静音，然后**同步平移所有时间轴**，下游零改动。

★ 处理对象（同目录全量同步，漏一个字幕就会飘）：
    audio/vo.wav             ← 插入静音（原文件备份 .bak_pause）
    audio/vo.words.json      ← 逐字时间戳
    audio/vo_mapped_v2.json  ← 4 字一组 tokens（make_fx_spec 用）
    audio/durations.json     ← 每句 start/end
    audio/shot_bounds.json   ← 每镜边界
    audio/vo.srt, vo_cloud.srt（存在则一并平移）

用法：
    PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
    $PY vo_repause.py --proj <工程目录> [--dry-run]
                      [--sent 0.42] [--comma 0.18] [--db -42]

    --proj   工程目录（含 audio/vo.wav、audio/vo.words.json、phrases.txt）
    --dry-run 只出报告不落盘
退出码：0 成功；2 无标点可处理；3 已存在 .bak_pause（重复执行保护，需 --force）
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np

PUNC_SENT = "。！？"
PUNC_MID = "，、：；"
FFMPEG = "/Users/mac/.local/bin/ffmpeg"
FFPROBE = "/Users/mac/.local/bin/ffprobe"


# ─────────────────────────── 音频读写 ───────────────────────────
def load_pcm(path, sr=None):
    cmd = [FFMPEG, "-v", "error", "-i", path, "-f", "s16le", "-ac", "1"]
    if sr:
        cmd += ["-ar", str(sr)]
    cmd += ["-"]
    r = subprocess.run(cmd, capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg 解码失败: {r.stderr.decode()[-300:]}")
    return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32) / 32768.0


def probe(path, key, stream="format"):
    if stream == "format":
        cmd = [FFPROBE, "-v", "error", "-show_entries", f"format={key}",
               "-of", "csv=p=0", path]
    else:
        cmd = [FFPROBE, "-v", "error", "-select_streams", "a:0",
               "-show_entries", f"stream={key}", "-of", "csv=p=0", path]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.stdout.strip()


def save_wav(path, data, sr):
    """写 16bit PCM wav（build_ad_video 读得动，ffmpeg 也认）"""
    import wave
    pcm = np.clip(data, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


# ─────────────────────── 能量包络与插入点 ───────────────────────
def frame_db(a, sr, hop=0.02):
    n = int(hop * sr)
    m = len(a) // n
    if m == 0:
        return np.array([]), n
    f = a[:m * n].reshape(m, n)
    rms = np.sqrt((f ** 2).mean(axis=1)) + 1e-9
    return 20 * np.log10(rms), n


def silence_len_at(db, hop, t, win=0.25, thr=-42):
    """t 秒附近 ±win 内、以 t 为中心的连续低能量段长度（秒）"""
    c = int(t / hop)
    w = int(win / hop)
    lo, hi = max(0, c - w), min(len(db), c + w)
    if hi <= lo:
        return 0.0
    seg = db[lo:hi]
    # 从中心向两侧扩散，遇到高于阈值的帧就停
    i = c - lo
    if i >= len(seg):
        i = len(seg) - 1
    if seg[i] >= thr:
        # 中心不是静音：在窗口里找最近的低能量帧
        low = np.where(seg < thr)[0]
        if len(low) == 0:
            return 0.0
        i = low[np.argmin(np.abs(low - (c - lo)))]
    l = i
    while l - 1 >= 0 and seg[l - 1] < thr:
        l -= 1
    r = i
    while r + 1 < len(seg) and seg[r + 1] < thr:
        r += 1
    return (r - l + 1) * hop


def best_insert_point(a, sr, t, win=0.22):
    """在 t 秒 ±win 内找能量最低的 20ms 帧中心，作为真正插入点（落在自然气口上）"""
    hop = 0.02
    n = int(hop * sr)
    lo = max(0, int((t - win) * sr))
    hi = min(len(a), int((t + win) * sr))
    if hi - lo < n * 2:
        return t
    seg = a[lo:hi]
    m = len(seg) // n
    if m < 2:
        return t
    f = seg[:m * n].reshape(m, n)
    rms = np.sqrt((f ** 2).mean(axis=1))
    k = int(np.argmin(rms))
    return lo / sr + (k + 0.5) * hop


def make_pad(a, sr, need, slots):
    """静音填充素材。

    ★三次踩坑（2026-10-29，全部实测）：
      ① 「最长低能量段」→ 拿到文件首尾的数字零（-180dB），插进去像突然真空；
      ② 「-38~-70dB 段的首帧到末帧」→ 中间夹着整句语音，往停顿里塞了人声循环；
      ③ 「从标点区间取样」→ CosyVoice 的标点区间并非真静音，而是**把字尾拖长**
         填满（实测峰值 -10dB），取出来的还是尾音。
    正解：用极低幅度白噪（约 -62dBFS），两端做淡化。
    成片有 BGM 铺底（-20dB 上下）时完全不可闻，也不会出现数字零那种「真空感」。
    """
    rng = np.random.default_rng(11)
    return rng.normal(0, 8e-4, need).astype(np.float32)


# ─────────────────────── 标点定位（以 words 为准）───────────────────────
def punct_slots(words):
    """★关键：CosyVoice 的 vo.words.json **把标点本身也当成一个 word**，
    它的 [begin,end] 区间就是模型给出的停顿区（实测一律 72~73ms，句号偶尔 146ms）。

    返回 [(words下标 i, 标点字符, 区间起, 区间止, 下一字起, 前一字止)]，i 指向标点那一项。
    **「下一字起」必须一起带出来**——插入点和补入量都要靠它算真实气口（见 2026-10-29 返工）。
    """
    out = []
    for i, w in enumerate(words):
        ch = w["text"]
        if ch in PUNC_SENT or ch in PUNC_MID:
            nb = words[i + 1]["begin_time"] / 1000.0 if i + 1 < len(words) else w["end_time"] / 1000.0
            pe = words[i - 1]["end_time"] / 1000.0 if i > 0 else w["begin_time"] / 1000.0
            out.append((i, ch, w["begin_time"] / 1000.0, w["end_time"] / 1000.0, nb, pe))
    return out


def find_insert_point(a, sr, b, e, nb, prev_end):
    """★2026-10-29 第三次返工：真正稳的插入点定位。返回 (秒, 模式, 气口剩余) 或 (None, None, 0)。

    前三版都栽在同一件事上——**信任 vo.words.json 给的标点时间**：
      ① 中点法：中点常落在字尾余音上（−25dB 正在发声）；
      ② 字尾结束法：mihub/mihuc 有 16~17 处区间的 [begin,end] 本身错位（标点被标到
         **下一字起音之后**），扫出来的「静音」已经落进下一个字里，切下去等于砍下一字；
      ③ 紧窗口 + 「不越过下一字」硬约束：因为 nb 本身偏早，窗口里根本没有气口，
         全部退化成软切。
    终版改为**能量主导、words 只当「选哪个气口」的参照**：
      ① 以标点中心为锚，±0.35/0.60s 开窗做 5ms 能量包络；
      ② 找连续 ≥40ms 低于 −42dB 的段 = 真实气口。字内部不可能有 40ms 这么安静的段，
         所以气口判据本身就保证了「不会切在字里」，不必再靠 nb 兜底；
      ③ 选离标点中心最近的那一段，插入点放段中点，气口剩余量 ge−pos 交给下游算补入量；
      ④ 窗口内一个气口都没有（真连读）→ 退到范围内能量最低点「软切」，
         下游对该点加 60ms 余弦淡出，把「啪一下切断」变成「轻轻收住」。

    模式：'gap' = 干净气口（切点 < −42dB，随便切）；'soft' = 连读没气口（需淡出）。
    """
    hop = 0.005
    n = int(hop * sr)
    mid = (b + e) / 2.0
    lo_t = max(0.0, mid - 0.35)
    hi_t = min(len(a) / sr, mid + 0.60)
    lo, hi = int(lo_t * sr), int(hi_t * sr)
    if hi - lo < n * 8:
        return None, None, 0.0
    seg = a[lo:hi]
    m = len(seg) // n
    if m < 8:
        return None, None, 0.0
    db = 20 * np.log10(np.sqrt((seg[:m * n].reshape(m, n) ** 2).mean(axis=1)) + 1e-9)
    ts = lo_t + np.arange(m) * hop

    thr = -42.0
    k = max(2, int(0.040 / hop))

    # ① 找气口，取离标点中心最近的一段
    best = None
    i = 0
    while i + k <= m:
        if db[i:i + k].max() < thr:
            j = i + k
            while j < m and db[j] < thr:
                j += 1
            gs = float(ts[i])
            ge = float(ts[min(j, m - 1)])
            if ge - gs >= 0.030:
                gm = (gs + ge) / 2.0
                d = abs(gm - mid)
                # 只认可落在标点附近的（气口中心不超出 −0.28 ~ +0.45s）
                if -0.28 <= (gm - mid) <= 0.45 and (best is None or d < best[0]):
                    best = (d, gs, ge)
            i = j
        else:
            i += 1
    if best:
        _, gs, ge = best
        pos = gs + (ge - gs) * 0.5
        return pos, "gap", gap_until_onset(db, ts, gs, hop)

    # ② 连读没气口：范围内最低点软切
    near = np.where((ts >= mid - 0.28) & (ts <= mid + 0.45))[0]
    if len(near) == 0:
        return None, None, 0.0
    j = int(near[int(np.argmin(db[near]))])
    return float(ts[j]), "soft", 0.0


def gap_until_onset(db, ts, gs, hop, onset_db=-32.0, hold=3):
    """从**气口起点**量到「下一字真正起音」= 这个气口本来有多长。

    ★两个要点：
      ① 必须从 gs 量、不是从插入点量。插入点在气口中心，从它量只算到后半段，
         前半段静音被漏掉 → 补入量偏大 → 总停顿比设定值长一倍。
      ② 不能用 nb−pos（下一字 begin）：CosyVoice 的逐字 begin 偏差能到 260ms
         （实测 mihub「外」标 5.21s、音频实际 4.95s 就起音），拿它算补入量
         会让总停顿忽长忽短。用能量直接看到起音才准。
    """
    k = int((gs - ts[0]) / hop)
    k = max(0, min(k, len(db) - hold - 1))
    for i in range(k, len(db) - hold):
        if all(db[i + q] > onset_db for q in range(hold)):
            return max(0.0, float(ts[i] - gs))
    return max(0.0, float(ts[-1] - gs))


# ─────────────────────── 时间映射 ───────────────────────
def remap(t, inserts):
    """t 秒 → 新时间。inserts 为 [(pos, dur, mode)] 升序；pos < t 的插入累加"""
    s = 0.0
    for item in inserts:
        p, d = item[0], item[1]
        if p < t - 1e-6:
            s += d
        else:
            break
    return t + s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--proj", required=True)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sent", type=float, default=0.38, help="句号目标停顿(秒)")
    ap.add_argument("--comma", type=float, default=0.15, help="逗号目标停顿(秒)")
    ap.add_argument("--dun", type=float, default=0.11, help="顿号目标停顿(秒)")
    ap.add_argument("--colon", type=float, default=0.16, help="冒号/分号目标停顿(秒)")
    ap.add_argument("--db", type=float, default=-42.0, help="（保留）静音判定阈值")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    A = os.path.join(a.proj, "audio")
    wav = os.path.join(A, "vo.wav")
    words_p = os.path.join(A, "vo.words.json")
    for p in (wav, words_p):
        if not os.path.exists(p):
            raise SystemExit(f"缺文件 {p}")

    bak = wav + ".bak_pause"
    bdir = os.path.join(A, ".bak_pause")
    meta_p = os.path.join(bdir, "meta.json")
    jsons = ["vo.words.json", "durations.json", "vo_mapped_v2.json",
             "shot_bounds.json", "vo.srt", "vo_cloud.srt"]

    try:
        cur_dur = float(probe(wav, "duration"))
    except Exception:
        cur_dur = 0.0
    meta = json.load(open(meta_p, encoding="utf-8")) if os.path.exists(meta_p) else None

    def snapshot():
        if a.dry_run:                      # dry-run 只读，不许改任何文件
            return
        os.makedirs(bdir, exist_ok=True)
        shutil.copy2(wav, bak)
        for _n in jsons:
            _p = os.path.join(A, _n)
            if os.path.exists(_p):
                shutil.copy2(_p, os.path.join(bdir, _n))

    if meta:
        if abs(meta.get("after_dur", -9) - cur_dur) < 0.03:
            # 这条配音已经补过停顿，且之后没被重新合成
            if not (a.force or a.dry_run):
                print("  已补过停顿（配音未被替换），跳过")
                return 0
            if a.dry_run:
                # 只读模式：当前盘上是「已补过停顿」的版本 → 改成读备份原件做估算，不动任何文件
                if os.path.exists(bak) and os.path.exists(os.path.join(bdir, "vo.words.json")):
                    wav = bak
                    words_p = os.path.join(bdir, "vo.words.json")
                    print("  [dry-run] 当前为已补停顿版 → 按备份原件重算（不落盘）")
            else:
                if meta.get("recoverable") is False:
                    raise SystemExit(
                        "这条的原始配音快照已不可用（recoverable=false）——重做请重新合成配音"
                        "（pipeline_run.py --tts-only）后再跑本脚本")
                shutil.copy2(bak, wav)                    # --force：回到补停顿之前
                for _n in jsons:
                    _s = os.path.join(bdir, _n)
                    if os.path.exists(_s):
                        shutil.copy2(_s, os.path.join(A, _n))
                print("  --force：已从备份恢复原始配音与时间轴")
        else:
            # 配音被重新合成过 → 旧快照作废，用当前配音重建
            print("  检测到新配音 → 重建备份与时间轴快照")
            snapshot()
    else:
        if os.path.exists(bak):
            # 早期版本留下的备份但没有元数据：无法判断 wav 是否已插过停顿
            raise SystemExit(f"{bak} 存在但缺 .bak_pause/meta.json，无法判断是否已处理；"
                             f"请先移走 audio/.bak_pause 再重跑")
        snapshot()

    W = json.load(open(words_p, encoding="utf-8"))
    words = W["words"]
    text = W["original_text"]
    sr = int(probe(wav, "sample_rate", "stream") or 24000)
    audio = load_pcm(wav, sr)
    dur = len(audio) / sr
    db, hop_n = frame_db(audio, sr)

    slots = punct_slots(words)
    if not slots:
        print("无标点可处理")
        return 2

    def target_of(ch):
        if ch in PUNC_SENT:
            return a.sent
        if ch == "，":
            return a.comma
        if ch == "、":
            return a.dun
        return a.colon

    inserts, rows = [], []
    for wi, ch, b, e, nb, pe in slots:
        if wi == 0 or wi >= len(words) - 1:
            continue                      # 句首/句末标点不处理（后面没内容）
        target = target_of(ch)
        pos, mode, after_gap = find_insert_point(audio, sr, b, e, nb, pe)
        if pos is None:
            rows.append((wi, ch, b, 0.0, 0.0, b, "跳过"))
            continue
        if mode == "gap":
            cur = max(0.0, after_gap)     # 插入点到气口尽头，本来就有这么长静音
            need = target - cur
        else:
            # 连读没气口：切点前后都是语音，只补半量并把上限压到 0.20s
            cur = 0.0
            need = min(target, 0.20)
        note = "" if mode == "gap" else "软切"
        # 相邻标点极近（<0.15s）视为同一处，合并取大值
        if inserts and pos - inserts[-1][0] < 0.15:
            prev_pos, prev_need, prev_mode = inserts[-1]
            keep = "gap" if "gap" in (prev_mode, mode) else "soft"
            inserts[-1] = (prev_pos, max(prev_need, need), keep)
            rows.append((wi, ch, b, cur, max(0.0, need), pos, "合并"))
            continue
        rows.append((wi, ch, b, cur, max(0.0, need), pos, note))
        if need > 0.005:
            inserts.append((pos, need, mode))

    inserts.sort()
    total_add = sum(d for _, d, _m in inserts)
    print(f"══ {os.path.basename(a.proj.rstrip('/'))}  原时长 {dur:.2f}s")
    print(f"{'字位':>4} {'标点':<2} {'现有':>6} {'补入':>6}  插入点  备注")
    for wi, ch, b, cur, need, pos, note in rows:
        print(f"{wi:>6} {ch:<2} {cur*1000:5.0f}ms {need*1000:5.0f}ms  {pos:6.2f}s {note}")
    n_soft = sum(1 for _p, _d, m in inserts if m == "soft")
    print(f"→ 插入 {len(inserts)} 处（其中软切 {n_soft} 处），总增 {total_add:.2f}s → {dur + total_add:.2f}s")

    if a.dry_run:
        return 0

    # ── 拼新音频 ──
    pad_slots = [(b, e) for _, _, b, e, _nb, _pe in slots]

    def fade_out(x, ms):
        """余弦淡出：比线性更接近语音自然衰减，听不出「被掐断」"""
        f = max(24, int(ms / 1000.0 * sr))
        f = min(f, len(x) - 1)
        if f > 8:
            x[-f:] *= (0.5 * (1 + np.cos(np.linspace(0, np.pi, f)))).astype(np.float32)
        return x

    pieces, last = [], 0
    fmin = max(24, int(0.006 * sr))
    for pos, d, mode in inserts:
        s = int(pos * sr)
        if s <= last:
            continue
        seg = audio[last:s].copy()
        # ★gap 切在真静音里，6ms 只为防爆音；soft 切在连读的字缝上，
        #   用 60ms 余弦淡出让尾音自然收住（这正是「卡、像被切掉」的解药）
        fade_out(seg, 60 if mode == "soft" else 6)
        pieces.append(seg)
        pad = make_pad(audio, sr, max(1, int(d * sr)), pad_slots)
        # 两端各 8ms 淡入淡出，避免爆音
        f = min(int(0.008 * sr), max(1, len(pad) // 2 - 1))
        if len(pad) > 2 * f:
            pad[:f] *= np.linspace(0, 1, f, dtype=np.float32)
            pad[-f:] *= np.linspace(1, 0, f, dtype=np.float32)
        pieces.append(pad)
        last = s
    tail = audio[last:].copy()
    if inserts and last > 0 and len(tail) > fmin:
        tail[:fmin] *= np.linspace(0, 1, fmin, dtype=np.float32)
    pieces.append(tail)
    new = np.concatenate(pieces)
    save_wav(wav, new, sr)
    print(f"  新配音 {len(new)/sr:.2f}s → {wav}（原件 {os.path.basename(bak)}）")

    # ── 同步时间轴 ──
    # ① words
    for w in W["words"]:
        w["begin_time"] = round(remap(w["begin_time"] / 1000.0, inserts) * 1000)
        w["end_time"] = round(remap(w["end_time"] / 1000.0, inserts) * 1000)
    W["duration_sec"] = round(len(new) / sr, 6)
    json.dump(W, open(words_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ② durations
    dp = os.path.join(A, "durations.json")
    if os.path.exists(dp):
        D = json.load(open(dp, encoding="utf-8"))
        for s in D["sentences"]:
            s["start"] = round(remap(s["start"], inserts), 3)
            s["end"] = round(remap(s["end"], inserts), 3)
        json.dump(D, open(dp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ③ vo_mapped_v2（tokens，毫秒 + "00:00:0x.xx" 字符串）
    mp = os.path.join(A, "vo_mapped_v2.json")
    if os.path.exists(mp):
        M = json.load(open(mp, encoding="utf-8"))

        def ts_to_ms(s):
            m = re.match(r"(\d+):(\d+):(\d+(?:\.\d+)?)", s)
            h, mi, se = int(m.group(1)), int(m.group(2)), float(m.group(3))
            return (h * 3600 + mi * 60 + se) * 1000.0

        def ms_to_ts(ms):
            h = int(ms // 3600000)
            mi = int((ms % 3600000) // 60000)
            se = (ms % 60000) / 1000.0
            return f"{h:02d}:{mi:02d}:{se:05.2f}"

        for it in M.get("transcription", []):
            o = it.get("offsets") or {}
            if "from" in o:
                o["from"] = round(remap(o["from"] / 1000.0, inserts) * 1000)
            if "to" in o:
                o["to"] = round(remap(o["to"] / 1000.0, inserts) * 1000)
            t = it.get("timestamps") or {}
            if "from" in t:
                t["from"] = ms_to_ts(remap(ts_to_ms(t["from"]), inserts))
            if "to" in t:
                t["to"] = ms_to_ts(remap(ts_to_ms(t["to"]), inserts))
        json.dump(M, open(mp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ④ shot_bounds
    bp = os.path.join(A, "shot_bounds.json")
    if os.path.exists(bp):
        B = json.load(open(bp, encoding="utf-8"))
        B = [[round(remap(s, inserts), 3), round(remap(e, inserts), 3)] for s, e in B]
        json.dump(B, open(bp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # ⑤ srt
    for name in ("vo.srt", "vo_cloud.srt"):
        sp = os.path.join(A, name)
        if not os.path.exists(sp):
            continue
        out = []
        for line in open(sp, encoding="utf-8"):
            m = re.match(r"(\d+):(\d+):(\d+),(\d+) --> (\d+):(\d+):(\d+),(\d+)", line.strip())
            if m:
                g = list(map(int, m.groups()))
                s = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
                e = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
                s, e = remap(s, inserts), remap(e, inserts)

                def fmt(x):
                    hh = int(x // 3600)
                    mm = int((x % 3600) // 60)
                    ss = int(x % 60)
                    ms = int(round((x - int(x)) * 1000))
                    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"
                out.append(f"{fmt(s)} --> {fmt(e)}\n")
            else:
                out.append(line)
        open(sp, "w", encoding="utf-8").writelines(out)

    print("  时间轴已同步：words / durations / vo_mapped_v2 / shot_bounds / srt")
    import time as _t
    json.dump({"orig_dur": round(dur, 3), "after_dur": round(len(new) / sr, 3),
               "sent": a.sent, "comma": a.comma, "dun": a.dun, "colon": a.colon,
               "inserts": len(inserts), "added_sec": round(total_add, 3),
               "at": _t.strftime("%Y-%m-%d %H:%M")},
              open(meta_p, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
