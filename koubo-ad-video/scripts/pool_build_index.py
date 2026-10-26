#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""根据已归档结果生成第 3 批索引（JSON + Markdown + 改名映射），供后续检索/回溯/批量改名。"""
import os, sys, json, hashlib, subprocess, datetime

sys.path.insert(0, "/tmp/adwork/ingest")
from finalize import NAMES, VIDEO, LIB, SIDE, BACKUP

FFPROBE = "/Users/mac/.local/bin/ffprobe"


def md5(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def probe_v(p):
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=width,height,r_frame_rate,nb_frames,codec_name,pix_fmt",
             "-show_entries", "format=duration", "-of", "json", p],
            capture_output=True, text=True).stdout
        d = json.loads(out)
        st = d["streams"][0]
        num, den = st.get("r_frame_rate", "0/1").split("/")
        fps = round(int(num) / int(den), 3) if int(den) else None
        return {"w": st["width"], "h": st["height"], "fps": fps,
                "frames": int(st.get("nb_frames", 0)),
                "codec": st.get("codec_name"), "pix_fmt": st.get("pix_fmt"),
                "dur": round(float(d["format"]["duration"]), 2)}
    except Exception as e:
        return {"err": str(e)}


def main():
    os.makedirs(SIDE, exist_ok=True)
    rows = []
    for src_name, (out_name, scene, desc, basis) in NAMES.items():
        dst = os.path.join(LIB, out_name)
        if not os.path.exists(dst):
            print(f"  ⚠️ 缺归档件 {out_name}")
            continue
        from PIL import Image
        Image.MAX_IMAGE_PIXELS = None
        w, h = Image.open(dst).size
        orig = os.path.join(BACKUP, src_name)
        rows.append({
            "type": "图片", "name": out_name, "path": dst, "scene": scene,
            "resolution": f"{w}x{h}", "ratio": "16:9",
            "size_mb": round(os.path.getsize(dst) / 1048576, 2),
            "origin": src_name,
            "md5_origin": md5(orig) if os.path.exists(orig) else None,
            "md5_final": md5(dst),
            "desc": desc, "basis": basis,
            "confidence": "低" if "⚠️" in basis else "中",
        })

    vdst = os.path.join(LIB, VIDEO[1])
    if os.path.exists(vdst):
        pb = probe_v(vdst)
        vorig = os.path.join(BACKUP, VIDEO[0])
        rows.append({
            "type": "视频", "name": VIDEO[1], "path": vdst, "scene": VIDEO[2],
            "resolution": f"{pb.get('w')}x{pb.get('h')}", "ratio": "16:9",
            "fps": pb.get("fps"), "duration_s": pb.get("dur"),
            "frames": pb.get("frames"), "codec": f"{pb.get('codec')}/{pb.get('pix_fmt')}/crf18/无音轨",
            "size_mb": round(os.path.getsize(vdst) / 1048576, 2),
            "origin": VIDEO[0],
            "md5_origin": md5(vorig) if os.path.exists(vorig) else None,
            "md5_final": md5(vdst),
            "desc": VIDEO[3], "basis": VIDEO[4], "confidence": "中",
        })

    rows.sort(key=lambda r: (r["type"], r["name"]))
    meta = {
        "batch": "第 3 批",
        "date": "2026-09-29",
        "count": len(rows),
        "img_count": sum(1 for r in rows if r["type"] == "图片"),
        "vid_count": sum(1 for r in rows if r["type"] == "视频"),
        "统一规格": {"图片": "16:9 2560x1440 JPEG q95", "视频": "1920x1080 30fps H.264 yuv420p crf18 无音轨"},
        "去水印": {"图片": "模板笔画掩膜(stroke-mask th0.03/d12)，9/9 OCR 复核零残留",
                 "视频": "逐帧 inpaint 313帧，2 个站点，抽帧 OCR 复核零残留"},
        "存放方式": "平铺在图库主体根目录（沿用 _取名指南.txt 既有规则）",
        "备份": BACKUP,
        "原文件去向": "/Volumes/陈灿会展/.Trashes/501/（软删除，可恢复）",
    }
    json.dump({"meta": meta, "items": rows},
              open(os.path.join(SIDE, "素材索引.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # 改名映射（供批量改名：key=现用名, value=新名）
    json.dump({r["name"]: r["name"] for r in rows},
              open(os.path.join(SIDE, "改名映射.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # Markdown 表格
    md = ["# 第 3 批素材归档清单 · 2026-09-29", "",
          f"- 入库 **{meta['count']}** 项（{meta['img_count']} 图 + {meta['vid_count']} 视频），平铺在 `分镜图库/小绿人/` 主体根目录",
          f"- 图片统一：**{meta['统一规格']['图片']}**；视频统一：**{meta['统一规格']['视频']}**",
          f"- 去水印：图片 {meta['去水印']['图片']}；视频 {meta['去水印']['视频']}",
          f"- 原文件冷备份：`{BACKUP}`；入口原文件已移入废纸篓（可恢复）",
          "", "| # | 类型 | 归档名 | 场景 | 分辨率 | 时长 | 大小 | 内容描述 | 命名置信 |",
          "|---|------|--------|------|--------|------|------|----------|----------|"]
    for i, r in enumerate(rows, 1):
        dur = f"{r.get('duration_s')}s" if r["type"] == "视频" else "—"
        md.append(f"| {i} | {r['type']} | `{r['name']}` | {r['scene']} | {r['resolution']} | {dur} | "
                  f"{r['size_mb']}MB | {r['desc']} | {r['confidence']} |")
    md += ["", "## 命名依据（推断来源）", ""]
    for i, r in enumerate(rows, 1):
        md.append(f"{i}. **{r['name']}** ← `{r['origin']}`  ")
        md.append(f"   {r['basis']}")
    md += ["", "## 待复核（建议肉眼过一遍）", "",
           "以下条目自动推断置信较低，建议打开素材核一眼；如需改名，直接告诉我 "
           "「现名=新名」即可批量替换。"]
    low = [(i, r) for i, r in enumerate(rows, 1) if r["confidence"] == "低"]
    if low:
        for i, r in low:
            md.append(f"- #{i} `{r['name']}`")
    else:
        md.append("- 无")
    open(os.path.join(SIDE, "迁移清单.md"), "w", encoding="utf-8").write("\n".join(md))
    print(f"索引 {len(rows)} 条 → {SIDE}")
    for r in rows:
        print(f"  {r['name']:34s} {r['resolution']:11s} {r['size_mb']:>6.1f}MB  置信{r['confidence']}")


if __name__ == "__main__":
    main()
