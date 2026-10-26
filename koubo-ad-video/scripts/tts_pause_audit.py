# -*- coding: utf-8 -*-
"""
配音断句质检 + 音频手术（koubo-ad-video 技能）。v2

★ 背景（2026-09-24 灿爹实测拍板）：
  CosyVoice 段内逗号的停顿全靠模型预测、不可靠（已知前科：
  "想要薄肌，肌酸怎么选" 整句零停顿；"销量摆着，闭眼入的底气"
  停顿砸进「闭眼|入」词中间、逗号处反而连读）。网络热词/生造词
  （闭眼入、薅羊毛…）尤其容易被词内乱断。

★ 原理：不重新合成（零积分、确定性），在拼接好的配音音频上做手术——
  信号源（两层）：
    A. whisper 原始 item 边界（whisper 在真实停顿处分段）= 模型实际停顿点
    B. silencedetect 静音区间 = 可手术的音频区域
  判定：
    漏停 — 原文 ，、； 边界上**没有** whisper item 停顿 → 需补停顿：
           若附近(±0.2~+0.45s)有静音区（含"停错位置"的）→ replace
           [逗号处, 静音尾] 为 0.18s 干净停顿（顺带吞掉词内误停）；
           没有 → 纯插入 0.18s。
    误停(仅报告) — whisper item 停顿落在无标点边界。多为数字/主语后
           的自然韵律（保留），真正害人的词内误停会被上面的 replace 吞掉。
  修复后必须重跑 whisper + align_vo 刷 tokens，再渲染。

★ v3 教训（2026-09-24 「摆着闭眼，入」二次返工）：
  「逗号漏停 + 逗号后方 ≤0.7s 处有词内误停」同时出现 = 拼音对齐在该区域
  偏移（实测偏 2 字），replace 会把逗号**前面**的自然停顿当成误置停顿吞掉、
  真正的词内误停原样保留 → 等于白切。这种组合**禁止自动手术**，定式改为：
  重合成该句（合成文本里把该逗号升级成句号，字幕文本不动）。
  另：whisper item 相接≠没有停顿（0.2s 停顿常被 item 合并掩盖），漏停判定
  只作线索，最终以 silencedetect + 听感为准。

用法:
  python3 tts_pause_audit.py <vo.wav> <phrases.txt> --heard vo16.json [--fix <out.wav>]
    --heard    whisper **原始**转写 json（whisper-cli -oj 产物，不是 fixed）
    --fix      输出修复后 wav（给同路径先备份 .bak）；不给只出报告
    --min-gap 0.08 item 停顿判定；--insert 0.18 补入时长；--db -33
退出码: 0=无问题; 3=已修复; 4=有问题未修
"""
import json, sys, os, re, wave, shutil, subprocess

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from align_vo import load_heard, align, pchars  # noqa: E402

import numpy as np  # noqa: E402


def detect_silences(wav_path, db=-33, min_dur=0.08):
    cmd = ["ffmpeg", "-i", wav_path,
           "-af", f"silencedetect=noise={db}dB:d={min_dur}", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    starts, out = [], []
    for line in r.stderr.splitlines():
        m = re.search(r"silence_start: ([\d.]+)", line)
        if m:
            starts.append(float(m.group(1)))
        m = re.search(r"silence_end: ([\d.]+)", line)
        if m and starts:
            out.append((starts.pop(0), float(m.group(1))))
    if starts:
        with wave.open(wav_path, "rb") as w:
            dur = w.getnframes() / w.getframerate()
        out.append((starts.pop(0), dur))
    return out


def load_wav(path):
    try:
        with wave.open(path, "rb") as w:
            sr, ch, sw, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
        if sw == 4:
            a = np.frombuffer(raw, dtype=np.float32)
            peak = np.abs(a).max() or 1.0
            if peak <= 1.0:
                a = a * 32767.0
        elif sw == 2:
            a = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
        else:
            raise wave.Error(f"位深 {sw}")
        if ch > 1:
            a = a.reshape(-1, ch).mean(axis=1)
        return a, sr
    except wave.Error:
        # CosyVoice 的 float32 wav wave 模块打不开 → ffmpeg 解码成 mono s16
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", path, "-f", "s16le", "-ac", "1", "-"],
                           capture_output=True)
        if r.returncode != 0:
            raise SystemExit(f"ffmpeg 解码失败: {r.stderr.decode()[-300:]}")
        sr = int(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                                 "-show_entries", "stream=sample_rate", "-of", "csv=p=0", path],
                                capture_output=True, text=True).stdout.strip())
        return np.frombuffer(r.stdout, dtype=np.int16).astype(np.float32), sr


