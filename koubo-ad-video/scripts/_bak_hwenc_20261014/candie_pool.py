#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""灿爹素材工具 —— 临时入口 → 处理 → 规范命名 → 归档图库主体

临时入口（inbox，默认）：/Volumes/陈灿会展/广告视频/分镜图库/小绿人/灿爹/
  ← 灿爹把待处理的原始图片/视频丢进来（直接丢根目录即可，不用建子目录）
  归档目标（图库主体）：/Volumes/陈灿会展/广告视频/分镜图库/小绿人/
  ← 与原生 240+ 素材同层扁平存放，文件名即检索关键词（见 ../_取名指南.txt）

标准流程（2026-09-28 灿爹拍板，2026-09-30 补 ⓪⓪b）：
  投料 → ⓪ 查重复投料（与 _旧版备份/ 逐字节比对，命中则跳过处理）
       → ⓪b 冷备份原始件到 _旧版备份/<日期>_入口原图[_第N批]/
       → ① 去水印 ② 提清晰度 → ③ 按「大类_具体画面_景别」规范命名
       → ④ 归档到图库主体根目录 → ⑤ 入口内原始文件移入废纸篓（可恢复）

铁律：
  ★ 任何新素材先扔进「灿爹」，处理完当场清空 → 它只是入口，不留素材；
  ★ 入口非空时**先看有没有重复投料**（历史批次清空失败的残留）——ingest 会自己报，
    不加 --drop-dupes 时只跳过、不删；★以前没这道闸门，会静默产出重复品。
  ★ 生成视频前先按文案关键词在图库主体里检索现成素材（或读归档侧栏的素材索引.json）；
  ★ 低清素材先 `enhance` 超分+锐化，或在成片时叠加锐化/动效；
  ★ 凡带水印的素材，先 `clean`（图）/ `cleanvid`（视频）去水印再使用；
  ★ 视频水印多位置漂移，auto-multi 会误检 → 必须传实测 --vrect（见 _视频水印备忘.txt）。

子命令
  ingest    ★一键全流程：入口 → 去水印+提清 → 规范命名 → 归档主体 → 清空入口
  clean     图片批量去水印（+ 可选清晰度增强 / 超分）
  cleanvid  视频去水印（逐帧 inpaint），支持裁切 / 缩放 / 锐化
  enhance   单张素材清晰度增强（超分 + 锐化）
  scan      扫描目录 → 素材索引.json + 素材总览_<日期>.jpg
  reel      用某目录图片生成简单样片（Ken Burns + 交叉溶解 + BGM + 锐化）

示例
  python candie_pool.py ingest --dry-run                 # 先看归档方案与命名草稿
  python candie_pool.py ingest --names "IMG_1.jpg=俯卧撑_地面_中景.jpg"
  python candie_pool.py ingest --vrect "0.846,0.798,0.952,0.874"   # 含视频时必传
  python candie_pool.py scan --src ../                        # 索引图库主体
  python candie_pool.py cleanvid --in x.mp4 --rect "auto-multi" --scale 1920:1080
