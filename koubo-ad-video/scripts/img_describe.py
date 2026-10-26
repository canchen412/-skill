#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""img_describe.py —— 无多模态模型时的本地视觉信息榨取器（macOS Vision）。

一次输出：粗标签 / 人脸数 / 人体数 / 姿态 / OCR 文字 / 主色 / 尺寸。
用途：给新入库素材做「机器预填描述」，再拼接触表请人目视确认。

用法：
    python3 img_describe.py <图1> <图2> ...
    python3 img_describe.py --summary <图...>     # 只打人类可读单行摘要
    python3 img_describe.py --ocr-only <图...>
"""
import sys, os, json

try:
    import Vision, Quartz
    from Foundation import NSURL
except ImportError:
    sys.exit("缺 pyobjc-Vision（用 /Users/mac/.workbuddy/binaries/python/envs/default/bin/python 跑）")

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp", ".heic"}


def _cg(path):
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if not src:
        return None
    return Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)


def _run(cg, requests):
    h = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = h.performRequests_error_(requests, None)
    if not ok[0]:
        return False
    return True


def _dominant_colors(cg, k=4):
    """缩到 12x12 取像素，做极简量化（粗桶），返回占比前 k 的主色 HEX。"""
    import objc
    w = h = 12
    cs = Quartz.CGColorSpaceCreateDeviceRGB()
    buf = objc.allocateBuffer(w * h * 4)           # 索引返回 int 的可写缓冲
    ctx = Quartz.CGBitmapContextCreateWithData(
        buf, w, h, 8, w * 4, cs,
        Quartz.kCGImageAlphaPremultipliedLast, None, None)
    if ctx is None:
        return []
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, w, h), cg)
    buckets = {}
    for i in range(0, w * h * 4, 4):
        r, g, b = int(buf[i]), int(buf[i + 1]), int(buf[i + 2])
        q = (r >> 5, g >> 5, b >> 5)          # 每通道 8 档
        key = (q[0] * 32 + 16, q[1] * 32 + 16, q[2] * 32 + 16)
        buckets[key] = buckets.get(key, 0) + 1
    total = w * h or 1
    top = sorted(buckets.items(), key=lambda kv: -kv[1])[:k]
    out = []
    for (r, g, b), n in top:
        out.append({"hex": "#%02X%02X%02X" % (r, g, b), "pct": round(n / total, 3)})
    return out


def _tone(colors):
    """把主色翻译成人话色调，便于快速判断棚拍/暗调/冷白。"""
    if not colors:
        return ""
    r = g = b = 0
    for c in colors:
        v = c["hex"]
        r += int(v[1:3], 16) * c["pct"]
        g += int(v[3:5], 16) * c["pct"]
        b += int(v[5:7], 16) * c["pct"]
    mx, mn = max(r, g, b), min(r, g, b)
    lum = (0.299 * r + 0.587 * g + 0.114 * b)
    tags = []
    tags.append("暗调" if lum < 80 else ("中间调" if lum < 160 else "亮调"))
    if mx - mn < 22:
        tags.append("无彩/灰调")
    elif r >= g >= b:
        tags.append("暖棕" if b < g - 18 else "暖中性")
    elif b >= g >= r:
        tags.append("冷蓝")
    else:
        tags.append("彩色偏" + ("绿" if g == mx else ("红" if r == mx else "蓝")))
    return "/".join(tags)


def describe(path, topn=8, ocr=True):
    cg = _cg(path)
    if cg is None:
        return {"file": os.path.basename(path), "error": "无法解码"}
    W = Quartz.CGImageGetWidth(cg)
    H = Quartz.CGImageGetHeight(cg)

    creq = Vision.VNClassifyImageRequest.alloc().init()
    freq = Vision.VNDetectFaceRectanglesRequest.alloc().init()
    hreq = Vision.VNDetectHumanRectanglesRequest.alloc().init()
    reqs = [creq, freq, hreq]
    treq = None
    if ocr:
        treq = Vision.VNRecognizeTextRequest.alloc().init()
        treq.setRecognitionLevel_(1)          # accurate
        try:
            treq.setRecognitionLanguages_(["zh-Hans", "en-US"])
        except Exception:
            pass
        treq.setUsesLanguageCorrection_(True)
        reqs.append(treq)

    if not _run(cg, reqs):
        return {"file": os.path.basename(path), "error": "Vision 请求失败"}

    labels = []
    for obs in (creq.results() or [])[:topn]:
        ident = str(obs.identifier())
        labels.append({"leaf": ident.split(".")[-1].replace("_", " "),
                       "conf": round(float(obs.confidence()), 3)})

    faces = len(freq.results() or [])
    humans = len(hreq.results() or [])

    texts = []
    if treq is not None:
        for obs in (treq.results() or []):
            cand = obs.topCandidates_(1)
            if cand and len(cand):
                s = str(cand[0].string()).strip()
                if s:
                    texts.append(s)

    colors = _dominant_colors(cg)
    return {
        "file": os.path.basename(path),
        "path": path,
        "wh": [W, H],
        "ratio": round(W / H, 3) if H else 0,
        "labels": labels,
        "faces": faces,
        "humans": humans,
        "tone": _tone(colors),
        "colors": colors,
        "text": texts,
    }


def summary_line(d):
    if d.get("error"):
        return f"[X] {d['file']}  {d['error']}"
    lab = " / ".join(f"{x['leaf']}({x['conf']})" for x in d["labels"][:6])
    txt = ("，文字：" + "|".join(d["text"][:6])) if d.get("text") else "，无文字"
    return (f"[O] {d['file']}  {d['wh'][0]}x{d['wh'][1]}\n"
            f"    标签：{lab}\n"
            f"    人脸{d['faces']} 人体{d['humans']}  色调：{d['tone']}{txt}")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    mode_sum = "--summary" in sys.argv
    ocr = "--ocr-only" in sys.argv or not ("--no-ocr" in sys.argv)
    files = [p for p in args if os.path.splitext(p)[1].lower() in IMAGE_EXT]
    if not files:
        sys.exit("用法: img_describe.py [--summary] <图1> <图2> ...")
    for p in files:
        d = describe(p, ocr=ocr)
        if mode_sum:
            print(summary_line(d))
            print()
        else:
            print(json.dumps(d, ensure_ascii=False))
