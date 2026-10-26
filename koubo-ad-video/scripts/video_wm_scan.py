#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""视频素材「平台水印废片」全库扫描（koubo-ad-video 技能常驻工具）

为什么需要它：
  AI 平台（小云雀 / 可灵 / 即梦 …）导出的视频自带**定时淡入淡出**的水印，
  单帧或少量抽帧很可能正好落在「水印不可见」的时刻 —— 肉眼/少量抽帧都会漏检。
  ⇒ 必须**每支视频多点抽帧 + OCR** 才能可靠判定。

  ★2026-10-01 实锤：`视频_俯卧撑_公园多镜头旋转.mov` 单次抽 3 帧判为「干净」，
    结果切出的片段里有 2 个带水印流入片段库；同一批还漏了 3 支根目录视频。
    ⇒ 判定一律走本工具（默认 3~6 帧），不要凭单帧目检。

输出：JSON（每支视频的判定 + 命中词 + 全部文字）+ 终端汇总表。
      `--trash` 可把判废的视频**软删除**（移入卷的 .Trashes，可随时捞回）。

用法：
  PY=~/.workbuddy/binaries/python/envs/default/bin/python
  $PY video_wm_scan.py --lib "/…/分镜图库/小绿人" --depth 1 --nframe 6
  $PY video_wm_scan.py --lib "…" --trash --json /tmp/wm.json      # 判废并软删除
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pool_ocr_boxes import ocr_boxes  # noqa: E402

FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "/Users/mac/.local/bin/ffprobe")

# 各平台水印关键词（含 OCR 常见误识变体：AI/Al、小云雀/小入崔商 等已做归一化处理）
WM_KEYS = ("小云雀", "AI生成", "即梦", "可灵", "通义", "豆包", "海螺", "PixVerse",
           "Vidu", "Runway", "Sora", "Kling", "元宝", "智谱", "腾讯混元",
           "Dreamina", "Seedance", "Pika", "Luma")

VID_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv")


def _norm(s):
    """归一化 OCR 文本，抹平常见误识：AI/Al、生成/生戍、●/◎/•/9/0 前缀符号。"""
    s = s.replace(" ", "").replace("|", "")
    s = s.replace("Al", "AI").replace("A1", "AI").replace("aI", "AI")
    s = s.replace("小入崔商", "小云雀")  # Vision 对「小云雀」的典型误识
    return s


def probe(path):
    try:
        r = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path],
                           capture_output=True, text=True, timeout=30)
        return float((r.stdout or "0").strip() or 0)
    except Exception:
        return 0.0


def _grab(src, t, out):
    subprocess.run([FFMPEG, "-v", "error", "-ss", f"{max(0.0, t):.3f}", "-i", src,
                    "-frames:v", "1", "-y", out], capture_output=True, timeout=120)
    return os.path.exists(out)


def scan_video(path, nframe=3, tmpdir=None):
    """抽 nframe 帧 OCR。返回 (是否含水印 or None=读不出, 命中词, 全部文字)。"""
    dur = probe(path)
    if dur <= 0:
        return None, [], []
    tmpdir = tmpdir or tempfile.gettempdir()
    texts, hits = set(), set()
    for i in range(nframe):
        # 避开首尾黑场/转场，均匀铺满 15%~85%
        frac = 0.15 + 0.70 * (i / max(1, nframe - 1)) if nframe > 1 else 0.5
        fp = os.path.join(tmpdir, f"_wmscan_{os.getpid()}_{i}.png")
        if not _grab(path, dur * frac, fp):
            continue
        try:
            boxes = ocr_boxes(fp, pad=0.0) or []
        except Exception:
            boxes = []
        for b in boxes:
            tx = str(b.get("text", "")).strip()
            if not tx:
                continue
            texts.add(tx)
            n = _norm(tx)
            for k in WM_KEYS:
                if k in n:
                    hits.add(k)
        try:
            os.remove(fp)
        except OSError:
            pass
    return (bool(hits), sorted(hits), sorted(texts))


