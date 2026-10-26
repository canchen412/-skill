#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
safe_frame.py —— 分镜图「安全框」检测与自动修正（0 积分）

背景（2026-09-20 用户反馈：生成图主体靠下，成片里一半被裁 / 被底部字幕挡住）：
  源图 3:2 → 成片 16:9 时渲染器 cover **顶部对齐裁掉底部 15.6%**，
  底部字幕区(两行)又约占 19.8%，zoompan 推近再吃 ~4%
  ⇒ 真正安全区 ≈ 原图顶部 0% ~ 64%。主体越过这条线就会出问题。

check: 逐图检测「内容(主体)纵向范围」，输出是否高危
fix  : 把图整体等比缩小并顶部对齐、底部用同图模糊补边，让主体落回安全区

用法:
  python3 safe_frame.py --check <文件或目录> [--topn 20]
  python3 safe_frame.py --fix <文件...> [--safe 0.62] [--backup-dir 原图备份]
"""
import argparse, os, sys, subprocess, json
from PIL import Image

FF = "/Users/mac/.local/bin/ffmpeg"
SAFE_DEFAULT = 0.64   # 安全底线：内容底部归一化位置不超过它


def content_rows(path, k=0.55):
    """返回 (top, bottom) 归一化「显著内容」纵向范围。

    算法（2026-09-20 实测）：灰度缩放 256 宽 → 逐行横向梯度均值(局部对比度)
    → 移动平均平滑 → 阈值取峰值 * k。背景/平滑地面梯度低会被排除，
    主体(角色、产品、道具)梯度高会被圈进来。
    """
    import numpy as np
    im = Image.open(path).convert("L")
    W, H = im.size
    w = 256
    h = max(8, int(H * w / W))
    a = np.asarray(im.resize((w, h)), dtype=float)
    grad = np.abs(np.diff(a, axis=1)).mean(axis=1)
    # 移动平均，避免孤立噪点行
    win = max(3, h // 40)
    ker = np.ones(win) / win
    sm = np.convolve(np.r_[grad, grad[-1] * np.ones(win - 1)], ker, mode="valid")[: h]
    thr = sm.max() * k
    rows = np.where(sm > thr)[0]
    if len(rows) == 0:
        return 0.0, 1.0
    return float(rows.min()) / h, float(rows.max() + 1) / h


def judge(bottom, safe=SAFE_DEFAULT):
    """判定：safe 内安全；超过 0.844 会被裁掉(严重)"""
    if bottom > 0.844:
        return "严重·会被裁掉"
    if bottom > safe:
        return "高危·会被字幕挡"
    return "OK"


def cmd_check(args):
    targets = []
    if os.path.isdir(args.path):
        for f in sorted(os.listdir(args.path)):
            if f.startswith(".") or f.startswith("_"):
                continue
            if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                targets.append(os.path.join(args.path, f))
    else:
        targets = [args.path]
    rows = []
    for p in targets:
        try:
            t, b = content_rows(p)
        except Exception as e:
            print("跳过(无法读取):", p, e)
            continue
        rows.append((os.path.basename(p), t, b, judge(b, args.safe)))
    rows.sort(key=lambda r: -r[2])
    bad = [r for r in rows if r[3] != "OK"]
    print(f"共检测 {len(rows)} 张 | 安全 {len(rows)-len(bad)} 张 | 需要处理 {len(bad)} 张")
    print(f"{'文件':<44}{'顶':>6}{'底':>7}  判定")
    for r in rows[: args.topn]:
        print(f"{r[0][:44]:<44}{r[1]:>6.2f}{r[2]:>7.2f}  {r[3]}")
    if args.json:
        json.dump([{"file": r[0], "top": r[1], "bottom": r[2], "verdict": r[3]} for r in rows],
                  open(args.json, "w"), ensure_ascii=False, indent=1)
        print("详情 ->", args.json)


def cmd_fix(args):
    for path in args.files:
        if not os.path.exists(path):
            print("不存在:", path); continue
        t, b = content_rows(path)
        if b <= args.safe:
            print(f"[跳过·已安全] {os.path.basename(path)} 底部={b:.2f}")
            continue
        W, H = Image.open(path).size
        # 顶部对齐 overlay(y=0) ⇒ 新底部 = b * s；要 b*s ≤ safe ⇒ s = safe / b
        s = min(1.0, args.safe / b)
        nw, nh = int(round(W * s)), int(round(H * s))
        if args.backup_dir:
            os.makedirs(args.backup_dir, exist_ok=True)
            import shutil
            shutil.copy2(path, os.path.join(args.backup_dir, os.path.basename(path)))
        tmp = path + ".fix.png"
        vf = (f"split=2[a][b];[a]scale={nw}:{nh}[fg];"
              f"[b]scale={W}:{H},gblur=sigma=45[bg];[bg][fg]overlay=0:0")
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", path,
                        "-filter_complex", vf, tmp], check=True)
        os.replace(tmp, path)
        t2, b2 = content_rows(path)
        print(f"[已修正] {os.path.basename(path)}  底部 {b:.2f} → {b2:.2f}  (缩放 {s:.2f})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--fix", action="store_true")
    ap.add_argument("path", nargs="?", help="--check 用：文件或目录")
    ap.add_argument("--files", nargs="*", default=[], help="--fix 用：文件列表")
    ap.add_argument("--safe", type=float, default=SAFE_DEFAULT)
    ap.add_argument("--topn", type=int, default=25)
    ap.add_argument("--json", default=None)
    ap.add_argument("--backup-dir", default=None, help="--fix 前把原图备份到该目录")
    a = ap.parse_args()
    if a.check and a.path:
        cmd_check(a)
    elif a.fix and a.files:
        cmd_fix(a)
    else:
        ap.print_help()
