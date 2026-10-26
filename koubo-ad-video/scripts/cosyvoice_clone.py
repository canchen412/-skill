"""CosyVoice 3/2 本地零样本克隆配音（koubo-ad-video 技能）。

★ 用户拍板默认（2026-09-18 定 1.1×，2026-09-19 用户对比 1.1/1.2/1.25 后拍板升 **1.2×**）：
   **没给配音时直接用 CosyVoice3 + 固定声音样本 + 语速 1.2 倍**。
   所以最短调用就是一行的：只需给 --text-file（或 --text），其余全部有默认值：
       <venv>/bin/python scripts/cosyvoice_clone.py --text-file 文案.txt --out 配音.mp3

   默认值：
     model  = Fun-CosyVoice3-0.5B（Apache 2.0 可商用；音色相似度最高，CPU 约 24x rtf）
     speaker= assets/voice/speaker_sample.wav（用户声音样本，11.6s 单声道 22.05k）
     prompt = assets/voice/prompt.txt（该样本音的 whisper 转写，zero_shot 模式必填）
     speed  = 1.2（用户 2026-09-19 拍板；CosyVoice 内部对 mel 帧做线性插值，音高不变、吐字更利落）

环境（已从 /tmp 迁到持久目录，重启不丢）：
    repo  : /Users/mac/.workbuddy/models/cosyvoice/CosyVoice
    python: /Users/mac/.workbuddy/models/cosyvoice/venv/bin/python   （Python 3.10 + torch 2.2.2）
    模型  : <repo>/pretrained_models/{Fun-CosyVoice3-0.5B, CosyVoice2-0.5B}

依赖提示：只能用该 venv 的 python 跑，且**不要**用 /tmp 里的旧路径。
"""
import sys, os, re, json, argparse, subprocess

# 2026-09-23 arm64 迁移实测：Mac 上不给这个变量，CosyVoice3 加载 MPS 设备时会
# 在 unsupported op 上**静默崩溃**（无 traceback，日志停在 "use wetext frontend"）；
# 加上后不支持的算子自动回退 CPU。MPS 生效时 rtf≈7~14，纯 CPU rtf 可达 1800+（8GB 机器）。
os.environ.setdefault("PYTORCH_ENABLE_MPS_FALLBACK", "1")

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
DEFAULT_ROOT = "/Users/mac/.workbuddy/models/cosyvoice/CosyVoice"

# CosyVoice 仓库根目录（含 third_party/Matcha-TTS）。可用环境变量 COSYVOICE_ROOT 覆盖。
COSY_ROOT = os.environ.get("COSYVOICE_ROOT", DEFAULT_ROOT)
sys.path.insert(0, COSY_ROOT)
sys.path.insert(0, os.path.join(COSY_ROOT, "third_party", "Matcha-TTS"))

import torch  # noqa: E402

# ★设备选择（2026-09-23 arm64 迁移）：CosyVoice 仓库的 model.py / frontend.py 里已打了 MPS 补丁
# （torch.backends.mps.is_available() 为真就走 mps）。但在 8G 统一内存的 M 系机器上实测：
#   - MPS 路径每步推理都要 host↔GPU 往返，且和别的进程抢统一内存 → 单句 40+ 分钟出不来；
#   - CPU 路径（旧机 Intel 上一直用的）稳定在 ~24x rtf，整条 230 字口播约 20~40 分钟跑完。
# 所以默认强制 CPU；确实想试 MPS 就 COSYVOICE_DEVICE=mps。
#
# ★2026-09-27 复测（内存空闲状态，诺特烟酰胺 4 条之后）：MPS 仍然不可用 ——
#   模型加载正常（46s），但推理首步即抛
#     TypeError: Cannot convert a float64 Tensor to MPS as the MPS framework doesn't support float64
#   （CosyVoice 内部存在 float64 张量，MPS 不支持）。同机同文本 CPU 实测：rtf 14.8~21.4、
#   段间隔 20~28s/段（内存充足时），模型加载 ~30s。**结论：本机 CPU 是唯一可用后端，不要再试 MPS。**
#   ⇒ 配音提速只能靠「保证内存独占、别让它掉到 300~760s/段」，见 SKILL.md 变更日志 #59。
_DEV = os.environ.get("COSYVOICE_DEVICE", "cpu").strip().lower()
if _DEV == "cpu":
    try:
        torch.backends.mps.is_available = lambda: False  # noqa: E731
    except Exception:
        pass
else:
    try:
        if not torch.backends.mps.is_available():
            print("[cosyvoice_clone] COSYVOICE_DEVICE=mps 但本机 MPS 不可用，将由代码自行回退", flush=True)
    except Exception:
        pass

import torchaudio  # noqa: E402
from cosyvoice.cli.cosyvoice import AutoModel  # noqa: E402

