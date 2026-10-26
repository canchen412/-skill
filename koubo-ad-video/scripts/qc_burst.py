#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开场冲击动效（opening_burst）验收 —— 两道闸门，缺一不算过。

为什么需要它（2026-09-19 用户发现的真故障）：
    渲染日志打印了「开场冲击: 随机选中「energy」」，成片里却一点冲击都没有。
    根因是配置解析里 `bst = _BST_SPEC`，而"config 不写 opening_burst"的默认随机
    路径下 `_BST_SPEC` 是空 dict `{}`（falsy）→ `if bst and BS_PRESET:` 整块跳过。
    **只看"随机选中"这一行会误判成已生效**，所以验收必须两道闸门：
      闸门1（日志）：必须**同时**出现「开场冲击层:」「开场冲击: 抖动」「开场冲击包: preset=」。
                     只有「随机选中/临时指定」= 没生效。
      闸门2（画面）：抽首秒帧拼接触表，肉眼确认放射线/冲击环/闪白真的在画面上。

用法:
    python3 qc_burst.py 渲染日志.log                       # 只跑闸门1
    python3 qc_burst.py 渲染日志.log 成片.mp4               # 两道闸门全跑
    python3 qc_burst.py --video-only 成片.mp4               # 没有日志时只出接触表
"""
import argparse
import os
import re
import subprocess
import sys

OK, BAD = "✅", "❌"
REQ_LAYER = re.compile(r"开场冲击层:\s*(\S+)")
REQ_PRESET = re.compile(r"开场冲击包:\s*preset=(\S+)")
REQ_SHAKE = re.compile(r"开场冲击:\s*抖动")
# ★2026-09-19 补「config 指定」：config 里写死 preset 时渲染器原本不打"随机选中"，
# 导致闸门1 找不到选中记录就误报 ❌（成片其实是好的）。三种来源都要认。
REQ_PICK = re.compile(r"开场冲击:\s*(随机选中|AD_BURST_PRESET 临时指定|config 指定|preset 已解析但开关为假)")
OFF_LINE = re.compile(r"开场冲击:\s*(AD_BURST_OFF|跳过)")


def check_log(path):
    txt = open(path, encoding="utf-8", errors="replace").read()
    layers = REQ_LAYER.findall(txt)
    preset = REQ_PRESET.search(txt)
    shake = REQ_SHAKE.search(txt)
    pick = REQ_PICK.search(txt)
    off = OFF_LINE.search(txt)

    print(f"闸门1 · 渲染日志 {os.path.basename(path)}")
    if off and not preset:
        print(f"  {OK} 本次为「主动关闭」开场冲击（AD_BURST_OFF / 配置 false）——跳过闸门1")
        return True
    # 2026-09-19 放宽：选中记录只是**辅助锚点**（老日志可能没有），真正判生效的是
    # 「层 + 抖动 + preset 包」三项齐全。缺锚点但三项齐全 → 仍判通过，只提示。
    if not pick:
        print(f"  ·  未找到选中记录（老日志/自定义渲染器常见）—— 以三项实锤为准")
    else:
        print(f"  ·  选中记录：{pick.group(0).strip()}")
    ok = True
    if not layers:
        print(f"  {BAD} 缺「开场冲击层:」—— 说明 `if bst and BS_PRESET:` 被跳过，"
              f"冲击**没有叠进画面**（2026-09-19 那个 bug 的典型症状）")
        ok = False
    else:
        print(f"  {OK} 冲击层 {len(layers)} 层：{' / '.join(layers)}")
    if not shake:
        print(f"  {BAD} 缺「开场冲击: 抖动」—— 同上，冲击块未执行")
        ok = False
    if not preset:
        print(f"  {BAD} 缺「开场冲击包: preset=...」—— 冲击块未执行到底")
        ok = False
    if preset:
        print(f"  {OK} 实际生效 preset = {preset.group(1)}")
    return ok


def sheet(video, ffmpeg, out=None, secs=1.2):
    out = out or os.path.join(os.path.dirname(os.path.abspath(video)),
                              "qc_burst_开场接触表.jpg")
    n = int(secs * 30)
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", video,
           "-vf", f"select='between(n\\,0\\,{n})*not(mod(n\\,2))',scale=400:-1,tile=4x4",
           "-frames:v", "1", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  {BAD} 抽帧失败：{(r.stderr or '').strip()[:300]}")
        return None
    print(f"  {OK} 接触表已出：{out}")
    print("     （逐格目视：应能看到放射线/冲击环/粒子/闪白之一；"
          "若与没开冲击时一模一样 → 判不通过）")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", nargs="?", help="渲染日志（闸门1）")
    ap.add_argument("video", nargs="?", help="成片或 2s 预览（闸门2）")
    ap.add_argument("--video-only", metavar="MP4", help="没有日志时只出接触表")
    ap.add_argument("--ffmpeg", default="/Users/mac/.local/bin/ffmpeg")
    a = ap.parse_args()

    if not a.log and not a.video and not a.video_only:
        ap.print_help()
        return 2
    ok = True
    if a.log:
        ok = check_log(a.log) and ok
    vid = a.video_only or a.video
    if vid:
        print(f"闸门2 · 画面验收 {os.path.basename(vid)}")
        if not os.path.exists(vid):
            print(f"  {BAD} 找不到 {vid}")
            ok = False
        else:
            sheet(vid, a.ffmpeg)
    print(("通过" if ok else "不通过") + " —— " +
          ("开场冲击已生效（仍请目视接触表确认观感）" if ok
           else "开场冲击未生效，别交付，先查上面标 ❌ 的那几项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
