# -*- coding: utf-8 -*-
"""素材体检 + 抽静帧（零第三方依赖，只用 ffmpeg + 标准库）。

为什么需要它（2026-09-19 用户反馈「产品视频有点抖动，用图片替代」）：
  实拍产品视频是手持拍的，**晃动幅度常常超过观众容忍度**，而且静态产品本来就
  没有「必须动」的理由 —— 换成静帧反而更稳、更清晰、更好看（还 0 积分）。
  但「抖不抖」不能靠肉眼一遍遍看，所以这里用 ffmpeg 自带的工业级算法量化：
    · vidstabdetect —— 稳像仪使用的运动估计，输出每帧各区块的位移矢量
    · blurdetect    —— 逐帧模糊度（值越小越锐利）
  两个滤镜都是 ffmpeg 原生，**不需要 numpy/PIL**，系统 python3 直接能跑。

三个用法（都在 --win 窗口内分析，不给则全片）：

  1) 体检：这条素材抖不抖？能不能直接入镜？
     python3 pick_still.py 视频.mp4 --win 4.0 7.5 --shake

  2) 找最清晰的几帧（换静帧时挑帧用）
     python3 pick_still.py 视频.mp4 --win 4.0 7.5 --best 5

  3) 直接抽成可入镜的静帧（可顺手修非 16:9 的变形坑）
     python3 pick_still.py 视频.mp4 --win 4.0 7.5 --pick 3 --out 前缀 \
         [--crop16x9 top|center|bottom]

判定阈值（已换算到 1920 宽，单位像素；★2026-09-19 用实测样本标定）：
  稳   jit ≤ 1.0 且 mv ≤ 2.0   → 可直接当 prod 视频镜
  轻晃 jit ≤ 2.0 且 mv ≤ 4.0   → 能用，但优先考虑换静帧
  抖   其余                     → **别用**，换静帧（--pick）或换图库/AI 图
  标定样本：图库单杠引体 0.06/0.10（稳）、米当卡 28.MP4 0.73/1.12（稳）、
            米当卡 32.MP4 2.56/4.05（抖 —— 用户明确反馈"抖"的就是它）。
  ※ 筛查闸门、不是绝对判据：① 用户说抖就以用户为准，直接换静帧；
    ② 数值过关也要 Read 目视确认构图与清晰度。
  ※ 渲染器 prod 档会把素材**放大 1.65 倍**（scale=2304 → zoompan 裁到 1920），
    源素材每个像素的颤抖都会被放大（实测 32.MP4 过链后 mv 4.05→5.07、jit 2.56→3.81），
    所以阈值按**成片观感**定，不要按源素材分辨率定。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

FF = os.environ.get("FFMPEG") or "/Users/mac/.local/bin/ffmpeg"
FP = os.environ.get("FFPROBE") or "/Users/mac/.local/bin/ffprobe"
REF_W = 1920.0                      # 阈值按 1920 宽归一


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def probe(video):
    r = _run([FP, "-v", "error", "-show_entries", "format=duration",
              "-show_entries", "stream=width,height", "-of", "csv=p=0", video])
    w = h = 0
    dur = 0.0
    for line in (r.stdout or "").splitlines():
        parts = line.split(",")
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
            w, h = int(parts[0]), int(parts[1])
        elif len(parts) == 1 and parts[0].replace(".", "").isdigit():
            dur = float(parts[0])
    return w, h, dur


def shake_stats(video, t0, t1, shakiness=5, accuracy=9):
    """平均每帧全局位移(mv) 与高频抖动(jit)，已归一到 1920 宽。"""
    tmp = os.path.join(tempfile.mkdtemp(prefix="pickstill_"), "m.trf")
    cmd = [FF, "-v", "error"]
    if t0:
        cmd += ["-ss", f"{t0}"]
    if t1:
        cmd += ["-t", f"{max(0.1, t1 - t0)}"]
    cmd += ["-i", video,
            "-vf", f"vidstabdetect=result={tmp}:shakiness={shakiness}:"
                   f"accuracy={accuracy}:fileformat=ascii",
            "-f", "null", "-"]
    _run(cmd)
    if not os.path.exists(tmp):
        return None
    xs, ys = [], []

    def flush(c):
        if c and c["dx"]:
            xs.append(sum(c["dx"]) / len(c["dx"]))
            ys.append(sum(c["dy"]) / len(c["dy"]))

    cur = {}
    with open(tmp, encoding="utf-8", errors="replace") as f:
        for line in f:
            if re.match(r"Frame\s+\d+", line):
                flush(cur)
                # 注意：区块数据跟 "Frame N (List ..." 在**同一行**，不能 continue 掉
                cur = {"dx": [], "dy": []}
            if not cur:
                continue
            # (LM dx dy x y size contrast match) —— 按对比度加权，弱区块别左右判断
            for lm in re.findall(r"\(LM\s+(-?\d+)\s+(-?\d+)\s+\d+\s+\d+\s+\d+\s+"
                                 r"([\d.]+)", line):
                dx, dy, cw = int(lm[0]), int(lm[1]), float(lm[2])
                rep = max(1, int(round(cw * 4)))
                cur["dx"] += [dx] * rep
                cur["dy"] += [dy] * rep
    flush(cur)
    n = min(len(xs), len(ys))
    if n < 4:
        return {"n": n, "mv": 0.0, "jit": 0.0, "w": 0, "h": 0, "k": 1.0}
    w, h, _ = probe(video)
    k = REF_W / float(w or REF_W)
    d = [abs(xs[i] - xs[i - 1]) + abs(ys[i] - ys[i - 1]) for i in range(1, n)]
    mv = sum(d) / len(d) * 0.5 * k                     # 每帧平均位移（两轴均值）
    jit = (sum(abs(d[i] - d[i - 1]) for i in range(1, len(d))) /
           max(1, len(d) - 1)) * 0.5 * k               # 二阶差分 = 高频抖
    return {"n": n, "mv": mv, "jit": jit, "w": w, "h": h, "k": k}


def verdict(st):
    if not st or st["n"] < 4:
        return "未知", "区块太少（画面太空/太糊），建议直接 Read 目视判断"
    if st["jit"] <= 1.0 and st["mv"] <= 2.0:
        return "稳", "可直接当 prod 视频镜用"
    if st["jit"] <= 2.0 and st["mv"] <= 4.0:
        return "轻晃", "能用，但优先考虑换成静帧（--pick）"
    return "抖", "**不建议用**：抽静帧（--pick）/ 换图库 / AI 生成"


def blur_series(video, t0, t1, fps=10):
    """逐帧模糊度（越小越锐），返回 [(秒, 模糊值)]。"""
    cmd = [FF, "-v", "error"]
    if t0:
        cmd += ["-ss", f"{t0}"]
    if t1:
        cmd += ["-t", f"{max(0.1, t1 - t0)}"]
    cmd += ["-i", video, "-vf",
            f"fps={fps},blurdetect,metadata=mode=print:file=-",
            "-f", "null", "-"]
    r = _run(cmd)
    out, step = [], 1.0 / fps
    for i, line in enumerate(re.findall(r"lavfi\.blur=([\d.]+)", r.stdout or r.stderr)):
        out.append((t0 + i * step, float(line)))
    return out


def export(video, t, out, crop16x9=None, width=None):
    vf = []
    if crop16x9:
        w, h, _ = probe(video)
        ch = int(round(w * 9 / 16.0))
        ch -= ch % 2
        if ch <= h:
            y = {"top": 0, "center": max(0, (h - ch) // 2),
                 "bottom": max(0, h - ch)}[crop16x9]
            vf.append(f"crop={w}:{ch}:0:{y}")
        else:
            cw = int(round(h * 16 / 9.0))
            cw -= cw % 2
            vf.append(f"crop={cw}:{h}:{(w - cw) // 2}:0")
    if width:
        vf.append(f"scale={int(width)}:-1:flags=lanczos")
    cmd = [FF, "-v", "error", "-ss", f"{t:.3f}", "-i", video, "-frames:v", "1"]
    if vf:
        cmd += ["-vf", ",".join(vf)]
    cmd += ["-y", out]
    r = _run(cmd)
    return os.path.exists(out), (r.stderr or "").strip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--win", nargs=2, type=float, metavar=("START", "END"),
                    help="分析窗口（秒）。不给则全片")
    ap.add_argument("--shake", action="store_true", help="只做抖动体检")
    ap.add_argument("--best", type=int, default=0, help="列出最清晰的 N 帧（不改文件）")
    ap.add_argument("--pick", type=int, default=0, help="抽 N 张最清晰静帧")
    ap.add_argument("--out", default="", help="--pick 的输出前缀，如 /path/静帧")
    ap.add_argument("--crop16x9", choices=["top", "center", "bottom"],
                    help="抽帧时同时裁成 16:9（修非 16:9 素材的强制拉伸坑）")
    ap.add_argument("--width", type=int, default=0, help="抽帧时缩到指定宽（默认保持原尺寸）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    a = ap.parse_args()

    video = a.video
    if not os.path.exists(video):
        sys.exit(f"找不到文件：{video}")
    w, h, dur = probe(video)
    t0 = a.win[0] if a.win else 0.0
    t1 = a.win[1] if a.win else dur
    if dur and t0 > dur:
        sys.exit(f"--win 起点 {t0}s 超过片长 {dur:.2f}s")

    st = shake_stats(video, t0, t1)
    vd, why = verdict(st)
    frames = None
    if (a.best or a.pick) and not a.shake:
        frames = blur_series(video, t0, t1)
        frames.sort(key=lambda x: x[1])       # 模糊值越小越锐

    if a.json:
        print(json.dumps({"video": video, "size": [w, h], "dur": dur,
                          "win": [t0, t1], "shake": st, "verdict": vd,
                          "best": frames[:max(a.best, a.pick)] if frames else []},
                         ensure_ascii=False))
        return

    print(f"素材 {os.path.basename(video)}  {w}x{h}  {dur:.2f}s"
          + (f"  窗口 [{t0}-{t1}s]" if a.win else "  全片"))
    if st and st["n"] >= 4:
        print(f"  抖动体检：平均位移={st['mv']:.2f}px  高频抖动={st['jit']:.2f}px"
              f"  (区块 {st['n']}，已归一到 1920 宽) → 【{vd}】{why}")
    else:
        print(f"  抖动体检：无法判定（有效区块 {st['n'] if st else 0}）→ {why}")

    if frames:
        print(f"  最清晰 {min(a.best or a.pick, len(frames))} 帧"
              f"（模糊值越小越锐）：")
        for t, b in frames[:max(a.best, a.pick)]:
            print(f"    {t:7.2f}s  模糊={b:.2f}")
        if frames:
            print(f"  最糊：{frames[-1][0]:.2f}s 模糊={frames[-1][1]:.2f}")
    if a.pick:
        if not a.out:
            sys.exit("--pick 需要同时给 --out 前缀")
        for i, (t, b) in enumerate(frames[:a.pick], 1):
            dst = f"{a.out}_{i}.png"
            ok, err = export(video, t, dst, a.crop16x9, a.width)
            note = f"（{t:.2f}s 模糊={b:.2f}）" if ok else f"失败：{err}"
            print(f"  [抽帧{i}] {os.path.basename(dst)} {note}")
        print("  ※ 抽完请 Read 目视确认构图/清晰度，别只看数值")


if __name__ == "__main__":
    main()