def _find_bin(name):
    """找 ffmpeg/ffprobe：env 覆盖 > PATH > 旧机写死路径"""
    env = os.environ.get("FFMPEG_BIN" if name == "ffmpeg" else "FFPROBE_BIN")
    if env and os.path.exists(env):
        return env
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = os.path.join(d, name)
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return f"/Users/mac/.local/bin/{name}"


FF = _find_bin("ffmpeg")
CV3_PREFIX = "You are a helpful assistant.<|endofprompt|>"

# 用户拍板的固定默认声音与语速
DEFAULT_MODEL = "Fun-CosyVoice3-0.5B"
DEFAULT_SPEAKER = os.path.join(SKILL_DIR, "assets", "voice", "speaker_sample.wav")
DEFAULT_PROMPT_FILE = os.path.join(SKILL_DIR, "assets", "voice", "prompt.txt")
DEFAULT_SPEED = 1.2

# 常用模型别名 -> 目录名
MODEL_ALIAS = {
    "3": "Fun-CosyVoice3-0.5B",
    "cv3": "Fun-CosyVoice3-0.5B",
    "cosyvoice3": "Fun-CosyVoice3-0.5B",
    "2": "CosyVoice2-0.5B",
    "cv2": "CosyVoice2-0.5B",
    "cosyvoice2": "CosyVoice2-0.5B",
}


def split_sentences(text):
    text = text.replace("\r", "").replace("\n", "")
    parts = re.split(r"(?<=[。！？!?；;])", text)
    return [p.strip() for p in parts if p.strip()]


def resolve_model(m):
    m = MODEL_ALIAS.get(str(m).strip().lower(), m)
    if os.path.isabs(m) or os.path.sep in m:
        return m
    return os.path.join(COSY_ROOT, "pretrained_models", m)


