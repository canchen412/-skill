# -*- coding: utf-8 -*-
"""配音韵律质检：把"实际停顿"精确映射到"第几个字之前"，判断断句有没有错位。

为什么需要它（2026-09-19 用户反馈"可瘦子连米饭都塞不下两碗，碳水从哪来"被念错后加）：
  whisper.cpp 的 `-ml 4` 输出是**4 字一组**的时间戳，用它定位停顿只能精确到 ±2 字
  —— 用户听出来的错位（"都这样·练" 停到了 "练·是" 之间、"
一勺顶一碗·米饭" 停在 "碗·米" 之间）
  在 4 字组诊断里全都模糊不清。本脚本改用**能量包络找断点 + 逐字时间戳定位**，
  能直接说出"停顿落在 X 和 Y 两个字之间"，并自动对照原文标点判对错。

用法（必须用装了 openai-whisper 的解释器，即 cosyvoice venv）：
    /Users/mac/.workbuddy/models/cosyvoice/venv/bin/python \
        scripts/qc_vo_pause.py <配音.mp3> <配音文本.txt>
    # 需要 ffmpeg 在 PATH 里（whisper 内部要调）
    export PATH=/Users/mac/.local/bin:$PATH

判据：
  · 低能量段 = 连续 ≥3 帧（20ms/帧）低于 `--db`（默认 -40dBFS）→ 一个"断点"
  · 每个断点用逐字时间轴映射到"第 N 字之前"
  · ≥0.28s 的长停顿若不落在标点/段末（±1 字容差）→ ⚠ 断句错位
  · 0.06~0.28s 的短断点若不落在标点 → ⚠ 碎停（把词切开，用户能听出来）
  · 退出码非 0 = 有可疑断点

⚠️ 这是**筛查清单，宁多勿漏**：判据只回答"这个停顿离最近的标点有多远"，
   汉语里正常的辅音阻塞/换气也会产生 0.06~0.10s 的浅断点，会被一并列出。
   必须结合"上下文"列目视判断（词被切开=真问题，句读处=正常）。
   已知假阳性：数字/百分比处（如"90%"）whisper 的字时间戳会被拉长，会误报。
   实测基准：旧版配音 30.6s 共 19 处断点，工具挑出 8 处可疑，其中**真问题 2 处全中**
   （"都这样·练"被念成"这样练·是"、"一勺顶一碗·米饭"），其余为轻微碎停/假阳性。
"""
import sys, os, re, json, argparse, subprocess, tempfile, bisect

HERE = os.path.dirname(os.path.abspath(__file__))
FF = os.environ.get("FFMPEG_BIN", "/Users/mac/.local/bin/ffmpeg")
PUNCT = "，。！？；：、,.!?;:"
DIGITS = set("0123456789%％")   # 落点附近含这些字符 → 时间戳易被 whisper 拉长，判为假阳性


