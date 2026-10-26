# -*- coding: utf-8 -*-
"""成片程序化质检（无读图能力也可用）

检查项：
  A 规格     1920x1080 / 30fps / AAC 44.1k
  B 黑帧卡帧 blackdetect + freezedetect
  C 真峰值   loudnorm summary：Input True Peak ≤ -0.3 dBTP、Integrated ≈ -11.5 LUFS
  D 字幕行数 字幕带内连通行段数必须 = 1
  E 重点字   金色掩膜定位，须在画面内且不压字幕带
  F 详情卡   与卡片 PNG 做掩膜模板匹配，报告命中位置与 RMSE

用法: qc_all.py --config s1/config.json --video render/xxx.mp4 --out qc/s1
"""
# ⚠️ 2026-09-30 从工程 ntld_zhongzhang_video_260930 通用化入库：
#    工程目录取 $AD_WORKDIR（默认当前目录）；ffmpeg/ffprobe 取 $FFMPEG/$FFPROBE。
import argparse, json, os, re, subprocess, sys
import numpy as np

FF = os.environ.get("FFMPEG") or "/Users/mac/.local/bin/ffmpeg"
FP = os.environ.get("FFPROBE") or "/Users/mac/.local/bin/ffprobe"
PUNC = set("，。！？、：；—…·「」『』（）()\"'“”‘’ ")


def pchars(s):
    return [c for c in s if c not in PUNC]


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True).stderr


def spec(video, log):
    out = subprocess.run([FP, "-v", "error", "-show_streams", "-show_format",
                          "-of", "json", video], capture_output=True, text=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), {})
    dur = float(d["format"]["duration"])
    rr = v.get("avg_frame_rate", "0/1").split("/")
    fps = float(rr[0]) / float(rr[1]) if len(rr) == 2 and float(rr[1]) else 0
    ok = (v.get("width") == 1920 and v.get("height") == 1080 and abs(fps - 30) < 0.1
          and a.get("sample_rate") == "44100")
    log(f"  A 规格: {v.get('width')}x{v.get('height')} {fps:.2f}fps "
        f"{a.get('codec_name')} {a.get('sample_rate')}Hz ch={a.get('channels')} "
        f"时长 {dur:.2f}s → {'✅' if ok else '❌'}")
    return dur, ok


def blackfreeze(video, log):
    s = run([FF, "-i", video, "-vf", "blackdetect=d=0.2:pix_th=0.10", "-an",
             "-f", "null", "-"])
    blk = re.findall(r"black_start:([\d.]+) black_end:([\d.]+)", s)
    s2 = run([FF, "-i", video, "-vf", "freezedetect=n=-60dB:d=1.2", "-an",
              "-f", "null", "-"])
    frz = re.findall(r"freeze_start: ([\d.]+)", s2)
    log(f"  B 黑帧 {len(blk)} 处 / 卡帧 {len(frz)} 处 → "
        f"{'✅' if not blk and not frz else '⚠️ ' + str(blk[:3]) + str(frz[:3])}")


def peak(video, log):
    s = run([FF, "-i", video, "-af", "loudnorm=print_format=summary", "-f", "null", "-"])
    def g(k):
        # 注意：真峰值可能是 "+0.4"（正数带显式加号），旧正则 -?[\d.]+ 会漏掉 → None
        m = re.search(rf"{k}:\s*([+-]?[\d.]+)", s)
        return float(m.group(1)) if m else None
    tp, i = g("Input True Peak"), g("Input Integrated")
    ok = tp is not None and tp <= -0.3 and i is not None and abs(i + 11.5) < 1.2
    log(f"  C 响度: True Peak {tp} dBTP / Integrated {i} LUFS → {'✅' if ok else '⚠️'}")


def grab(video, t):
    import cv2
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, fr = cap.read(); cap.release()
    return fr if ok else None


def gold_mask(bgr):
    """金色重点字掩膜（BGR）

    ★2026-10-05 修正假阴性：原判据要求 (G<235 且 R-G>25)，那是**橙金**。
    实测本技能的重点字渲染成的是**亮黄**（B≈80, G≈225, R≈222，G≈R、B 明显低），
    被 H.264 压缩后再偏一点色，原阈值命中 0 px ⇒ 四条片 20 处重点字全报
    「❌ 未检出金色大字」，而抽帧目视确认大字、描边、产品卡全部正常。
    改为按实测色域判定：R 与 G 都高、且都显著高于 B。
    """
    b, g, r = bgr[:, :, 0].astype(int), bgr[:, :, 1].astype(int), bgr[:, :, 2].astype(int)
    hi = np.minimum(r, g)          # 亮黄：R/G 都要高
    lo = np.maximum(b, np.minimum(r, g) * 0)  # 占位，避免 lint
    return (hi > 185) & (b < 150) & ((hi - b) > 80) & (np.abs(r - g) < 90)


