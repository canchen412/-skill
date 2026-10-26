#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 macOS Vision 的 VNClassifyImageRequest 给素材打粗标签，辅助命名。
输出英文标签 topN（normalized confidence）。
"""
import sys, os, json
try:
    import Vision, Quartz
    from Foundation import NSURL
except ImportError:
    sys.exit("缺 pyobjc-Vision")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"}


def classify(path, topn=6):
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if not src:
        return None
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if cg is None:
        return None
    req = Vision.VNClassifyImageRequest.alloc().init()
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = handler.performRequests_error_([req], None)
    if not ok[0]:
        return None
    out = []
    for obs in (req.results() or [])[:topn]:
        ident = str(obs.identifier())
        # 多级标签取最后一段（更具体），保留全串备查
        out.append({"raw": ident, "leaf": ident.split(".")[-1].replace("_", " "),
                    "conf": round(float(obs.confidence()), 3)})
    return out


if __name__ == "__main__":
    for p in sys.argv[1:]:
        if p.startswith("-"):
            continue
        if os.path.splitext(p)[1].lower() not in IMAGE_EXT:
            continue
        res = classify(p)
        print(json.dumps({"file": os.path.basename(p), "labels": res}, ensure_ascii=False))
