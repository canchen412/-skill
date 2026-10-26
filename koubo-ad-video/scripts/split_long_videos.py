#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""长镜头视频 → 分镜片段拆条 + 备注索引（koubo-ad-video 技能常驻工具）

做什么：
  0. **水印预检（默认开）**：源视频 + 每个切出的片段都抽帧 OCR 查平台水印
     （小云雀/可灵/即梦…）。带水印的一律**不入库**——源视频整支跳过、片段就地丢弃。
     ★水印是定时淡入淡出的，少量抽帧必漏（实锤：`.mov` 源单次 3 帧判「干净」，
       切出的 3 个片段在 6 帧采样下全部检出）⇒ 默认抽 4 帧，宁可多花几秒。
  1. 用 ffmpeg `select='gt(scene,TH)'` 检测镜头切换点，切成独立片段；
  2. 每段重编码为统一规格（1920×1080 / 30fps / 16:9 cover-crop / 无音轨 / crf18）；
  3. 抽 3 帧做内容分析：景别（绿主体掩膜 → 回退背景差掩膜）、Vision 场景标签、
     OCR 文字、片段内运动量；
  4. 输出可检索索引（JSON + Markdown）+ 接触表预览图 + 废片清单。

命名：`<大类>_<动作描述>镜<NN>_<景别>.mp4`（延续图库三段式「大类_具体画面_景别」）

用法：
  PY=~/.workbuddy/binaries/python/envs/default/bin/python
  $PY split_long_videos.py --out "/…/视频分镜" --th 0.30 --min-sec 1.2 \
        --src "视频A.mp4" --src "视频B.mp4" ...
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
import cv2

FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "/Users/mac/.local/bin/ffprobe")
SKILL = os.path.expanduser("~/.workbuddy/skills/koubo-ad-video")
DESCRIBE = f"{SKILL}/scripts/img_describe.py"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from video_wm_scan import scan_video as wm_scan_video  # noqa: E402

OUT_W, OUT_H, OUT_FPS = 1920, 1080, 30

# ══ 动作词 → (大类, 动作描述) ══  ★顺序敏感：更具体的（带场地）必须排在前
ACTION_RULES = [
    (r"俯卧撑.*雨中|雨中.*俯卧撑", ("训练", "俯卧撑雨中田径场")),
    (r"俯卧撑.*公园|公园.*俯卧撑", ("训练", "俯卧撑公园旋转")),
    (r"俯卧撑", ("训练", "俯卧撑多镜头")),
    (r"健身房|卧推|深蹲|硬拉|哑铃|杠铃|器械|举铁", ("训练", "健身房训练")),
    (r"引体", ("训练", "单杠引体向上")),
    (r"悬垂举腿|举腿", ("训练", "单杠悬垂举腿")),
    (r"悬垂展腹", ("训练", "单杠悬垂展腹")),
    (r"跑步|田径场|跑道", ("训练", "田径跑步")),
    (r"出拳|打拳|击打", ("训练", "挥拳发力")),
    (r"冲泡|搅拌", ("展示", "冲泡演示")),
    (r"托瓶|持瓶|产品", ("产品", "产品展示")),
    (r"光束|粒子|尘埃", ("场景", "氛围空镜")),
]
# ══ 内容标签 → 适用场景 ══
SCENE_RULES = [
    (r"barbell|dumbbell|weight|workout|health club|gym|exercise|sport|training", "训练频率 / 练得勤 / 练完累 / 白折腾"),
    (r"pull.?up|chin.?up|pullup|horizontal bar", "训练频率 / 练得勤 / 力量感"),
    (r"push.?up|plank", "训练频率 / 坚持 / 练完累"),
    (r"sky|cloud|outdoor|sunset|dawn|night sky", "氛围空镜 / 转场垫片"),
    (r"water|splash|wave|rain|snow|fog|liquid", "冲击转场 / 力量爆发"),
    (r"fire|flame|spark|smoke|explosion", "爆发 / 冲击力"),
    (r"bottle|container|product|packag", "产品出镜 / 卖点展示"),
    (r"food|meal|plate|tableware|bowl", "饮食 / 高蛋白 / 一日三餐"),
    (r"phone|computer|screen|desk|laptop", "熬夜玩手机 / 久坐"),
    (r"mirror|bathroom", "照镜子 / 建模 / 自我审视"),
    (r"person|people|adult|statue|figurine|illustration|art", "人物镜 / 主角出镜"),
]


