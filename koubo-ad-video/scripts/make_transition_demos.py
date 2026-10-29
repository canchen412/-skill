#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""转场 / 淡入淡出 demo 生成器（技能内置版）

用途：换品类时快速重做「转场形态 demo」，给灿爹挑。
      默认素材是姜黄素 VORSE 的「健身房举铁 → 实验室 SGS」，一暗一明最易看清形态。

产出（落在 --out 目录）：
  T00.mp4 ~ T11.mp4   12 种转场（含 T00 现行推镜对照）
  F1.mp4  ~ F9.mp4    9 种淡入淡出
  转场候选_总览.mp4    12 段串联
  淡入淡出候选_总览.mp4 9 段串联
  转场形态对比.jpg     12 格「转场进行中」定格
  淡入淡出形态对比.jpg  9 行 × 3 时刻

用法：
  python make_transition_demos.py --out /tmp/tr_demo
  python make_transition_demos.py --out ./demo --src-a imgA.jpg --src-b imgB.jpg
  python make_transition_demos.py --out ./demo --only fades

⚠️ 三条铁律（踩过的坑，别改回去）：
  1. xfade 的 offset 必须 <= len(A) - T。A 素材要比转场起点长一个 T：
     A=1.56s / offset=1.20 / T=0.36 ⇒ 输出 2.70s。让 A 正好 1.2s 会被砍到 1.57s。
  2. fade=t=in 做白场必须写 color=white，默认从黑渐变，白场后会闪一帧黑。
  3. xfade 原生 fadeblack/fadewhite 的纯色峰值只 2 帧（0.06s），肉眼等于没黑。
     真黑/白场必须三段式 concat：A fade out → 纯色 hold → B fade in。
