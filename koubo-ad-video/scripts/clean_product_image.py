#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
详情图净化：OCR 定位功效/违规表述 → 裁切或擦除，只留主体，0 积分。

背景（2026-09-19 用户要求）：
    用户详情图常带功效类词（力量up / 引爆肌能 / 快速供应ATP / 缓解练后疲劳…），
    这类表述在部分平台会被判违规。以后**详情图进卡片/画面之前一律先过本工具**：
      ① 能裁掉 → 直接裁（保主体，最干净）
      ② 压在主体上裁不掉 → 本地擦除（纯色/渐变底效果好）
      ③ 擦不干净 / 整张都是功效图 → 弃用换图（图库干净图 → 实拍抽帧 → AI 生成）

OCR 用 macOS 原生 Vision（scripts/vision_ocr，离线免费中文准）。

用法:
    # 1) 扫描：列出文字块 + 标出哪些是功效词
    clean_product_image.py 图.jpg --ocr
    # 2) 标注图：红=功效词 绿=可保留，用于目视确认
    clean_product_image.py 图.jpg --mark /tmp/marked.png
    # 3) 自动建议怎么处理（裁上/裁下/擦除/换图）
    clean_product_image.py 图.jpg --suggest --out /tmp/clean.png
    # 4) 手动裁切（像素）
    clean_product_image.py 图.jpg --crop 0,600,1170,1390 --out /tmp/clean.png
    # 5) 裁掉上半部分（百分比 0~1 或像素，两种都认）
    clean_product_image.py 图.jpg --crop-y 0.42:1 --out /tmp/clean.png
    # 6) 擦除功效词区域（本地填充，不联网）
    clean_product_image.py 图.jpg --erase --out /tmp/clean.png
    # 7) 只擦部分块（按 --ocr 出的序号）
    clean_product_image.py 图.jpg --erase --only 2,3,4 --out /tmp/clean.png

