#!/usr/bin/env python3
# 生成「带停顿配音」：在镜1 末字后插 PAUSE 秒静音，后续 tokens 时间戳整体后移
# 依据 opening_banner.md 拍板定式：extend 1.15 需配合 vo_with_pause（画面停住、声音也停住）
# tokens 时间轴在 vo_mapped.json 的 transcription[].offsets（毫秒）
import json, re, subprocess, sys

FF = "/Users/mac/.local/bin/ffmpeg"
W = "/Users/mac/WorkBuddy/2026-10-05-09-33-49/jisuan_alan_video_261006"
PAUSE = 1.15
PAUSE_MS = PAUSE * 1000.0
P = re.compile(r'[，。！？、：；—…·～「」『』《》""\'\'（）()\s]')


def hook_n(sid):
    c = json.load(open(f"{W}/{sid}/config.json"))
    t = c["phrases"][0]["text"]
    return len(P.sub("", t)), t


def dur(p):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", p],
        capture_output=True, text=True).stdout.strip())


def make(sid):
    vo = f"{W}/{sid}/audio/vo.mp3"
    out = f"{W}/gen/vo_with_pause_{sid}.mp3"
    n, txt = hook_n(sid)
    toks = json.load(open(f"{W}/{sid}/audio/vo_mapped.json"))
    seg = toks["transcription"]

    cnt, cut = 0, 0.0
    for s in seg:
        cnt += len(P.sub("", s["text"]))
        cut = float(s["offsets"]["to"])
        if cnt >= n:
            break
    print(f"{sid}: 镜1「{txt}」{n}字 → 末字 @{cut/1000:.3f}s")

    subprocess.run([FF, "-y", "-v", "error", "-i", vo, "-filter_complex",
        f"[0:a]atrim=0:{cut/1000:.4f},asetpts=N/SR/TB[a];"
        f"anullsrc=r=44100:cl=mono,atrim=0:{PAUSE}[p];"
        f"[0:a]atrim={cut/1000:.4f},asetpts=N/SR/TB[b];"
        f"[a][p][b]concat=n=3:v=0:a=1[o]",
        "-map", "[o]", "-ar", "44100", "-ac", "1",
        "-c:a", "libmp3lame", "-q:a", "2", out], check=True)
    print(f"  配音 {dur(vo):.2f}s → {dur(out):.2f}s")

    m = 0
    for s in seg:
        off = s.get("offsets") or {}
        if "from" not in off or float(off["from"]) <= cut - 60:
            continue
        off["from"] = round(float(off["from"]) + PAUSE_MS)
        if off.get("to") is not None:
            off["to"] = round(float(off["to"]) + PAUSE_MS)
        ts = s.get("timestamps")
        if isinstance(ts, dict) and ts.get("from"):
            def bump(x, d):
                h, m2, rest = x.split(":")
                sec, cs = rest.split(".")
                tot = int(h) * 3600 + int(m2) * 60 + int(sec) + int(cs) / 100 + d / 1000.0
                return f"{int(tot//3600):02d}:{int((tot%3600)//60):02d}:{tot%60:05.2f}"
            ts["from"] = bump(ts["from"], PAUSE_MS)
            if ts.get("to"):
                ts["to"] = bump(ts["to"], PAUSE_MS)
        m += 1
    dst = f"{W}/{sid}/audio/vo_mapped_pause.json"
    json.dump(toks, open(dst, "w"), ensure_ascii=False, indent=1)
    print(f"  tokens 后移 {m} 段 → {dst.split('/')[-1]}")


if __name__ == "__main__":
    for sid in sys.argv[1:]:
        make(sid)
