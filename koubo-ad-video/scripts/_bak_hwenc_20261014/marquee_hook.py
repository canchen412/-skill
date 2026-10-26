#!/usr/bin/env python3
"""marquee_hook.py —钩子句「跑马灯」快切镜（本地 ffmpeg 合成，0 积分）。

场景
----
灿爹：「一般如果我说"今天我们讲，某某某"之前，会说一句话，
比如『19块9 是你三个月大的健身成本』，这句话对应的分镜图片把小绿人各种训练的
图片做一个跑马灯的效果，例如漫威里面的开头，可以做到吗？」

原理
----
渲染器本身**不支持**「一个 phrase挂多图」（`pp.get("src")` 只取单张）。
但每个 phrase 镜位在渲染期是**独立拼的一段 ffmpeg** ⇒
把钩子句按字符数**拆成 N 个连续短镜**（每镜0.4~0.6s），天然就是快切＝跑马灯。
本脚本只做「钩子句 → N 张训练图 + 字幕 + 转场whosh」的**预合成小片**，
供确认风格；确认后走 `phrases` 拆句 + 走场镜的标准管线（见技能#新增条目）。

用法
----
  python3 marquee_hook.py --text "3个月的健身成本只需要39块9，" \
      --images a.png b.png c.png --out hook.mp4
"""
import argparse
import json
import os
import subprocess
import sys
import tempfile

FF = "/Users/mac/.local/bin/ffmpeg"
FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"
W, H, FPS = 1920, 1080, 30


def fit16x9(src, dst, size=(W, H)):
    """按 16:9 铁律：cover 到目标画布 + 居中裁切（不做模糊延展）。"""
    subprocess.run([FF, "-v", "error", "-i", src,
                    "-vf", (f"scale={size[0]}:{size[1]}:force_original_aspect_ratio=increase,"
                            f"crop={size[0]}:{size[1]},setsar=1"),
                    "-frames:v", "1", "-q:v", "2", dst, "-y"], check=True)
    return dst


def build_segments(text, images, seg_dur=0.55, fade=0.06):
    """把钩子句按字数切成 N 段，每段配一张图 ⇒ 文字与图同步快切。"""
    n = len(images)
    # 字幕按标点/长度均分
    marks = []
    step = max(1, len(text) // n)
    for k in range(n):
        s = k * step
        e = len(text) if k == n - 1 else min(len(text), s + step)
        marks.append(text[s:e])
    return list(zip(marks, images))


def render(text, images, out, seg_dur=0.55, music=None, fade=0.05):
    """把钩子句做成 N 段快切。

    ★两个已踩的坑（别重复）：
    1) `zoompan=...:d=1` 配 `-loop 1 -t` 时，**zoompan 只吐 1 帧/输入帧**，
       实测每段只出 0.43s（要 0.52s）⇒ 段长被吃掉、整片偏短。
       正解：`-framerate {FPS} -loop 1` 先把静图变成视频流，再 `zoompan d={n}`（n=帧数）。
    2) **字幕不能按字数均分**：「3个月的健身成本只需要39块9，」会被切成
       「3个」「月的」「健身」…这种碎片，完全不能看。
       正解：**字幕整句只出现一次**，用最后一段（或独立叠加）承载全文，
       图片快切与字幕解耦——这才是漫威片头的做法（画面狂切、标题压轴）。
    """
    tmp = tempfile.mkdtemp(prefix="mq_")
    segs = build_segments(text, images, seg_dur)
    clips = []
    nframes = max(2, int(round(seg_dur * FPS)))
    for i, (sub, src) in enumerate(segs):
        img = fit16x9(src, f"{tmp}/f{i}.jpg")
        clip = f"{tmp}/c{i}.mp4"
        # 静图 → 视频流（每输入帧 1 输出帧）→ zoompan d=帧数（★保证段长精确）
        vf = (f"zoompan=z='min(zoom+0.0009,1.06)':d={nframes}"
              f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS},"
              f"fade=t=out:st={max(0, seg_dur-fade):.3f}:d={fade:.3f}")
        subprocess.run([FF, "-v", "error",
                        "-framerate", str(FPS), "-loop", "1", "-i", img,
                        "-vf", vf, "-frames:v", str(nframes),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p",
                        clip, "-y"], check=True)
        clips.append(clip)

    # ★字幕：整句压在最后一段上（画面快切、字幕不碎）
    ass = f"{tmp}/sub.ass"
    last = seg_dur * (len(clips) - 1)
    with open(ass, "w", encoding="utf-8") as f:
        f.write(
            "[Script Info]\nScriptType: v4.00+\nPlayResX: 1920\nPlayResY: 1080\n\n"
            "[V4+ Styles]\nFormat: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            "OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,"
            "Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding\n"
            "Style: D,Hiragino Sans GB,92,&H00FFFFFF,&H000000FF,&H00000000,&H90000000,"
            "0,0,0,0,100,100,0,0,1,4,2,2,60,60,70,1\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            f"Dialogue: 2,0:00:{last:05.2f},0:00:{(last+seg_dur):05.2f},D,,0,0,0,,"
            f"{{\\fad(120,120)}}{text}\n")

    lst = f"{tmp}/list.txt"
    with open(lst, "w") as f:
        f.write("".join(f"file '{c}'\n" for c in clips))
    cmd = [FF, "-v", "error", "-f", "concat", "-safe", "0", "-i", lst]
    if music:
        cmd += ["-i", music, "-shortest"]
    cmd += ["-vf", f"subtitles='{ass}'",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS), out, "-y"]
    subprocess.run(cmd, check=True)
    total = seg_dur * len(clips)
    print(f"✅ {out}  {len(clips)} 段 × {seg_dur}s = {total:.2f}s")
    print(f"   字幕：整句压在最后一段（{last:.2f}s 起）—— 画面快切、字幕不碎")
    for i, (sub, src) in enumerate(segs):
        print(f"   {i+1}. {os.path.basename(src)[:36]}")
    return out


def main():
    ap = argparse.ArgumentParser(description="钩子句跑马灯快切镜（0 积分）")
    ap.add_argument("--text", required=True, help="钩子句全文（不含书名号）")
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seg-dur", type=float, default=0.55, help="每张停留秒数")
    ap.add_argument("--music", default=None)
    a = ap.parse_args()
    render(a.text, a.images, a.out, a.seg_dur, a.music)


if __name__ == "__main__":
    main()
