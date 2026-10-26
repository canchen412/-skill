#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重点字动效/音效 A/B 对比片生成器

同一句文案 + 同一张底图，渲染 4 组「动效 × 音效」方案并拼成一条对比片，
让灿爹直接看哪种手感对 —— 而不是口头描述。

实现：直接生成 ASS + ffmpeg 烧字幕，与 build_ad_video.py 的 kw_ass() 同机制。
"""
import json, os, subprocess, sys

FF = "/Users/mac/.local/bin/ffmpeg"
FFP = "/Users/mac/.local/bin/ffprobe"
SKILL = "/Users/mac/.workbuddy/skills/koubo-ad-video"
SFX = f"{SKILL}/assets/sfx"
FONT_DIR = f"{SKILL}/assets/fonts"
W, H, FPS = 1920, 1080, 30

# ---------- 方案表：4 组，每组 (标签, ASS动画, 音效, 颜色) ----------
def kw_body(txt, x, y, color, size, style):
    """style: pop | rise | type | mark | pulse | stroke | hero
    与 build_ad_video.py kw_ass() 同参数曲线。"""
    if style == "pop":       # 现状：机械弹跳，126% 折角停住
        return (f"{{\\an5\\pos({x},{y})\\c{color}\\fs{size}"
                f"\\fscx30\\fscy30\\t(0,90,\\fscx126\\fscy126)"
                f"\\t(90,210,\\fscx100\\fscy100)\\fad(0,160)}}{txt}")
    if style == "rise":      # 上滑定住
        y0 = min(y + 80, 1000)
        return (f"{{\\an5\\move({x},{y0},{x},{y},0,220)\\c{color}\\fs{size}"
                f"\\fscx96\\fscy96\\t(0,220,\\fscx100\\fscy100)\\fad(70,170)}}{txt}")
    if style == "type":
        inner = "".join(f"{{\\alpha&HFF&\\t({i*110},{i*110},\\alpha&H00&)}}{c}"
                        for i, c in enumerate(txt))
        return f"{{\\an5\\pos({x},{y})\\c{color}\\fs{size}\\fad(40,170)}}{inner}"
    if style == "stroke":
        return (f"{{\\an5\\pos({x},{y})\\c{color}\\fs{size}\\bord8"
                f"\\1a&HFF&\\t(0,340,\\1a&H00&)\\fad(0,170)}}{txt}")
    if style == "hero":      # 现状：超级重点字 118% 过冲 + 中段脉冲
        return (f"{{\\an5\\pos({x},{y})\\c{color}\\fs{size}"
                f"\\fscx25\\fscy25\\t(0,150,\\fscx118\\fscy118)"
                f"\\t(150,290,\\fscx100\\fscy100)\\t(560,620,\\fscx108\\fscy108)"
                f"\\t(620,700,\\fscx100\\fscy100)\\fad(0,210)}}{txt}")
    # ★方案A「elastic」：过冲回弹一次再落定（弹簧手感，不像机械折角）
    return (f"{{\\an5\\pos({x},{y})\\c{color}\\fs{size}"
            f"\\fscx20\\fscy20\\t(0,120,\\fscx112\\fscy112)"
            f"\\t(120,230,\\fscx96\\fscy96)\\t(230,330,\\fscx103\\fscy103)"
            f"\\t(330,420,\\fscx100\\fscy100)\\fad(0,140)}}{txt}")
    # ★方案B「blurpop」：带一点模糊感的快速冲入（高级感来自速度+柔化）


ASS_HEAD = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Big,新青年体,132,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,6,2,5,60,60,60,1
Style: Sub,新青年体,78,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,5,1,2,60,60,60,1
Style: Tag,新青年体,52,&H00C8C8C8,&H00C8C8C8,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,4,1,5,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def ts(sec):
    h = int(sec // 3600); m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"


def build(W, word, color, size, x, y, t0, end, style, txt=None):
    return f"Dialogue: 2,{ts(t0)},{ts(end)},Big,,0,0,0,,{kw_body(txt or word, x, y, color, size, style)}"


def sfx_vol(name):
    """与渲染器 DEFAULT_SFX_VOL 对齐"""
    tbl = {"kw_glass": 0.72, "kw_marker": 0.74, "kw_tick": 0.62, "kw_bloom": 0.78,
           "kw_boom": 0.88, "kw_thump": 0.84, "kw_charge": 0.80, "kw_spring": 0.76,
           "kw_shine": 0.78, "kw_snap": 0.74, "kw_pluck": 0.76, "kw_bellup": 0.78,
           "kw_drop": 0.76, "kw_spark": 0.70, "kw_triple": 0.76, "kw_pop": 0.80,
           "kw_ding": 0.75, "kw_click": 0.58, "kw_riser": 0.72}
    return tbl.get(name, 0.75)


def make_one(tag, word, style, sfx, color, bg, out, fps_dur=1.4):
    """渲染单个方案条：底图 + 大字 + 标签 + 音效"""
    lines = [ASS_HEAD.rstrip(), ""]
    t0 = 0.35
    end = t0 + fps_dur
    lines.append(build(bg, word, color, 132, 430, 300, t0, end + 1.2, style))
    # 方案标签
    lines.append(f"Dialogue: 2,{ts(0)},{ts(fps_dur+0.6)},Tag,,0,0,0,,"
                 f"{{\\an5\\pos(430,640)\\c&H00E8E8E8}}{tag}")
    # 音效名
    lines.append(f"Dialogue: 2,{ts(0)},{ts(fps_dur+0.6)},Tag,,0,0,0,,"
                 f"{{\\an5\\pos(430,760)\\c&H00B0B0B0}}动效 {style}  ×  音效 {sfx}")
    assp = os.path.join(os.path.dirname(out), f"_cmp_{style}_{tag}.ass")
    open(assp, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    fd = f":fontsdir={FONT_DIR}"
    sf = f"{SFX}/{sfx}.wav"
    subprocess.run([FF, "-y", "-v", "error", "-loop", "1", "-t", str(fps_dur + 0.6), "-i", bg,
                    "-i", sf,
                    "-filter_complex",
                    f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                    f"subtitles={assp}{fd}[v];"
                    f"[1:a]volume={sfx_vol(sfx):.4f}[a]",
                    "-map", "[v]", "-map", "[a]", "-t", str(fps_dur + 0.6),
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "44100", out], check=True)
    print(f"  ✓ {tag}: style={style} sfx={sfx}")


if __name__ == "__main__":
    print(__doc__)