def probe(path):
    r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=width,height,r_frame_rate",
                        "-show_entries", "format=duration", "-of", "json", path],
                       capture_output=True, text=True)
    d = json.loads(r.stdout or "{}")
    st = (d.get("streams") or [{}])[0]
    dur = float(d.get("format", {}).get("duration") or 0)
    fps = 0.0
    fr = st.get("r_frame_rate") or "0/1"
    if "/" in fr:
        a, b = fr.split("/")
        fps = float(a) / max(float(b), 1e-6)
    return int(st.get("width") or 0), int(st.get("height") or 0), fps, dur


def scene_cuts(path, th):
    r = subprocess.run([FFMPEG, "-hide_banner", "-nostats", "-i", path,
                        "-filter:v", f"select='gt(scene,{th})',showinfo",
                        "-an", "-f", "null", "-"], capture_output=True, text=True)
    return sorted({float(x) for x in re.findall(r"pts_time:([0-9.]+)", r.stderr)})


def _sig(path, t, tmp):
    """归一化灰度缩略图（去均值/除标准差）——比直方图更能反映画面「布局」是否变了。"""
    grab(path, t, tmp)
    img = cv2.imread(tmp)
    if img is None:
        return None
    g = cv2.cvtColor(cv2.resize(img, (96, 54)), cv2.COLOR_BGR2GRAY).astype(np.float32)
    return (g - g.mean()) / (g.std() + 1e-6)


def verify_cut(path, t, tmp, corr_th=0.45):
    """真切点：切后画面布局持续不同于切前；运动尖峰：前后还是一回事。
    ★直方图相关对同色系场景过于宽容（实测真切点也有 0.62+），改用**灰度空间结构相关**。"""
    before, after = [], []
    for i, dt in enumerate((-0.80, -0.60, -0.40, -0.20)):
        s = _sig(path, max(0.0, t + dt), f"{tmp}_b{i}.png")
        if s is not None:
            before.append(s)
    for i, dt in enumerate((0.20, 0.40, 0.60, 0.80)):
        s = _sig(path, t + dt, f"{tmp}_a{i}.png")
        if s is not None:
            after.append(s)
    if not before or not after:
        return True, None
    corrs = [float((b * a).mean()) for b in before for a in after]
    c = float(np.mean(corrs))
    return (c < corr_th), round(c, 3)


def build_segments(dur, cuts, min_sec, head_guard=0.30):
    bounds = [0.0] + [c for c in cuts if head_guard < c < dur - 0.20] + [dur]
    segs = [[bounds[i], bounds[i + 1]] for i in range(len(bounds) - 1)]
    merged = []
    for s in segs:
        if merged and (s[1] - s[0]) < min_sec:
            merged[-1][1] = s[1]                      # 太短 → 并入上一段
        else:
            merged.append(list(s))
    if len(merged) > 1 and (merged[0][1] - merged[0][0]) < min_sec:
        merged[1][0] = merged[0][0]
        merged.pop(0)
    return merged


def cut(src, t0, t1, out):
    vf = (f"scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
          f"crop={OUT_W}:{OUT_H},fps={OUT_FPS},setsar=1")
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{t0:.3f}", "-i", src,
                    "-t", f"{t1 - t0:.3f}", "-an", "-vf", vf,
                    "-c:v", "h264_videotoolbox", "-q:v", "75",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", out],
                   check=True)


