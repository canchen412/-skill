#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""带坐标的 OCR（macOS Vision）—— 用于**自动定位水印**，替代肉眼抽帧目检。

★为什么需要它：AI 助手看不到图，无法靠目检确定水印站点位置；
  而水印是「平台名文字」这种固定模板 —— OCR 能同时给出文字和 bbox，
  于是「抽帧 → OCR → 聚类站点」就能全自动拿到 delogo/inpaint 框。

输出坐标约定（与 ffmpeg delogo 一致，原点在**左上**，全部为**相对值** 0~1）：
  {"text": "小云雀AI", "rx0":0.84, "ry0":0.79, "rx1":0.95, "ry1":0.87}

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  $PY ocr_boxes.py <图片路径> [<图片2> ...]        # 打印 JSON 行
  $PY ocr_boxes.py frame.png --pad 0.01            # 外扩 1% 余量
"""
import argparse, json, os, sys

try:
    import Vision
    import Quartz
    from Foundation import NSURL
except ImportError:
    sys.stderr.write("缺少 pyobjc 依赖，brew/pyobjc 安装见 pool_ocr.py 文档头\n")
    sys.exit(2)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"}


def ocr_boxes(path, pad=0.0):
    """返回 [(text, rx0, ry0, rx1, ry1), ...]（左上角原点，相对坐标）。"""
    if os.path.splitext(path)[1].lower() not in IMAGE_EXT:
        return None
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if not src:
        return None
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        return None
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)                  # accurate
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(False)
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = handler.performRequests_error_([req], None)
    if not ok[0]:
        return None
    out = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if not cands or not len(cands):
            continue
        text = str(cands[0].string())
        bb = obs.boundingBox()                   # 原点左下角
        x0 = float(bb.origin.x)
        y0 = 1.0 - float(bb.origin.y + bb.size.height)   # 转成「上边」
        x1 = float(bb.origin.x + bb.size.width)
        y1 = 1.0 - float(bb.origin.y)
        out.append({
            "text": text,
            "rx0": round(max(0.0, x0 - pad), 4), "ry0": round(max(0.0, y0 - pad), 4),
            "rx1": round(min(1.0, x1 + pad), 4), "ry1": round(min(1.0, y1 + pad), 4),
            "conf": round(float(cands[0].confidence()), 3),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--pad", type=float, default=0.0, help="bbox 外扩余量（相对值）")
    ap.add_argument("--match", default=None, help="只输出包含该关键字的结果")
    a = ap.parse_args()
    for p in a.files:
        res = ocr_boxes(p, pad=a.pad)
        if res is None:
            print(json.dumps({"file": p, "error": "decode-fail"}, ensure_ascii=False))
            continue
        if a.match:
            res = [r for r in res if a.match in r["text"]]
        print(json.dumps({"file": os.path.basename(p), "w": None, "h": None, "boxes": res},
                         ensure_ascii=False))


if __name__ == "__main__":
    main()
