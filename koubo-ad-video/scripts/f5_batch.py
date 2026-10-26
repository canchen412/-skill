#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★F5-TTS 批量合成器（2026-10-09）—— 一次加载模型，全句循环合成
─────────────────────────────────────────────────────────────────
解决：每句单独调 CLI 会重复加载模型（每次 10-15s）。
内置：数字归一化 / speed 分级（钩子 1.35）/ polish 裁幽灵 / 句间拼接。
输出：vo.wav（整条配音）+ durations.json（每句全局起止——直接喂 token 生成）。

用法:
  python3 f5_batch.py --jobs f5_jobs.json
f5_jobs.json:
{
  "ref_audio": "/tmp/ref_24k.wav",
  "ref_text":  "参考音频的转录（与音频严格对应）",
  "gap": 0.22,
  "out": "vo.wav",
  "durations": "durations.json",
  "sentences": [
    {"text": "练了两年才发现，健身到最后拼的其实是建模。", "speed": 1.35},
    {"text": "我自己就是现成的例子，一周六练从没断过。", "speed": 1.2}
  ]
}
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

import mlx.core as mx
import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "f5_gen", "/Users/mac/.workbuddy/models/mlx-audio/venv/lib/python3.13/site-packages/f5_tts_mlx/generate.py")
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)
F5TTS = gen.F5TTS
SAMPLE_RATE = gen.SAMPLE_RATE
POLISH = os.path.join(HERE, "polish_f5.py")
# ★多音字自动校正（2026-10-13）：同音字替换 + pypinyin 短语读音覆盖
sys.path.insert(0, HERE)
from polyphone_fix import apply_poly, register_phrase_overrides, merged_map

# ★文本归一化 v2（2026-10-09 通用化，替换原肌酸专用硬编码表）
# 背景：F5 对「300mg / 98% / SGS / VORSE」这类裸数字字母必乱读（实测 whisper 实锤：
#       「单粒300mg」→ 一串乱语、「VORSE」→ V2CE、「今天咱们讲，姜黄素」整段漏读）。
# 规则：config norm_map（品牌词，优先级最高）→ 百分比 → 数字+单位 → 剩余数字 → 字母谐音。
CN_D = "零一二三四五六七八九"
CN_U = ["", "十", "百", "千"]
CN_BIG = ["", "万", "亿", "兆"]
LETTER_CN = {"A": "诶", "B": "必", "C": "西", "D": "迪", "E": "伊", "F": "艾弗",
             "G": "吉", "H": "诶曲", "I": "艾", "J": "杰", "K": "凯", "L": "艾欧",
             "M": "艾姆", "N": "恩", "O": "欧", "P": "皮", "Q": "扣", "R": "阿尔",
             "S": "艾斯", "T": "替", "U": "优", "V": "维", "W": "达布留",
             "X": "艾克斯", "Y": "歪", "Z": "贼"}
UNIT_CN = {"mg": "毫克", "μg": "微克", "ug": "微克", "mcg": "微克", "g": "克",
           "kg": "千克", "ml": "毫升", "l": "升", "kcal": "大卡",
           "km": "公里", "cm": "厘米", "mm": "毫米"}


