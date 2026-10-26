#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
批量配音：一次加载模型，顺序合成多条文案（★2026-09-23 用户"一次给多条文案"场景）

为什么需要：
  单条跑 cosyvoice_clone.py 时，每条都要重新 AutoModel() 一次（实测 ≈95s 纯加载）。
  两条片 = 白等 95s；而且本机 8GB 内存**不能起两个进程**（会 swap，反而更慢）。
  所以正确做法是「一个进程、加载一次、顺序合成 N 条」——本机唯一能省加载成本的姿势。

音色与单条脚本完全一致（同一个模型/样本/语速/分句/段间 gap），产物可直接进渲染流程。

用法:
  # jobs.json: [{"name":"条A","text_file":"a.txt","out":"a.mp3","seg_dir":"seg_a"}, ...]
  python3 cosyvoice_batch.py --jobs jobs.json
  python3 cosyvoice_batch.py --jobs jobs.json --speed 1.2 --only 3   # 只重跑第 3 个 job（1 起始）
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SINGLE = os.path.join(HERE, "cosyvoice_clone.py")

# 复用单条脚本：它把 sys.path / 设备强制 CPU / 常量(FF, DEFAULT_SPEAKER, CV3_PREFIX…)都设好了，
# 且 main() 在 __main__ 保护下不会执行，import 它是安全且零重复代码的。
_spec = importlib.util.spec_from_file_location("cosyvoice_clone", SINGLE)
cc = importlib.util.module_from_spec(_spec)
sys.modules["cosyvoice_clone"] = cc
_spec.loader.exec_module(cc)

torch = cc.torch
torchaudio = cc.torchaudio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True, help="jobs.json 路径")
    ap.add_argument("--model-dir", default=cc.DEFAULT_MODEL)
    ap.add_argument("--speaker", default=cc.DEFAULT_SPEAKER)
    ap.add_argument("--prompt-text", default="")
    ap.add_argument("--mode", default="zero_shot", choices=["zero_shot", "cross_lingual"])
    ap.add_argument("--speed", type=float, default=cc.DEFAULT_SPEED)
    ap.add_argument("--seg-gap", type=float, default=0.22)
    ap.add_argument("--only", default="", help="只跑指定 job 序号（1 起始，逗号分隔）")
    a = ap.parse_args()

    jobs = json.load(open(a.jobs, encoding="utf-8"))
    only = {int(x) for x in re.split(r"[,\s]+", a.only.strip()) if x} if a.only else set()

    model_dir = cc.resolve_model(a.model_dir)
    is_cv3 = "CosyVoice3" in model_dir
    print(f"[batch] model={model_dir} cv3={is_cv3} speed={a.speed} jobs={len(jobs)}", flush=True)

    model = cc.AutoModel(model_dir=model_dir)     # ★只加载这一次
    sr = model.sample_rate
    print(f"[batch] sample_rate={sr}", flush=True)

    prompt = a.prompt_text
    if not prompt and os.path.exists(cc.DEFAULT_PROMPT_FILE):
        prompt = open(cc.DEFAULT_PROMPT_FILE, encoding="utf-8").read().strip()
    if is_cv3 and prompt and not prompt.startswith("You are a helpful assistant"):
        prompt = cc.CV3_PREFIX + prompt

    for ji, job in enumerate(jobs, 1):
        if only and ji not in only:
            print(f"[batch] skip job {ji} ({job.get('name', '')})", flush=True)
            continue
        name = job.get("name", f"job{ji}")
        text = job.get("text") or open(job["text_file"], encoding="utf-8").read()
        sents = cc.split_sentences(text)
        print(f"\n[batch] ==== job {ji}/{len(jobs)} 「{name}」 "
              f"{len(sents)} 句 / {len(text)} 字 ====", flush=True)

        seg_dir = job.get("seg_dir", "")
        if seg_dir:
            os.makedirs(seg_dir, exist_ok=True)
            with open(os.path.join(seg_dir, "segments.json"), "w", encoding="utf-8") as f:
                json.dump({"seg_gap": a.seg_gap, "speed": a.speed,
                           "segments": [{"i": i, "text": s} for i, s in enumerate(sents, 1)]},
                          f, ensure_ascii=False, indent=1)

        chunks = []
        for i, s in enumerate(sents, 1):
            seg_path = os.path.join(seg_dir, f"seg_{i:02d}.wav") if seg_dir else None
            print(f"  [{i}/{len(sents)}] {s}", flush=True)
            if i > 1 and a.seg_gap > 0:
                chunks.append(torch.zeros(1, int(sr * a.seg_gap), dtype=torch.float32))
            if a.mode == "zero_shot":
                gen = model.inference_zero_shot(s, prompt, a.speaker, stream=False, speed=a.speed)
            else:
                t = (cc.CV3_PREFIX + s) if is_cv3 else s
                gen = model.inference_cross_lingual(t, a.speaker, stream=False, speed=a.speed)
            piece = [j["tts_speech"].cpu() for j in gen]
            if not piece:
                raise SystemExit(f"job {ji} 第 {i} 段没有生成音频")
            seg_audio = torch.cat(piece, dim=1)
            if seg_path:
                torchaudio.save(seg_path, seg_audio, sr)
                print(f"       → {os.path.basename(seg_path)} "
                      f"({seg_audio.shape[1] / sr:.2f}s)", flush=True)
            chunks.append(seg_audio)

        audio = torch.cat(chunks, dim=1)
        wav = os.path.splitext(job["out"])[0] + ".wav"
        torchaudio.save(wav, audio, sr)
        print(f"[batch] wav -> {wav} ({audio.shape[1] / sr:.2f}s)", flush=True)
        subprocess.run([cc.FF, "-y", "-loglevel", "error", "-i", wav,
                        "-ar", "24000", "-ac", "1", "-b:a", "192k", job["out"]], check=True)
        print(f"[batch] mp3 -> {job['out']}", flush=True)

    print("\n[batch] 全部完成", flush=True)


if __name__ == "__main__":
    main()
