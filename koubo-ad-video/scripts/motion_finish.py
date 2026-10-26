#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""motion_finish.py —— 成片「收尾工序」：在原片与「原片+粒子+隔镜对焦」之间**随机交替**

★灿爹铁律（2026-10-01 拍板）：
  渲染器出来的成片，交付前必跑本工序，随机二选一：
    A 模式 = 原片（不加任何东西，渲染器输出即为交付）
    B 模式 = **原片 100% 保留**，只额外叠加「漂浮粒子 + 隔镜对焦呼吸」
  目的 = 让系列片在"干净"与"有呼吸感"之间自然轮换，不扎堆同一种观感。
  ★B 模式不是重渲 —— 它不动原片任何一帧内容（字幕/音效/重点字/开场冲击全保留），
    只是后加两层效果，所以没有"重画丢原味"的风险。

随机规则：以 62% 概率切换到另一种模式、38% 保持 —— 形成"随机交替"而不长期扎堆。

用法：
  $PY motion_finish.py --src 渲染输出.mp4 --out 成片.mp4
        [--table 对应表.html]         # 有则 B 模式的隔镜对焦能精确落在镜边界
        [--focus-every 2]             # 每 N 镜做一次对焦呼吸
        [--force a|b]                 # 调试用，强制某一模式
        [--count 88] [--strength 1.9] [--size 1.5]
"""
from __future__ import annotations

import argparse
import json
import os
import random
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "..", "assets", "_motion_mode_last.json")
STATE = os.path.abspath(STATE)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def pick(force: str | None, last: str | None) -> str:
    if force in ("a", "b", "A", "B"):
        return force.upper()
    if last not in ("A", "B"):
        return random.choice(["A", "B"])
    switch = random.random() < 0.62
    return ({"A": "B", "B": "A"}[last] if switch else last)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True, help="渲染器输出的原片")
    ap.add_argument("--out", required=True, help="交付路径")
    ap.add_argument("--table", default=None, help="《分镜—文案—素材对应表》HTML")
    ap.add_argument("--focus-every", type=int, default=2)
    ap.add_argument("--focus-sec", type=float, default=0.45)
    ap.add_argument("--count", type=int, default=88)
    ap.add_argument("--strength", type=float, default=1.9)
    ap.add_argument("--size", type=float, default=1.5)
    ap.add_argument("--force", default=None, choices=["a", "A", "b", "B"])
    a = ap.parse_args()

    st = load_state()
    last = st.get("last")
    mode = pick(a.force, last)

    if mode == "A":
        if os.path.abspath(a.src) != os.path.abspath(a.out):
            shutil.copy2(a.src, a.out)
        note = "原片（渲染器输出直接交付）"
        print(f"🎲 模式 A（原片）｜上次 = {last or '无'}")
    else:
        cmd = [sys.executable, os.path.join(HERE, "add_dust_to_video.py"),
               "--src", a.src, "--out", a.out,
               "--count", str(a.count), "--strength", str(a.strength), "--size", str(a.size)]
        if a.table:
            cmd += ["--table", a.table, "--focus-every", str(a.focus_every),
                    "--focus-sec", str(a.focus_sec)]
        print(f"🎲 模式 B（原片 + 漂浮粒子 + 隔镜对焦呼吸）｜上次 = {last or '无'}")
        subprocess.run(cmd, check=True)
        note = f"原片 100% 保留 + 粒子({a.count}颗) + 隔镜对焦(每{a.focus_every}镜)"

    st["last"] = mode
    st["history"] = (st.get("history") or [])[-19:] + [
        {"t": time.strftime("%Y-%m-%d %H:%M"), "mode": mode, "out": os.path.basename(a.out), "note": note}]
    save_state(st)
    print(f"✅ {a.out}\n   模式 {mode}：{note}")


if __name__ == "__main__":
    main()
