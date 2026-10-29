#!/usr/bin/env python3
"""分镜图入库（图库 → 正式目录 + 更新 md5 索引）。

2026-10-29 米糊 15 张新图入库时新建。把「绿皮检测 + 查重 + 规范改名 + 更新索引」
四件事一次做完，避免手工 mv 之后索引不同步（下次查重判不出来，库里出现重复图）。

用法：
    python3 ingest_shots.py --src shots_v4/final --dest "小绿人/米糊/英之素/图片" \
        --map map.json [--dry]

map.json 形如：
    {"a2.png": "产品_厨房窗边扶桶盖_中景.png", ...}

判重：读 `<图库根>/_库索引_md5.json` 的 entries（不重新算全库 md5，外置盘太慢），
      md5 命中就跳过并在报告里打出已存在的相对路径。
绿皮：G>R+18 且 G>B+18 且饱和>28 的像素占比 >6% 判小绿人（IP 混入闸门）。
"""
import os, sys, json, shutil, hashlib, argparse, time

LIB_ROOT = os.environ.get("AD_LIB_ROOT") or "/Volumes/陈灿会展/广告视频/分镜图库"
INDEX = "_库索引_md5.json"
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")


def md5_of(p, chunk=1 << 20):
    h = hashlib.md5()
    with open(p, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def green_ratio(p):
    from PIL import Image
    im = Image.open(p).convert("RGB")
    im.thumbnail((480, 480))
    w, h = im.size
    px = im.load()
    c = 0
    n = 0
    for y in range(0, h, 2):
        for x in range(0, w, 2):
            r, g, b = px[x, y]
            n += 1
            mx, mn = max(r, g, b), min(r, g, b)
            sat = (mx - mn) * 100 // mx if mx else 0
            if g > r + 18 and g > b + 18 and sat > 28:
                c += 1
    return c / max(1, n)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--dest", required=True, help="相对图库根的目录，如 小绿人/米糊/英之素/图片")
    ap.add_argument("--map", required=True, help="JSON：[src文件名] -> 目标文件名")
    ap.add_argument("--root", default=LIB_ROOT)
    ap.add_argument("--dry", action="store_true")
    ap.add_argument("--skip-green", action="store_true")
    ap.add_argument("--green-min", type=float, default=0.06,
                    help="绿皮占比下限（0~1），中远景/暗场图会被误判，此时配 --skip-green")
    a = ap.parse_args()

    mp = json.load(open(a.map, encoding="utf-8"))
    idx_path = os.path.join(a.root, INDEX)
    idx = json.load(open(idx_path, encoding="utf-8"))
    entries = idx.get("entries", {})
    known = {}
    for k, v in entries.items():
        md5 = v.get("md5")
        if md5 and md5 not in known:
            known[md5] = k

    dest_dir = os.path.join(a.root, a.dest)
    os.makedirs(dest_dir, exist_ok=True)

    report = {"added": [], "skipped_dup": [], "failed": [], "not_green": []}
    for src_name, dst_name in mp.items():
        p = os.path.join(a.src, src_name)
        if not os.path.abspath(p).lower().endswith(IMG_EXT):
            continue
        if not os.path.isfile(p):
            report["failed"].append((src_name, "源文件不存在"))
            continue
        m = md5_of(p)
        if m in known:
            report["skipped_dup"].append((src_name, known[m]))
            continue
        # ⚠️ 绿皮占比只对「人物占画面 1/3 以上」的图准（特写/近景 ~16%）。
        #    中远景 + 暗场（夜间餐厅/暗棚）会掉到 0.0x%，b2 实测 0.01% 但确实是本 IP
        #    ⇒ 阈值只作参考，--skip-green 用于「已逐张目视确认」的批次。
        gr = green_ratio(p)
        report.setdefault("green_pct", []).append((src_name, round(gr * 100, 2)))
        if not a.skip_green and gr < a.green_min:
            report["not_green"].append((src_name, round(gr * 100, 2)))
            continue
        out = os.path.join(dest_dir, dst_name)
        if os.path.exists(out):
            report["failed"].append((src_name, "目标已存在：" + dst_name))
            continue
        if not a.dry:
            shutil.copy2(p, out)
            rel = os.path.relpath(out, a.root)
            entries[rel] = {"size": os.path.getsize(out),
                            "mtime": int(os.path.getmtime(out)),
                            "md5": m}
            known[m] = rel
        report["added"].append((src_name, dst_name))

    if not a.dry and report["added"]:
        idx["entries"] = entries
        idx["count"] = len(entries)
        idx["built_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        bak = idx_path + ".bak_" + time.strftime("%y%m%d_%H%M%S")
        shutil.copy2(idx_path, bak)
        json.dump(idx, open(idx_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        report["index_backup"] = os.path.basename(bak)
        report["index_count"] = idx["count"]

    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("\n入库 %d / 重复跳过 %d / 非小绿人 %d / 失败 %d"
          % (len(report["added"]), len(report["skipped_dup"]),
             len(report["not_green"]), len(report["failed"])))
    return 0 if not report["failed"] and not report["not_green"] else 1


if __name__ == "__main__":
    sys.exit(main())
