#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
harvest_library_video.py — 分镜图库「新视频素材收割」工具（2026-09-19 用户要求）

用户会经常往分镜图库丢 AI 生成的原始视频（多个片段拼在一起，有的协调有的不协调）。
流程：--check 检测未整理的新视频 →（AI 目视抽帧定切分点）→ --cut 剪出可用片段
按三段式命名入库 → 台账登记（原片保留不动）。

用法：
  --check                                  列出图库里尚未整理的视频（含时长/分辨率）
  --cut SRC --parts "名称:起:止,名称2:起:止" [--frames "名称:时刻,名称2:时刻"]
      剪片段（去音轨、crf17 保留原分辨率）+ 抽静帧，全部登记台账
  --forget SRC                             从台账移除（重新整理用）

切分点判断必须由 AI 目视完成（ffmpeg scene detect 对 AI 生成的一镜到底无效），
本脚本只负责机械剪切与登记，防止重复劳动。
"""
import argparse
import json
import os
import subprocess
import sys
import time

FFMPEG = os.environ.get("FFMPEG_BIN") or "/Users/mac/.local/bin/ffmpeg"
FFPROBE = os.environ.get("FFPROBE_BIN") or "/Users/mac/.local/bin/ffprobe"
# 分镜图库根目录：环境变量 AD_SHOT_LIB 或 --lib 覆盖（命令行优先级最高）
DEFAULT_LIB = os.environ.get("AD_SHOT_LIB") or "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"
LEDGER_NAME = "_已整理视频.json"
VIDEO_EXT = (".mp4", ".mov", ".m4v", ".webm", ".avi", ".MP4", ".MOV")
SKIP_PREFIX = ("._", "视频_", "开场_")  # 已按规范命名的 = 已整理过
MIN_SEG = 1.0      # 剪出的片段至少 1 秒
CRF = "17"


def ffprobe_info(path):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,r_frame_rate:format=duration",
             "-of", "json", path],
            capture_output=True, text=True, timeout=30).stdout
        d = json.loads(out)
        st = d["streams"][0]
        dur = float(d["format"].get("duration") or 0)
        return {"w": st.get("width"), "h": st.get("height"), "dur": round(dur, 2)}
    except Exception as e:
        return {"error": str(e)}


def ledger_path(lib):
    return os.path.join(lib, LEDGER_NAME)


def load_ledger(lib):
    p = ledger_path(lib)
    if os.path.exists(p):
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return {"harvested": {}}


def save_ledger(lib, led):
    led["updated"] = time.strftime("%Y-%m-%d %H:%M")
    json.dump(led, open(ledger_path(lib), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)


def find_raw_videos(lib):
    """未整理的原始视频：以 SKIP_PREFIX 之外的命名的视频文件（台账里也没有）。"""
    led = load_ledger(lib)
    raws = []
    for root, _dirs, files in os.walk(lib):
        for f in sorted(files):
            if f.startswith("._") or not f.endswith(VIDEO_EXT):
                continue
            if f.startswith(SKIP_PREFIX):
                continue
            p = os.path.join(root, f)
            if f in led.get("harvested", {}):
                continue
            raws.append(p)
    return raws, led


def cmd_check(lib):
    raws, led = find_raw_videos(lib)
    print(f"图库：{lib}")
    done = led.get("harvested", {})
    if done:
        print(f"已整理 {len(done)} 个原片（台账 {LEDGER_NAME}）：")
        for k, v in sorted(done.items()):
            if v.get("parts_n") or v.get("frames_n"):
                detail = f" → 剪出 {v.get('parts_n', 0)} 段 + {v.get('frames_n', 0)} 帧"
            else:
                detail = f"（{v.get('note', '已处理')}）"
            print(f"  ✓ {k}{detail}（{v.get('date', '?')}）")
    if not raws:
        print("\n没有待整理的新视频 ✅")
        return 0
    print(f"\n⚠️ 发现 {len(raws)} 个未整理的新视频：")
    for p in raws:
        info = ffprobe_info(p)
        if "error" in info:
            print(f"  - {os.path.basename(p)}  (探测失败: {info['error']})")
            continue
        sz = os.path.getsize(p) / 1024 / 1024
        print(f"  - {os.path.basename(p)}  {info['w']}x{info['h']} "
              f"{info['dur']}s {sz:.1f}MB")
    print("\n→ 处理流程（AI 执行）：① fps=2 抽帧网格 Read 目视分段；② 场景检测找硬切点；"
          "③ 逐段判质量（构图/协调性/时长≥1s）；④ 不协调但画面好的段抽静帧；"
          "⑤ --cut 剪出入库；⑥ 剪完首尾帧复检。")
    return 0


def cmd_cut(lib, src, parts, frames):
    if not os.path.exists(src):
        print(f"找不到源视频：{src}", file=sys.stderr)
        return 2
    info = ffprobe_info(src)
    if "error" in info:
        print(f"探测失败：{info['error']}", file=sys.stderr)
        return 2
    outdir = os.path.dirname(src)
    made, frame_made = [], []

    for name, a, b in parts:
        if b - a < MIN_SEG:
            print(f"  ✗ 跳过 {name}：{b - a:.2f}s 短于 {MIN_SEG}s 下限", file=sys.stderr)
            continue
        outp = os.path.join(outdir, name + ".mp4")
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", src,
             "-an", "-c:v", "libx264", "-preset", "medium", "-crf", CRF,
             "-pix_fmt", "yuv420p", "-movflags", "+faststart", outp, "-y"],
            timeout=600)
        if r.returncode != 0:
            print(f"  ✗ 剪切失败：{name}", file=sys.stderr)
            continue
        d = ffprobe_info(outp)
        made.append({"name": name + ".mp4", "t": [round(a, 2), round(b, 2)],
                     "dur": d.get("dur")})
        print(f"  ✓ 片段 {name}.mp4（{a:.2f}-{b:.2f}s → {d.get('dur')}s）")

    for name, ts in frames:
        outp = os.path.join(outdir, name + ".png")
        r = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error",
             "-ss", f"{ts:.2f}", "-i", src, "-frames:v", "1",
             "-q:v", "2", outp, "-y"], timeout=120)
        if r.returncode != 0:
            print(f"  ✗ 抽帧失败：{name}", file=sys.stderr)
            continue
        frame_made.append({"name": name + ".png", "t": round(ts, 2)})
        print(f"  ✓ 静帧 {name}.png（{ts:.2f}s）")

    led = load_ledger(lib)
    led.setdefault("harvested", {})[os.path.basename(src)] = {
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "dur": info.get("dur"), "res": f"{info.get('w')}x{info.get('h')}",
        "parts": made, "frames": frame_made,
        "parts_n": len(made), "frames_n": len(frame_made)}
    save_ledger(lib, led)
    print(f"台账已登记 → {ledger_path(lib)}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="分镜图库新视频收割")
    ap.add_argument("--lib", default=DEFAULT_LIB)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--cut", metavar="SRC")
    ap.add_argument("--parts", default="", help="名称:起:止,名称2:起:止")
    ap.add_argument("--frames", default="", help="名称:时刻,名称2:时刻")
    ap.add_argument("--forget", metavar="SRC")
    a = ap.parse_args()

    if a.check:
        return cmd_check(a.lib)
    if a.forget:
        led = load_ledger(a.lib)
        led.get("harvested", {}).pop(a.forget, None)
        save_ledger(a.lib, led)
        print(f"已移除台账记录：{a.forget}")
        return 0
    if a.cut:
        parts = []
        for item in filter(None, a.parts.split(",")):
            n, s, e = item.rsplit(":", 2)
            parts.append((n.strip(), float(s), float(e)))
        frames = []
        for item in filter(None, a.frames.split(",")):
            n, t = item.rsplit(":", 1)
            frames.append((n.strip(), float(t)))
        return cmd_cut(a.lib, a.cut, parts, frames)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
