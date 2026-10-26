#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""本地抠图（macOS Vision，离线免费）—— 输出人物前景 RGBA PNG

用法: cutout_local.py <输入图> <输出_fg.png>
"""
import sys
import os
import numpy as np
import Vision
import Quartz
from Foundation import NSURL
from PIL import Image


def main():
    src, out = sys.argv[1], sys.argv[2]
    url = NSURL.fileURLWithPath_(os.path.abspath(src))
    cg_src = Quartz.CGImageSourceCreateWithURL(url, None)
    if cg_src is None:
        print("读图失败"); return 1
    cg = Quartz.CGImageSourceCreateImageAtIndex(cg_src, 0, None)
    W = Quartz.CGImageGetWidth(cg); H = Quartz.CGImageGetHeight(cg)
    print(f"图像 {W}x{H}")

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    req = Vision.VNGenerateForegroundInstanceMaskRequest.alloc().init()
    ok, err = handler.performRequests_error_([req], None)
    if not ok or not req.results():
        print("Vision 请求失败:", err); return 2
    obs = req.results()[0]
    inst = obs.allInstances()
    print("检测到前景实例:", len(inst))

    # PyObjC 会把 error: out 参数一并返回 -> 结果是 (pb, err) 元组。
    # 早期版本直接把元组当 pb 用，导致 GetWidth/GetBaseAddress 全取到 0（本机踩过）。
    res = obs.generateScaledMaskForImageForInstances_fromRequestHandler_error_(
        inst, handler, None)
    pb, perr = res if isinstance(res, tuple) else (res, None)
    if pb is None:
        print("mask 生成失败:", perr); return 3

    w = Quartz.CVPixelBufferGetWidth(pb)
    h = Quartz.CVPixelBufferGetHeight(pb)
    bpr = Quartz.CVPixelBufferGetBytesPerRow(pb)
    fmt = Quartz.CVPixelBufferGetPixelFormatType(pb)
    if not (w and h):
        print(f"mask 尺寸异常 {w}x{h}（pixel format {fmt}）"); return 4
    Quartz.CVPixelBufferLockBaseAddress(pb, 0)
    base = Quartz.CVPixelBufferGetBaseAddress(pb)
    if base is None:
        Quartz.CVPixelBufferUnlockBaseAddress(pb, 0)
        print("mask 无可用基址"); return 5
    buf = base.as_buffer(bpr * h)
    # mask 可能是 32 位浮点灰度(L00f) 或 8 位(L008)，按 bpr/w 反推每像素字节数
    bpp = bpr // w
    if bpp == 4:
        arr = np.frombuffer(buf, dtype=np.float32).reshape(h, bpr // 4)[:, :w].copy()
        arr = np.clip(arr * 255.0, 0, 255).astype(np.uint8)
    else:
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bpr)[:, :w].copy()
    Quartz.CVPixelBufferUnlockBaseAddress(pb, 0)
    print(f"mask {w}x{h} bpp={bpp} 覆盖率 {100.0*arr.mean()/255:.1f}%")

    mask = Image.fromarray(arr, "L")
    if mask.size != (W, H):
        mask = mask.resize((W, H), Image.LANCZOS)
    rgb = Image.open(src).convert("RGB")
    rgba = rgb.copy()
    rgba.putalpha(mask)
    rgba.save(out)
    print("已保存:", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
