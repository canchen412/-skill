#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pool_ocr.py —— 全库素材「批量文字识别」，用于一次性核对品牌 / 他品穿帮 / 功效词。

★ 为什么必须用这个而不是 `vision_ocr`：
  `scripts/vision_ocr`（Swift 编译版）在本机偶发不可用 —— Xcode CommandLineTools 的
  swift 6.1 与 MacOSX.sdk 版本不匹配时报
      error: redefinition of module 'SwiftBridging'
      error: SDK is not supported by the compiler
  改用 pyobjc 走同一套 macOS Vision 框架，结果质量相同（离线、免费、中文准），
  且不依赖 swift 工具链。

依赖（一次性）：
  /Users/mac/.workbuddy/binaries/python/envs/default/bin/pip install \
      pyobjc-framework-Vision pyobjc-framework-Quartz

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/pool_ocr.py

  # ① 扫整个图库（递归，自动跳过 ._ 元数据与隐藏目录）
  $PY $S --dir "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"

  # ② 只扫清单（每行一个：绝对路径，或相对 --dir 的文件名）
  $PY $S --list /tmp/pool_v3.txt --dir "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"

  # ③ 存文件（TSV），并只在 stderr 留进度
  $PY $S --dir ... --out /tmp/ocr_out.txt

  # ④ 只扫某个大类
  $PY $S --dir "/Volumes/.../小绿人" --match "产品_*"

输出：制表符分隔，每行 `<相对路径>\t<图上文字用 | 拼接>`；
      图上无文字 → `[无文字]`（占多数，属正常）；解码失败 → `[FAIL]`。
      进度每 20 张打到 stderr，最后一行 `DONE <n>`。

典型用法（品牌核对）：把输出 grep 品牌关键词即可一眼看出他品：
  grep -E "诺特|金维他|VORSE|爱兰|米当卡|肌酸|液体钙" /tmp/ocr_out.txt

局限（务必知悉）：
  - OCR 只认「图上有文字」的图。原图无品牌文字（纯动作镜、纯场景镜）永远读不出东西，
    这类图他品风险低，但不代表可跳过目视（如粉标白瓶若无清晰文字仍可能漏）。
  - 因此**品牌台账 = OCR 结果 + 关键白瓶镜人工放大复核**，二者缺一不可。
"""
import argparse
import os
import sys

try:
    import Vision
    import Quartz
    from Foundation import NSURL
except ImportError:
    sys.stderr.write(
        "缺少 pyobjc 依赖。请先安装：\n"
        "  /Users/mac/.workbuddy/binaries/python/envs/default/bin/pip install "
        "pyobjc-framework-Vision pyobjc-framework-Quartz\n"
    )
    sys.exit(2)

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".heic", ".tif", ".tiff", ".bmp", ".webp"}
SKIP_PREFIX = "_"          # _通用池 / _判废 / _旧画风 / _他品污染隔离 等：按需用 --include-underscore 纳入
DEFAULT_BASE = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人"


def ocr_image(path):
    """返回图上文字列表；解码失败返回 None，无文字返回 []。"""
    url = NSURL.fileURLWithPath_(path)
    src = Quartz.CGImageSourceCreateWithURL(url, None)
    if not src:
        return None
    cg = Quartz.CGImageSourceCreateImageAtIndex(src, 0, None)
    if not cg:
        return None
    req = Vision.VNRecognizeTextRequest.alloc().init()
    req.setRecognitionLevel_(0)                       # 0 = accurate（1 = fast）
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(False)             # 关掉纠错：品牌名/型号更可信
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg, None)
    ok = handler.performRequests_error_([req], None)
    if not ok[0]:
        return None
    out = []
    for obs in (req.results() or []):
        cands = obs.topCandidates_(1)
        if cands and len(cands):
            out.append(str(cands[0].string()))
    return out


def scan_dir(base, include_underscore=False, match=None):
    """递归收集图片相对路径，稳定排序。"""
    import fnmatch
    found = []
    for root, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        if not include_underscore:
            dirs[:] = [d for d in dirs if not d.startswith(SKIP_PREFIX)]
        for f in files:
            if f.startswith("._") or f.startswith("."):
                continue
            if os.path.splitext(f)[1].lower() not in IMAGE_EXT:
                continue
            rel = os.path.relpath(os.path.join(root, f), base)
            if match and not fnmatch.fnmatch(f, match):
                continue
            found.append(rel)
    return sorted(found)


def load_list(listfile, base):
    """清单每行：绝对路径 或 相对 base 的路径。返回相对路径列表。"""
    out = []
    with open(listfile, encoding="utf-8") as fh:
        for line in fh:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            out.append(os.path.relpath(s, base) if os.path.isabs(s) else s)
    return out


def main():
    ap = argparse.ArgumentParser(
        description="全库素材批量 OCR（macOS Vision via pyobjc）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--dir", default=DEFAULT_BASE, help="素材根目录（默认灿爹图库）")
    ap.add_argument("--list", help="清单文件；给了就只扫清单，不递归目录")
    ap.add_argument("--match", help="只扫文件名匹配该 glob 的图，如 '产品_*'")
    ap.add_argument("--out", help="结果写文件（默认 stdout）")
    ap.add_argument("--include-underscore", action="store_true",
                    help="把 _通用池/_他品污染隔离 等下划线目录也纳入（默认跳过）")
    ap.add_argument("--progress", type=int, default=20, help="每 N 张报一次进度（默认 20）")
    args = ap.parse_args()

    base = os.path.abspath(args.dir)
    if not os.path.isdir(base):
        sys.stderr.write(f"目录不存在：{base}\n")
        sys.exit(1)

    names = (load_list(args.list, base) if args.list
             else scan_dir(base, args.include_underscore, args.match))
    if not names:
        sys.stderr.write("没有找到任何图片。\n")
        sys.exit(1)

    sink = open(args.out, "w", encoding="utf-8") if args.out else sys.stdout
    sys.stderr.write(f"目标 {len(names)} 张（根目录 {base}）\n")
    try:
        for i, n in enumerate(names, 1):
            full = os.path.join(base, n)
            if not os.path.exists(full):
                sink.write(f"{n}\t[MISSING]\n")
            else:
                txt = ocr_image(full)
                if txt is None:
                    sink.write(f"{n}\t[FAIL]\n")
                else:
                    joined = " | ".join(t.strip() for t in txt if t.strip())
                    sink.write(f"{n}\t{joined if joined else '[无文字]'}\n")
            if i % max(1, args.progress) == 0:
                sys.stderr.write(f"--- {i}/{len(names)} ---\n")
            sink.flush()
    finally:
        if args.out:
            sink.close()
        sys.stderr.write(f"DONE {len(names)}\n")


if __name__ == "__main__":
    main()