退出码: 0 正常；3 = 图里没有功效词；4 = 建议弃用换图（洗不干净）
"""
import argparse
import json
import os
import re
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_BIN = os.path.join(HERE, "vision_ocr")

# ── 功效 / 绝对化 / 医疗暗示词：命中即"要处理" ──────────────────────────────
CLAIM_WORDS = [
    # 功效宣称（动词类：最容易漏，实测"提高ATP合成率"曾漏检）
    "up", "提高", "提升", "促进", "有助于", "帮助", "利于", "强化", "优化", "增强",
    "增加",
    "激活", "唤醒", "引爆", "爆", "快充", "充能", "燃", "燃脂", "刮油",
    "瘦", "减脂", "减肥", "排油", "排毒", "免疫", "修复", "缓解", "恢复", "回复",
    "供应", "供给", "助力", "改善", "超越", "极速", "速效", "持久", "抗疲劳",
    "疲劳", "精力", "泵感", "膨胀", "围度", "增长", "生长", "强壮", "变强", "变快",
    "效果", "功效", "疗效", "治疗", "治愈", "预防", "抗衰", "抗氧", "代谢",
    "吸收率", "直接吸收", "小分子", "代餐", "饱腹", "不反弹",
    # 比较级（"更强/更快/更猛"）
    "更强", "更快", "更猛", "更持久", "更省心",
    # 英文功效词
    "fast charge", "boost", "burn", "enhance", "improve", "slim", "fat burn",
    "energy up", "power up", "effect",
    # 绝对化用语（广告法）
    "最好", "第一", "顶级", "最强", "no.1", "唯一", "首创",
]
# 强保留特征：块本身是**法定标注 / 参数**（食品类别、净含量、执行标准…）
#   命中它就覆盖功效判定——否则「运动营养食品 速度力量类」会被"速"字误杀。
STRONG_KEEP = [
    r"力量类", r"速度力量", r"营养食品", r"净含量", r"保质期", r"生产日期",
    r"贮存", r"执行标准", r"配料表", r"^\s*配料", r"纯度", r"一水肌酸", r"肌酸酐",
    r"\d+(\.\d+)?\s*(g|克|mg|毫克|kg|ml)\b",
]
# 一般保留特征（品名/品牌/成分名）
KEEP_PATTERNS = STRONG_KEEP + [
    r"米当卡", r"midangka", r"fdp", r"^肌酸$", r"肌酸粉", r"99\.9+", r"4\s*个\s*9",
]
# 主体锚点：裁切后必须**至少还剩一个**，否则裁出来是没有产品的废图。
#   注意"肌酸"两字**不算**锚点（图表坐标轴标签也是它，太弱），必须有品牌/品名/参数级证据。
ANCHOR_PATTERNS = [
    r"米当卡|midangka", r"肌酸粉|一水肌酸|肌酸酐", r"净含量", r"纯度", r"配料",
    r"执行标准", r"保质期", r"运动营养食品", r"\d+(\.\d+)?\s*(g|克|mg)\b",
    r"品名|规格",
]


def _hit(word, low):
    """纯英文/数字词做词边界匹配，避免 up 命中 SUPER/GROUP"""
    w = word.lower()
    if re.fullmatch(r"[a-z0-9.]+", w):
        return re.search(r"(?<![a-z])" + re.escape(w) + r"(?![a-z])", low) is not None
    return w in low


def classify(text):
    """返回 (is_claim, hits)"""
    t = (text or "").strip()
    if not t:
        return False, []
    low = t.lower()
    claims = [c for c in CLAIM_WORDS if _hit(c, low)]
    if not claims:
        return False, []
    # 法定标注 / 参数强特征 → 覆盖功效判定（如"运动营养食品 速度力量类"含"速"）
    if any(re.search(p, low, re.I) for p in STRONG_KEEP):
        return False, []
    return True, claims


def ocr(path, merge_line=True):
    """调 Vision OCR，返回 {width,height,blocks:[{text,x,y,w,h}]}"""
    if not os.path.exists(OCR_BIN):
        sys.exit(f"[净化] 缺少 OCR 组件：{OCR_BIN}\n"
                 f"       请先编译：cd {HERE} && "
                 f"swiftc -O -o vision_ocr vision_ocr.swift -framework Vision -framework AppKit")
    cmd = [OCR_BIN, path] + (["--line"] if merge_line else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit(f"[净化] OCR 失败：{r.stderr.strip()}")
    return json.loads(r.stdout)


def analyze(path):
    d = ocr(path)
    blocks = []
    for i, b in enumerate(d["blocks"], 1):
        bad, hits = classify(b["text"])
        blocks.append({"i": i, "text": b["text"], "x": b["x"], "y": b["y"],
                       "w": b["w"], "h": b["h"], "conf": b.get("conf", 0),
                       "dirty": bad, "hits": hits})
    return {"width": d["width"], "height": d["height"], "blocks": blocks}


def report(a):
    W, H = a["width"], a["height"]
    dirty = [b for b in a["blocks"] if b["dirty"]]
    print(f"图像 {W}x{H}  文字块 {len(a['blocks'])} 个，其中功效/违规表述 {len(dirty)} 个")
    print("-" * 78)
    for b in sorted(a["blocks"], key=lambda z: z["y"]):
        flag = "✗ 功效" if b["dirty"] else "· 保留"
        hits = ("  ← " + "/".join(b["hits"][:5])) if b["dirty"] else ""
        print(f"{flag}  #{b['i']:<2d} [{b['x']:>4d},{b['y']:>4d} {b['w']:>4d}x{b['h']:<3d}]"
              f"  {b['text'][:38]}{hits}")
    print("-" * 78)
    if dirty:
        y0 = min(b["y"] for b in dirty)
        y1 = max(b["y"] + b["h"] for b in dirty)
        print(f"功效词纵向范围： y {y0} ~ {y1}（占全图 {y0/H:.0%} ~ {y1/H:.0%}）")
    else:
        print("✅ 无功效词，可直接使用")
    return dirty


def mark(path, out, a):
    im = Image.open(path).convert("RGB")
    dr = ImageDraw.Draw(im)
    for b in a["blocks"]:
        box = [b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"]]
        if b["dirty"]:
            dr.rectangle(box, outline=(255, 40, 40), width=5)
            dr.rectangle([box[0], box[1] - 30, box[0] + 60, box[1]],
                         fill=(255, 40, 40))
            dr.text((box[0] + 6, box[1] - 28), f"#{b['i']}", fill=(255, 255, 255))
        else:
            dr.rectangle(box, outline=(0, 190, 90), width=3)
    im.save(out)
    print(f"标注图已写出：{out}（红=功效词需处理，绿=可保留）")


def _px(v, total, default):
    """支持 '0.42'（比例）与 '600'（像素）"""
    if v is None or v == "":
        return default
    f = float(v)
    return int(round(f * total)) if f <= 1.0 else int(round(f))


def crop_box(a, crop=None, crop_y=None):
    W, H = a["width"], a["height"]
    if crop:
        x0, y0, x1, y1 = [int(t) for t in crop.split(",")]
        return x0, y0, x1, y1
    if crop_y:
        s, e = crop_y.split(":")
        y0 = _px(s, H, 0)
        y1 = _px(e, H, H)
        return 0, y0, W, y1
    return 0, 0, W, H


def do_crop(path, out, box):
    im = Image.open(path)
    im.crop(box).save(out)
    print(f"已裁切 {box} → {out}（{im.crop(box).width}x{im.crop(box).height}）")


def erase(path, out, a, only=None, mode="edge", pad_ratio=0.6):
    """
    本地擦除（不联网）。mode:
      edge  四周像素双向插值（纯色/渐变底最佳）
      blur  周边取样重度模糊块
      solid 取四周主色涂平
    """
    im = Image.open(path).convert("RGB")
    arr = np.asarray(im).astype(np.float32)
    H, W, _ = arr.shape
    targets = [b for b in a["blocks"] if b["dirty"] and (only is None or b["i"] in only)]
    if not targets:
        print("[净化] 没有要擦除的块")
        return
    for b in targets:
        pad = max(4, int(b["h"] * pad_ratio))
        x0 = max(0, b["x"] - pad)
        y0 = max(0, b["y"] - pad)
        x1 = min(W, b["x"] + b["w"] + pad)
        y1 = min(H, b["y"] + b["h"] + pad)
        if x1 - x0 < 3 or y1 - y0 < 3:
            continue
        if mode == "solid":
            ring = np.concatenate([
                arr[max(0, y0 - 3):y0 + 1, x0:x1].reshape(-1, 3),
                arr[y1 - 1:y1 + 3, x0:x1].reshape(-1, 3),
                arr[y0:y1, max(0, x0 - 3):x0 + 1].reshape(-1, 3),
                arr[y0:y1, x1 - 1:x1 + 3].reshape(-1, 3)])
            arr[y0:y1, x0:x1] = np.median(ring, axis=0)
        elif mode == "blur":
            patch = Image.fromarray(arr[y0:y1, x0:x1].astype(np.uint8))
            arr[y0:y1, x0:x1] = np.asarray(
                patch.filter(ImageFilter.GaussianBlur(max(6, (y1 - y0) // 4)))).astype(np.float32)
        else:  # edge
            top = arr[max(0, y0 - 2):y0, x0:x1].mean(axis=0)
            bot = arr[y1:y1 + 2, x0:x1].mean(axis=0)
            lef = arr[y0:y1, max(0, x0 - 2):x0].mean(axis=1)
            rig = arr[y0:y1, x1:x1 + 2].mean(axis=1)
            if top.shape[0] != x1 - x0:
                top = np.repeat(arr[max(0, y0 - 1):max(1, y0), x0:x1].mean(axis=0)[None, :], x1 - x0, 0)
            if bot.shape[0] != x1 - x0:
                bot = np.repeat(arr[max(0, y1 - 1):y1, x0:x1].mean(axis=0)[None, :], x1 - x0, 0)
            if lef.shape[0] != y1 - y0:
                lef = np.repeat(arr[y0:y1, max(0, x0 - 1):max(1, x0)].mean(axis=1), y1 - y0, 0)
            if rig.shape[0] != y1 - y0:
                rig = np.repeat(arr[y0:y1, max(0, x1 - 1):x1].mean(axis=1), y1 - y0, 0)
            vy = np.linspace(0, 1, y1 - y0)[:, None, None]
            v = top[None, :, :] * (1 - vy) + bot[None, :, :] * vy
            ux = np.linspace(0, 1, x1 - x0)[None, :, None]
            h = lef[:, None, :] * (1 - ux) + rig[:, None, :] * ux
            arr[y0:y1, x0:x1] = (v + h) / 2
    Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).save(out)
    print(f"已擦除 {len(targets)} 处功效块（mode={mode}）→ {out}")
    print("⚠️ 本地擦除只对纯色/渐变底干净；复杂纹理背景请 Read 目视，不行就换图")


def suggest(path, a, out=None):
    """给出处理建议：裁上 / 裁下 / 擦除 / 换图"""
    W, H = a["width"], a["height"]
    dirty = [b for b in a["blocks"] if b["dirty"]]
    if not dirty:
        print("✅ 无功效词 → 建议：直接用")
        return "clean", None
    dy0 = min(b["y"] for b in dirty)
    dy1 = max(b["y"] + b["h"] for b in dirty)
    pad = int(max(H * 0.03, max(b["h"] for b in dirty) * 1.2))
    keep = [b for b in a["blocks"] if not b["dirty"]]

    # 候选 A：裁掉顶部（功效全在上方）
    a_ok = dy1 + pad < H * 0.7 and (H - (dy1 + pad)) >= H * 0.35
    # 候选 B：裁掉底部
    b_ok = dy0 - pad > H * 0.3 and (dy0 - pad) >= H * 0.35
    below = [b for b in keep if b["y"] > dy1]      # 下方保留块
    above = [b for b in keep if b["y"] + b["h"] < dy0]

    # ★ 硬判据：裁完剩下的部分必须还留有"主体锚点"（品牌/品名/净含量/纯度…）
    #   否则裁出来就是一张没有产品的废图（IMG_6538 裁完只剩图表轴标签"肌酸"→ 废）
    def anchors(bs):
        return [b for b in bs
                if any(re.search(p, b["text"], re.I) for p in ANCHOR_PATTERNS)]

    # ★ 全图压根没有主体锚点 → 这张图整个是功效图（图表/箭头/宣称），裁擦都无意义
    if not anchors(a["blocks"]):
        print("→ 建议【弃用换图】：全图没有任何主体锚点（品牌/品名/净含量/纯度/配料），"
              "整张就是功效图，裁或擦都救不回产品主体")
        print("   改用：图库干净图 → 实拍视频抽帧（pick_still.py）→ AI 生成（需确认积分）")
        return "replace", None

    print(f"功效块 y {dy0}~{dy1}（全图高 {H}，占 {dy0/H:.0%}~{dy1/H:.0%}）")
    print(f"  · 上方可保留块 {len(above)} 个 / 下方可保留块 {len(below)} 个"
          f"（下方含主体锚点 {len(anchors(below))} 个）")

    def emit(kind, box, note, code):
        h = box[3] - box[1]
        w = box[2] - box[0]
        print(f"→ 建议【{kind}】crop-y {box[1]}:{box[3]}  （剩余 {w}x{h} = 原高 {h/H:.0%}，"
              f"宽高比 {w/h:.2f}）{note}")
        print("   ⚠️ 必须 Read 目视确认：主体（产品/人物）完整、没被切、不留半截文字")
        if out:
            do_crop(path, out, box)
        return code, box

    if a_ok and anchors(below):
        return emit("裁掉上部", (0, dy1 + pad, W, H), "", "crop_top")
    if a_ok and below and not anchors(below):
        print(f"→ 建议【弃用换图】：裁掉上部后只剩 {len(below)} 个块且**没有主体锚点**"
              f"（品牌/品名/净含量/纯度），裁出来是废图")
        print("   改用：图库干净图 → 实拍视频抽帧（pick_still.py）→ AI 生成（需确认积分）")
        return "replace", None
    if b_ok and above and not below and anchors(above):
        return emit("裁掉下部", (0, 0, W, dy0 - pad), "", "crop_bottom")
    if dy1 - dy0 > H * 0.55:
        print("→ 建议【弃用换图】：功效词纵跨全图半数以上，裁剪救不回主体")
        return "replace", None
    # 功效词夹在中间、上下都有要保留的内容 → 裁不掉。
    # 这种图多是"信息图/示意图"（标题+功效+参数三段式），**本地擦除会在图上留一条色带、
    # 比原来更难看**，所以默认判弃用；坚持用原图才走 --erase（擦完必须目视）。
    print("→ 建议【弃用换图】：功效词夹在中间裁不掉，且这种图多为信息图/示意图，"
          "本地擦除会留色带反而更难看")
    print("   改用：图库干净图 → 实拍视频抽帧（pick_still.py）→ AI 生成（需确认积分）")
    print("   若坚持保留原图：--erase 本地擦除（纯色/渐变底才干净），擦完必须 Read 目视")
    return "replace", None


def verify(path, quiet=False):
    """闭环复检：对处理后的图再 OCR 一遍，确认功效词真的清干净了、主体锚点还在。
    实测必要性：IMG_6537 裁切后仍残留"提高ATP合成率充"（词表缺"提高"时漏判）。"""
    a = analyze(path)
    W, H = a["width"], a["height"]
    dirty = [b for b in a["blocks"] if b["dirty"]]
    anch = [b for b in a["blocks"] if any(
        re.search(p, b["text"], re.I) for p in ANCHOR_PATTERNS)]
    ok = True
    print(f"[复检] {os.path.basename(path)}  {W}x{H}  文字块 {len(a['blocks'])}")
    if dirty:
        ok = False
        print(f"  ❌ 仍残留 {len(dirty)} 处功效/违规表述：")
        for b in sorted(dirty, key=lambda z: z["y"]):
            print(f"     y={b['y']:<5d} {b['text'][:40]}  ← {'/'.join(b['hits'][:4])}")
        print("     → 继续处理（再裁 / 再擦）或直接换图")
    if not anch:
        ok = False
        print("  ❌ 主体锚点全没了（品牌/品名/净含量/纯度/配料）→ 这张已经不是产品图了，换图")
    if ok:
        print("  ✅ 无残留功效词，主体锚点齐 → 可交付（仍建议 Read 目视一次构图）")
    return ok


def main():
    ap = argparse.ArgumentParser(description="详情图净化：功效词裁切/擦除（0 积分）")
    ap.add_argument("img")
    ap.add_argument("--ocr", action="store_true", help="只扫描报告文字块与功效词")
    ap.add_argument("--mark", help="输出标注图（红=功效 绿=保留）")
    ap.add_argument("--suggest", action="store_true", help="自动给处理建议")
    ap.add_argument("--crop", help="像素裁切 x0,y0,x1,y1")
    ap.add_argument("--crop-y", help="纵向裁切 起:止（支持 0.42:1 比例或 600:1390 像素）")
    ap.add_argument("--erase", action="store_true", help="本地擦除功效词区域")
    ap.add_argument("--only", help="只处理这些块序号，如 2,3,4")
    ap.add_argument("--fill", default="edge", choices=["edge", "blur", "solid"],
                    help="擦除填充方式（默认 edge）")
    ap.add_argument("--no-verify", action="store_true",
                    help="处理完不跑闭环复检（默认会复检：再 OCR 一次确认清干净）")
    ap.add_argument("--verify", help="只对已有图片做复检（如清洗后的成品）")
    ap.add_argument("--out", help="输出图片路径")
    args = ap.parse_args()

    if args.verify:
        sys.exit(0 if verify(args.verify) else 5)

    if not os.path.exists(args.img):
        sys.exit(f"找不到图片：{args.img}")
    a = analyze(args.img)

    if args.ocr or not any([args.mark, args.suggest, args.crop, args.crop_y, args.erase]):
        dirty = report(a)
        if not dirty:
            sys.exit(3)

    if args.mark:
        mark(args.img, args.mark, a)
    if args.suggest:
        act, _ = suggest(args.img, a, args.out)
        if act in ("crop_top", "crop_bottom") and args.out and not args.no_verify:
            print()
            verify(args.out)
        if act == "replace":
            sys.exit(4)
    if args.crop or args.crop_y:
        box = crop_box(a, args.crop, args.crop_y)
        if not args.out:
            sys.exit("--crop/--crop-y 需要同时给 --out")
        do_crop(args.img, args.out, box)
        if not args.no_verify:
            print()
            verify(args.out)
    if args.erase:
        if not args.out:
            sys.exit("--erase 需要同时给 --out")
        only = {int(t) for t in args.only.split(",")} if args.only else None
        erase(args.img, args.out, a, only=only, mode=args.fill)
        if not args.no_verify:
            print()
            verify(args.out)


if __name__ == "__main__":
    main()
