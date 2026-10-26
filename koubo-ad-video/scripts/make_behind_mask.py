#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
make_behind_mask.py —— behind 重点字的「人物遮挡蒙版」序列（2026-10-14 v3 重写）

为什么重写
─────────
v1/v2 用**静态椭圆**近似人物位置，错在：
  ① 运镜（zoom_in / pan_left）会让成片里的人物移动变形，静态椭圆必然错位；
  ② 第一镜是 AI 动态视频素材，人物一直在动；
  → 结果就是「字被硬切掉一块」，而不是「字藏在人后面」，灿爹直接判：效果糟糕。

v3 做法（本文件）：
  对**已渲染的底片帧**逐帧估计人物区，mask 天然跟随运镜与活动人物。
  人物区估计 = macOS Vision **显著性**（VNGenerateAttentionBasedSaliency）
    · 实测比「人像分割 VNGeneratePersonSegmentationRequest」稳得多：后者是真人
      模型，对本 IP 动漫素材只出碎片（人占白比 6%，bbox 满屏、抽样点全 0）；
    · 显著性虽是软热图，但可选IF：归一化 → 阈值 → 形态学闭 + 填洞 + 膨胀
      （把零散热区连成整块人身）→ 羽化；
    · 底部字幕带强制设为背景（显著性会把字幕吃掉 → 误抠字）。

蒙版语义（★务必记住，v1 在这里翻过车）
──────────────────────────────────────
  alphamerge 会用蒙版值**替换** behind PNG 全部像素的 alpha：
    mask 白(255) = 保留字 alpha        （此处没有人物，字正常显示）
    mask 黑(0)   = 字 alpha 抠掉透明    （此处是人物，露出 base 的人体像素）
  ⚠️ 绝不能做成「全白底 + 黑椭圆」：非字区会被设成 alpha=255 的不透明黑，
     黑块直接盖死整帧底片（v1 实测症状）。

用法
────
  make_behind_mask.py --spec <fx_spec_*.json> --frames <底片帧目录> --out <mask目录>
                      [--threshold 0.38] [--dilate 26] [--feather 6] [--fps 30]
  只有 behind 字在场的帧才做分割（其余帧复用或直接全黑），开销可控。