def load_align_module():
    """复用 align_vo.py 的拼音强制对齐（whisper 听错字也能对上位置）"""
    import importlib.util
    p = os.path.join(HERE, "align_vo.py")
    spec = importlib.util.spec_from_file_location("align_vo", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def read_chars(text):
    """去标点取字流，并记录"每个字后面是否跟标点"（标点断点集合）"""
    chars, punct_after = [], set()
    for ch in text.replace("\r", "").replace("\n", ""):
        if ch in PUNCT or ch.isspace():
            if chars:
                punct_after.add(len(chars))
        else:
            chars.append(ch)
    return chars, punct_after


def dips_from_audio(wav, db_th=-40.0, win=0.02, min_frames=3):
    """读 16k 单声道 wav（用标准库 wave 带头解析，避免裸字节读导致整体错位）"""
    import wave
    import numpy as np
    with wave.open(wav, "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if x.size == 0:
        raise SystemExit(f"解码失败：{wav} 没有采样")
    print(f"音频 {x.size/sr:.2f}s @ {sr}Hz")
    n = int(win * sr)
    dbs = []
    for i in range(0, len(x) - n + 1, n):
        s = x[i:i + n]
        dbs.append((i / sr, 20 * float(np.log10(float(np.sqrt((s ** 2).mean())) + 1e-9))))
    dips, run = [], []
    for t, d in dbs:
        if d < db_th:
            run.append(t)
        else:
            if len(run) >= min_frames:
                dips.append((run[0], run[-1] + win))
            run = []
    if len(run) >= min_frames:
        dips.append((run[0], run[-1] + win))
    return dips


def heard_from_words(wav, model_name="small"):
    """openai-whisper 逐字（词）时间戳 → align_vo 需要的 heard 结构"""
    import whisper
    m = whisper.load_model(model_name)
    r = m.transcribe(wav, language="zh", word_timestamps=True, fp16=False, verbose=False)
    heard = []
    for seg in r["segments"]:
        for w in seg.get("words", []):
            t = w["word"].strip()
            if not t:
                continue
            a, b = w["start"], w["end"]
            n = len(t)
            for i, c in enumerate(t):
                heard.append({"c": c, "s": a + (b - a) * i / n, "e": a + (b - a) * (i + 1) / n})
    return heard


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("audio")
    ap.add_argument("text")
    ap.add_argument("--db", type=float, default=-40.0, help="低能量判据（dBFS），默认 -40")
    ap.add_argument("--model", default="small", help="whisper 模型，默认 small")
    ap.add_argument("--long", type=float, default=0.28, help="长停顿阈值秒，默认 0.28")
    args = ap.parse_args()

    text = open(args.text, encoding="utf-8").read()
    chars, punct_after = read_chars(text)
    av = load_align_module()

    wav = os.path.join(tempfile.mkdtemp(prefix="qcvo_"), "a.wav")
    # 16k 单声道 PCM wav：numpy 侧带头解析、whisper 侧直接读，两边共用同一份数据
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-i", args.audio,
                    "-ar", "16000", "-ac", "1", "-acodec", "pcm_s16le",
                    "-f", "wav", "-y", wav], check=True)

    heard = heard_from_words(wav, args.model)
    times = av.align(heard, chars)          # 每个字 (start, end)
    starts = [t[0] for t in times]

    dips = dips_from_audio(wav, db_th=args.db)

    # 标点断点的"应有停顿时刻" = 标点前那个字的结束时间（用逐字时间轴，抗 whisper 抖动）
    pun_times = [times[b - 1][1] for b in sorted(punct_after) if 0 < b <= len(chars)]
    dur_audio = len(open(wav, "rb").read())  # 仅用于忽略首尾，真正时长下面算
    import wave as _w
    with _w.open(wav, "rb") as _f:
        dur_audio = _f.getnframes() / _f.getframerate()

    print(f"字流 {len(chars)} 字；标点断点 {len(pun_times)} 处；"
          f"检出断点 {len(dips)} 处（<{args.db}dBFS 且 ≥0.06s）\n")
    print(f"{'断点':>16} {'时长':>6} {'距最近标点':>9}  上下文（｜=停顿处）        判定")
    bad_long, bad_short, digit_sus = [], [], []
    for a, b in dips:
        if a < 0.12 or b > dur_audio - 0.12:      # 首尾静音不算
            continue
        dist = min((abs(a - t) for t in pun_times), default=9.9)
        # 落点（仅供参考）：第一个"起点 ≥ 停顿起点"的字
        bi = max(0, min(len(chars), bisect.bisect_left(starts, a)))
        ctx = "".join(chars[max(0, bi - 7):bi]) + "｜" + "".join(chars[bi:bi + 7])
        dur = b - a
        ok = dist <= 0.18
        # ★2026-09-19 补：落点附近 ±3 字含数字/百分号时，whisper 的字时间戳会被明显拉长
        #   （"90%" 会把 9 和 0% 拆成两段），造成落点整体错位、误报"断句错位"。
        #   这类一律单列成"需目视"清单，不计入问题数，也不影响退出码。
        if (not ok) and any(c in DIGITS for c in chars[max(0, bi - 3):bi + 3]):
            verdict = "数字时间戳假阳性(需目视)"
            digit_sus.append((a, b, bi, dur))
        elif ok:
            verdict = "自然换气" if dur < args.long else "✓ 标点/段界"
        elif dur >= args.long:
            verdict = "⚠ 断句错位(长停顿)"
            bad_long.append((a, b, bi, dur))
        else:
            verdict = "⚠ 切开词" if dur >= 0.12 else "⚠ 微停"
            bad_short.append((a, b, bi, dur))
        print(f"{a:6.2f}-{b:6.2f}s {dur:5.2f}s {dist:8.2f}s  {ctx:<24}  {verdict}")

    print()
    for a, b, bi, _d in bad_long:
        print(f"⚠ 断句错位 {a:.2f}-{b:.2f}s（{b-a:.2f}s）：停顿落在 "
              f"…{''.join(chars[max(0,bi-8):bi])}｜{''.join(chars[bi:bi+8])}… 之间")
    for a, b, bi, _d in bad_short:
        print(f"⚠ 碎停     {a:.2f}-{b:.2f}s（{b-a:.2f}s）：停顿落在 "
              f"…{''.join(chars[max(0,bi-8):bi])}｜{''.join(chars[bi:bi+8])}… 之间")
    for a, b, bi, _d in digit_sus:
        print(f"·  数字附近 {a:.2f}-{b:.2f}s（{b-a:.2f}s）：落点 "
              f"…{''.join(chars[max(0,bi-8):bi])}｜{''.join(chars[bi:bi+8])}… "
              f"→ 工具假阳性，请**听一遍**确认")
    n = len(bad_long) + len(bad_short)
    print(f"\n结论：{'通过（无错位）' if n == 0 else f'发现 {n} 处问题'}"
          f"{f'（另有 {len(digit_sus)} 处数字附近需目视/试听）' if digit_sus else ''}")
    sys.exit(0 if n == 0 else 1)


if __name__ == "__main__":
    main()