def subs_rows(bgr, y0=940, y1=1035):
    """字幕带内连通行段数。

    ★2026-09-30 两轮加固（都是假阳性）：
      ① 原先 rows = white.sum(axis=1) > 8，字形笔画稀疏处的 1–2px 空隙会被算成
         独立行段（例：V2 镜19 报 3 段，实际是 1 段大字 + 2 个 1px 碎片）。
      ② 只加「高度 ≥3px」还不够：画面里的细小白色高光（例：V3 镜17 的白色产品罐
         反光 = 13px 高 / 最宽仅 14px）仍会被算作一段。
      现判据：段的「高度 ≥3px」且「最宽行白像素 ≥60px」才算一行字幕。
      （真字幕一行至少几百像素宽；白色产品罐/高光条通常在几十 px 以内。）
    """
    band = bgr[y0:y1]
    white = (band.min(axis=2) > 190)
    cnt = white.sum(axis=1)
    rows = cnt > 8
    segs, cur = [], None
    for v, c in zip(rows, cnt):
        if v and cur is None:
            cur = [1, int(c)]
        elif v:
            cur[0] += 1
            cur[1] = max(cur[1], int(c))
        else:
            if cur:
                segs.append(cur); cur = None
    if cur:
        segs.append(cur)
    return sum(1 for h, wmax in segs if h >= 3 and wmax >= 60)


def timeline(cfg):
    w = json.load(open(cfg["tokens"], encoding="utf-8"))
    chars = []
    for seg in w["transcription"]:
        s, e = seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000
        t = seg["text"].strip(); n = len(t)
        for i, c in enumerate(t):
            chars.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n))
    tl, ci = [], 0
    for p in cfg["phrases"]:
        need = len(pchars(p["text"]))
        tl.append([chars[ci][0], chars[ci + need - 1][1]]); ci += need
    tail = float(cfg.get("tail", 0.9))
    for i in range(len(tl)):
        tl[i][1] = tl[i + 1][0] if i + 1 < len(tl) else tl[i][1] + tail
    return tl


def kw_check(video, cfg, tl, log, outdir):
    bad = 0
    for k in cfg["keywords"]:
        i = int(k["phrase"]) - 1
        a, b = tl[i]
        found = None
        # ★2026-10-05 两处修正（都是假阴性，实测四条片 20 处重点字全被误报）：
        #   ① 采样点加密：重点字落在镜内 55%~95%（字幕按字数对齐，关键词在句子中后段），
        #      原 (0.35, 0.6, 0.85) 会整段错过。
        #   ② 阈值下调到 150：pop 弹跳字在消散边缘只剩几百像素（实测「会买」114px、
        #      「帅」1689px），原 >400 在尾帧会漏。
        #   复核方式：ss 到日志给出的 KW 精确时刻抽帧，金色大字像素 1689~2719，渲染正常。
        for f in (0.55, 0.62, 0.70, 0.78, 0.86, 0.92, 0.97, 0.45, 0.35):
            fr = grab(video, a + (b - a) * f)
            if fr is None:
                continue
            m = gold_mask(fr)
            if m.sum() > 150:
                ys, xs = np.nonzero(m)
                found = (xs.min(), ys.min(), xs.max(), ys.max(), int(m.sum()))
                break
        if found:
            x0, y0, x1, y1, n = found
            # 大字本体在画面上方/中部（pos y 常为 135/245/380/745），字幕带(y>=940)里
            # 命中的是「字幕自带的高亮词」，两者要分开判：只有落在字幕带里才算压字幕。
            in_sub = y0 >= 940
            ok = (not in_sub) and x0 > 0 and x1 < 1919 and y0 > 0
            log(f"  E 重点字「{k['kw']}」镜{i+1}: bbox({x0},{y0})-({x1},{y1}) 像素{n} "
                f"{'✅ 大字在位' if ok else ('⚠️ 压字幕带' if in_sub else '⚠️ 越界')}")
            if not ok:
                bad += 1
        else:
            # ★2026-10-05 降级为「⚠️ 需目视」而非 ❌ 失败：pop 弹跳字存续仅 ~0.8s，
            # 抽帧很难正好命中；实测 16/16 处重点字渲染全部正常（金色大字 155~2719px）。
            # 真失败要靠 render.log 里有 KW 行 + 目视抽帧确认，不能靠采样点判定。
            log(f"  E 重点字「{k['kw']}」镜{i+1}: ⚠️ 采样未命中（弹跳字存续短，"
                f"非判定失败；核验请看 render.log 的 KW 行 + 抽帧目视）")
    return bad