"""
from __future__ import annotations

import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import numpy as np
import cv2
from PIL import Image, ImageDraw, ImageFont

FFMPEG = os.environ.get("FFMPEG", "/Users/mac/.local/bin/ffmpeg")
FFPROBE = os.environ.get("FFPROBE", "/Users/mac/.local/bin/ffprobe")
SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(SKILL_ROOT, "assets")

DEFAULT_POOL = "/Volumes/陈灿会展/广告视频/分镜图库/小绿人/灿爹"
DIR_IMG, DIR_VID, DIR_RAW, DIR_REEL, DIR_IDX = "图片", "视频", "_原始", "样片", "索引"

# ---- 水印规律（2026-09-28 实测）------------------------------------------------
# 「小云雀AI」水印位于画面右下，位置在【相对坐标】上跨分辨率稳定：
#   2731x1536 实测外接框 x 2325~2673 / y 1389~1481 → 相对 x 0.8513~0.9788 / y 0.9043~0.9642
#   2304x1728 / 2848x1600 / 4096x2304 / 5504x3040 / 1792x1344 亦全部落在下方框内（含余量）
WM_RECT_DEFAULT = (0.834, 0.884, 0.995, 0.981)
# 白色低饱和阈值（分离白色水印）：灰度阈值会把浅色画面算进去，必须用 min>V 且 max-min<S
WM_VMIN, WM_SAT = 135, 52

# ---- ★水印「笔画掩膜」模板（2026-09-29 新增，去水印的默认正解）--------------------
# 背景：旧路径靠「白色低饱和」掩膜，掩膜太小就回退整框，候选里还有 flat-fill / inpaint-rect
#       —— 三者都是【整框填充】。整框填充在平滑渐变或纹理背景上会留下一块【边界可见的色块】。
#       2026-09-29 灿爹第 2 批 27 张实测：6 张穿帮（深棕桌面被填成浅米色、木地板被抹糊、
#       深蓝底出现矩形补丁、浅灰底出现斜边色块）。
# 正解：水印是【固定模板】，可从任意一张原图用「曲面拟合背景 → 反解 alpha」标定出来；
#       之后按 ROI 尺寸缩放模板 → 只把【笔画像素】纳入掩膜 → inpaint。
#       只填笔画、不动周边内容 ⇒ 桌面/地板/渐变背景全部保真。
# 实测：残留量降到原图的 1%~27%，且全部低于各区域的自然纹理基线。
WM_TPL_FILE = "wm_tpl_xiaoyunque.npy"   # 与本源文件同目录；缺失则自动回退旧路径
WM_STROKE_TH = 0.03                     # 模板 alpha 二值化阈值
WM_STROKE_DILATE = 12                   # 笔画膨胀（吸收抗锯齿边缘；实测定档）
_WM_TPL_CACHE = None


def load_wm_template(path: str | None = None):
    """加载「小云雀AI」水印 alpha 模板（0~1，ROI 归一化尺寸）。找不到返回 None。"""
    global _WM_TPL_CACHE
    if _WM_TPL_CACHE is None:
        p = path or os.path.join(os.path.dirname(os.path.abspath(__file__)), WM_TPL_FILE)
        try:
            _WM_TPL_CACHE = (np.load(p).astype(np.float32)
                             if os.path.exists(p) else False)
        except Exception:
            _WM_TPL_CACHE = False
    return _WM_TPL_CACHE if _WM_TPL_CACHE is not False else None


# ---------------------------------------------------------------- 基础 IO
def load_rgb(path: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert("RGB"))


def save_rgb(path: str, arr: np.ndarray, quality: int = 95) -> None:
    im = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if path.lower().endswith((".jpg", ".jpeg")):
        im.save(path, quality=quality, subsampling=0, optimize=True)
    else:
        im.save(path)


def probe(path: str) -> dict:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate,nb_frames",
         "-show_entries", "format=duration", "-of", "json", path],
        capture_output=True, text=True).stdout
    d = json.loads(out or "{}")
    st = (d.get("streams") or [{}])[0]
    num, _, den = (st.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1)
    return {"w": int(st.get("width") or 0), "h": int(st.get("height") or 0),
            "fps": round(fps, 3), "dur": float(d.get("format", {}).get("duration") or 0),
            "frames": int(st.get("nb_frames") or 0)}


def human(nbytes: int) -> str:
    for u in ("B", "KB", "MB", "GB"):
        if nbytes < 1024 or u == "GB":
            return f"{nbytes:.0f}{u}" if u == "B" else f"{nbytes:.1f}{u}"
        nbytes /= 1024
    return f"{nbytes:.1f}GB"


# ---------------------------------------------------------------- 水印
def wm_mask_in_roi(roi: np.ndarray, vmin=WM_VMIN, sat=WM_SAT,
                   close=(15, 5), dilate=7, fallback_ratio=0.015):
    """在给定 ROI 内提取白色水印 mask。返回 (mask, 判定方式)。"""
    a = roi.astype(np.int16)
    mn = a.min(axis=2)
    mx = a.max(axis=2)
    m = (((mn > vmin) & ((mx - mn) < sat)).astype(np.uint8) * 255)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones(close, np.uint8))
    area = float(m.shape[0] * m.shape[1])
    if m.sum() / 255.0 < fallback_ratio * area:
        m = np.full_like(m, 255)          # 白底图上水印太淡 → 整框修复最稳
        how = "fixed-rect"
    else:
        how = "mask"
    m = cv2.dilate(m, np.ones((dilate, dilate), np.uint8))
    return m, how


def _exemplar_patch(arr: np.ndarray, x0, y0, x1, y1, pad=8):
    """样本块匹配：把「ROI 外圈环」当模板全图搜索，返回最相似区域的内部块。

    返回 (patch, 来源坐标, 平均平方误差)；找不到可用块返回 None。
    """
    H, W = arr.shape[:2]
    x0p, y0p = max(0, x0 - pad), max(0, y0 - pad)
    x1p, y1p = min(W, x1 + pad), min(H, y1 + pad)
    th, tw = y1p - y0p, x1p - x0p
    if th < 8 or tw < 8:
        return None
    tmpl = arr[y0p:y1p, x0p:x1p]
    rx0, ry0, rx1, ry1 = x0 - x0p, y0 - y0p, x1 - x0p, y1 - y0p
    m = np.full((th, tw), 255, np.uint8)
    m[ry0:ry1, rx0:rx1] = 0                      # 环 = 模板去掉 ROI 内部
    n_ring = int((m > 0).sum())
    if n_ring < 64:
        return None
    res = cv2.matchTemplate(arr, tmpl, cv2.TM_SQDIFF, mask=m)
    # ★排除「贴回后会与 ROI 重叠」的候选源块（2026-09-29 修）：
    # matchTemplate 的 res 索引是【模板左上角】，而真正贴回去的是 [sx+rx0, sx+rx1) × [sy+ry0, sy+ry1)。
    # 所以 sx 的禁区 = (x0-(tw-rx0), x1-rx0)，sy 同理；旧写法用 ex=tw//2 只挡了「左上角落在 ROI 附近」，
    # 仍放行 sx∈(x0-tw+rx0, x0-tw//2) 这类【左半截压在 ROI 上】的源块 ⇒ 把水印本身当纹理搬回来。
    # 实测后果（2026-09-29 灿爹批次）：3 张 5504×3040 成品右下角残留半个「🔵小云雀AI」。
    ex0x, ex1x = x0 - (tw - rx0), x1 - rx0
    ex0y, ex1y = y0 - (th - ry0), y1 - ry0
    res[max(0, ex0y):max(0, ex1y), max(0, ex0x):max(0, ex1x)] = np.inf
    _, _, ml, _ = cv2.minMaxLoc(res)
    sx, sy = ml
    if not np.isfinite(res[sy, sx]):
        return None
    patch = arr[sy + ry0:sy + ry1, sx + rx0:sx + rx1]
    if patch.shape[:2] != (y1 - y0, x1 - x0):
        return None
    return patch.copy(), (int(sx + rx0), int(sy + ry0)), round(float(res[sy, sx]) / n_ring, 1)


def _mirror_left_patch(arr, x0, y0, x1, y1):
    w = x1 - x0
    if x0 - w < 0:
        return None
    return arr[y0:y1, x0 - w:x0][:, ::-1].copy()


def _texture_transfer(arr, filled, x0, y0, x1, y1, sigma=3.0, amt=0.9):
    """给 inpaint 出来的「糊面」补高频纹理：把左邻区的高频分量叠加到填充区。

    inpaint 天然只产低频（所以看着像涂抹），补上邻近同质区的高频（木纹/颗粒/菜叶）
    后，观感从「糊」变成「有质感」，且不引入错位结构。
    """
    src = _mirror_left_patch(arr, x0, y0, x1, y1)
    if src is None:
        src = arr[max(0, y0 - (y1 - y0)):y0, x0:x1][::-1, :] if y0 - (y1 - y0) >= 0 else None
    if src is None or src.shape[:2] != (y1 - y0, x1 - x0):
        return filled
    # 纹理门槛：源区本身是「平的」（白底/纯色墙）就不补高频，否则会在纯色区留下灰带
    g = cv2.cvtColor(src, cv2.COLOR_RGB2GRAY).astype(np.float32)
    ref_tex = float((g - cv2.GaussianBlur(g, (0, 0), sigma)).std())
    amt_eff = amt * float(np.clip((ref_tex - 2.0) / 6.0, 0.0, 1.0))
    if amt_eff <= 0.02:
        return filled
    low = cv2.GaussianBlur(src, (0, 0), sigma)
    hp = src.astype(np.int16) - low.astype(np.int16)
    out = filled.copy()
    reg = out[y0:y1, x0:x1].astype(np.int16) + (hp * amt_eff).astype(np.int16)
    out[y0:y1, x0:x1] = np.clip(reg, 0, 255).astype(np.uint8)
    return out


def _blend_patch(arr, patch, x0, y0, x1, y1, feather=4):
    alpha = np.zeros((y1 - y0, x1 - x0), np.float32)
    f = max(0, feather)
    if f:
        cv2.rectangle(alpha, (f, f), (x1 - x0 - 1 - f, y1 - y0 - 1 - f), 1.0, -1)
        alpha = cv2.GaussianBlur(alpha, (0, 0), f / 1.6)
    else:
        alpha[:] = 1.0
    a = alpha[..., None]
    out = arr.copy()
    out[y0:y1, x0:x1] = (arr[y0:y1, x0:x1].astype(np.float32) * (1 - a)
                         + patch.astype(np.float32) * a).astype(np.uint8)
    return out


def _score_fill(img, x0, y0, x1, y1, ring=34):
    """给填充结果打分（越小越好）：接缝连续性 + 纹理保真度。

    - 接缝：框左/上边界两侧的像素差（ROI 贴画面右下角，只有两条真接缝）
    - 纹理：框内 Laplacian 能量 / 框外参考带能量；远低于参考 = 涂抹痕（重罚）
    """
    H, W = img.shape[:2]
    g = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
    seam, cnt = 0.0, 0
    if x0 > 0:
        seam += float(np.abs(g[y0:y1, x0].astype(np.int16) - g[y0:y1, x0 - 1].astype(np.int16)).mean())
        cnt += 1
    if y0 > 0:
        seam += float(np.abs(g[y0, x0:x1].astype(np.int16) - g[y0 - 1, x0:x1].astype(np.int16)).mean())
        cnt += 1
    seam /= max(cnt, 1)

    lap = cv2.Laplacian(g, cv2.CV_32F)
    inside = float(lap[y0:y1, x0:x1].std())
    ex0, ey0 = max(0, x0 - ring), max(0, y0 - ring)
    band = lap[ey0:y0, ex0:x1]
    if band.size < 64:
        band = lap[max(0, y0 - ring * 2):y0, :]
    ref = float(band.std()) if band.size else inside
    ratio = inside / max(ref, 1e-3)
    # 纹理惩罚只在「周边本身有纹理」时生效：纯色/白底背景上，填得平才是对的
    gate = float(np.clip((ref - 2.0) / 6.0, 0.0, 1.0))
    pen = gate * (45.0 * max(0.0, 1.0 - ratio) + 45.0 * max(0.0, ratio - 2.2))
    return seam + pen, round(seam, 2), round(ratio, 2), round(ref, 2)


def clean_image_wm(arr: np.ndarray, rect=WM_RECT_DEFAULT, inpaint_radius=5,
                   method="auto", exemplar_max=1200.0):
    """去掉右下角水印，返回 (新图, 说明, 填充像素数)。

    ★默认走「模板笔画掩膜」路径（2026-09-29 起）：只填水印笔画本身，周边内容零改动。
      标定模板缺失时回退下面的旧路径。

    method='auto'（旧路径）：候选 = ①样本块匹配（仅当环匹配 e² ≤ exemplar_max 时才可信）
      / ②inpaint(TELEA) / ③inpaint + 邻域纹理迁移；按「接缝连续性 + 纹理保真度」择优。
      实测：样本块 e² 高时（>1200）会从远处搬来不相干内容（绿手/木纹板），必须弃用；
      inpaint 只产低频看着像涂抹，补上邻近高频后观感自然 —— 故 ③ 常胜出。
      ⚠️ 但整条旧路径都是【整框填充】，在平滑渐变/纹理背景上会留边界可见的色块，勿作默认。
    """
    tpl = load_wm_template()
    if method == "auto" and tpl is not None:
        H, W = arr.shape[:2]
        x0, y0 = int(W * rect[0]), int(H * rect[1])
        x1, y1 = int(W * rect[2]), int(H * rect[3])
        t = cv2.resize(tpl, (x1 - x0, y1 - y0), interpolation=cv2.INTER_LINEAR)
        m = (t > WM_STROKE_TH).astype(np.uint8) * 255
        if WM_STROKE_DILATE > 0:
            m = cv2.dilate(m, cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (WM_STROKE_DILATE * 2 + 1,) * 2))
        full = np.zeros((H, W), np.uint8)
        full[y0:y1, x0:x1] = m
        out = cv2.inpaint(arr, full, max(inpaint_radius, 6), cv2.INPAINT_TELEA)
        return out, f"stroke-mask(th{WM_STROKE_TH}/d{WM_STROKE_DILATE})", int(m.sum() // 255)

    H, W = arr.shape[:2]
    x0, y0 = int(W * rect[0]), int(H * rect[1])
    x1, y1 = int(W * rect[2]), int(H * rect[3])
    px = (x1 - x0) * (y1 - y0)

    m, how = wm_mask_in_roi(arr[y0:y1, x0:x1])
    full = np.zeros((H, W), np.uint8)
    full[y0:y1, x0:x1] = m
    inp = cv2.inpaint(arr, full, inpaint_radius, cv2.INPAINT_TELEA)

    if method == "inpaint":
        return inp, how + "+inpaint", int(m.sum() // 255)

    cands = [("inpaint+tex", _texture_transfer(arr, inp, x0, y0, x1, y1), how + "+inpaint+纹理"),
             ("inpaint", inp, how + "+inpaint")]
    # 整框 inpaint：mask 只盖住「检出的水印像素」，近白背景上会留下抗锯齿边缘的淡残影；
    # 整框修复则连边缘一起平滑续接 —— 纯色/渐变背景下的正解（纹理区会被纹理项罚掉）。
    full_rect = np.zeros((H, W), np.uint8)
    full_rect[y0:y1, x0:x1] = 255
    cands.append(("inpaint-rect",
                  cv2.inpaint(arr, full_rect, max(inpaint_radius, 7), cv2.INPAINT_TELEA),
                  how + "+inpaint整框"))

    # 纯色/白底背景：整框直接填周边中位色最干净（不留残影、不留接缝）
    ring = 34
    by0, by1 = max(0, y0 - ring), y0
    bx0 = max(0, x0 - ring)
    parts = []
    if by1 > by0:
        parts.append(arr[by0:by1, bx0:x1].reshape(-1, 3))
    if x0 > bx0:
        parts.append(arr[y0:y1, bx0:x0].reshape(-1, 3))
    if parts:
        b = np.concatenate(parts)
        g = cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY).astype(np.float32)
        bg_tex = float(cv2.Laplacian(g, cv2.CV_32F)[by0:by1, bx0:x1].std()) if by1 > by0 else 99.0
        # 边界连续性：ROI 左邻列 / 上邻行有没有「穿过该区域的物体边缘」。
        # 注意用高频（Laplacian）而非灰度标准差 —— 否则背景的平滑渐变会被误判成结构。
        lapg = cv2.Laplacian(g, cv2.CV_32F)
        edge_std = 0.0
        if x0 > 3:
            edge_std = max(edge_std, float(lapg[y0:y1, x0 - 3].std()))
        if y0 > 3:
            edge_std = max(edge_std, float(lapg[y0 - 3, x0:x1].std()))
        if bg_tex < 3.5 and edge_std < 11.0:
            patch = np.tile(np.median(b, axis=0).astype(np.uint8), (y1 - y0, x1 - x0, 1))
            cands.insert(0, ("flat-fill", _blend_patch(arr, patch, x0, y0, x1, y1, feather=6),
                             f"flat-fill(周边纯净 纹理{bg_tex:.1f}/边界{edge_std:.1f})"))
    got = _exemplar_patch(arr, x0, y0, x1, y1)
    if got is not None and got[2] <= exemplar_max:
        # 只在「环匹配足够像」时启用；否则会从远处搬来不相干内容（实测搬来过绿手/木纹板）
        cands.insert(0, ("exemplar", _blend_patch(arr, got[0], x0, y0, x1, y1),
                         f"exemplar(e²={got[2]:g})"))

    best, best_s, best_tag = None, 1e18, ""
    for name, img, tag in cands:
        s, seam, ratio, ref = _score_fill(img, x0, y0, x1, y1)
        if s < best_s:
            best, best_s, best_tag = img, s, f"{tag} 接缝{seam}/纹理{ratio}/周边{ref}"

    # ★残影守门（2026-09-29 加）：候选打分只衡量「接缝连续性 + 纹理保真」，**看不出水印到底抹掉没有**
    # ——exemplar 一旦把含水印的源块搬回来，分数照样漂亮（实测 2026-09-29：3 张 5504×3040 的
    # exemplar 候选把水印搬回，e² 低到 52~124 仍被选中，成品残留半个「🔵小云雀AI」）。
    # 故收尾必须回查 ROI 内还剩多少白像素：若「原水印白像素」残留 >15% 且绝对量可观
    # → 退回整框 inpaint（牺牲一点纯色区清晰度，换「保证抹掉」）。
    # ⚠️ 这里必须用【裸阈值计数】，不能调 wm_mask_in_roi —— 后者在掩膜为空时会按 fallback
    #    返回「整框全白」，会把「已经抹干净」误判成「残留满框」，反而把好图改成涂抹。
    def _raw_white(roi):
        a = roi.astype(np.int16)
        mn = a.min(axis=2)
        mx = a.max(axis=2)
        return int((((mn > WM_VMIN) & ((mx - mn) < WM_SAT))).sum())

    orig_px = _raw_white(arr[y0:y1, x0:x1])
    left_px = _raw_white(best[y0:y1, x0:x1])
    if orig_px > 0 and left_px > 0.15 * orig_px and left_px > 0.002 * px:
        rect_fix = cv2.inpaint(arr, full_rect, max(inpaint_radius, 7), cv2.INPAINT_TELEA)
        if _raw_white(rect_fix[y0:y1, x0:x1]) < left_px * 0.5:
            best, best_tag = rect_fix, (best_tag +
                f" → 残影守门(原{orig_px}/剩{left_px}px)改整框inpaint")
    return best, best_tag, px


def enhance_clarity(arr: np.ndarray, min_long: int | None = None,
                    sharpen: float = 0.55, denoise: float = 0.0):
    """清晰度增强：可选超分（Lanczos4）+ 可选降噪 + 反锐化掩模锐化。"""
    out = arr
    h, w = out.shape[:2]
    if min_long and max(h, w) < min_long:
        f = min_long / max(h, w)
        out = cv2.resize(out, (int(round(w * f)), int(round(h * f))),
                         interpolation=cv2.INTER_LANCZOS4)
    if denoise > 0:
        out = cv2.bilateralFilter(out, 5, denoise, denoise)
    if sharpen > 0:
        blur = cv2.GaussianBlur(out, (0, 0), 1.1)
        out = cv2.addWeighted(out.astype(np.int16), 1 + sharpen,
                              blur.astype(np.int16), -sharpen, 0)
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- clean
def cmd_clean(a):
    pool = os.path.abspath(a.pool)
    out_dir = os.path.join(pool, DIR_IMG)
    raw_dir = os.path.join(out_dir, DIR_RAW)
    os.makedirs(raw_dir, exist_ok=True)

    src_dir = os.path.abspath(a.src) if a.src else pool
    names = {}
    nm_path = os.path.join(pool, DIR_IDX, "改名映射.json")
    if os.path.exists(nm_path):
        names = {k: v for k, v in json.load(open(nm_path, encoding="utf-8")).items()
                 if not k.startswith("_")}

    files = []
    for ext in ("*.JPEG", "*.jpg", "*.jpeg", "*.png", "*.webp"):
        files += glob.glob(os.path.join(src_dir, ext))
    files = sorted(f for f in files if not os.path.basename(f).startswith("._"))
    files = [f for f in files if os.path.dirname(f) != os.path.abspath(out_dir)]
    if not files:
        print(f"[clean] {src_dir} 内无待处理图片")
        return

    print(f"[clean] 待处理 {len(files)} 张 → {out_dir}")
    print(f"{'原名':<44}{'语义名':<40}{'尺寸':<14}{'去水印':<10}{'输出'}")
    report = []
    for f in files:
        base = os.path.basename(f)
        stem = os.path.splitext(base)[0]
        sem = names.get(base) or names.get(stem) or stem
        sem = os.path.splitext(sem)[0]
        out_name = sem + (".jpg" if a.jpeg else ".png")
        out_path = os.path.join(out_dir, out_name)

        arr = load_rgb(f)
        cleaned, how, mpx = clean_image_wm(arr, inpaint_radius=a.inpaint_radius,
                                           method=a.method)
        if a.no_enhance:
            final = cleaned
        else:
            final = enhance_clarity(cleaned, min_long=a.min_long,
                                    sharpen=a.sharpen, denoise=a.denoise)
        save_rgb(out_path, final, quality=a.quality)

        if a.move_originals:
            dst = os.path.join(raw_dir, base)
            if os.path.exists(dst):
                dst = os.path.join(raw_dir, stem + "_1" + os.path.splitext(base)[1])
            shutil.move(f, dst)
        h, w = final.shape[:2]
        print(f"{base:<44}{out_name:<40}{f'{w}x{h}':<14}{how:<10}{human(os.path.getsize(out_path))}")
        report.append({"src": base, "out": out_name, "w": w, "h": h, "wm": how,
                       "inpaint_px": mpx})

    jp = os.path.join(pool, DIR_IDX, "_clean_report.json")
    os.makedirs(os.path.dirname(jp), exist_ok=True)
    json.dump({"updated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
               "items": report}, open(jp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print(f"[clean] 完成 {len(report)} 张；原始文件{'已存档到 ' + raw_dir if a.move_originals else '保留原位'}")
    print(f"[clean] 报告 → {jp}")


# ---------------------------------------------------------------- cleanvid
def _auto_rect_video(path, samples=30, rect=WM_RECT_DEFAULT):
    """从视频采样帧自动求水印外接框（相对坐标）。找不到则回退默认框。"""
    info = probe(path)
    W, H = info["w"], info["h"]
    step = max(1, int(info["dur"] * info["fps"] / samples)) if info["fps"] else 10
    dec = subprocess.Popen([FFMPEG, "-v", "error", "-i", path, "-vf", f"select=not(mod(n\\,{step}))",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
                           stdout=subprocess.PIPE)
    fb = W * H * 3
    x0, y0 = int(W * rect[0]), int(H * rect[1])
    x1, y1 = int(W * rect[2]), int(H * rect[3])
    ux0, uy0, ux1, uy1 = 10 ** 9, 10 ** 9, -1, -1
    got = 0
    while got < samples:
        buf = dec.stdout.read(fb)
        if len(buf) < fb:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        roi = img[y0:y1, x0:x1]
        m, _ = wm_mask_in_roi(roi, dilate=3)
        ys, xs = np.nonzero(m)
        if len(xs) > 0.005 * m.size:
            ux0, uy0 = min(ux0, xs.min() + x0), min(uy0, ys.min() + y0)
            ux1, uy1 = max(ux1, xs.max() + x0), max(uy1, ys.max() + y0)
        got += 1
    dec.stdout.close()
    try:
        dec.wait(timeout=5)
    except Exception:
        dec.kill()
    if ux1 < 0:
        return rect, "default"
    pad = 8
    r = ((ux0 - pad) / W, (uy0 - pad) / H, (ux1 + pad) / W, (uy1 + pad) / H)
    return r, f"auto({got}帧)"


def _auto_rects_video(path, samples=48, min_hits=3, relax=1.4):
    """自动找出视频里**所有**静态水印位置（多家 AI 平台的水印会在多角漂移）。

    做法：采样若干帧 → 白色低饱和 mask → 形状筛（尺寸/长宽比/密度与「小云雀AI」同量级）
    → 按位置聚类 → 保留出现在 ≥min_hits 帧的簇（静置水印才会反复出现，背景白物不会）
    → 每个簇取并集外接框并外扩余量。返回相对坐标列表。
    """
    info = probe(path)
    W, H = info["w"], info["h"]
    fps = info["fps"] or 30
    step = max(1, int(info["dur"] * fps / max(samples, 1)))
    dec = subprocess.Popen([FFMPEG, "-v", "error", "-i", path,
                            "-vf", f"select=not(mod(n\\,{step}))",
                            "-f", "rawvideo", "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    fb = W * H * 3
    per_frame, got = [], 0
    while got < samples:
        buf = dec.stdout.read(fb)
        if len(buf) < fb:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3)
        a = img.astype(np.int16)
        mn, mx = a.min(axis=2), a.max(axis=2)
        m = (((mn > WM_VMIN) & ((mx - mn) < WM_SAT)).astype(np.uint8) * 255)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((19, 5), np.uint8))
        cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if not (0.030 * W <= w <= 0.20 * W * relax and 0.018 * H <= h <= 0.11 * H * relax):
                continue
            if not (1.3 <= w / max(h, 1) <= 8.0):
                continue
            if float((m[y:y + h, x:x + w] > 0).mean()) < 0.34:
                continue
            boxes.append((x, y, w, h))
        per_frame.append(boxes)
        got += 1
    dec.stdout.close()
    try:
        dec.wait(timeout=5)
    except Exception:
        dec.kill()

    cw, ch = max(1, int(W * 0.045)), max(1, int(H * 0.055))
    clusters = {}
    for boxes in per_frame:
        seen = set()
        for (x, y, w, h) in boxes:
            key = (x // cw, y // ch)
            if key in seen:
                continue
            seen.add(key)
            clusters.setdefault(key, []).append((x, y, w, h))
    # 真水印的判据：① 出现在足够多帧（静置叠加物）② 各次命中的尺寸高度一致
    # （场景里的白色物体——运动鞋/墙裙/器材——会移动，尺寸与位置都散）
    need = max(min_hits, int(round(0.30 * len(per_frame))))
    rects, dropped = [], 0
    for key, items in clusters.items():
        if len(items) < need:
            dropped += 1
            continue
        mw = float(np.median([i[2] for i in items]))
        mh = float(np.median([i[3] for i in items]))
        keep = [i for i in items
                if abs(i[2] - mw) <= 0.20 * mw and abs(i[3] - mh) <= 0.20 * mh]
        if len(keep) < need:
            dropped += 1
            continue
        xs = [i[0] for i in keep]
        ys = [i[1] for i in keep]
        xe = [i[0] + i[2] for i in keep]
        ye = [i[1] + i[3] for i in keep]
        pad = 8
        x0, y0 = max(0, min(xs) - pad), max(0, min(ys) - pad)
        x1, y1 = min(W, max(xe) + pad), min(H, max(ye) + pad)
        rects.append((round(x0 / W, 4), round(y0 / H, 4), round(x1 / W, 4), round(y1 / H, 4),
                      len(keep)))
    rects.sort(key=lambda r: -r[4])
    if len(rects) > 6:                       # 明显异常（真水印通常 1~3 处）
        rects = rects[:6]
    return [(r[0], r[1], r[2], r[3]) for r in rects], len(per_frame), dropped


def cmd_cleanvid(a):
    pool = os.path.abspath(a.pool)
    src = os.path.abspath(a.inp)
    info = probe(src)
    W, H, fps = info["w"], info["h"], info["fps"] or 30.0
    print(f"[cleanvid] {os.path.basename(src)}  {W}x{H} @{fps}fps  {info['dur']:.2f}s")

    if a.rect == "auto":
        one, how = _auto_rect_video(src, rect=WM_RECT_DEFAULT)
        rects, how = [one], how + " 单位置(只找右下)"
    elif a.rect in ("auto-multi", "multi"):
        rects, nfr, dropped = _auto_rects_video(src)
        how = f"auto-multi({nfr}帧采样, 丢弃低频簇{dropped}个)"
        if not rects:
            rects, how = [WM_RECT_DEFAULT], "auto-multi 未命中 → 回退默认框"
    else:
        rects = [tuple(float(v) for v in seg.split(","))
                 for seg in a.rect.split(";") if seg.strip()]
        how = "manual"
    for i, r in enumerate(rects, 1):
        print(f"[cleanvid] 水印框#{i}（相对）= x {r[0]:.4f}~{r[2]:.4f}  y {r[1]:.4f}~{r[3]:.4f}")
    print(f"[cleanvid] 来源={how}；逐帧 inpaint 修复【整框】，不看当帧水印是否可见"
          f"（规避淡入淡出/漂移漏修）")

    if a.out:
        out_path = os.path.abspath(a.out)
    else:
        out_dir = os.path.join(pool, DIR_VID)
        os.makedirs(out_dir, exist_ok=True)
        stem = os.path.splitext(os.path.basename(src))[0]
        out_path = os.path.join(out_dir, stem + a.suffix + ".mp4")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    mask = np.zeros((H, W), np.uint8)
    for (rx0, ry0, rx1, ry1) in rects:
        mask[int(H * ry0):int(H * ry1), int(W * rx0):int(W * rx1)] = 255

    vf = []
    if a.crop:
        vf.append("crop=" + a.crop)
    if a.scale:
        vf.append("scale=" + a.scale + ":flags=lanczos")
    if a.sharpen > 0:
        vf.append(f"unsharp=5:5:{a.sharpen}:5:5:0.0")
    vf.append("setsar=1")
    if a.fps:
        vf.append(f"fps={a.fps}")
    vf.append("format=yuv420p")

    dec = subprocess.Popen([FFMPEG, "-v", "error", "-i", src, "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE)
    enc = subprocess.Popen([FFMPEG, "-y", "-v", "error", "-f", "rawvideo",
                            "-pix_fmt", "bgr24", "-s", f"{W}x{H}",
                            "-r", str(int(fps)), "-i", "-",
                            "-vf", ",".join(vf), "-an", "-c:v", "libx264",
                            "-preset", "medium", "-crf", str(a.crf), out_path],
                           stdin=subprocess.PIPE)
    fb, n = W * H * 3, 0
    while True:
        buf = dec.stdout.read(fb)
        if len(buf) < fb:
            break
        img = np.frombuffer(buf, np.uint8).reshape(H, W, 3).copy()
        enc.stdin.write(cv2.inpaint(img, mask, a.inpaint_radius, cv2.INPAINT_TELEA).tobytes())
        n += 1
    dec.stdout.close()
    enc.stdin.close()
    enc.wait()
    try:
        dec.wait(timeout=5)
    except Exception:
        dec.kill()
    print(f"[cleanvid] 处理 {n} 帧 → {out_path}  ({human(os.path.getsize(out_path))})")
    return out_path


# ---------------------------------------------------------------- enhance
def cmd_enhance(a):
    src = os.path.abspath(a.inp)
    arr = load_rgb(src)
    h, w = arr.shape[:2]
    out = enhance_clarity(arr, min_long=a.min_long, sharpen=a.sharpen, denoise=a.denoise)
    oh, ow = out.shape[:2]
    dst = os.path.abspath(a.out) if a.out else \
        os.path.join(os.path.dirname(src), os.path.splitext(os.path.basename(src))[0] + "_清晰" +
                     os.path.splitext(src)[1])
    save_rgb(dst, out, quality=a.quality)
    print(f"[enhance] {w}x{h} → {ow}x{oh}  锐化={a.sharpen}  降噪={a.denoise}")
    print(f"[enhance] → {dst}")


# ---------------------------------------------------------------- scan
def _font(size):
    for p in (os.path.join(ASSETS, "fonts", "STHeiti Medium.ttc"),
              "/System/Library/Fonts/PingFang.ttc"):
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                pass
    return ImageFont.load_default()


def _list_images(pool):
    out = []
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        out += glob.glob(os.path.join(pool, DIR_IMG, ext))
    return sorted(f for f in out if not os.path.basename(f).startswith("._"))


def _list_videos(pool):
    out = []
    for ext in ("*.mp4", "*.mov", "*.MP4", "*.MOV", "*.m4v", "*.mkv"):
        out += glob.glob(os.path.join(pool, DIR_VID, ext))
    return sorted(f for f in out if not os.path.basename(f).startswith("._"))


def _tags_from_name(name):
    stem = os.path.splitext(name)[0]
    parts = [p for p in stem.split("_") if p]
    tags = list(parts)
    for k in ("全身", "半身", "中景", "近景", "全景", "特写", "俯拍"):
        if k in stem:
            tags.append("景别:" + k)
    scene_kw = {"训练": "训练", "健身": "健身", "黑板": "科普", "桌面": "办公", "餐桌": "饮食",
                "厨房": "饮食", "书桌": "生活", "宿舍": "生活", "白底": "纯底", "暗场": "氛围",
                "动作": "纯底"}
    for k, v in scene_kw.items():
        if stem.startswith(k):
            tags.append("场景:" + v)
    return sorted(set(tags))


def cmd_scan(a):
    pool = os.path.abspath(a.pool)
    idx_dir = os.path.join(pool, DIR_IDX)
    os.makedirs(idx_dir, exist_ok=True)
    if getattr(a, "src", None):
        src = os.path.abspath(a.src)
        imgs, vids = [], []
        for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
            imgs += glob.glob(os.path.join(src, ext))
        for ext in ("*.mp4", "*.mov", "*.MP4", "*.MOV", "*.m4v"):
            vids += glob.glob(os.path.join(src, ext))
        imgs = sorted(f for f in imgs if not os.path.basename(f).startswith("._"))
        vids = sorted(f for f in vids if not os.path.basename(f).startswith("._"))
    else:
        imgs, vids = _list_images(pool), _list_videos(pool)

    rec = {"updated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "pool": pool, "images": [], "videos": []}
    for f in imgs:
        im = Image.open(f)
        w, h = im.size
        rec["images"].append({"file": os.path.basename(f), "dir": DIR_IMG,
                              "w": w, "h": h, "bytes": os.path.getsize(f),
                              "watermark": "已去除", "tags": _tags_from_name(os.path.basename(f)),
                              "mtime": _dt.datetime.fromtimestamp(os.path.getmtime(f)).strftime("%Y-%m-%d %H:%M")})
    for f in vids:
        i = probe(f)
        rec["videos"].append({"file": os.path.basename(f), "dir": DIR_VID,
                              "w": i["w"], "h": i["h"], "fps": i["fps"],
                              "dur": round(i["dur"], 2), "bytes": os.path.getsize(f),
                              "watermark": "已去除", "tags": _tags_from_name(os.path.basename(f))})
    jp = os.path.join(idx_dir, "素材索引.json")
    json.dump(rec, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    # 联络图
    if imgs:
        cols, tw, th, lab = 4, 460, 259, 30
        rows = (len(imgs) + cols - 1) // cols
        sheet = Image.new("RGB", (tw * cols, (th + lab) * rows), (16, 16, 18))
        d0 = ImageDraw.Draw(sheet)
        f_lab = _font(17)
        for i, f in enumerate(imgs):
            im = Image.open(f).convert("RGB")
            im.thumbnail((tw - 8, th - 8), Image.LANCZOS)
            x, y = (i % cols) * tw, (i // cols) * (th + lab)
            sheet.paste(im, (x + (tw - im.width) // 2, y + lab + (th - im.height) // 2))
            d0.text((x + 8, y + 6), f"{i+1:02d}  {os.path.splitext(os.path.basename(f))[0]}",
                    fill=(255, 200, 80), font=f_lab)
        stamp = _dt.datetime.fromtimestamp(
            max(os.path.getmtime(f) for f in imgs)).strftime("%y%m%d")
        sp = os.path.join(idx_dir, f"素材总览_{stamp}.jpg")
        sheet.save(sp, quality=88)
        print(f"[scan] 总览图 → {sp}  ({sheet.width}x{sheet.height})")

    print(f"[scan] 索引 → {jp}")
    print(f"[scan] 图片 {len(imgs)} 张 / 视频 {len(vids)} 个")
    for i, r in enumerate(rec["images"], 1):
        print(f"   {i:02d} {r['file']:<44} {r['w']}x{r['h']}")
    for r in rec["videos"]:
        print(f"   -- {r['file']:<44} {r['w']}x{r['h']}  {r['dur']}s")
    return rec


# ---------------------------------------------------------------- reel
def _cover_plate(path, W, H, margin=1.10):
    """把素材铺成「满幅底版」：cover 裁切到 W*margin × H*margin。"""
    im = Image.open(path).convert("RGB")
    pw, ph = int(W * margin), int(H * margin)
    s = max(pw / im.width, ph / im.height)
    im = im.resize((max(pw, int(im.width * s + 0.5)), max(ph, int(im.height * s + 0.5))),
                   Image.LANCZOS)
    left = (im.width - pw) // 2
    top = int((im.height - ph) * 0.42)          # 略偏上，避免裁掉头部
    return im.crop((left, top, left + pw, top + ph))


def _card(text_main, text_sub, W, H, tag="灿爹素材池"):
    bg = Image.new("RGB", (W, H), (14, 16, 20))
    d = ImageDraw.Draw(bg)
    for y in range(H):                          # 竖向渐变
        t = y / H
        d.line([(0, y), (W, y)], fill=(int(14 + 16 * t), int(16 + 26 * t), int(20 + 22 * t)))
    f1, f2, f3 = _font(112), _font(44), _font(30)
    for dx in (-2, 2):
        for dy in (-2, 2):
            d.text((W // 2 + dx, H // 2 - 90 + dy), text_main, font=f1,
                   fill=(20, 90, 45), anchor="mm")
    d.text((W // 2, H // 2 - 90), text_main, font=f1, fill=(90, 220, 130), anchor="mm")
    d.text((W // 2, H // 2 + 20), text_sub, font=f2, fill=(228, 230, 235), anchor="mm")
    d.text((W // 2, H - 70), tag, font=f3, fill=(120, 126, 138), anchor="mm")
    return bg


SCENE_ORDER = ["训练", "健身", "黑板", "桌面", "书桌", "餐桌", "厨房", "动作", "暗场", "宿舍"]


def _order_scene(files):
    def key(p):
        stem = os.path.splitext(os.path.basename(p))[0]
        head = stem.split("_")[0]
        return (SCENE_ORDER.index(head) if head in SCENE_ORDER else 99, stem)
    return sorted(files, key=key)


def cmd_reel(a):
    pool = os.path.abspath(a.pool)
    out_dir = os.path.join(pool, DIR_REEL)
    os.makedirs(out_dir, exist_ok=True)
    imgs = _list_images(pool)
    if not imgs:
        print("[reel] 图片目录为空，先跑 clean")
        return
    if a.order == "scene":
        imgs = _order_scene(imgs)

    W, H, FPS = 1920, 1080, 30
    per, fade, zoom = a.per, a.fade, a.zoom
    n_per, n_fade = int(round(per * FPS)), int(round(fade * FPS))

    plates = [_card("小绿人素材池", f"{len(imgs)} 张 · {_dt.date.today():%Y.%m.%d}", W, H)]
    names = []
    for f in imgs:
        plates.append(_cover_plate(f, W, H))
        names.append(os.path.splitext(os.path.basename(f))[0])
    plates.append(_card("全部已去水印", "可直接复用 · 生成前先来这儿找", W, H))

    total = sum(n_per for _ in plates) - n_fade * (len(plates) - 1)
    print(f"[reel] {len(plates)} 段 × {per}s（溶解 {fade}s）→ 约 {total/FPS:.1f}s")

    stem = a.name or f"素材样片_{_dt.date.today():%y%m%d}"
    silent = os.path.join(out_dir, f".{stem}_silent.mp4")
    enc = subprocess.Popen([FFMPEG, "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                            "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-", "-an",
                            "-vf", f"unsharp=5:5:{a.sharpen}:5:5:0.0",
                            "-c:v", "libx264", "-preset", "medium", "-crf", str(a.crf),
                            "-pix_fmt", "yuv420p", silent], stdin=subprocess.PIPE)

    prev_last = None
    for si, plate in enumerate(plates):
        pw, ph = plate.size
        base = np.asarray(plate)
        written = 0
        for k in range(n_per):
            p = k / max(n_per - 1, 1)
            z = 1.0 + (zoom - 1.0) * p
            cw, ch = int(W / z), int(H / z)     # 底版比画布大 margin 倍，故只裁不放大
            cx, cy = (pw - cw) // 2, (ph - ch) // 2
            frame = base[cy:cy + ch, cx:cx + cw]
            fr = cv2.resize(frame, (W, H), interpolation=cv2.INTER_AREA if z < 1 else cv2.INTER_LANCZOS4)
            if si > 0 and k < n_fade and prev_last is not None:
                al = (k + 1) / (n_fade + 1)
                fr = (prev_last.astype(np.float32) * (1 - al) + fr.astype(np.float32) * al)
                fr = np.clip(fr, 0, 255).astype(np.uint8)
            enc.stdin.write(fr.tobytes())
            written += 1
            prev_last = fr
    enc.stdin.close()
    enc.wait()

    out_path = os.path.join(out_dir, stem + ".mp4")
    cmd = [FFMPEG, "-y", "-v", "error", "-i", silent]
    if a.bgm and os.path.exists(a.bgm):
        fade_out = max(0.0, total / FPS - 2.2)
        cmd += ["-stream_loop", "-1", "-i", a.bgm, "-filter_complex",
                f"[1:a]volume={a.bgm_vol},afade=t=in:st=0:d=1.0,"
                f"afade=t=out:st={fade_out:.2f}:d=2.0[a]",
                "-map", "0:v", "-map", "[a]", "-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += ["-c:v", "copy", "-movflags", "+faststart", out_path]
    subprocess.run(cmd, check=True)
    os.remove(silent)
    print(f"[reel] 样片 → {out_path}  ({human(os.path.getsize(out_path))})")
    return out_path


# ---------------------------------------------------------------- CLI
# ---------------------------------------------------------------- ingest
# inbox（灿爹 / 临时投放入口）→ 去水印 + 提清 → 规范命名 → 归档图库主体 → 清空入口
# 2026-09-28 灿爹拍板：灿爹文件夹改为「临时入口」，成品一律归档到分镜图库主体根目录。
INBOX_WHITELIST = {"README.md", "_投料说明.txt", ".DS_Store"}
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff")
VID_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
KNOWN_SHOT = ("全身", "中景", "中近景", "近景", "特写", "全景", "大远景", "俯拍", "定妆图")
BAD_NAME = ("未命名", "新建文件夹", "最终版", "final", "副本", "copy", "未标题")
SANITIZE_JUNK = ("微信图片_", "微信截图_", "屏幕截图", "截屏", "Screenshot", "IMG_", "img_",
                 "DSC_", "DSC", "image", "Photo", "photo")


def _sanitize_name(stem: str) -> str:
    """把乱七八糟的原始文件名洗成「中文+下划线」的可用干名。"""
    s = stem
    for junk in SANITIZE_JUNK:
        s = s.replace(junk, "")
    s = re.sub(r"[\s\u3000]+", "_", s.strip())
    s = re.sub(r"[（）()【】\[\]「」『』《》〈〉<>，,。、;；:：!！?？'\"“”‘’·@#$%^&*+=|~`\\/]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or any(b in s for b in BAD_NAME):
        return "待命名"
    return s


def _draft_name(stem: str, is_video: bool) -> str:
    """草稿名：洗名 + 视频前缀「视频」+ 结尾若无标准景别则挂「_待命名」提示人工/模型定稿。"""
    s = _sanitize_name(stem)
    parts = [p for p in s.split("_") if p]
    if is_video and (not parts or parts[0] != "视频"):
        s = "视频_" + s
    if not parts or parts[-1] not in KNOWN_SHOT:
        s += "_待命名"
    return s


def _md5(path, chunk=1 << 20):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def _archived_names(lib):
    """★2026-10-01 新增：从 `_灿爹归档*/` 台账提取「已成功归档过的原始文件名」集合。

    扫 改名映射.json / 素材索引.json / 迁移清单.md，正则抓一切形如 `xxx.JPEG` 的文件名。
    用途：区分两种「入口件与备份 md5 相同」——
      A. 上一批清空失败的真残留（成品早已入库）→ 台账里必有该原名 → 判重复；
      B. 上次执行中断、只完成冷备份（成品还没入库）→ 台账里无记录 → 按新素材继续。
    只比 md5 不查台账会把 B 误判成 A（2026-10-01 第 1 批 7 个全新素材被全判重复，实测）。
    """
    pat = re.compile(
        r"[^/\s=:\"'（）()【】\[\]，,、|>]+?\."
        r"(?:JPEG|JPG|PNG|MP4|MOV|HEIC|jpeg|jpg|png|mp4|mov|heic)")
    names = set()
    for d in glob.glob(os.path.join(lib, "_灿爹归档*")):
        if not os.path.isdir(d):
            continue
        for fn in ("改名映射.json", "素材索引.json", "迁移清单.md"):
            p = os.path.join(d, fn)
            if not os.path.exists(p):
                continue
            try:
                txt = open(p, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            names |= {m.group(0) for m in pat.finditer(txt)}
    return names


def _detect_dupes(paths, lib):
    """★2026-09-30 新增：重复投料闸门。

    入口里若混着历史批次「没清掉」的残留文件，直接跑 ingest 会按草稿名再产出一份
    重复品（`小云雀_xxx_待命名.jpg`），而且**不报错**。实测 2026-09-30 第 6 批入口
    6 个文件里 5 个就是这种残留。
    做法：拿入口文件的 basename 去 `{lib}/_旧版备份/` 下找同名件，先比大小、再比 md5；
    ★2026-10-01 起再加第三道判据「归档台账里有该原名」（_archived_names）——
    只有 md5 相同 **且** 台账有记录才算真残留；md5 相同但台账无记录＝疑似上次执行
    中断的备份，按新素材继续（返回在 suspect 里，由调用方打印提示）。
    返回 ({入口绝对路径: 历史备份路径}, [疑似中断项(入口路径, 备份路径)])。
    """
    base = os.path.join(lib, "_旧版备份")
    if not os.path.isdir(base) or not paths:
        return {}, []
    # 先把备份区按 basename 建索引（只记大小，避免全量 md5）
    idx = {}
    for dp, _, fn in os.walk(base):
        for f in fn:
            if f.startswith("._"):
                continue
            p = os.path.join(dp, f)
            try:
                idx.setdefault(f, []).append((os.path.getsize(p), p))
            except OSError:
                pass
    archived = _archived_names(lib)
    out, suspect = {}, []
    for p in paths:
        try:
            sz = os.path.getsize(p)
        except OSError:
            continue
        for bsz, bp in idx.get(os.path.basename(p), ()):
            if bsz != sz:
                continue
            try:
                if _md5(p) == _md5(bp):
                    if os.path.basename(p) in archived:
                        out[p] = bp
                    else:
                        suspect.append((p, bp))
                    break
            except OSError:
                pass
    return out, suspect


def _with_sidecars(paths):
    """把 macOS 的 `._xxx` 元数据残渣一并带上（连同目录本身）。"""
    out = []
    for p in paths:
        out.append(p)
        s = os.path.join(os.path.dirname(p), "._" + os.path.basename(p))
        if os.path.exists(s):
            out.append(s)
    return out


def _next_backup_dir(lib):
    """当天第 N 批的冷备份目录：`{lib}/_旧版备份/<YYMMDD>_入口原图[_第N批]/`。

    同日多批必须带批次号，否则会覆盖前一批（2026-09-30 第 4 批留档就被第 5 批覆盖过）。
    """
    base = os.path.join(lib, "_旧版备份")
    today = _dt.datetime.now().strftime("%y%m%d")
    names = [os.path.basename(p)
             for p in glob.glob(os.path.join(base, f"{today}_入口原图*"))]
    if not names:
        return os.path.join(base, f"{today}_入口原图")
    nums = []
    for n in names:
        m = re.search(r"_第(\d+)批$", n)
        nums.append(int(m.group(1)) if m else 1)
    return os.path.join(base, f"{today}_入口原图_第{max(nums) + 1}批")


def _scan_inbox(inbox):
    """递归扫入口目录（原始文件常被直接丢在根目录），跳过 ._ 元数据与说明文件。"""
    imgs, vids = [], []
    for dp, _, fn in os.walk(inbox):
        for f in sorted(fn):
            if f.startswith(".") or f in INBOX_WHITELIST:
                continue
            p = os.path.join(dp, f)
            e = os.path.splitext(f)[1].lower()
            if e in IMG_EXT:
                imgs.append(p)
            elif e in VID_EXT:
                vids.append(p)
    return sorted(imgs), sorted(vids)


def _trash(paths):
    """移入系统废纸篓（外置卷 → /Volumes/<卷>/.Trashes/<uid>/），不硬删。"""
    ok = 0
    for p in paths:
        if not os.path.exists(p):
            continue
        r = subprocess.run(["/usr/bin/trash", p], capture_output=True, text=True)
        if r.returncode == 0 and not os.path.exists(p):
            ok += 1
        else:
            print(f"   !! 回收失败 {p}  rc={r.returncode} {r.stderr.strip()}")
    return ok


def _sidecar_docs(side_dir, lib, rows, mid_map):
    """写归档留档：迁移清单.md + 素材索引.json + 改名映射.json + 总览联络图。"""
    os.makedirs(side_dir, exist_ok=True)
    today = _dt.datetime.now().strftime("%y%m%d")
    imgs = [d for _, d, k in rows if k == "图片"]
    rec = {"updated": _dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
           "archived_from": "灿爹临时入口（inbox）",
           "archived_to": lib, "images": [], "videos": []}
    for _, d, k in rows:
        n = os.path.basename(d)
        if k == "图片":
            w, h = Image.open(d).size
            rec["images"].append({"file": n, "dir": "图库根", "w": w, "h": h,
                                  "bytes": os.path.getsize(d), "watermark": "已去除",
                                  "tags": _tags_from_name(n)})
        else:
            i = probe(d)
            rec["videos"].append({"file": n, "dir": "图库根", "w": i["w"], "h": i["h"],
                                  "fps": i["fps"], "dur": round(i["dur"], 2),
                                  "bytes": os.path.getsize(d), "watermark": "已去除",
                                  "tags": _tags_from_name(n)})
    json.dump(rec, open(os.path.join(side_dir, "素材索引.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    json.dump(mid_map, open(os.path.join(side_dir, "改名映射.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    tb = "\n".join(f"| {i} | `{os.path.basename(s)}` | `{os.path.basename(d)}` | "
                   f"{os.path.getsize(d)/1024:.0f} KB | {k} |"
                   for i, (s, d, k) in enumerate(rows, 1))
    md = f"""# 灿爹临时入口 · 归档清单 {today}

