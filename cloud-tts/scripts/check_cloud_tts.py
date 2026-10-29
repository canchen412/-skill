#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""云端配音一键体检 —— 出片前/Key 轮换后跑一次，10 秒给结论。

检查三件事（任一项 FAIL 就非零退出，可直接接进脚本当闸门）：
  1. 配置齐不齐  ~/.workbuddy/secrets/dashscope.env 是否含 Key/模型/音色/语速
  2. 脚本齐不齐  cloud_cosyvoice_ws.py + mk_tokens_cloud.py 是否在位
  3. 真的能合成  **实打实跑一次短句合成**（不是 ping），验 Key 有效 + 音色可用 + 时间戳通路通

用法：
    python3 check_cloud_tts.py                # 默认拿音色 ID 当文本，合成一句
    python3 check_cloud_tts.py --text "今天这条视频"
    python3 check_cloud_tts.py --quiet        # 只打结论行

为什么要真跑：百炼的 Key 失效/额度耗尽/音色被回收，在「读配置」阶段全都看不出来，
只有真发一次合成请求才暴露 —— 而这三件事恰好是出片中途最容易翻车的点。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SECRETS = os.path.expanduser("~/.workbuddy/secrets/dashscope.env")
PY = sys.executable
WS = os.path.join(HERE, "cloud_cosyvoice_ws.py")
MK = os.path.join(HERE, "mk_tokens_cloud.py")

OK, BAD, WARN = "✓", "✗", "!"


def load_env(path=SECRETS):
    got = {}
    if not os.path.exists(path):
        return got
    for ln in open(path, encoding="utf-8"):
        ln = ln.strip()
        if not ln or ln.startswith("#"):
            continue
        ln = re.sub(r"^export\s+", "", ln)
        if "=" not in ln:
            continue
        k, v = ln.split("=", 1)
        got[k.strip()] = v.strip().strip('"').strip("'")
    return got


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="", help="体检用的短句（默认用音色 ID 名，够长能触发时间戳）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    def p(line=""):
        if not a.quiet:
            print(line, flush=True)

    p("══ 云端配音体检（cloud-tts）")
    fails = []

    # ── 1. 配置 ──
    env = load_env()
    if not os.path.exists(SECRETS):
        p(f"  {BAD} 1/3 配置：找不到 {SECRETS}")
        p(f"      → 建这个文件，至少写：\n"
          f"        export DASHSCOPE_API_KEY='sk-...'\n"
          f"        export COSYVOICE_MODEL='cosyvoice-v3.5-plus'\n"
          f"        export COSYVOICE_VOICE_ID='cosyvoice-v3.5-plus-...'\n"
          f"        export COSYVOICE_RATE='1.1'")
        fails.append("配置")
    else:
        key = env.get("DASHSCOPE_API_KEY") or os.environ.get("DASHSCOPE_API_KEY", "")
        voice = env.get("COSYVOICE_VOICE_ID") or os.environ.get("COSYVOICE_VOICE_ID", "")
        model = env.get("COSYVOICE_MODEL") or os.environ.get("COSYVOICE_MODEL", "")
        rate = env.get("COSYVOICE_RATE") or os.environ.get("COSYVOICE_RATE", "")
        if not key:
            p(f"  {BAD} 1/3 配置：{SECRETS} 里没有 DASHSCOPE_API_KEY")
            fails.append("配置")
        else:
            p(f"  {OK} 1/3 配置：Key={key[:12]}…{key[-6:]}（{len(key)} 字符）  "
              f"model={model or '(缺，将回退脚本默认)'}  rate={rate or '(缺)'}")
            if not voice:
                p(f"      {WARN} 没设 COSYVOICE_VOICE_ID —— 会退回脚本内置默认音色，"
                  f"出片音色可能不是你要的那条")
            # ★必须把 Key 也灌进 os.environ：第 3 步的子进程靠继承环境拿 Key
            for k in ("DASHSCOPE_API_KEY", "COSYVOICE_MODEL",
                      "COSYVOICE_VOICE_ID", "COSYVOICE_RATE"):
                if env.get(k):
                    os.environ.setdefault(k, env[k])

    # ── 2. 脚本 ──
    missing = [p_ for p_ in (WS, MK) if not os.path.exists(p_)]
    if missing:
        p(f"  {BAD} 2/3 脚本：缺 " + "、".join(missing))
        fails.append("脚本")
    else:
        p(f"  {OK} 2/3 脚本：cloud_cosyvoice_ws.py + mk_tokens_cloud.py 均在位")

    # ── 3. 真跑一次合成 ──
    if "配置" in fails or "脚本" in fails:
        p(f"  {WARN} 3/3 跳过实跑（前置没过）")
    else:
        text = a.text or "今天这条视频，我们只讲一件事，先算账。"
        p(f"  ·  3/3 实跑合成：{text[:24]}…（{len(text)} 字）")
        with tempfile.TemporaryDirectory() as td:
            tf = os.path.join(td, "t.txt")
            open(tf, "w", encoding="utf-8").write(text)
            t0 = time.time()
            r = subprocess.run([PY, WS, "--text-file", tf, "--out", os.path.join(td, "vo")],
                               capture_output=True, text=True)
            dt = time.time() - t0
            if r.returncode != 0:
                p(f"  {BAD} 3/3 合成失败（{dt:.1f}s）—— 最常见三种原因：")
                p(f"      ① Key 失效/被轮换　② 百炼未开通或欠费　③ 音色 ID 被回收（1 年未用会自动删）")
                p("      " + (r.stderr or r.stdout or "").strip().replace("\n", "\n      ")[-600:])
                fails.append("合成")
            else:
                wav = os.path.join(td, "vo.wav")
                wj = os.path.join(td, "vo.words.json")
                nbytes = os.path.getsize(wav) if os.path.exists(wav) else 0
                nwords = 0
                if os.path.exists(wj):
                    nwords = len(json.load(open(wj, encoding="utf-8"))["words"])
                if nbytes < 1000 or nwords == 0:
                    p(f"  {BAD} 3/3 合成异常：音频 {nbytes} 字节 / 时间戳 {nwords} 词（{dt:.1f}s）")
                    fails.append("合成")
                else:
                    secs = (nbytes - 44) / 2 / 24000
                    p(f"  {OK} 3/3 合成成功（{dt:.1f}s）：音频 {secs:.2f}s / {nbytes} 字节，"
                      f"逐字时间戳 {nwords} 词")

    p()
    if fails:
        p(f"结论：❌ 不可用 —— {'、'.join(fails)} 环节没过。出片会中止（默认 --tts-engine cloud）。")
        return 1
    p("结论：✅ 云端配音可用。出片直接用默认参数即可，无需任何手动步骤。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
