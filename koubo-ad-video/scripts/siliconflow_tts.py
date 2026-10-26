#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SiliconFlow 云 TTS 适配（IndexTTS-2 / CosyVoice2，OpenAI 兼容接口）
用法:
  首次: python3 siliconflow_tts.py key sk-xxxx          # 保存 key
  建音色: python3 siliconflow_tts.py voice              # 上传 speaker_sample.wav 建克隆音色
  合成: python3 siliconflow_tts.py synth "文本" out.mp3 [speed]   # speed 默认 1.2
key 存 ~/.workbuddy/siliconflow.json (600)
"""
import json, os, sys, urllib.request, uuid, wave, shutil, tempfile

BASE = "https://api.siliconflow.cn"
CFG = os.path.expanduser("~/.workbuddy/siliconflow.json")
SAMPLE = "/Users/mac/.workbuddy/skills/koubo-ad-video/assets/voice/speaker_sample.wav"
STATE = os.path.expanduser("~/.workbuddy/siliconflow_voice.json")
# IndexTTS-2：中文克隆口碑最好；CosyVoice2 备选（与本地同系）
MODELS = ["IndexTeam/IndexTTS-2", "FunAudioLLM/CosyVoice2-0.5B"]

def key():
    if os.path.exists(CFG):
        return json.load(open(CFG))["key"]
    sys.exit("✗ 未保存 key，先跑: python3 siliconflow_tts.py key sk-xxxx")

def http(url, data=None, headers=None, files=None, method=None):
    h = dict(headers or {})
    if files:
        boundary = uuid.uuid4().hex
        body = b""
        for k, v in (data or {}).items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
        for k, (fn, blob, ct) in files.items():
            body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"; filename=\"{fn}\"\r\nContent-Type: {ct}\r\n\r\n".encode() + blob + b"\r\n"
        body += f"--{boundary}--\r\n".encode()
        h["Content-Type"] = f"multipart/form-data; boundary={boundary}"
        req = urllib.request.Request(url, data=body, headers=h, method=method or "POST")
    else:
        h.setdefault("Content-Type", "application/json")
        req = urllib.request.Request(url, data=json.dumps(data).encode() if data else None,
                                     headers=h, method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=120) as r:
        ct = r.headers.get("Content-Type", "")
        blob = r.read()
        return (json.loads(blob) if "json" in ct else blob), r.status

def cmd_key(k):
    os.makedirs(os.path.dirname(CFG), exist_ok=True)
    json.dump({"key": k}, open(CFG, "w"))
    os.chmod(CFG, 0o600)
    print("✓ key 已保存")

def cmd_voice():
    auth = {"Authorization": f"Bearer {key()}"}
    blob = open(SAMPLE, "rb").read()
    # ★text=参考音频的转录文本必带——不带时上传 200 但合成恒 500（实测 00:14）
    prompt_file = os.path.join(os.path.dirname(SAMPLE), "prompt.txt")
    trans = open(prompt_file, encoding="utf-8").read().strip() if os.path.exists(prompt_file) else ""
    results = {}
    for model in MODELS:
        name = "chandie" + uuid.uuid4().hex[:6] + ("_idx" if "Index" in model else "_cv2")
        try:
            resp, code = http(f"{BASE}/v1/uploads/audio/voice",
                              data={"model": model, "customName": name, "text": trans},
                              headers=auth,
                              files={"file": ("sample.wav", blob, "audio/wav")})
            vid = resp.get("voice_id") or (resp.get("data") or {}).get("voice_id")
            print(f"✓ {model} → {vid}")
            results[model] = {"voice_id": vid, "name": name}
        except urllib.error.HTTPError as e:
            print(f"✗ {model}: {e.code} {e.read()[:200]}")
    json.dump(results, open(STATE, "w"), ensure_ascii=False, indent=1)
    print(f"✓ 音色登记 → {STATE}")

def cmd_synth(text, out, speed=1.2, model=None):
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    model = model or (MODELS[0] if MODELS[0] in state else MODELS[-1])
    if model not in state:
        sys.exit("✗ 还没建音色，先跑: python3 siliconflow_tts.py voice")
    payload = {"model": model, "input": text, "voice": state[model]["voice_id"],
               "response_format": "mp3", "speed": speed, "sample_rate": 44100}
    blob, code = http(f"{BASE}/v1/audio/speech", data=payload,
                      headers={"Authorization": f"Bearer {key()}", "User-Agent": "Mozilla/5.0"})
    open(out, "wb").write(blob)
    print(f"✓ {out}  {len(blob)/1024:.0f}KB  model={model}  speed={speed}")

if __name__ == "__main__":
    a = sys.argv[1:]
    if a and a[0] == "key":
        cmd_key(a[1])
    elif a and a[0] == "voice":
        cmd_voice()
    elif a and a[0] == "synth":
        cmd_synth(a[1], a[2], float(a[3]) if len(a) > 3 else 1.2,
                  a[4] if len(a) > 4 else None)
    else:
        print(__doc__)
