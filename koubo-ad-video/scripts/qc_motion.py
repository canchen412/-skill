# -*- coding: utf-8 -*-
"""逐镜动效质检：同一镜内多点采样，取相邻采样帧的最大差异，≈0% 说明该镜取景被夹死（动效失效）。

用法:
  python3 qc_motion.py <成片.mp4> <config.json> [--thresh 12]

判定（帧间内容残差 MAE，已剔除字幕区）:
  普通镜: 夹死 <3 | 偏弱 3-8 | 正常 >8
  抠图分层镜（有 fg_motion）: 夹死 <1.8 | 偏弱 1.8-4 | 正常 >4
    （动效集中在 fg 层、bg 是软化模糊图，全镜残差天然小于普通镜）
  —— 不要用"|Δ|>28 的像素占比"，它会被字幕逐字变化和压缩噪声灌到 9x%，完全掩盖夹死问题。
  —— 采样为镜内 10% / 32% / 56% / 80% 四点，相邻两两求 MAE 取最大值。
     （★2026-09-24 由原"两点 30%/72%"改为"四点取最大"：两点采样会漏掉局部/一次性运动——
      开场冲击环只在镜内一段时间扩散，两点恰好避让 ⇒ 有运动的镜被误判"夹死"
      （实测残差 2.7，而该镜真实相邻帧 MAE 18.8~22.8）。首点压到 10% 是为采到
      popin 弹入——它只占镜首约 0.6s，镜长 2.6s 时约占 23%，首点取 18% 会刚好错过。）
"""
import sys, os, json, subprocess, tempfile, re
import numpy as np
from PIL import Image

FF = "/Users/mac/.local/bin/ffmpeg"


def pchars(t):
    return [c for c in t if c not in "，。！？、：；—…·～「」『』《》\"\"''（）()" and not c.isspace()]


def timeline_of(cfg):
    w = json.load(open(cfg["tokens"], encoding="utf-8"))
    chars = []
    for seg in w["transcription"]:
        s, e = seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000
        txt = seg["text"].strip()
        n = len(txt)
        for i, c in enumerate(txt):
            chars.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n, c))
    tl, ci = [], 0
    for p in cfg["phrases"]:
        need = len(pchars(p["text"]))
        tl.append((p["text"][:14], chars[ci][0], chars[ci + need - 1][1]))
        ci += need
    total = tl[-1][2] + cfg.get("tail", 1.1)
    return [(i, t, s, (tl[i + 1][1] if i + 1 < len(tl) else total))
            for i, (t, s, e) in enumerate(tl)]


def main():
    video, cfgp = sys.argv[1], sys.argv[2]
    thresh = float(sys.argv[sys.argv.index("--thresh") + 1]) if "--thresh" in sys.argv else 12.0
    cfg = json.load(open(cfgp, encoding="utf-8"))
    fps = int(cfg.get("fps", 30))
    tl = timeline_of(cfg)

    tmp = tempfile.mkdtemp()
    print(f"{'镜':>3} {'区间':>16} {'残差':>6} {'像素差':>7}  判定        动效      素材")
    bad = []
    for i, text, s, ce in tl:
        dur = ce - s
        # ★2026-09-24 改多点采样：原来只在镜内 30% / 72% 取两点相减，存在**采样盲区**——
        #   局部或周期性的运动（最典型的是开场冲击环：只在镜内约 0.7~1.2s 扩散一次）
        #   可能恰好落在两点之外，于是两个采样点都是"干净帧"⇒ 误判"夹死/静止"。
        #   实测：条2 镜1（沙漠剪影，区间 0.00-2.26s）两点取 0.678 / 1.627s，恰好都避开了
        #   0.90s 的冲击环爆发 ⇒ 报残差 2.7（夹死），而镜内真实相邻帧 MAE 高达 18~22。
        #   现改为镜内 4 点（10% / 32% / 56% / 80%）两两相邻求 MAE，**取最大值**为镜运动量。
        #   首点特意压到 10%（不是 18%）：抠图分层镜的 `popin` 弹入只占镜首约 0.6s，
        #   镜长 2.6s 时 0.6s ≈ 23%，首点取 18% 刚好落在弹入结束之后 ⇒ 误判夹死（实测 2.8）。
        #   取点仍不贴首尾（成片时间轴有零点几秒偏移，贴边会踩到相邻镜）。
        fr = [s + dur * k for k in (0.10, 0.32, 0.56, 0.80)]
        paths = [os.path.join(tmp, f"m{i}_{j}.png") for j in range(len(fr))]
        try:
            for t, p in zip(fr, paths):
                subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", video,
                                "-frames:v", "1", p], check=True)
        except subprocess.CalledProcessError:
            print(f"{i+1:3d} {s:6.2f}-{ce:6.2f}  抽帧失败")
            continue
        ims = []
        for p in paths:
            arr = np.array(Image.open(p).convert("L")).astype(float)
            ims.append(arr[: arr.shape[0] - 170, :])   # 剔除底部字幕区（逐字变化会污染指标）
        maes = [np.abs(ims[j + 1] - ims[j]).mean() for j in range(len(ims) - 1)]
        mae = max(maes)
        _k = maes.index(mae)
        pct = 100 * (np.abs(ims[_k + 1] - ims[_k]) > 28).mean()
        ph = cfg["phrases"][i]
        s_ = ph.get("src")
        if not isinstance(s_, str):              # prod 段 src 是 [起,止] 区间
            s_ = ph.get("video") or ph.get("fg") or ""
        motion = ph.get("fg_motion") or ph.get("motion") or ""
        # 抠图分层镜（有 fg_motion）动效集中在 fg 层、且 bg 是软化模糊图（缩放几乎无像素变化），
        # 全镜残差天然小于普通镜 ⇒ **单列阈值**。实测正常区 2.8~17（fg 呼吸 + bg 缓动），
        # 真失效时只剩压缩噪声 ≈0.5。按普通镜 3.0 判会把正常分层镜误报成"夹死"。
        thr_dead, thr_weak = (1.8, 4.0) if motion else (3.0, 8.0)
        if mae < thr_dead:
            judge = "★夹死/静止"; bad.append(i + 1)
        elif mae < thr_weak:
            judge = "偏弱"
        else:
            judge = "正常"
        print(f"{i+1:3d} {s:6.2f}-{ce:6.2f} {mae:6.1f} {pct:6.1f}%  {judge:12s} {motion:9s} {os.path.basename(s_)}")
    print("\n异常镜:", bad if bad else "无")


if __name__ == "__main__":
    main()
