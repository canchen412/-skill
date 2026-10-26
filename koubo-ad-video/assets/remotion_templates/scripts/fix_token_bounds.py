#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用实测语音边界重建 token（修正「句内按字数线性分配」带来的尾部虚长）

★为什么需要（2026-10-07灿爹反馈「这两句话中间隔了太久」实测出来的）：
   镜9「这笔账算到这儿」token 记到 19.83s，但**音频实际 18.68s 就念完了**
   （尾音「儿」拖长后有 1.15s 静音）⇒ 后面所有内容都跟着晚 1.15s。
   根因：早先重建 token 时「段内按字数线性分配」把末尾静音也算进了句子里。

做法：以 phrases 为单位，句首/句末时间用**能量实测**（0.05s 窗口 RMS），
     句内再按字数线性分配到每个 token。
     ⚠️ 0.05s 窗口在本音频上可用（语音字间低谷 >0.05s）；更细会误判。
"""
import json, re, sys, wave
import numpy as np
import subprocess

FF = "/Users/mac/.local/bin/ffmpeg"
PUNCT = re.compile(r'[，。！？、：；—…·～「」『』《》""\'\'（）()\s]')
WIN = 0.05          # RMS 窗口（秒）
THR = 0.010         # 有声阈值
STEP = 0.02         # 搜索步长


def _pcs(t):
    """返回去掉标点的字符列表（PUNCT 是编译后的 Pattern，不能直接 in）"""
    return [c for c in t if not PUNCT.match(c)]


def rms_env(path, sr=16000):
    tmp = "/tmp/_tokalign.wav"
    subprocess.run([FF, "-y", "-v", "error", "-i", path,
                    "-ar", str(sr), "-ac", "1", tmp], check=True)
    w = wave.open(tmp)
    a = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    w.close()
    win = int(WIN * sr)
    return np.sqrt(np.convolve(a ** 2, np.ones(win) / win, mode="same")), len(a) / sr


def refine_bounds(vo, phrases_txt, old_token, out_token):
    env, total = rms_env(vo)
    sr = 16000

    def loud(t):
        i = int(t * sr)
        return 0.0 if i >= len(env) or i < 0 else float(env[i])

    def first_loud(t0, fwd=0.6):
        t = max(0.0, t0)
        while t < min(total, t0 + fwd):
            if loud(t) > THR:
                return t
            t += STEP
        return max(0.0, t0)

    def last_loud(t1, back=1.5):
        t = min(total, t1)
        while t > max(0.0, t1 - back):
            if loud(t) > THR:
                return t
            t -= STEP
        return min(total, t1)

    # 旧 token → 逐字时间
    old = json.load(open(old_token, encoding="utf-8"))
    chars = []
    for s in old["transcription"]:
        f = s["offsets"]["from"] / 1000.0
        t = s["offsets"]["to"] / 1000.0
        n = len(s["text"])
        for i, c in enumerate(s["text"]):
            chars.append((c, f + (t - f) * i / n, f + (t - f) * (i + 1) / n))

    phrases = [l.strip() for l in open(phrases_txt, encoding="utf-8") if l.strip()]

    bounds, ci = [], 0
    for p in phrases:
        n = len(_pcs(p))
        old_s = chars[ci][1]
        old_e = chars[ci + n - 1][2]
        s = first_loud(max(0.0, old_s - 0.35), fwd=0.5)
        e = last_loud(min(total, old_e + 0.15), back=1.5)
        if e <= s:
            e = old_e
        bounds.append((s, e, p))
        ci += n

    # 逐句 → token（句内按字数分配）
    tr = []
    for s, e, p in bounds:
        cs = _pcs(p)
        n = len(cs)
        j = 0
        while j < n:
            m = min(4, n - j)
            ts = s + (e - s) * j / n
            te = s + (e - s) * (j + m) / n
            tr.append({"timestamps": {"from": f"00:00:{ts:05.2f}", "to": f"00:00:{te:05.2f}"},
                       "offsets": {"from": int(ts * 1000), "to": int(te * 1000)},
                       "text": "".join(cs[j:j + m])})
            j += m

    json.dump({"systeminfo": "rms-boundary-aligned", "model": {},
               "params": {"win": WIN, "thr": THR, "total": round(total, 2)},
               "result": {"language": "zh"}, "transcription": tr},
              open(out_token, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("镜  起      止      时长    间隔    文案")
    for i, (s, e, p) in enumerate(bounds, 1):
        gap = bounds[i][0] - e if i < len(bounds) else 0.0
        flag = "  ⚠️ 间隔>1s" if gap > 1.0 else ""
        print(f"{i:2d} {s:6.2f} {e:6.2f} {e-s:6.2f}s {gap:6.2f}s{flag}  {p[:26]}")
    print(f"\n总字 {sum(len(_pcs(p)) for p in phrases)}  "
          f"末字 {bounds[-1][1]:.2f}s / 音频 {total:.2f}s")
    return bounds


if __name__ == "__main__":
    vo, phr, old, out = sys.argv[1:5]
    refine_bounds(vo, phr, old, out)