"""
import argparse
import os
import shutil
import subprocess

FF = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/usr/local/bin/ffmpeg"
FPS = 30
DA, DB = 1.56, 1.50          # A / B 素材长度（A 必须 > T0 + TD）
TD = 0.36                    # 转场时长
T0 = 1.20                    # 转场起点
TAIL = DA + DB - TD          # = 2.70s，每段统一长度

DEFAULT_A = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/举铁_健身房哑铃配米饭_中景.jpg"
DEFAULT_B = ("/Volumes/陈灿会展/广告视频/分镜图库/小绿人/姜黄素/VORSE/图片/"
             "产品_指VORSE瓶SGS认证_实验室_中景_16x9.jpg")


def run(cmd, tag=""):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[FAIL {tag}] {r.stderr.strip()[-900:]}")
        return False
    return True


def ensure_font(out_dir):
    """ffmpeg 的 drawtext 里路径带空格要转义，直接复制成无空格名字最省事。"""
    dst = os.path.join(out_dir, "font.ttc")
    if os.path.exists(dst):
        return dst
    for src in ("/System/Library/Fonts/STHeiti Medium.ttc",
                "/System/Library/Fonts/PingFang.ttc",
                "/System/Library/Fonts/Supplemental/Songti.ttc"):
        if os.path.exists(src):
            # 用 copyfile 而不是 copy2：copy2 会连 chflags 一起复制，
            # 在沙箱/受限目录下会 PermissionError: Operation not permitted。
            shutil.copyfile(src, dst)
            return dst
    return ""


def shot(img, dur, out, zoom_rate=0.035):
    n = round(dur * FPS)
    return run([FF, "-y", "-v", "error",
                "-loop", "1", "-framerate", str(FPS), "-t", f"{dur}", "-i", img,
                "-vf", ("scale=2880:-2,crop=2880:1620:0:0,"
                        f"zoompan=z='1+{zoom_rate}*on/{n}':"
                        "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                        f"d=1:s=1920x1080:fps={FPS},setsar=1"),
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "12",
                "-g", "1", "-pix_fmt", "yuv420p", "-an", out], "shot")


def N(i, o):
    return f"[{i}]fps={FPS},format=yuv420p,setsar=1,settb=AVTB[{o}]"


# ---------------- 转场族 T00~T11 ----------------
def push(t):
    """T00 现行推镜：A 不动、B 从左滑入覆盖（流水线 mix 模式的真实做法）"""
    return (f"{N(0,'a')};{N(1,'b')};"
            f"[a][b]overlay=x='W*max(0,min(1,({T0+TD:.2f}-t)/{TD}))':y=0:eval=frame[v]")


def xf(tr):
    return (f"{N(0,'a')};{N(1,'b')};"
            f"[a][b]xfade=transition={tr}:duration={TD}:offset={T0}[v]")


XFADES = [
    ("T00", "现在用的：直线推镜", push("")),
    ("T01", "叠化 fade", xf("fade")),
    ("T02", "水平模糊溶 hblur", xf("hblur")),
    ("T03", "圆形开洞 circleopen", xf("circleopen")),
    ("T04", "径向旋转 radial", xf("radial")),
    ("T05", "垂直开合 vertopen", xf("vertopen")),
    ("T06", "像素化 pixelize", xf("pixelize")),
    ("T07", "横向切片 hlslice", xf("hlslice")),
    ("T08", "整幅擦除 wipeleft", xf("wipeleft")),
    ("T09", "斜切 diagtl", xf("diagtl")),
    # 10 coverup 已否决，编号留空避免灿爹看错行
    ("T11", "风扫 hlwind", xf("hlwind")),
    ("T12", "去色过渡 fadegrays", xf("fadegrays")),
]


# ---------------- 淡出族 F1~F9 ----------------
def dip(color="black", hold=0.12, fade=0.32):
    """真·黑场/白场：A 淡出 → 纯色停留 hold → B 淡入（三段式，见铁律 3）"""
    a_out = 1.15
    b_in = TAIL - a_out - hold
    return (
        f"[0:v]fps={FPS},setsar=1,trim=0:{a_out},setpts=PTS-STARTPTS,"
        f"fade=t=out:st={a_out-fade:.3f}:d={fade}:color={color},format=yuv420p[a1];"
        f"color=c={color}:s=1920x1080:r={FPS}:d={hold},format=yuv420p[c1];"
        f"[1:v]fps={FPS},setsar=1,trim=0:{b_in:.3f},setpts=PTS-STARTPTS,"
        f"fade=t=in:st=0:d={fade}:color={color},format=yuv420p[b1];"
        f"[a1][c1][b1]concat=n=3:v=1:a=0[v]"
    )


def zoom_out_fade():
    """A 缩小、四周露黑底，同时叠化到 B —— 镜头被推远收掉"""
    a = (f"[0:v]fps={FPS},scale=2880:-2,crop=2880:1620:0:0,"
         f"zoompan=z='if(lt(on,20),1,max(0.74,1-0.26*(on-20)/26))':"
         "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
         f"d=1:s=1920x1080:fps={FPS},setsar=1,settb=AVTB[a]")
    return a + f";{N(1,'b')};[a][b]xfade=transition=fade:duration=0.42:offset={T0}[v]"


def blur_dissolve():
    """转场窗口内整幅糊掉再化开 —— 最柔，几乎看不见接缝"""
    a = (f"[0:v]fps={FPS},format=yuv420p,setsar=1,settb=AVTB,"
         f"gblur=sigma=16:enable='between(t,{T0-0.04:.2f},{T0+0.40:.2f})'[a]")
    return a + f";{N(1,'b')};[a][b]xfade=transition=fade:duration=0.40:offset={T0}[v]"


def drift_fade():
    """A 整体向上飘走、底部露黑，同时化到 B —— 老电影换卷感"""
    return (f"color=c=black:s=1920x1080:r={FPS}:d={DA}[bg];"
            f"[0:v]fps={FPS},scale=1920:-2,crop=1920:1080:0:0,setsar=1[aimg];"
            f"[bg][aimg]overlay=x=0:y='-170*min(1,t/1.30)':shortest=0[ao];"
            f"[ao]trim=end={DA},setpts=PTS-STARTPTS,format=yuv420p,settb=AVTB[a];"
            f"{N(1,'b')};[a][b]xfade=transition=fade:duration=0.42:offset={T0}[v]")


def solo(kind):
    """B 类：单镜自己的淡入 / 淡出，不接下一镜"""
    f = ("fade=t=in:st=0:d=0.55" if kind == "in"
         else f"fade=t=out:st={TAIL-0.62:.2f}:d=0.62")
    return f"[0:v]fps={FPS},format=yuv420p,setsar=1,{f}[v]"


FADES = [
    ("F1", "黑场过渡 dip to black", dip("black")),
    ("F2", "白场过渡 dip to white", dip("white", hold=0.10, fade=0.30)),
    ("F3", "颗粒溶解 dissolve", xf("dissolve")),
    ("F4", "慢叠化 slow fade（0.36s 长融）", xf("fadeslow")),
    ("F5", "缩小淡出（推远收掉）", zoom_out_fade()),
    ("F6", "模糊溶解 blur dissolve", blur_dissolve()),
    ("F7", "上飘淡出 drift up", drift_fade()),
    ("F8", "单镜淡入：从黑渐显", solo("in")),
    ("F9", "单镜淡出：渐隐到黑", solo("out")),
]


def build(tag, label, chain, font, a, b, out, img_only=False):
    if font:
        chain += (f";[v]drawtext=fontfile={font}:text='{tag}  {label}':x=44:y=38:"
                  f"fontsize=40:fontcolor=white:box=1:boxcolor=black@0.60:"
                  f"boxborderw=18:enable='lt(t,2.15)'[vo]")
        vmap = "[vo]"
    else:
        vmap = "[v]"
    cmd = [FF, "-y", "-v", "error", "-i", a]
    if not img_only:
        cmd += ["-i", b]
    cmd += ["-filter_complex", chain, "-map", vmap,
            "-c:v", "libx264", "-preset", "medium", "-crf", "17",
            "-pix_fmt", "yuv420p", "-r", str(FPS), "-an", out]
    return run(cmd, tag)


def contact_sheet(items, out_jpg, font, cols, picks, tag_prefix=""):
    """items: [(tag, label)]；picks: {tag: (t1,t2,t3)} 相对单段起点"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        print("无 PIL，跳过对比图:", e)
        return
    cw, ch = 340, 191
    rows = (len(items) + cols - 1) // cols
    im = Image.new("RGB", (cw * cols, (ch + 46) * rows), (18, 18, 20))
    f = ImageFont.truetype(font, 24, index=0) if font else ImageFont.load_default()
    for i, (tag, label) in enumerate(items):
        for j, t in enumerate(picks.get(tag, (1.22, 1.38, 1.54))):
            p = f"/tmp/_cs_{tag}_{j}.jpg"
            subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t:.3f}",
                            "-i", os.path.join(tag_prefix, f"{tag}.mp4"),
                            "-frames:v", "1", p], check=False)
            if os.path.exists(p):
                im.paste(Image.open(p).resize((cw, ch)), (j * cw, i * (ch + 46)))
        d = ImageDraw.Draw(im)
        d.rectangle([0, i * (ch + 46) + ch, cw * cols, i * (ch + 46) + ch + 46],
                    fill=(30, 30, 34))
        d.text((12, i * (ch + 46) + ch + 11), f"{tag}  {label}", font=f, fill=(240, 240, 240))
    im.save(out_jpg, quality=92)
    print("对比图:", out_jpg, im.size)


