#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fork 双进程 TTS 并行实测：模型只载一次，子进程 COW 共享权重"""
import importlib.util, json, os, sys, time
CC = "/Users/mac/.workbuddy/skills/koubo-ad-video/scripts/cosyvoice_clone.py"
spec = importlib.util.spec_from_file_location("cosyvoice_clone", CC)
cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
torch, torchaudio = cc.torch, cc.torchaudio
torch.set_num_threads(4)   # ★fork 并行下每进程限 4 线程（8核分两半），避免争抢
SR = None

SENTS = [
 "练了两年才发现，健身到最后拼的其实是建模。",
 "我自己就是现成的例子，一周六练从没断过。",
 "餐桌上顿顿围着碳水和蛋白质转。",
 "果蔬那一格常年空着。",
]
OUT = "/tmp/fork_tts"
os.makedirs(OUT, exist_ok=True)

prompt = open(cc.DEFAULT_PROMPT_FILE, encoding="utf-8").read().strip()
model_dir = cc.resolve_model(cc.DEFAULT_MODEL)
if "CosyVoice3" in model_dir and not prompt.startswith("You are a helpful assistant"):
    prompt = cc.CV3_PREFIX + prompt

def synth_one(model, text, out_wav):
    gen = model.inference_zero_shot(text, prompt, cc.DEFAULT_SPEAKER, stream=False, speed=1.2)
    piece = [j["tts_speech"].cpu() for j in gen]
    audio = torch.cat(piece, dim=1)
    torchaudio.save(out_wav, audio, SR)
    return audio.shape[1] / SR

def run_group(model, idxs, tag, t_start):
    for i in idxs:
        t1 = time.time()
        d = synth_one(model, SENTS[i], f"{OUT}/{tag}_s{i}.wav")
        print(f"  [{tag}] 句{i+1} {time.time()-t1:.0f}s ({d:.1f}s音频)", flush=True)
    print(f"[{tag}] 全组完成 {time.time()-t_start:.0f}s", flush=True)

print("== 模型加载…", flush=True)
t0 = time.time()
model = cc.AutoModel(model_dir=model_dir)
SR = model.sample_rate
print(f"== 模型加载完成 {time.time()-t0:.0f}s\n", flush=True)

# ── 基线：单进程 4 句串行（8 线程） ──
torch.set_num_threads(8)
print("== 基线：单进程串行 4 句", flush=True)
tb = time.time()
run_group(model, [0,1,2,3], "base", tb)
BASE = time.time() - tb

# ── fork 双进程：2+2（每进程 4 线程） ──
print("\n== fork 双进程 2+2", flush=True)
tp = time.time()
kids = []
for tag, idxs in [("pA", [0,1]), ("pB", [2,3])]:
    pid = os.fork()
    if pid == 0:
        t_start = time.time()
        try:
            run_group(model, idxs, tag, t_start)
        finally:
            os._exit(0)
    kids.append(pid)
for pid in kids: os.waitpid(pid, 0)
PAR = time.time() - tp

print(f"""
════════ 结果 ════════
单进程串行 4 句 : {BASE:.0f}s
fork 双进程 2+2 : {PAR:.0f}s
加速比          : {BASE/PAR:.2f}x
""", flush=True)