def _int_cn(n):
    """整数 → 中文读法（10-19 读「十X」不读「一十X」，符合口播习惯）"""
    if n == 0:
        return "零"
    if n >= 100000000:
        hi, lo = divmod(n, 100000000)
        return _int_cn(hi) + "亿" + ("" if lo == 0 else
                                     ("零" + _int_cn(lo) if lo < 10000000 else _int_cn(lo)))
    if n >= 10000:
        hi, lo = divmod(n, 10000)
        return _int_cn(hi) + "万" + ("" if lo == 0 else
                                     ("零" + _int_cn(lo) if lo < 1000 else _int_cn(lo)))
    seg, zero = "", False
    for pos in (3, 2, 1, 0):
        v = (n // 10 ** pos) % 10
        if v == 0:
            zero = seg != ""
        else:
            if zero:
                seg += "零"
                zero = False
            if v == 1 and pos == 1 and not seg:
                seg += "十"                          # 10-19：十X
            else:
                seg += CN_D[v] + CN_U[pos]
    return seg


def _dec_cn(s):
    """数字字符串（可含小数点）→ 中文读法"""
    if "." in s:
        a, b = s.split(".")
        return _int_cn(int(a)) + "点" + "".join(CN_D[int(c)] for c in b)
    return _int_cn(int(s))


def norm(t, norm_map=None):
    """★喂给 F5 前的最后一步：把数字/字母全部转成中文可读词（防乱读+漏读）"""
    if norm_map:
        for k in sorted(norm_map, key=len, reverse=True):   # 长词优先
            t = t.replace(k, norm_map[k])
    # 1) 百分比：98% → 百分之九十八
    t = re.sub(r"(\d+(?:\.\d+)?)\s*%", lambda m: "百分之" + _dec_cn(m.group(1)), t)
    # 2) 数字+单位：300mg → 三百毫克（单位大小写不敏感）
    #    ★结尾用 (?![A-Za-z]) 而非 \b：中文汉字也是 \w，「10mg黑胡椒」的 g|黑
    #      之间不构成词边界，\b 会漏配（实测踩坑）。
    def _u(m):
        return _dec_cn(m.group(1)) + UNIT_CN.get(m.group(2).lower(), m.group(2))
    t = re.sub(r"(\d+(?:\.\d+)?)\s*(mg|μg|ug|mcg|kg|kcal|km|cm|mm|ml|g|l)(?![A-Za-z])",
               _u, t, flags=re.IGNORECASE)
    # 3) 剩余数字：4瓶 → 四瓶
    t = re.sub(r"\d+(?:\.\d+)?", lambda m: _dec_cn(m.group(0)), t)
    # 4) 剩余连续拉丁字母串（≥2）→ 逐字母谐音（SGS → 艾斯吉艾斯）
    t = re.sub(r"[A-Za-z]{2,}", lambda m: "".join(LETTER_CN.get(c.upper(), c) for c in m.group(0)), t)
    return t

WSP = "/Users/mac/.workbuddy/models/whisper.cpp"
WHISPER = f"{WSP}/build/bin/whisper-cli"
WHISPER_MODEL = f"{WSP}/models/ggml-base.bin"


def _py(s):
    """汉字 → 无调拼音列表。

    ★为什么走拼音而不是字面：ggml-base 中文输出是**繁体**，且常见错字
      （「碳蛋脸」→「淡臉」、「建模」→「節目」）。字面比对必误报；
      但繁体:練/简体:练、错字:碳→淡 在拼音空间是**同/异**关系清晰的：
      简体字与其繁体字拼音相同（不误报），被吞的字压根没有对应音节（必报）。
    """
    try:
        from pypinyin import lazy_pinyin, Style
        return lazy_pinyin("".join(re.findall(r"[\u4e00-\u9fff]", s)), style=Style.NORMAL)
    except Exception:
        return list(re.findall(r"[\u4e00-\u9fff]", s))


def missing_words(wav, words):
    """听一遍 wav，返回「很可能被 F5 吞掉」的必读词。

    判据：词的拼音序列是否作为**子序列**出现在 ASR 拼音里。
      · 允许 ASR 错字/繁体（同音字拼音相同）→ 不误报；
      · 真漏读时该音节压根不存在 → 精准命中。
    """
    if not words:
        return []
    try:
        subprocess.run([WHISPER, "-m", WHISPER_MODEL, "-f", wav, "-l", "zh",
                        "--no-timestamps", "--output-txt", "-of", "/tmp/_f5_asr"],
                       capture_output=True, timeout=90)
        raw = open("/tmp/_f5_asr.txt", encoding="utf-8", errors="ignore").read()
    except Exception as e:
        print(f"      (ASR 不可用，跳过校验: {e})", flush=True)
        return []
    got = _py(raw)
    if not got:
        return []
    out = []
    for w in words:
        t = _py(w)
        if not t:
            continue
        i = 0
        for tok in got:                       # 顺序子序列匹配
            if i < len(t) and tok == t[i]:
                i += 1
        if i < len(t):
            out.append(w)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    a = ap.parse_args()
    J = json.load(open(a.jobs, encoding="utf-8"))

    ref_audio, ref_text = J["ref_audio"], J["ref_text"]
    norm_map = J.get("norm_map") or {}
    # ★2026-10-14：必读词（重点字/数字/产品名）。F5 在长句尾段偶发吞字，
    #   灿爹报过「今天我们讲，碳蛋脸」→「讲蛋脸」。合成后用 ASR 复判，缺字重来。
    must_words = [w for w in (J.get("must_words") or []) if len(w) >= 2]
    if must_words:
        print(f"[f5b] 必读词校验: {'、'.join(must_words)}", flush=True)
    # ★多音字校正表：内置基础表 + jobs 里的 poly_map（pipeline 已合并项目 poly.json）
    poly_map = merged_map(J.get("poly_map") or {})
    register_phrase_overrides(poly_map)
    # 每句先做多音字替换再归一化（替换只改 TTS 输入，时间轴 tokens 用原文）
    texts = [apply_poly(item["text"], poly_map) for item in J["sentences"]]
    gap = float(J.get("gap", 0.22))
    out_wav, out_json = J["out"], J.get("durations", "durations.json")

    t0 = time.time()
    print(f"[f5b] 模型加载…", flush=True)
    f5tts = F5TTS.from_pretrained("lucasnewman/f5-tts-mlx")
    print(f"[f5b] 模型加载完成 {time.time()-t0:.0f}s", flush=True)

    audio, sr = sf.read(ref_audio)
    if sr != SAMPLE_RATE:
        raise ValueError(f"参考音频须 24kHz，当前 {sr}")
    audio = mx.array(audio)
    rms = mx.sqrt(mx.mean(mx.square(audio)))
    if rms < gen.TARGET_RMS:
        audio = audio * gen.TARGET_RMS / rms

    # 句级合成（模型只此一份）
    durs = []          # 每句实际时长
    t1 = time.time()
    for i, item in enumerate(J["sentences"], 1):
        text = norm(texts[i - 1], norm_map)
        speed = float(item.get("speed", 1.2))
        raw = f"/tmp/_f5b_{i:02d}.wav"
        text_inp = gen.convert_char_to_pinyin([ref_text + " " + text])
        # ★缺字重试：同一句最多跑 3 次，逐次加推理步数/降 CFG 打破采样路径。
        #   只对**出现在本句里的必读词**做校验，不整句比对（base ASR 错字多，
        #   整句比对必然误报 → 白白重合成几十次）。
        checks = [w for w in must_words if all(c in text for c in w)]
        plan = [(16, 2.0, speed), (24, 1.8, speed * 0.97), (32, 1.5, speed)]
        for attempt, (steps, cfg, spd) in enumerate(plan, 1):
            dur_frames = int(gen.estimated_duration(audio, ref_text, text, spd) * gen.FRAMES_PER_SEC)
            wave, _ = f5tts.sample(
                mx.expand_dims(audio, axis=0), text=text_inp, duration=dur_frames,
                steps=steps, method="rk4", speed=spd, cfg_strength=cfg,
                sway_sampling_coef=-1.0)
            wave = wave[audio.shape[0]:]
            mx.eval(wave)
            sf.write(raw, np.array(wave), SAMPLE_RATE)
            miss = missing_words(raw, checks) if checks else []
            if not miss:
                if attempt > 1:
                    print(f"    第{attempt}次合成通过 ✓", flush=True)
                break
            print(f"    ⚠️ 句{i:02d} 第{attempt}次缺「{'、'.join(miss)}」→ 重合成", flush=True)
        durs.append(len(wave) / SAMPLE_RATE)
        print(f"  句{i:02d} {len(wave)/SAMPLE_RATE:.2f}s  {text[:16]}", flush=True)
    print(f"[f5b] 全句合成 {time.time()-t1:.0f}s（含加载共 {time.time()-t0:.0f}s）", flush=True)

    # polish 裁首字幽灵 + 尾部（逐句；whisper 一次性进程，代价可接受）
    # ★head 用「多音字替换后」的发音文本取前6字——whisper 转录的是实际发音，
    #   用原句（囤）做前缀永远匹配不上 → 幽灵字裁不掉 → 成片漏读感。
    for i in range(1, len(J["sentences"]) + 1):
        head = re.sub(r"[，。、！？：；\s]", "", texts[i - 1])[:6]
        subprocess.run([sys.executable, POLISH, f"/tmp/_f5b_{i:02d}.wav",
                        f"/tmp/_f5b_{i:02d}_p.wav", head], check=True,
                       capture_output=True)

    # 拼接 + 段界
    chunks, timeline, pos = [], [], 0.0
    for i in range(1, len(J["sentences"]) + 1):
        with __import__("wave").open(f"/tmp/_f5b_{i:02d}_p.wav", "rb") as w:
            sr = w.getframerate()
            a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
        d = len(a) / sr
        timeline.append({"i": i, "text": J["sentences"][i-1]["text"],
                         "start": round(pos, 3), "end": round(pos + d, 3)})
        chunks.append(a)
        pos += d
        if i < len(J["sentences"]):
            g = np.zeros(int(gap * sr), dtype="<i2")
            chunks.append(g); pos += gap
    full = np.concatenate(chunks)
    import wave as wv
    with wv.open(out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
        w.writeframes(full)
    json.dump({"total": round(pos, 3), "sentences": timeline},
              open(out_json, "w"), ensure_ascii=False, indent=1)
    print(f"[f5b] ✓ {out_wav} 全长 {pos:.1f}s + {out_json}", flush=True)

if __name__ == "__main__":
    main()