def concat(made, out_mp4, root):
    lst = os.path.join(root, "_concat.txt")
    with open(lst, "w") as f:
        for p in made:
            f.write(f"file '{p}'\n")
    if run([FF, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-pix_fmt", "yuv420p", "-an", out_mp4], "concat"):
        print("总览:", out_mp4, len(made), "段")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="./transition_demos")
    ap.add_argument("--src-a", default=DEFAULT_A)
    ap.add_argument("--src-b", default=DEFAULT_B)
    ap.add_argument("--only", choices=["all", "transitions", "fades"], default="all")
    a = ap.parse_args()

    root = os.path.abspath(a.out)
    clips = os.path.join(root, "clips")
    os.makedirs(clips, exist_ok=True)
    font = ensure_font(root)

    ca, cb = os.path.join(clips, "fa.mp4"), os.path.join(clips, "fb.mp4")
    clong = os.path.join(clips, "fa_long.mp4")
    shot(a.src_a, DA, ca)
    shot(a.src_b, DB, cb)
    shot(a.src_a, TAIL, clong, zoom_rate=0.02)

    if a.only in ("all", "transitions"):
        made = []
        for tag, label, chain in XFADES:
            out = os.path.join(root, f"{tag}.mp4")
            if build(tag, label, chain, font, ca, cb, out):
                made.append(out)
                print("OK ", tag, label)
            else:
                print("ERR", tag, label)
        if made:
            concat(made, os.path.join(root, "转场候选_总览.mp4"), root)
        contact_sheet([(t, l) for t, l, _ in XFADES],
                      os.path.join(root, "转场形态对比.jpg"), font, 4,
                      {t: (1.36, 1.40, 1.44) for t, _, _ in XFADES}, root + "/")

    if a.only in ("all", "fades"):
        made = []
        for tag, label, chain in FADES:
            out = os.path.join(root, f"{tag}.mp4")
            src_a = clong if tag in ("F8", "F9") else ca
            if build(tag, label, chain, font, src_a, cb, out, img_only=(tag in ("F8", "F9"))):
                made.append(out)
                print("OK ", tag, label)
            else:
                print("ERR", tag, label)
        if made:
            concat(made, os.path.join(root, "淡入淡出候选_总览.mp4"), root)
        picks = {"F1": (1.02, 1.22, 1.48), "F2": (1.02, 1.20, 1.48),
                 "F3": (1.22, 1.38, 1.54), "F4": (1.22, 1.38, 1.54),
                 "F5": (1.05, 1.28, 1.50), "F6": (1.22, 1.38, 1.54),
                 "F7": (1.05, 1.28, 1.50), "F8": (0.05, 0.26, 0.62),
                 "F9": (2.02, 2.34, 2.66)}
        contact_sheet([(t, l) for t, l, _ in FADES],
                      os.path.join(root, "淡入淡出形态对比.jpg"), font, 3, picks, root + "/")


if __name__ == "__main__":
    main()