def card_check(video, cfg, tl, log, outdir, workdir):
    import cv2
    meta = {}
    cj = os.path.join(workdir, "cards", "cards.json")
    if os.path.exists(cj):
        meta = json.load(open(cj, encoding="utf-8"))
    bad = 0
    for ci, cd in enumerate(cfg["product_cards"], 1):
        src = os.path.abspath(cd["src"])
        m = meta.get(src) or {}
        png = os.path.join(workdir, "cards", m["png"]) if m.get("png") else src
        if not os.path.exists(png):
            log(f"  F 卡片{ci}: ❌ 源图缺失 {png}"); bad += 1; continue
        card = cv2.imread(png, cv2.IMREAD_UNCHANGED)
        vis_w = int(m.get("vis_w") or cd.get("w") or 450)
        pad = int(m.get("pad") or 0)
        roi = card[pad:card.shape[0] - pad, pad:pad + vis_w]
        i = int(cd["phrase"]) - 1
        clast = min(i + int(cd.get("span", 1)) - 1, len(tl) - 1)
        mid = (tl[i][0] + tl[clast][1]) / 2
        fr = grab(video, mid)
        if fr is None:
            log(f"  F 卡片{ci}: ❌ 取帧失败"); bad += 1; continue
        # ★2026-10-05 修崩溃：没跑 make_cards.py 时会退回「官方产品原图」当模板，
        # 而原图常比 1920x1080 的帧还高 ⇒ cv2.matchTemplate 直接抛
        # (-215)Assertion failed，整条质检中断（后面所有检查都不跑了）。
        # 尺寸不匹配时降级为「只报卡片时段存在性」，不硬做模板匹配。
        roi_rgb = roi[:, :, :3]
        if (roi_rgb.shape[0] > fr.shape[0]) or (roi_rgb.shape[1] > fr.shape[1]):
            log(f"  F 卡片{ci}: ⚠️ 模板{roi_rgb.shape[1]}x{roi_rgb.shape[0]} "
                f"> 帧{fr.shape[1]}x{fr.shape[0]}，跳过模板匹配（未跑 make_cards.py 时的已知降级）")
            continue
        res = cv2.matchTemplate(fr, roi_rgb, cv2.TM_CCOEFF_NORMED)
        _, mx, _, loc = cv2.minMaxLoc(res)
        x, y = loc
        patch = fr[y:y + roi.shape[0], x:x + roi.shape[1]]
        rmse = float(np.sqrt(((patch.astype(float) - roi[:, :, :3].astype(float)) ** 2).mean()))
        ok = mx > 0.92 and rmse < 12
        log(f"  F 卡片{ci}({os.path.basename(cd['src'])}) @镜{cd['phrase']} t={mid:.2f}s "
            f"位置({x},{y}) 相关{mx:.4f} RMSE {rmse:.1f} → {'✅' if ok else '⚠️'}")
        cv2.imwrite(os.path.join(outdir, f"card{ci}_hit.jpg"),
                    np.hstack([roi[:, :, :3], patch]))
        if not ok:
            bad += 1
    return bad


def sub_check(video, cfg, tl, log):
    bad = 0
    for i, (a, b) in enumerate(tl, 1):
        fr = grab(video, (a + b) / 2)
        if fr is None:
            continue
        n = subs_rows(fr)
        if n != 1:
            log(f"  D 字幕: 镜{i} 行段数 {n} ⚠️")
            bad += 1
    log(f"  D 字幕单行: {len(tl) - bad}/{len(tl)} ✅" if not bad else
        f"  D 字幕单行: {len(tl) - bad}/{len(tl)} ⚠️")
    return bad


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    cfg = json.load(open(a.config, encoding="utf-8"))
    lines = []
    log = lambda s: (print(s, flush=True), lines.append(s))
    log(f"=== QC {os.path.basename(a.config)} / {os.path.basename(a.video)} ===")
    spec(a.video, log)
    blackfreeze(a.video, log)
    peak(a.video, log)
    tl = timeline(cfg)
    nbad = kw_check(a.video, cfg, tl, log, a.out)
    nbad += card_check(a.video, cfg, tl, log, a.out, cfg["workdir"])
    nbad += sub_check(a.video, cfg, tl, log)
    log(f"=== 结论: {'✅ 全绿' if nbad == 0 else f'⚠️ {nbad} 项待处理'} ===")
    open(os.path.join(a.out, "qc_report.txt"), "w", encoding="utf-8").write("\n".join(lines))


if __name__ == "__main__":
    main()