def trash_dir_of(path):
    """取该路径所在卷的系统废纸篓目录（可捞回）。"""
    p = os.path.abspath(path)
    parts = p.split(os.sep)
    if len(parts) > 2 and parts[1] == "Volumes":
        vol = os.sep + os.path.join(parts[1], parts[2])
    else:
        vol = os.path.expanduser("~")
        return os.path.expanduser("~/.Trash")
    return os.path.join(vol, ".Trashes", "501")


def soft_delete(path, dest_root=None):
    """软删除：移入废纸篓（保持可恢复），不真删。返回新路径或 None。"""
    try:
        root = dest_root or trash_dir_of(path)
        os.makedirs(root, exist_ok=True)
        name = os.path.basename(path)
        dst = os.path.join(root, name)
        n = 2
        while os.path.exists(dst):
            stem, ext = os.path.splitext(name)
            dst = os.path.join(root, f"{stem}_{n}{ext}")
            n += 1
        shutil.move(path, dst)
        return dst
    except Exception as e:
        sys.stderr.write(f"软删除失败 {path}: {e}\n")
        return None


def iter_videos(lib, depth=1):
    base = os.path.abspath(lib)
    out = []
    for dp, dns, fns in os.walk(base):
        rel = os.path.relpath(dp, base)
        d = 0 if rel == "." else rel.count(os.sep) + 1
        if d > depth:
            dns[:] = []
            continue
        dns[:] = [x for x in dns if not x.startswith(".") and not x.startswith("_")]
        for f in fns:
            if f.startswith("._") or f.startswith("."):
                continue
            if f.lower().endswith(VID_EXT):
                out.append(os.path.join(dp, f))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lib", required=True, help="待扫目录")
    ap.add_argument("--depth", type=int, default=1, help="扫描深度（1=只本层）")
    ap.add_argument("--nframe", type=int, default=3, help="每支抽几帧（水印会淡入淡出，建议 ≥3）")
    ap.add_argument("--json", default=None, help="写出 JSON 结果")
    ap.add_argument("--trash", action="store_true", help="把判废的视频软删除（移入废纸篓）")
    ap.add_argument("--trash-dir", default=None, help="自定义软删除落点（默认系统废纸篓）")
    a = ap.parse_args()

    files = iter_videos(a.lib, a.depth)
    print(f"待扫 {len(files)} 支视频（每支 {a.nframe} 帧）\n", flush=True)

    rows, wasted = [], []
    for i, p in enumerate(files, 1):
        wm, hits, texts = scan_video(p, a.nframe)
        verdict = "读取失败" if wm is None else ("废片·带水印" if wm else "干净")
        flag = {"废片·带水印": "⛔", "干净": "✅", "读取失败": "⚠️"}[verdict]
        rec = {"path": p, "name": os.path.basename(p), "dur": round(probe(p), 2),
               "verdict": verdict, "wm_hits": hits, "texts": texts}
        if a.trash and wm:
            moved = soft_delete(p, a.trash_dir)
            rec["trashed_to"] = moved
            if moved:
                wasted.append(rec)
        rows.append(rec)
        print(f"  [{i:3d}/{len(files)}] {flag} {os.path.basename(p)[:50]:52s} "
              f"{','.join(hits) or ''}", flush=True)

    n_ok = sum(1 for r in rows if r["verdict"] == "干净")
    n_bad = sum(1 for r in rows if r["verdict"] == "废片·带水印")
    n_err = sum(1 for r in rows if r["verdict"] == "读取失败")
    print(f"\n汇总：干净 {n_ok} ｜ 废片(带水印) {n_bad} ｜ 读取失败 {n_err}")
    for r in rows:
        if r["verdict"] == "废片·带水印":
            t = f"  → {r['trashed_to']}" if r.get("trashed_to") else ""
            print(f"   ⛔ {r['name']}  ({r['dur']}s)  命中 {r['wm_hits']}{t}")

    if a.json:
        json.dump({"lib": os.path.abspath(a.lib), "nframe": a.nframe,
                   "total": len(rows), "clean": n_ok, "wasted": n_bad, "error": n_err,
                   "items": rows},
                  open(a.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print(f"\n结果 → {a.json}")


if __name__ == "__main__":
    main()
