#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★双进程并行批量配音（2026-10-09 定型，替代 cosyvoice_batch.py 的串行）
─────────────────────────────────────────────────────────────────
原理：模型只加载一次 → fork 两个子进程 → 写时复制(COW)共享权重内存
      → 各自合成一半句子 → 父进程 wait 汇总。
实测（M2 8GB / 4+4 线程）：4 句 186s → 87s，加速 2.13x；内存无 swap 灾难。

产物与 cosyvoice_batch.py 完全兼容（seg_NN.wav + segments.json），
reconcat / mk_tokens 等下游脚本零改动。

用法（与旧脚本同参）:
  python3 run_tts_parallel.py --jobs jobs.json [--speed 1.2] [--seg-gap 0.22]
"""
import argparse
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SINGLE = os.path.join(HERE, "cosyvoice_clone.py")
_spec = importlib.util.spec_from_file_location("cosyvoice_clone", SINGLE)
cc = importlib.util.module_from_spec(_spec)
sys.modules["cosyvoice_clone"] = cc
_spec.loader.exec_module(cc)
torch, torchaudio = cc.torch, cc.torchaudio


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", required=True)
    ap.add_argument("--model-dir", default=cc.DEFAULT_MODEL)
    ap.add_argument("--speaker", default=cc.DEFAULT_SPEAKER)
    ap.add_argument("--prompt-text", default="")
    ap.add_argument("--mode", default="zero_shot", choices=["zero_shot", "cross_lingual"])
    ap.add_argument("--speed", type=float, default=cc.DEFAULT_SPEED)
    ap.add_argument("--seg-gap", type=float, default=0.22)
    ap.add_argument("--workers", type=int, default=2, help="fork 进程数（默认 2）")
    a = ap.parse_args()

    jobs = json.load(open(a.jobs, encoding="utf-8"))
    model_dir = cc.resolve_model(a.model_dir)
    is_cv3 = "CosyVoice3" in model_dir

    prompt = a.prompt_text
    if not prompt and os.path.exists(cc.DEFAULT_PROMPT_FILE):
        prompt = open(cc.DEFAULT_PROMPT_FILE, encoding="utf-8").read().strip()
    if is_cv3 and prompt and not prompt.startswith("You are a helpful assistant"):
        prompt = cc.CV3_PREFIX + prompt

    print(f"[par] model={model_dir} speed={a.speed} jobs={len(jobs)} workers={a.workers}", flush=True)
    t0 = time.time()
    model = cc.AutoModel(model_dir=model_dir)
    sr = model.sample_rate
    print(f"[par] 模型加载 {time.time()-t0:.0f}s sr={sr}", flush=True)

    # ── 1. 展平：收集全部 (seg_dir, seq, text) 任务单元 ──
    tasks = []          # [(seg_dir, seq, text)]
    meta = []           # 每个 job 的 segments.json 数据
    for ji, job in enumerate(jobs, 1):
        name = job.get("name", f"job{ji}")
        text = job.get("text") or open(job["text_file"], encoding="utf-8").read()
        sents = cc.split_sentences(text)
        seg_dir = job.get("seg_dir", "")
        if seg_dir:
            os.makedirs(seg_dir, exist_ok=True)
            with open(os.path.join(seg_dir, "segments.json"), "w", encoding="utf-8") as f:
                json.dump({"seg_gap": a.seg_gap, "speed": a.speed,
                           "segments": [{"i": i, "text": s} for i, s in enumerate(sents, 1)]},
                          f, ensure_ascii=False, indent=1)
        for i, s in enumerate(sents, 1):
            tasks.append((seg_dir, i, s))
        meta.append((name, len(sents)))
    print(f"[par] 共 {len(tasks)} 句", flush=True)

    # ── 2. 平衡拆分到 N 个队列（轮流分配，长句自然摊平） ──
    queues = [[] for _ in range(a.workers)]
    for t in tasks:
        queues[min(range(a.workers), key=lambda k: len(queues[k]))].append(t)

    # ── 3. fork 子进程合成 ──
    os.environ["TOKENIZERS_PARALLELISM"] = "false"
    kids = []
    tp = time.time()
    for wid, queue in enumerate(queues):
        pid = os.fork()
        if pid == 0:
            try:
                for seg_dir, i, s in queue:
                    out = os.path.join(seg_dir, f"seg_{i:02d}.wav") if seg_dir else f"/tmp/par_{wid}_{i}.wav"
                    gen = model.inference_zero_shot(s, prompt, a.speaker or cc.DEFAULT_SPEAKER,
                                                    stream=False, speed=a.speed)
                    piece = [j["tts_speech"].cpu() for j in gen]
                    audio = torch.cat(piece, dim=1)
                    torchaudio.save(out, audio, sr)
                    print(f"  [w{wid}] seg{i:02d} {audio.shape[1]/sr:.1f}s ← {s[:14]}", flush=True)
            finally:
                os._exit(0)
        kids.append(pid)
    for pid in kids:
        os.waitpid(pid, 0)
    print(f"[par] 全部合成完成 {time.time()-tp:.0f}s（串行预估 {int((time.time()-tp)*a.workers)}s）", flush=True)
    for name, n in meta:
        print(f"[par] job「{name}」{n} 句 ✓", flush=True)


import time  # noqa: E402  （fork 前无需 time，放底部避免子进程重复 import 开销）

if __name__ == "__main__":
    main()