def grab(path, t, tmp):
    subprocess.run([FFMPEG, "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", path,
                    "-frames:v", "1", tmp], check=True)
    return tmp


def _bbox_from_mask(m, sw, sh, min_frac=0.004):
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    m = cv2.morphologyEx(m, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    n, _lab, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    best = None
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] > min_frac * sh * sw:
            if best is None or stats[i, cv2.CC_STAT_AREA] > stats[best, cv2.CC_STAT_AREA]:
                best = i
    return None if best is None else stats[best]


def shot_level(frame_bgr):
    """景别：先用绿主体掩膜（小绿人）；测不到就用背景差掩膜（任意主体）。"""
    W = 900
    h0, w0 = frame_bgr.shape[:2]
    small = cv2.resize(frame_bgr, (W, int(W * h0 / w0)), interpolation=cv2.INTER_LANCZOS4)
    sh, sw = small.shape[:2]
    rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB)
    hsv = cv2.cvtColor(rgb, cv2.COLOR_RGB2HSV)
    Hs, S, V = hsv[:, :, 0].astype(int), hsv[:, :, 1].astype(int), hsv[:, :, 2].astype(int)
    green = (((Hs >= 30) & (Hs <= 95) & (S >= 55) & (V >= 35)) |
             ((Hs >= 25) & (Hs <= 100) & (S >= 110) & (V >= 40))).astype(np.uint8)
    st = _bbox_from_mask(green, sw, sh)
    how = "绿主体"
    if st is None:                                    # 回退：与边缘中位色差异
        bg = np.median(np.concatenate([rgb[:, :int(sw * .06)], rgb[:, -int(sw * .06):]], axis=1), axis=1)
        diff = np.abs(rgb.astype(np.int16) - np.repeat(bg[:, None, :], sw, axis=1)).sum(2)
        st = _bbox_from_mask((diff > 60).astype(np.uint8), sw, sh)
        how = "背景差"
    if st is None:
        return "中景", 0.0, how
    hp = st[cv2.CC_STAT_HEIGHT] / sh
    lv = "全身" if hp >= 0.78 else ("中景" if hp >= 0.50 else ("近景" if hp >= 0.30 else "特写"))
    return lv, round(float(hp), 3), how


def motion_mae(path):
    cap = cv2.VideoCapture(path)
    prev, vals = None, []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(cv2.resize(f, (480, 270)), cv2.COLOR_BGR2GRAY).astype(np.int16)
        if prev is not None:
            vals.append(float(np.abs(g - prev).mean()))
        prev = g
    cap.release()
    return round(float(np.mean(vals)), 2) if vals else 0.0


def describe_frames(paths):
    py = sys.executable
    r = subprocess.run([py, DESCRIBE, "--summary", *paths], capture_output=True, text=True)
    labels, texts, tone = [], [], ""
    for line in r.stdout.splitlines():
        m = re.search(r"标签：(.+)", line)
        if m:
            labels += [x.split("(")[0] for x in m.group(1).split("/")]
        m = re.search(r"文字：(.+)", line)
        if m and m.group(1).strip():
            texts.append(m.group(1).strip())
        m = re.search(r"色调：([^\s，]+)", line)
        if m:
            tone = m.group(1)
    return [x.strip() for x in labels if x.strip()][:8], texts, tone


def classify(src_name):
    for pat, (cat, act) in ACTION_RULES:
        if re.search(pat, src_name):
            return cat, act
    return "训练", "训练动作"


def scenes_of(labels, act):
    out = []
    joined = " ".join(labels).lower()
    for pat, sc in SCENE_RULES:
        if re.search(pat, joined):
            out += [x.strip() for x in sc.split("/")]
    # ★按「场景词」粒度去重（按整串去重会漏：两条规则输出的串不同但含同样的词）
    out = list(dict.fromkeys(x for x in out if x))
    return " / ".join(out) if out else "通用镜头"