def save_wav(path, a, sr):
    a = np.clip(a, -32768, 32767).astype(np.int16)
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(a.tobytes())


def main():
    ap = sys.argv[1:]
    def opt(name, default):
        return ap[ap.index(name) + 1] if name in ap and ap.index(name) + 1 < len(ap) else default
    wav, pf = ap[0], ap[1]
    heard_path = opt("--heard", None)
    fix_out = ap[ap.index("--fix") + 1] if "--fix" in ap and ap.index("--fix") + 1 < len(ap) else None
    min_gap = float(opt("--min-gap", "0.08"))
    insert_s = float(opt("--insert", "0.18"))
    db = float(opt("--db", "-33"))
    if not heard_path:
        raise SystemExit("必须给 --heard <whisper 原始 json>")

    # ---------- 文案：字符流 + 每个字符边界后的标点 ----------
    phrases = [l.strip() for l in open(pf, encoding="utf-8") if l.strip()]
    exp_chars, punct_after = [], {}
    for p in phrases:
        last = -1
        for c in p:
            if c in "，。！？、：；——…":
                if last >= 0:
                    punct_after.setdefault(last, set()).add(c)
            else:
                exp_chars.append(c)
                last = len(exp_chars) - 1
    N = len(exp_chars)

    # ---------- whisper 原始 item：分段边界 = 模型停顿点 ----------
    # 实测（2026-09-24）：whisper item 首尾相接（gap≈0），但**边界本身**由时间戳
    # token 决定——模型在哪停顿就在哪分段。所以所有 item 边界都算"模型停顿点"。
    wj = json.load(open(heard_path, encoding="utf-8"))
    items = wj["transcription"]
    heard_total = sum(len(re.sub(r"\s+", "", it["text"])) for it in items)
    model_breaks = set()   # 边界idx（=此前累计字符数）
    cum = 0
    for it in items[:-1]:
        cum += len(re.sub(r"\s+", "", it["text"]))
        if 0 < cum < N:
            model_breaks.add(cum)
    if heard_total != N:
        print(f"⚠ whisper 字数({heard_total}) ≠ 文案字数({N})，停顿判定可能偏移，请人工复核")

    # ---------- 每字时间（拼音对齐） ----------
    heard = load_heard(heard_path)
    heard.sort(key=lambda h: h["s"])
    times = align(heard, exp_chars)
    t0_first, t_last = times[0][0], times[-1][1]

    # ---------- 判定 ----------
    silences = detect_silences(wav, db=db)
    silences = [(s, e) for (s, e) in silences if e > t0_first - 0.05 and s < t_last + 0.10]

    def ctx(b):
        return "".join(exp_chars[max(0, b - 4):b]) + "▂" + "".join(exp_chars[b:b + 4])

    comma_b = [b for b in range(N) if punct_after.get(b - 1, set()) & set("，、；")]
    to_fix = [b for b in comma_b if b not in model_breaks]
    misplaced_info = [b for b in sorted(model_breaks) if b not in comma_b
                      and not (punct_after.get(b - 1, set()) & set("。！？；：——"))]

    # ★ v3：漏停 + 后方词内误停并存 → 对齐不可信，禁止手术（会切错位置）
    bad = []
    for b in to_fix:
        ct = times[b - 1][1]
        if any(ct - 0.05 <= times[b2 - 1][1] <= ct + 0.7 for b2 in misplaced_info):
            bad.append(b)
    if bad:
        for b in bad:
            print(f"  ⚠ @{times[b-1][1]:.2f}s 「{ctx(b)}」 逗号漏停且后方有词内误停——对齐偏移风险，音频手术会切错位置")
        print("  ★ 定式：重合成该句（合成文本里把该逗号升级成句号，字幕文本不动），不做手术")
        return 4

    print(f"模型停顿(item边界) {len(model_breaks)} 处 / 静音区 {len(silences)} 处 / 逗号顿号 {len(comma_b)} 处")
    for b in misplaced_info:
        print(f"  ℹ 自然停顿(保留) @{times[b-1][1]:.2f}s 「{ctx(b)}」 无标点——数字/主语后韵律")
    for b in to_fix:
        print(f"  ✗ 漏停 @{times[b-1][1]:.2f}s 「{ctx(b)}」 逗号处模型连读")
    if not to_fix:
        print("  ✓ 全部逗号停顿到位")
        return 0
    if not fix_out:
        print("（只报告未修复；加 --fix <out.wav> 执行音频手术）")
        return 4

    # ---------- 音频手术 ----------
    a, sr = load_wav(wav)
    ops, consumed = [], set()
    last_end = -1.0
    for b in sorted(to_fix, key=lambda x: times[x - 1][1]):
        ct = times[b - 1][1]
        if ct - last_end < 0.25:      # 与上一处太近，避免连环补
            continue
        sil = next(((s, e) for (s, e) in silences
                    if (s, e) not in consumed and ct - 0.05 <= s <= ct + 0.45), None)
        # ↑ v3：起点必须 ≥ 逗号边界-0.05。逗号**前**的静音是边界前韵律（如「销量|摆着」），
        #   吞了它等于把停顿挪错位；真误置停顿在边界后方，由词内误停守卫拦下改走重合成。
        if sil:
            consumed.add(sil)
            # replace：把 [逗号处, 静音尾] 整段换成干净停顿（顺带吞掉停错位置的静音）
            ops.append(["replace", max(0.0, ct - 0.02), sil[1], insert_s])
            print(f"  ✚ @{ct:.2f}s 「{ctx(b)}」 吞并误置停顿 {sil[0]:.2f}-{sil[1]:.2f} → 干净停顿 {insert_s}s")
        else:
            ops.append(["insert", ct, None, insert_s])
            print(f"  ✚ @{ct:.2f}s 「{ctx(b)}」 插入 {insert_s}s 停顿")
        last_end = ct + insert_s

    nf = int(0.010 * sr)
    pieces, cur = [], 0
    for kind, t, t_end, dur in ops:
        i0 = int(t * sr)
        if kind == "replace":
            i1 = int(t_end * sr)      # 跳过 [t, t_end] 原音频，换成静音
            seg = a[cur:i0]           # ★只保留到切口，[i0,i1) 的误置停顿被删除
            if nf > 0 and len(seg) > nf:
                seg[-nf:] *= np.linspace(1, 0, nf, dtype=np.float32)  # 切口渐出防爆音
            pieces.append(seg)
            pieces.append(np.zeros(int(dur * sr), dtype=np.float32))
            cur = i1
        else:                          # insert：原音频 [cur, t] 保留 + 补静音
            pieces.append(a[cur:i0])
            pieces.append(np.zeros(int(dur * sr), dtype=np.float32))
            cur = i0
    pieces.append(a[cur:])
    out = np.concatenate(pieces)

    if os.path.abspath(fix_out) == os.path.abspath(wav):
        shutil.copy2(wav, wav + ".bak")
        print(f"已备份原音频 → {wav}.bak")
    save_wav(fix_out, out, sr)
    sil2 = detect_silences(fix_out, db=db)
    print(f"手术完成 → {fix_out}（{len(ops)} 处；停顿区 {len(silences)}→{len(sil2)}）")
    print("⚠ 必须重跑 whisper + align_vo 刷 tokens 后再渲染")
    return 3


if __name__ == "__main__":
    sys.exit(main())