def main():
    ap = argparse.ArgumentParser(description="CosyVoice 3/2 zero-shot voice cloning")
    ap.add_argument("--model-dir", default=DEFAULT_MODEL,
                    help=f"模型目录/别名(2|3)；默认 {DEFAULT_MODEL}")
    ap.add_argument("--text-file", help="要合成的文案（txt，UTF-8）")
    ap.add_argument("--text", help="直接给文案字符串（与 --text-file 二选一）")
    ap.add_argument("--speaker", default=DEFAULT_SPEAKER,
                    help="样本音 wav（默认用技能内置的用户声音样本）")
    ap.add_argument("--prompt-text", default="",
                    help="样本音的文字转写（默认读 assets/voice/prompt.txt）")
    ap.add_argument("--mode", default="zero_shot", choices=["zero_shot", "cross_lingual"])
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                    help=f"语速，用户定稿 {DEFAULT_SPEED}")
    ap.add_argument("--seg-gap", type=float, default=0.22,
                    help="段间静音秒数（默认 0.22）。split_sentences 只按 。！？； 切段，"
                         "**段内逗号全靠模型预测、不可靠**（2026-09-19 实测：'想要薄肌，肌酸怎么选才不踩坑？' "
                         "整句零内部停顿，听成'想要薄肌肌酸，怎么选才不踩坑'）。"
                         "要停顿就把那个逗号在**合成文本里**升级成句号（字幕文本别改），"
                         "本参数就是拆段后补的静音——不补会贴死，比原来更糟")
    ap.add_argument("--seg-dir", default="",
                    help="★把每段单独存成 seg_01.wav… 并写 segments.json。分段落盘后可以"
                         "（a）用 qc_vo_pause.py 逐段质检、（b）只重 roll 坏的那段、（c）用"
                         " --rebuild-from 重新拼装——避免「一处坏、整条重来」（2026-09-19 新增）")
    ap.add_argument("--only", default="",
                    help="只合成指定段号（1 起，逗号分隔，如 7 或 7,9），其余段直接复用 --seg-dir 里"
                         "已有的 seg_XX.wav。用于重 roll 单段")
    ap.add_argument("--rebuild-from", default="",
                    help="不合成，直接把 <dir> 里的 seg_*.wav 按 --seg-gap 重新拼成 --out")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    if args.rebuild_from:
        import glob as _glob
        files = sorted(_glob.glob(os.path.join(args.rebuild_from, "seg_*.wav")))
        if not files:
            raise SystemExit(f"{args.rebuild_from} 里没有 seg_*.wav")
        parts = []
        sr = None
        for k, f in enumerate(files):
            a, s = torchaudio.load(f)
            if a.shape[0] > 1:
                a = a.mean(dim=0, keepdim=True)
            if sr is None:
                sr = s
            if k > 0 and args.seg_gap > 0:
                parts.append(torch.zeros(1, int(sr * args.seg_gap), dtype=torch.float32))
            parts.append(a)
        audio = torch.cat(parts, dim=1)
        wav = os.path.splitext(args.out)[0] + ".wav"
        torchaudio.save(wav, audio, sr)
        print(f"[rebuild] {len(files)} 段 → {wav} ({audio.shape[1]/sr:.2f}s)", flush=True)
        subprocess.run([FF, "-y", "-loglevel", "error", "-i", wav,
                        "-ar", "24000", "-ac", "1", "-b:a", "192k", args.out], check=True)
        print(f"[rebuild] mp3 -> {args.out}", flush=True)
        return

    if not args.text and not args.text_file:
        raise SystemExit("需要 --text-file 或 --text")

    model_dir = resolve_model(args.model_dir)
    is_cv3 = "CosyVoice3" in model_dir
    print(f"[cosyvoice_clone] model={model_dir} mode={args.mode} cv3={is_cv3} speed={args.speed}",
          flush=True)
    model = AutoModel(model_dir=model_dir)
    sr = model.sample_rate
    print(f"[cosyvoice_clone] sample_rate={sr}", flush=True)

    text = args.text if args.text else open(args.text_file, encoding="utf-8").read()
    sents = split_sentences(text)
    print(f"[cosyvoice_clone] {len(sents)} sentences / {len(text)} chars", flush=True)

    prompt = args.prompt_text
    if not prompt and os.path.exists(DEFAULT_PROMPT_FILE):
        prompt = open(DEFAULT_PROMPT_FILE, encoding="utf-8").read().strip()
    if is_cv3 and prompt and not prompt.startswith("You are a helpful assistant"):
        prompt = CV3_PREFIX + prompt

    chunks = []
    only = set()
    if args.only:
        only = {int(x) for x in re.split(r"[,\s]+", args.only.strip()) if x}
    seg_dir = args.seg_dir
    if seg_dir:
        os.makedirs(seg_dir, exist_ok=True)
        with open(os.path.join(seg_dir, "segments.json"), "w", encoding="utf-8") as f:
            json.dump({"seg_gap": args.seg_gap, "speed": args.speed,
                       "segments": [{"i": i, "text": s} for i, s in enumerate(sents, 1)]},
                      f, ensure_ascii=False, indent=1)
    for i, s in enumerate(sents, 1):
        seg_path = os.path.join(seg_dir, f"seg_{i:02d}.wav") if seg_dir else None
        if only and i not in only:
            if not (seg_path and os.path.exists(seg_path)):
                raise SystemExit(f"--only 指定了 {i} 之外的段，但 {seg_path} 不存在，无法复用")
            if i > 1 and args.seg_gap > 0:
                chunks.append(torch.zeros(1, int(sr * args.seg_gap), dtype=torch.float32))
            a, _s = torchaudio.load(seg_path)
            if a.shape[0] > 1:
                a = a.mean(dim=0, keepdim=True)
            chunks.append(a)
            print(f"  [{i}/{len(sents)}] 复用 {os.path.basename(seg_path)}  "
                  f"({a.shape[1]/sr:.2f}s)", flush=True)
            continue
        print(f"  [{i}/{len(sents)}] {s}", flush=True)
        if i > 1 and args.seg_gap > 0:  # 段间补静音（否则 torch.cat 会把两段贴死）
            chunks.append(torch.zeros(1, int(sr * args.seg_gap), dtype=torch.float32))
        if args.mode == "zero_shot":
            gen = model.inference_zero_shot(s, prompt, args.speaker, stream=False, speed=args.speed)
        else:
            t = (CV3_PREFIX + s) if is_cv3 else s
            gen = model.inference_cross_lingual(t, args.speaker, stream=False, speed=args.speed)
        piece = []
        for j in gen:
            piece.append(j["tts_speech"].cpu())
        seg_audio = torch.cat(piece, dim=1) if piece else None
        if seg_audio is None:
            raise SystemExit(f"第 {i} 段没有生成音频")
        if seg_path:
            torchaudio.save(seg_path, seg_audio, sr)
            print(f"       → {os.path.basename(seg_path)} ({seg_audio.shape[1]/sr:.2f}s)", flush=True)
        chunks.append(seg_audio)

    if not chunks:
        raise SystemExit("no audio generated")

    audio = torch.cat(chunks, dim=1)
    wav = os.path.splitext(args.out)[0] + ".wav"
    torchaudio.save(wav, audio, sr)
    print(f"[cosyvoice_clone] wav -> {wav} ({audio.shape[1]/sr:.2f}s)", flush=True)
    if os.path.abspath(wav) == os.path.abspath(args.out):
        # --out 本身就是 .wav：wav 已落盘，无需（也不能）原地转码
        print("[cosyvoice_clone] out is wav, skip mp3 transcode", flush=True)
        return
    subprocess.run([FF, "-y", "-loglevel", "error", "-i", wav,
                    "-ar", "24000", "-ac", "1", "-b:a", "192k", args.out], check=True)
    print(f"[cosyvoice_clone] mp3 -> {args.out}", flush=True)


if __name__ == "__main__":
    main()