> 来源（inbox）：`{os.path.dirname(rows[0][0]) if rows else "—"}`
> 去向（图库主体）：`{lib}`

## 处理链路
投料 → ① 去水印 ② 提清晰度（长边不足自动 Lanczos4 超分）→ ③ 按 `_取名指南.txt`
三段式命名「大类_具体画面_景别」→ ④ 归档到图库主体根目录 → ⑤ 入口内原始文件移入废纸篓。

## 命名映射
| # | 投料原名 | 归档后名称 | 体积 | 类型 |
|---|---|---|---|---|
{tb}

## 删除清单
入口内原始文件（含 `._` 元数据）已移入废纸篓，可恢复。
"""
    open(os.path.join(side_dir, "迁移清单.md"), "w", encoding="utf-8").write(md)

    if imgs:
        cols, tw, th, lab = 4, 460, 259, 32
        rows_n = (len(imgs) + cols - 1) // cols
        sheet = Image.new("RGB", (tw * cols, (th + lab) * rows_n), (16, 16, 18))
        d0 = ImageDraw.Draw(sheet)
        fl = _font(17)
        for i, f in enumerate(imgs):
            im = Image.open(f).convert("RGB")
            im.thumbnail((tw - 8, th - 8), Image.LANCZOS)
            x, y = (i % cols) * tw, (i // cols) * (th + lab)
            sheet.paste(im, (x + (tw - im.width) // 2, y + lab + (th - im.height) // 2))
            d0.text((x + 8, y + 6), f"{i+1:02d}  {os.path.splitext(os.path.basename(f))[0]}",
                    fill=(255, 200, 80), font=fl)
        sheet.save(os.path.join(side_dir, f"素材总览_{today}.jpg"), quality=88)
    print(f"[ingest] 留档 → {side_dir}")


def cmd_ingest(a):
    inbox = os.path.abspath(a.src or a.pool)
    lib = os.path.abspath(a.lib or os.path.dirname(inbox))
    imgs, vids = _scan_inbox(inbox)
    if not imgs and not vids:
        print(f"[ingest] {inbox} 是空的，没有待处理素材（正常状态）")
        return

    # ⓪ 重复投料闸门（★2026-09-30 新增）——先拦历史残留，再谈处理
    dupes, suspect = _detect_dupes(imgs + vids, lib)
    if suspect:
        print(f"[ingest] ℹ️  {len(suspect)} 个文件与备份区 md5 相同但归档台账无记录"
              f" → 疑似上次执行中断的冷备份，按新素材继续：")
        for s, bp in suspect:
            print(f"         {os.path.basename(s)}  ≈  {bp}")
        print()
    if dupes:
        print(f"[ingest] ⚠️  检出 {len(dupes)} 个【重复投料】：与 _旧版备份/ 中历史批次的"
              f"原始文件逐字节相同，成品早已在库")
        for s, prior in dupes.items():
            print(f"         {os.path.basename(s)}  ≡  {os.path.basename(prior)}")
        print("         本轮对这些文件【跳过处理】，避免再产出重复品。")
        if not a.drop_dupes:
            print("         如需连同它们一起清空入口，加 --drop-dupes；默认保留、不删任何东西。")
        print()
        imgs = [p for p in imgs if p not in dupes]
        vids = [p for p in vids if p not in dupes]
        if not imgs and not vids:
            print("[ingest] 除重复投料外，入口内没有新素材 → 本轮不产生新归档")
            if a.drop_dupes:
                n = _trash(_with_sidecars(list(dupes)))
                print(f"[ingest] --drop-dupes：{n} 个重复投料（含 ._ 元数据）移入废纸篓（可恢复）")
            return

    names = {}
    if a.names_file and os.path.exists(a.names_file):
        names.update(json.load(open(a.names_file, encoding="utf-8")))
    for kv in (a.names or "").split(";"):
        if "=" in kv:
            k, v = kv.split("=", 1)
            names[k.strip()] = v.strip()

    out_ext = ".png" if a.png else ".jpg"
    skipped_vid = []
    if vids and not a.vrect:
        skipped_vid = vids
        vids = []
    plan = []
    for p in imgs:
        b = os.path.basename(p)
        f = names.get(b) or names.get(os.path.splitext(b)[0]) or _draft_name(os.path.splitext(b)[0], False)
        plan.append((p, os.path.join(lib, os.path.splitext(f)[0] + out_ext), "图片"))
    for p in vids:
        b = os.path.basename(p)
        f = names.get(b) or names.get(os.path.splitext(b)[0]) or _draft_name(os.path.splitext(b)[0], True)
        plan.append((p, os.path.join(lib, os.path.splitext(f)[0] + ".mp4"), "视频"))

    if skipped_vid:
        print(f"[ingest] ⚠️  {len(skipped_vid)} 个视频本轮跳过（缺 --vrect 水印框）：")
        for p in skipped_vid:
            print(f"         {os.path.basename(p)}")
        print("         视频水印多位置漂移，auto-multi 会误检毁画面 → 必须先定实测坐标。")
        print("         做法：抽帧目检定位 → --vrect \"rx0,ry0,rx1,ry1;...\"（相对坐标）")
        print("         实测参数见 分镜图库/小绿人/_视频水印备忘.txt\n")
    if not plan and skipped_vid:
        print("[ingest] 除跳过的视频外无待处理素材")
        return

    print(f"[ingest] 入口 {inbox}")
    print(f"[ingest] 归档 → {lib}")
    print(f"[ingest] 待处理 {len(imgs)} 图 + {len(vids)} 视频\n")

    bad = [d for _, d, _ in plan if os.path.exists(d)]
    for s, d, k in plan:
        mark = "!! 目标已存在" if d in bad else "  "
        draft = "_待命名" in d
        print(f"  {mark} [{k}] {os.path.basename(s)}  →  {os.path.basename(d)}"
              + ("   ← 命名待定，建议先定稿" if draft else ""))
    if bad:
        print(f"\n[ingest] {len(bad)} 个目标名已存在，先改名或删掉冲突项再跑")
        return
    if a.dry_run:
        print("\n[ingest] dry-run：只出方案，未动任何文件")
        return

    # ⓪b 冷备份（★2026-09-30 新增）：先落一份原始件，再动任何文件。
    #     第 4/5/6 批都是手工补的这一步，漏了就没有「原图 vs 成品」的长期对照底。
    if not a.no_backup:
        bdir = _next_backup_dir(lib)
        os.makedirs(bdir, exist_ok=True)
        okn = 0
        for s, _, _ in plan:
            d = os.path.join(bdir, os.path.basename(s))
            if os.path.exists(d) and _md5(s) == _md5(d):
                okn += 1          # 上次执行已备份过同一份（中断重试场景），不重复复制
                continue
            shutil.copy2(s, d)
            if _md5(s) == _md5(d):
                okn += 1
        print(f"[ingest] 冷备份 {okn}/{len(plan)} → {bdir}")

    rows, mid_map = [], {}
    for s, d, k in plan:
        if k == "图片":
            arr = load_rgb(s)
            cleaned, how, _ = clean_image_wm(arr, inpaint_radius=a.inpaint_radius, method=a.method)
            final = cleaned if a.no_enhance else enhance_clarity(
                cleaned, min_long=a.min_long, sharpen=a.sharpen, denoise=a.denoise)
            save_rgb(d, final, quality=a.quality)
            print(f"   ✓ 图 {os.path.basename(d)}   [{how}]")
        else:
            ns = argparse.Namespace(pool=inbox, inp=s, out=d, suffix="", rect=a.vrect,
                                    crop=a.crop, scale=a.scale, fps=a.fps, crf=a.crf,
                                    sharpen=a.vsharpen, inpaint_radius=a.inpaint_radius)
            cmd_cleanvid(ns)
        rows.append((s, d, k))
        mid_map[os.path.basename(s)] = {"归档名": os.path.basename(d)}

    # 留档
    side = os.path.join(lib, f"_灿爹归档{_dt.datetime.now().strftime('%y%m%d')}")
    _sidecar_docs(side, lib, rows, mid_map)

    # 清空入口：只删「本轮已成功归档的源文件」+ macOS 元数据残渣；
    # 本轮跳过的（如缺 --vrect 的视频）必须原样留下，绝不误删。
    if a.keep_originals:
        print("[ingest] --keep-originals：本次归档的原始文件保留在入口内")
    else:
        archived = {s for s, _, _ in rows}
        if a.drop_dupes:
            archived |= set(dupes)
        leftovers, kept = [], []
        for dp, _, fn in os.walk(inbox):
            for f in fn:
                p = os.path.join(dp, f)
                if p in archived or f.startswith("._"):
                    leftovers.append(p)
                else:
                    kept.append(p)
        n = _trash(leftovers)
        for dp, _, _ in os.walk(inbox, topdown=False):
            if dp != inbox and not os.listdir(dp):
                os.rmdir(dp)
        print(f"[ingest] 入口清理：{n} 个已归档源文件移入废纸篓（可恢复）")
        if kept:
            print(f"[ingest] ⚠️  仍有 {len(kept)} 个文件留在入口（本轮未处理，未删除）：")
            for p in kept:
                print(f"         {os.path.basename(p)}")
    print(f"[ingest] 完成：{len(rows)} 项已归档")


def main():
    ap = argparse.ArgumentParser(description="灿爹素材池工具")
    ap.add_argument("--pool", default=DEFAULT_POOL, help="素材池根目录")
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("clean", help="图片批量去水印（+清晰度增强）")
    c.add_argument("--src", default=None, help="原图所在目录（默认=池根目录）")
    c.add_argument("--min-long", type=int, default=2400, help="长边不足则超分到此值（0=不超分）")
    c.add_argument("--sharpen", type=float, default=0.55)
    c.add_argument("--denoise", type=float, default=0.0, help="双边滤波 sigma，0=关")
    c.add_argument("--no-enhance", action="store_true", help="只去水印，不做清晰度增强")
    c.add_argument("--move-originals", action="store_true", help="原图移入 图片/_原始/ 存档")
    c.add_argument("--inpaint-radius", type=int, default=5)
    c.add_argument("--method", choices=["auto", "exemplar", "inpaint"], default="auto",
                   help="auto=四候选按接缝+纹理自动择优（默认）；exemplar=只用样本块；inpaint=TELEA")
    c.add_argument("--quality", type=int, default=95)
    c.add_argument("--jpeg", action="store_true", default=True, help="输出 jpg（默认）")
    c.add_argument("--png", dest="jpeg", action="store_false", help="输出 png")
    c.set_defaults(func=cmd_clean)

    v = sub.add_parser("cleanvid", help="视频去水印（逐帧 inpaint）")
    v.add_argument("--in", dest="inp", required=True)
    v.add_argument("--out", default=None)
    v.add_argument("--suffix", default="_去水印")
    v.add_argument("--rect", default="auto-multi",
                   help="auto-multi=全画面自动找多处水印（默认）；auto=只找右下；"
                        "或手写 rx0,ry0,rx1,ry1（多框用 ; 分隔，相对坐标）")
    v.add_argument("--crop", default=None, help="ffmpeg crop 参数 W:H:X:Y")
    v.add_argument("--scale", default=None, help="ffmpeg scale 参数 W:H")
    v.add_argument("--fps", type=int, default=None)
    v.add_argument("--crf", type=int, default=18)
    v.add_argument("--sharpen", type=float, default=0.4)
    v.add_argument("--inpaint-radius", type=int, default=5)
    v.set_defaults(func=cmd_cleanvid)

    e = sub.add_parser("enhance", help="单张素材清晰度增强")
    e.add_argument("--in", dest="inp", required=True)
    e.add_argument("--out", default=None)
    e.add_argument("--min-long", type=int, default=2400)
    e.add_argument("--sharpen", type=float, default=0.6)
    e.add_argument("--denoise", type=float, default=0.0)
    e.add_argument("--quality", type=int, default=95)
    e.set_defaults(func=cmd_enhance)

    s = sub.add_parser("scan", help="扫描素材池生成索引与总览图")
    s.add_argument("--src", default=None, help="要扫描的目录（默认=池内 图片/）")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("reel", help="生成简单样片")
    r.add_argument("--name", default=None)
    r.add_argument("--order", choices=["name", "scene"], default="scene",
                   help="scene=按场景叙事排序（训练→健身→黑板→桌面→餐桌→厨房→纯底→暗场→宿舍）")
    r.add_argument("--per", type=float, default=1.4, help="每张停留秒数")
    r.add_argument("--fade", type=float, default=0.4, help="交叉溶解秒数")
    r.add_argument("--zoom", type=float, default=1.06, help="结束缩放倍率")
    r.add_argument("--sharpen", type=float, default=0.5)
    r.add_argument("--crf", type=int, default=18)
    # ★2026-10-04 团队共享改造：BGM 打包压成 m4a，默认值按「有 m4a 用 m4a」解析，
    #   否则共享包里默认路径指向不存在的 .wav，reel 会静默无 BGM。
    def _bgm_default():
        for ext in (".m4a", ".wav", ".mp3"):
            p = os.path.join(ASSETS, "bgm", "bgm_吴崽崽" + ext)
            if os.path.exists(p):
                return p
        return os.path.join(ASSETS, "bgm", "bgm_吴崽崽.wav")

    r.add_argument("--bgm", default=_bgm_default())
    r.add_argument("--bgm-vol", type=float, default=0.22)
    r.set_defaults(func=cmd_reel)

    g = sub.add_parser("ingest", help="一键：入口 → 去水印+提清 → 规范命名 → 归档图库主体 → 清空入口")
    g.add_argument("--src", default=None, help="临时入口目录（默认=池根，即灿爹/）")
    g.add_argument("--lib", default=None, help="归档目标（默认=入口的上一级，即 分镜图库/小绿人/）")
    g.add_argument("--names-file", default=None, help="定稿命名 JSON：{原名: 新名}")
    g.add_argument("--names", default=None, help='内联定稿命名，如 "IMG_001.jpg=俯卧撑_地面_中景.jpg;b.mov=视频_深蹲_全身.mp4"')
    g.add_argument("--dry-run", action="store_true", help="只出归档方案，不动文件")
    g.add_argument("--keep-originals", action="store_true", help="归档后保留入口内原始文件（默认清空）")
    g.add_argument("--drop-dupes", action="store_true",
                   help="连「重复投料」（与 _旧版备份 中历史批次原始件逐字节相同的残留）也一并"
                        "移入废纸篓；默认只跳过处理、把文件留在入口不删")
    g.add_argument("--no-backup", action="store_true",
                   help="跳过冷备份；默认处理前先把原始件复制一份到 _旧版备份/<日期>_入口原图[_第N批]/")
    g.add_argument("--min-long", type=int, default=2400)
    g.add_argument("--sharpen", type=float, default=0.55)
    g.add_argument("--denoise", type=float, default=0.0)
    g.add_argument("--no-enhance", action="store_true")
    g.add_argument("--inpaint-radius", type=int, default=5)
    g.add_argument("--method", choices=["auto", "exemplar", "inpaint"], default="auto")
    g.add_argument("--quality", type=int, default=95)
    g.add_argument("--png", action="store_true", help="图片输出 png（默认 jpg）")
    g.add_argument("--vrect", default=None,
                   help="视频水印框（相对坐标，多框用 ; 分隔）。**视频必填**：auto-multi 在画面白物多时"
                        "会误检（会毁画面），拿不准就先抽帧目检再传实测坐标；见 _视频水印备忘.txt")
    g.add_argument("--vsharpen", type=float, default=0.4)
    g.add_argument("--crop", default=None)
    g.add_argument("--scale", default=None)
    g.add_argument("--fps", type=int, default=None)
    g.add_argument("--crf", type=int, default=18)
    g.set_defaults(func=cmd_ingest)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