def write_index_md(idx, out_dir):
    L = []
    L.append("# 视频分镜片段索引\n")
    L.append(f"> 生成：{idx['生成时间']} ｜ 输出：`{out_dir}` ｜ "
             f"切分阈值 scene>{idx['切分阈值']} ｜ 最短镜 {idx['最短镜']}s ｜ "
             f"规格 {OUT_W}×{OUT_H}/{OUT_FPS}fps/无音轨\n")
    L.append(f"**共 {idx['片段数']} 个片段**，来自 {len(set(i['源视频'] for i in idx['items']))} 支长视频。"
             "命名规则：`大类_动作镜NN_景别.mp4`（延续图库三段式）。")
    L.append(f"水印预检：{idx.get('水印预检', '—')}；带水印素材一律不入库"
             f"（本次判废 **{idx.get('废片数', 0)}** 项，见第三节）。\n")

    L.append("\n## 一、按适用场景检索\n")
    by_scene = {}
    for it in idx["items"]:
        for sc in it["适用场景"].split(" / "):
            by_scene.setdefault(sc.strip(), []).append(it["name"])
    L.append("| 适用场景 | 可用片段 |")
    L.append("|---|---|")
    for sc, names in sorted(by_scene.items(), key=lambda x: -len(x[1])):
        L.append(f"| {sc} | {len(names)} 个：`" + "` `".join(names) + "` |")

    L.append("\n## 二、片段明细（按源视频）\n")
    cur = None
    for it in idx["items"]:
        if it["源视频"] != cur:
            cur = it["源视频"]
            L.append(f"\n### {cur}")
            L.append(f"源路径：`{it['源路径']}`\n")
            L.append("| 文件名 | 起-止(s) | 时长 | 景别 | 运动量 | 内容标签 | 适用场景 |")
            L.append("|---|---|---|---|---|---|---|")
        L.append(f"| `{it['name']}` | {it['时间区间'][0]}–{it['时间区间'][1]} | "
                 f"{it['时长s']}s | {it['景别']}({it['主体高占比']}) | {it['帧间运动量']} | "
                 f"{', '.join(it['场景标签'][:4])} | {it['适用场景']} |")
    L.append("\n---\n")
    waste = idx.get("废水清单") or idx.get("废片清单") or []
    L.append("## 三、废片清单（检出平台水印，未入库）\n")
    if waste:
        L.append("| 名称 | 层级 | 源视频 | 命中水印词 |")
        L.append("|---|---|---|---|")
        for w in waste:
            L.append(f"| `{w['name']}` | {w.get('层级', '—')} | "
                     f"{w.get('源视频', '—')} | {'/'.join(w.get('命中水印词', []))} |")
        L.append("\n> 处置：源视频级 → **整支剔除，不要再用**；片段级 → 已删除。"
                 "如确需该素材，先按 `_视频水印备忘.txt` 去水印再重新拆条。")
    else:
        L.append("本次无（所有源视频与片段均通过水印预检）。")
    L.append("\n---\n")
    L.append("## 四、字段说明\n")
    L.append("- **景别**：绿主体掩膜（小绿人）或背景差掩膜测得的「主体高占比」推断；"
             "≥0.78 全身 / ≥0.50 中景 / ≥0.30 近景 / 其余特写。括号内为占比实测值。")
    L.append("- **运动量**：片段内相邻帧灰度差均值（MAE），越大画面越「动」。静图镜约 1–3，实拍/多镜头约 4–8。")
    L.append("- **内容标签**：macOS Vision 场景分类 top 标签（自动，仅供参考）。")
    L.append("- **适用场景**：由内容标签映射到「适合配哪种文案」，用于快速挑选。")
    L.append("- **时间区间**：该片段在原长视频中的起止秒，需要更长的上下文可按此回原片扩切。")
    with open(os.path.join(out_dir, "_视频分镜索引.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    print(f"  Markdown 索引 → _视频分镜索引.md")


def contact_sheet(idx, out_dir, cols=6, cw=320, ch=180):
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return
    items = idx["items"]
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cw * cols, (ch + 22) * rows), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    for i, it in enumerate(items):
        cap = cv2.VideoCapture(it["path"])
        n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(n * 0.5)))
        ok, fr = cap.read()
        cap.release()
        x, y = (i % cols) * cw, (i // cols) * (ch + 22)
        if ok:
            im = Image.fromarray(cv2.cvtColor(cv2.resize(fr, (cw, ch)), cv2.COLOR_BGR2RGB))
            sheet.paste(im, (x, y))
        d.text((x + 5, y + ch + 4),
               f"{i+1:02d} {it['时长s']}s {it['景别']} {it['动作']}", fill=(20, 20, 20))
    p = os.path.join(out_dir, "_视频分镜_接触表.jpg")
    sheet.save(p, quality=88)
    print(f"  接触表 → {os.path.basename(p)} {sheet.size}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--src", action="append", required=True)
    ap.add_argument("--th", type=float, default=0.25)
    ap.add_argument("--min-sec", type=float, default=1.2)
    ap.add_argument("--no-verify", action="store_true", help="跳过切点验证（默认开）")
    ap.add_argument("--verify-corr", type=float, default=0.45,
                    help="切前/切后直方图相关度上限；低于此值才算真切点")
    ap.add_argument("--tmp", default="/tmp/vsplit")
    ap.add_argument("--no-wm-check", action="store_true",
                    help="跳过水印预检（默认开：源视频与每个片段都查，带水印的丢弃）")
    ap.add_argument("--wm-frames", type=int, default=4, help="水印预检抽帧数（水印淡入淡出，建议 ≥4）")
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.tmp, exist_ok=True)
    # ★中转目录必须与 --out 同一文件系统：os.replace 跨设备（/tmp → 外置盘）会 EXDEV
    stage = os.path.join(a.out, "_tmp")
    os.makedirs(stage, exist_ok=True)
    items, n_cat = [], {}
    waste = []
    for si, src in enumerate(a.src, 1):
        if not os.path.exists(src):
            print(f"  ⚠️ 缺文件，跳过：{src}")
            continue
        # ── 水印预检①：源视频整支 ──────────────────────────────────
        if not a.no_wm_check:
            is_wm, hits, _tx = wm_scan_video(src, a.wm_frames, a.tmp)
            if is_wm:
                print(f"\n■ [{si}/{len(a.src)}] ⛔ {os.path.basename(src)} —— "
                      f"含水印（{'/'.join(hits)}）⇒ **整支跳过**"
                      f"（先去水印再拆，否则切出的片段全带水印）")
                waste.append({"name": os.path.basename(src), "path": src,
                              "层级": "源视频", "命中水印词": hits})
                continue
        w, h, fps, dur = probe(src)
        raw = scene_cuts(src, a.th)
        cuts = []
        for c in raw:
            if a.no_verify:
                cuts.append(c)
                continue
            ok, corr = verify_cut(src, c, f"{a.tmp}/v_{si:02d}_{int(c*100)}", a.verify_corr)
            print(f"     切点 {c:6.2f}s 验证 corr={corr} → {'✅ 真切' if ok else '✂️ 丢弃（运动尖峰）'}")
            if ok:
                cuts.append(c)
        segs = build_segments(dur, cuts, a.min_sec)
        cat, act = classify(os.path.basename(src))
        print(f"\n■ [{si}/{len(a.src)}] {os.path.basename(src)}  {w}×{h} {fps:.0f}fps {dur:.2f}s")
        print(f"   原始切点 {len(raw)} → 验真 {len(cuts)} → {len(segs)} 段")
        for k, (t0, t1) in enumerate(segs, 1):
            n_cat[cat] = n_cat.get(cat, 0) + 1
            tmpc = f"{stage}/{si:02d}_{k:02d}.mp4"
            cut(src, t0, t1, tmpc)
            lv, hp, how = "中景", 0.0, "—"
            votes = []
            frs = []
            for frac in (0.2, 0.5, 0.8):
                p = f"{a.tmp}/f_{si:02d}_{k:02d}_{int(frac*100)}.png"
                grab(tmpc, (t1 - t0) * frac, p)
                frs.append(p)
                bgr = cv2.imread(p)
                if bgr is not None:
                    _lv, _hp, _how = shot_level(bgr)
                    votes.append((_lv, _hp, _how))
            if votes:
                lv = max(set(v[0] for v in votes), key=lambda x: [v[0] for v in votes].count(x))
                hp = max(v[1] for v in votes)
                how = votes[0][2]
            labels, texts, tone = describe_frames(frs)
            mae = motion_mae(tmpc)
            name = f"{cat}_{act}镜{k:02d}_{lv}.mp4"
            dst = os.path.join(a.out, name)
            # ★防重名覆盖：不同源视频可能切出同名片段（如两个「俯卧撑镜02_全身」），
            #   旧写法 os.replace 会静默覆盖、丢片段。这里加后缀保命。
            if os.path.exists(dst):
                base = name[:-4]
                n = 2
                while os.path.exists(os.path.join(a.out, f"{base}_{n}.mp4")):
                    n += 1
                name = f"{base}_{n}.mp4"
                dst = os.path.join(a.out, name)
            os.replace(tmpc, dst)
            # ── 水印预检②：逐个片段（源采样点干净 ≠ 全片干净，水印会淡入淡出）──
            if not a.no_wm_check:
                is_wm, hits, _tx = wm_scan_video(dst, a.wm_frames, a.tmp)
                if is_wm:
                    try:
                        os.remove(dst)
                    except OSError:
                        pass
                    print(f"    镜{k:02d} ⛔ 片段含水印（{'/'.join(hits)}）→ 已丢弃 {name}")
                    waste.append({"name": name, "path": dst, "层级": "片段",
                                  "源视频": os.path.basename(src), "命中水印词": hits})
                    continue
            items.append({
                "name": name, "path": dst, "大类": cat, "动作": act, "景别": lv,
                "源视频": os.path.basename(src), "源路径": src,
                "镜序号": k, "镜总数": len(segs),
                "时间区间": [round(t0, 2), round(t1, 2)],
                "时长s": round(t1 - t0, 2),
                "主体高占比": hp, "景别判据": how,
                "帧间运动量": mae,
                "场景标签": labels, "画面文字": texts, "色调": tone,
                "适用场景": scenes_of(labels, act),
                "规格": f"{OUT_W}x{OUT_H}/{OUT_FPS}fps/no-audio",
            })
            print(f"    镜{k:02d} {t0:5.2f}-{t1:5.2f} ({t1-t0:4.2f}s) {lv:<3} MAE{mae:5.2f} → {name}")

    idx = {"生成时间": __import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M"),
           "输出目录": a.out, "切分阈值": a.th, "最短镜": a.min_sec,
           "水印预检": ("关" if a.no_wm_check else f"开（每支抽 {a.wm_frames} 帧 OCR）"),
           "废片数": len(waste), "废水清单": waste,
           "片段数": len(items), "大类统计": n_cat, "items": items}
    with open(os.path.join(a.out, "_视频分镜索引.json"), "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=1)
    write_index_md(idx, a.out)
    contact_sheet(idx, a.out)
    try:
        os.rmdir(stage)
    except OSError:
        pass
    print(f"\n合计 {len(items)} 个片段 → {a.out}")
    if waste:
        print(f"⛔ 水印废片 {len(waste)} 项未入库：")
        for w in waste:
            print(f"   [{w['层级']}] {w['name']}  命中 {'/'.join(w['命中水印词'])}")


if __name__ == "__main__":
    main()
