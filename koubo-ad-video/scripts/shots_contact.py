#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐镜中点抽帧验收表：按 config 的 tokens 算出每镜时间中点，抽帧拼图（带镜号+文本）。

用途：成片交付前的最后一道「画面-字幕-语义」目视把关（程序化 QC 查不出语义错配）。
用法: python shots_contact.py --config s1/config.json --video render/xxx.mp4 --out qc/s1_逐镜.jpg [--cols 4]
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import argparse, json, os, subprocess
import numpy as np
from PIL import Image, ImageDraw

FF = os.environ.get("FFMPEG") or "/Users/mac/.local/bin/ffmpeg"
PUNC = set("，。！？、：；—…·「」『』（）()\"'“”‘’ ")


def pchars(s):
    return [c for c in s if c not in PUNC]


def timeline(cfg):
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
        tl.append({"text": p["text"], "start": chars[ci][0], "end": chars[ci + need - 1][1]})
        ci += need
    TOTAL = tl[-1]["end"] + cfg.get("tail", 0.9)
    for i in range(len(tl)):
        tl[i]["cut_end"] = tl[i + 1]["start"] if i + 1 < len(tl) else TOTAL
    return tl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--cols", type=int, default=4)
    ap.add_argument("--tw", type=int, default=470)
    a = ap.parse_args()
    cfg = json.load(open(a.config, encoding="utf-8"))
    tl = timeline(cfg)
    TH = int(a.tw * 9 / 16)
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    tiles = []
    for i, t in enumerate(tl):
        mid = (t["start"] + t["cut_end"]) / 2
        p = f"/tmp/sc_{i:02d}.jpg"
        subprocess.run([FF, "-y", "-loglevel", "error", "-ss", f"{mid:.3f}",
                        "-i", a.video, "-frames:v", "1",
                        "-vf", f"scale={a.tw}:{TH}", p], capture_output=True)
        if os.path.exists(p):
            tiles.append((i + 1, mid, t["text"], p))
    cols = a.cols
    rows = (len(tiles) + cols - 1) // cols
    LH = 18
    sh = Image.new("RGB", (cols * (a.tw + 6) + 6, rows * (TH + LH + 6) + 6), (255, 255, 255))
    d = ImageDraw.Draw(sh)
    for k, (no, mid, txt, p) in enumerate(tiles):
        r, c = divmod(k, cols)
        x = c * (a.tw + 6) + 3
        y = r * (TH + LH + 6) + 3
        sh.paste(Image.open(p).convert("RGB"), (x, y))
        d.text((x + 2, y + TH + 2), f"#{no} @{mid:.1f}s {txt[:22]}", fill=(0, 0, 0))
    sh.save(a.out, quality=90)
    print(f"ok {sh.size} -> {a.out}  （{len(tiles)} 镜）")


if __name__ == "__main__":
    main()