"""
import argparse
import io
import json
import os
import re

import numpy as np
from PIL import Image, ImageFilter

try:
    from scipy.ndimage import binary_closing, binary_dilation, binary_fill_holes
    _HAS_SCIPY = True
except Exception:                       # 兜底：用 PIL 的 MaxFilter 近似膨胀
    _HAS_SCIPY = False

import Vision
from Foundation import NSURL
from AppKit import NSBitmapImageRep, NSPNGFileType
from Quartz import CIImage

# 底部字幕安全带：显著性常把字幕判成前景，必须排除，否则 behind 字被误抠
SAFE_BOTTOM = int(os.environ.get("SEG_SAFE_BOTTOM", 1040))


# ────────────────────────── Vision 显著性 ──────────────────────────
def saliency_map(path, w=1920, h=1080):
    """返回归一化到 0..1 的显著性 float32 数组（H,W）"""
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(
        NSURL.fileURLWithPath_(path), None)
    req = Vision.VNGenerateAttentionBasedSaliencyImageRequest.alloc().initWithCompletionHandler_(None)
    ok, err = handler.performRequests_error_([req], None)
    if not ok or err:
        raise RuntimeError(f"Vision 显著性失败 {path}: {err}")
    obs = req.results()[0]
    ci = CIImage.imageWithCVPixelBuffer_(obs.pixelBuffer())
    rep = NSBitmapImageRep.alloc().initWithCIImage_(ci)
    png = rep.representationUsingType_properties_(NSPNGFileType, None)
    im = Image.open(io.BytesIO(bytes(png))).convert("L").resize((w, h), Image.BILINEAR)
    a = np.asarray(im, dtype=np.float32)
    lo, hi = a.min(), a.max()
    if hi - lo < 1e-6:
        return np.zeros((h, w), dtype=np.float32)
    return (a - lo) / (hi - lo)


def person_mask_from_map(a, threshold=0.38, dilate=26, feather=6):
    """显著性热图 → 人物区 mask（uint8 L，白=人=抠字区）"""
    m = (a >= threshold)
    m[SAFE_BOTTOM:, :] = False                     # 排除字幕带

    if _HAS_SCIPY:
        m = binary_closing(m, structure=np.ones((9, 9), bool))
        m = binary_fill_holes(m)
        if dilate > 0:
            st = np.ones((5, 5), bool)
            for _ in range(max(1, dilate // 3)):
                m = binary_dilation(m, structure=st)
    else:
        img = Image.fromarray((m * 255).astype(np.uint8), "L")
        img = img.filter(ImageFilter.MaxFilter(9))
        m = np.asarray(img) > 128

    out = Image.fromarray((m.astype(np.uint8) * 255), "L")
    if feather > 0:
        out = out.filter(ImageFilter.GaussianBlur(feather))
    return out


def frame_index(name):
    m = re.search(r"(\d+)\.png$", name)
    return int(m.group(1)) if m else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--spec", required=True, help="fx_spec.json（取 behind 字的出没帧）")
    ap.add_argument("--frames", required=True, help="底片帧目录（out_base 抽出的 PNG 序列）")
    ap.add_argument("--out", required=True, help="mask 输出目录")
    ap.add_argument("--threshold", type=float, default=0.38)
    ap.add_argument("--dilate", type=int, default=26)
    ap.add_argument("--feather", type=float, default=6)
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--pad", type=int, default=3, help="帧号位数（必须与 remotion element 序列一致）")
    a = ap.parse_args()

    spec = json.load(open(a.spec, encoding="utf-8"))
    behinds = [k for k in spec.get("kw", []) if k.get("behind")]
    windows = []
    for k in behinds:
        f0 = int(round(k["startSec"] * a.fps))
        f1 = int(round((k["startSec"] + k.get("durSec", 1.8)) * a.fps))
        windows.append((k.get("text", "?"), f0, f1))
    if not windows:
        print("  无 behind 字 → 不需要人物蒙版")
        os.makedirs(a.out, exist_ok=True)
        return

    frames = sorted(f for f in os.listdir(a.frames) if f.lower().endswith(".png"))
    os.makedirs(a.out, exist_ok=True)
    blank = Image.new("L", (1920, 1080), 0)
    # ⚠️ 序列必须 0..N-1 **连续**：ffmpeg image2 按编号连续读，缺号会把后面的帧
    #    当成更早的时间戳 ⇒ 整条 mask 时间轴错位。窗口外写全黑即可（那时刻 behind
    #    序列本来就全透明，黑=抠掉 alpha，抠不抠都一样）。
    total = int(spec.get("totalFrames", 0)) or (frame_index(frames[-1]) + 1 if frames else 0)

    have = {frame_index(f): f for f in frames if frame_index(f) is not None}
    n_seg = 0
    for idx in range(total):
        name = have.get(idx)
        active = name is not None and any(f0 - 2 <= idx <= f1 + 2 for _, f0, f1 in windows)
        if not active:
            blank.save(f"{a.out}/mask-{idx:0{a.pad}d}.png")
            continue
        arr = saliency_map(os.path.join(a.frames, name))
        person_mask_from_map(arr, a.threshold, a.dilate, a.feather).save(
            f"{a.out}/mask-{idx:0{a.pad}d}.png")
        n_seg += 1

    for t, f0, f1 in windows:
        print(f"  mask: 「{t}」帧 {f0}-{f1} 人物区遮挡（逐帧 Vision 显著性）")
    print(f"  person_mask ✓ {len(frames)} 帧 → {a.out}（实算 {n_seg} 帧）")


if __name__ == "__main__":
    main()
