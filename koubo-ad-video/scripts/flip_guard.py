#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
flip_guard.py —— 「含文字/符号的图禁 flip」自动检测（SKILL.md #45 的流程卡点）
2026-10-02 新增。起因：爱兰纯肌酸 261002 镜5「保障_举盾A类认证」盾上大 ✅ 被 flip
成反勾（与 09-26 A条实锤同图同错），说明规则只靠记性必漏，必须脚本化拦截。

用法（渲染前必跑，❌ 即不得渲染）：
    python scripts/flip_guard.py --config s1/config.json --work <工程目录>

检测三层：
  ① 文件名/描述强符号关键词（勾|盾|箭|牌|证|历|钟|签|logo…）→ ❌ 候选
  ② vision_ocr（Swift Vision 框架）读图内文字，含 ≥2 字符可读块 → ❌
     （伪文字标签如货架乱码字母同样会被读出 → 一并拦）
  ③ 每镜输出「原图 | 水平镜像」并排图到 qc/flip_guard_镜N.jpg —— OCR 抓不到
     的方向性符号（对勾/箭头/手势）必须目视并排图确认，这一步不可跳过

exit code：有 ❌ = 1（渲染流程应中止）
"""
import argparse, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OCR_BIN = os.path.join(HERE, "vision_ocr")

STRONG_WORDS = ["勾", "盾", "箭", "奖", "牌", "证", "钟", "历", "签", "指",
                "logo", "字", "表", "旗", "路", "方向"]
# OCR 判「有文字」的最小可读块要求
MIN_BLOCK_CHARS = 2


def run_ocr(path):
    try:
        r = subprocess.run([OCR_BIN, path], capture_output=True, timeout=60)
        if r.returncode != 0:
            return []
        d = json.loads(r.stdout.decode("utf-8", "ignore"))
        return [b.get("text", "") for b in d.get("blocks", [])]
    except Exception as e:
        print(f"    ⚠️ OCR 失败：{e}")
        return []


def side_by_side(src, out):
    try:
        from PIL import Image, ImageOps
        im = Image.open(src).convert("RGB")
        w, h = im.size
        flip = ImageOps.mirror(im)
        W = 1280
        cw = W // 2
        ch = int(h * cw / w)
        canvas = Image.new("RGB", (W, ch), (24, 24, 24))
        canvas.paste(im.resize((cw, ch)), (0, 0))
        canvas.paste(flip.resize((cw, ch)), (cw, 0))
        canvas.save(out, quality=88)
        return True
    except Exception as e:
        print(f"    ⚠️ 并排图失败：{e}")
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--work", default=".", help="工程目录（并排图输出到 <work>/qc/）")
    a = ap.parse_args()

    cfg = json.load(open(a.config, encoding="utf-8"))
    qc = os.path.join(a.work, "qc")
    os.makedirs(qc, exist_ok=True)

    bad, warn, ok = [], [], []
    for i, p in enumerate(cfg["phrases"], 1):
        if not p.get("flip"):
            continue
        src = p.get("src", "")
        name = os.path.basename(src)
        text = p.get("text", "")
        desc = f"{name} {p.get('desc','')}"
        reasons = []

        # ① 强符号关键词
        hit = [w for w in STRONG_WORDS if w in desc]
        if hit:
            reasons.append(f"文件名含符号关键词:{'/'.join(hit[:3])}")

        # ② OCR 文字
        if os.path.isfile(src):
            blocks = [t for t in run_ocr(src) if len(t.strip()) >= MIN_BLOCK_CHARS]
            if blocks:
                reasons.append(f"OCR读到文字:{blocks[:3]}")
        else:
            reasons.append("素材不存在")

        # ③ 并排图（必出，供目视方向性符号）
        side = os.path.join(qc, f"flip_guard_镜{i}.jpg")
        side_by_side(src, side) if os.path.isfile(src) else None

        row = f"镜{i:2d} {name[:44]}"
        if reasons:
            bad.append((row, reasons, side))
        else:
            warn.append((row, side))

    print("=" * 62)
    print("flip_guard 检测结果（#45：含文字/符号的图禁 flip）")
    print("=" * 62)
    for row, reasons, side in bad:
        print(f"❌ {row}")
        for r in reasons:
            print(f"     {r}")
        print(f"     并排图: {side}")
    for row, side in warn:
        print(f"⚠️ {row}  无自动信号 —— 仍必须目视并排图确认无方向性符号（对勾/箭头/手势）")
        print(f"     并排图: {side}")
    if not bad and not warn:
        print("（无 flip 镜）")
    print("=" * 62)
    if bad:
        print(f"结论：{len(bad)} 镜禁 flip —— 去 flip 改用 motion(zoom/pan) 差异化，修完复跑本脚本")
        sys.exit(1)
    print("结论：自动层通过；⚠️ 镜以并排图目视为准")


if __name__ == "__main__":
    main()
