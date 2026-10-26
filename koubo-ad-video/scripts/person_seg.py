#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
person_seg.py —— macOS Vision 人像分割，生成「人物遮挡蒙版」序列
================================================================
用途（2026-10-14 灿爹「重点字 3D 景深，被人物遮住一点带」需求）：

  给 fx 重点字做 behind（字在人物身后）时，需要知道「人物到底占了画面哪块」。
  早先用**静态椭圆**近似人物位置，但运镜（zoom_in/pan_left）和第一镜的 AI 动态视频
  会让人物在成片里移动/变形 —— 静态椭圆必然错位，抠出来就是「字缺一块」的糟糕观感。

  本脚本对**已渲染的底片帧**逐帧做人像分割：mask 天然跟随运镜和视频里活动的人物。
  输出灰度 PNG（人=白255，背景=黑0），可直接喂 ffmpeg alphamerge 做 alpha 抠除。

  ⚠️ 只在 macOS 14+（Vision.personSegmentation）可用。依赖 pyobjc-framework-Vision /
     Quartz；已装。

用法:
  person_seg.py --frames <帧目录> --out <mask目录> [--feather 3] [--quality accurate]
                [--invert]     # 反相（人=黑=抠除区），默认不反相

输出文件名与输入帧同名（.png），便于 ffmpeg 用 -i mask-%03d.png 成组读取。
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageFilter

from Quartz import CoreVideo  # noqa: F401  (CoreVideo 通过 Quartz 暴露)
import Vision
from Foundation import NSURL


QUALITY = {"fast": 0, "balanced": 1, "accurate": 2}


def _to_numpy(pb):
    """CVPixelBuffer(mask) → numpy uint8 2D

    ★踩坑1：pyobjc 的 CoreVideo.CVPixelBufferGetBaseAddress 返回 varlist，
       int(addr) 直接 TypeError。
    ★踩坑2：CIContext.createCGImage 出来可能是 RGBA，直接 arr[:, :w] 会把 R/G/B/A
      混排当灰度（实测全图 81/111 的假值）。统一走 NSBitmapImageRep→PNG→PIL，
      由 PNG 解码器负责正确取通道。
    """
    import io
    from AppKit import NSBitmapImageRep, NSPNGFileType
    from Quartz import CIImage
    ci = CIImage.imageWithCVPixelBuffer_(pb)
    rep = NSBitmapImageRep.alloc().initWithCIImage_(ci)
    png = rep.representationUsingType_properties_(NSPNGFileType, None)
    im = Image.open(io.BytesIO(bytes(png)))
    if im.mode != "L":
        im = im.convert("L")
    # Vision 对小尺寸/插画素材偶给出**反相**结果：以「四角是否为背景」为准自动校正
    a = np.array(im)
    corners = np.concatenate([a[:40, :40].ravel(), a[:40, -40:].ravel(),
                              a[-40:, :40].ravel(), a[-40:, -40:].ravel()])
    if corners.mean() > 128:      # 四角是白 = 四角被判成「人」 → 反了
        a = 255 - a
    return a


def seg_image(path, quality="accurate", target=None, feather=0, invert=False):
    """单张图 → L 模式 mask（人=白）"""
    url = NSURL.fileURLWithPath_(path)
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)
    req = Vision.VNGeneratePersonSegmentationRequest.alloc().initWithCompletionHandler_(None)
    req.setQualityLevel_(QUALITY.get(quality, 2))
    ok, err = handler.performRequests_error_([req], None)
    if not ok or err:
        raise RuntimeError(f"Vision 分割失败 {path}: {err}")
    results = req.results()
    if not results:
        raise RuntimeError(f"无分割结果 {path}")
    arr = _to_numpy(results[0].pixelBuffer())
    m = Image.fromarray(arr, mode="L")
    if target:
        m = m.resize(target, Image.BILINEAR)
    # 二值化：Vision 输出是软概率图，收紧成硬边再羽化，遮挡边缘才干净
    a = np.array(m)
    a = np.where(a >= 128, 255, 0).astype(np.uint8)
    m = Image.fromarray(a, mode="L")
    if feather > 0:
        m = m.filter(ImageFilter.GaussianBlur(feather))
    if invert:
        import PIL.ImageOps as ImageOps
        m = ImageOps.invert(m)
    return m


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frames", required=True, help="输入帧目录")
    ap.add_argument("--out", required=True, help="mask 输出目录")
    ap.add_argument("--feather", type=float, default=3.0)
    ap.add_argument("--quality", default="accurate", choices=list(QUALITY))
    ap.add_argument("--invert", action="store_true", help="反相（人=黑）")
    ap.add_argument("--width", type=int, default=1920)
    ap.add_argument("--height", type=int, default=1080)
    a = ap.parse_args()

    exts = (".png", ".jpg", ".jpeg")
    files = sorted(f for f in os.listdir(a.frames) if f.lower().endswith(exts))
    if not files:
        sys.exit(f"帧目录为空: {a.frames}")
    os.makedirs(a.out, exist_ok=True)
    target = (a.width, a.height)

    for i, f in enumerate(files, 1):
        m = seg_image(os.path.join(a.frames, f), a.quality, target, a.feather, a.invert)
        m.save(os.path.join(a.out, os.path.splitext(f)[0] + ".png"))
        if i % 20 == 0 or i == len(files):
            print(f"  {i}/{len(files)}", flush=True)
    print(f"person_seg ✓ {len(files)} 帧 → {a.out}")


if __name__ == "__main__":
    main()
