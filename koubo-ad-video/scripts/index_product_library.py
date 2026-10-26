#!/usr/bin/env python3
"""产品图库索引与匹配（koubo-ad-video 技能 · 2026-09-18 用户定的接口）。

结构（用户口述）：`/Volumes/陈灿会展/广告视频/<商品分类>/<品牌>/产品图库/`
实测：顶层有的就是「分类」（肌酸/蛋白粉/米糊…），有的直接是「产品目录」（600卡拜拜丸…），
      所以脚本两层都扫，分类 -> 品牌 建索引；用户只发文案时按索引比对，命中就直接取图。

⚠️ 外置盘（exFAT/USB）`find` 递归很慢（实测 54s），全量两层 `ls` 也要 24s
   → **必须走缓存**：`--scan` 建索引（几分钟级），之后 `--query` 读缓存（毫秒级）。
   缓存默认 12 小时过期；没命中时自动提示重扫。

用法：
    S=".../scripts/index_product_library.py"
    python3 $S --scan                       # 重建索引
    python3 $S --query "米当卡一水肌酸 三角喵"   # 按文案匹配，输出候选 + 产品图库路径 + 图片清单
    python3 $S --list 肌酸/米当卡              # 直接列出某个目录下的产品图
"""
import os, re, json, time, argparse, difflib, subprocess

# 素材盘根目录：默认本机外置盘；换机器/换人用环境变量 AD_LIB_ROOT 覆盖，
# 或命令行 --root 覆盖（命令行优先级最高）。见 SKILL.md「换机器部署」。
ROOT_DEFAULT = os.environ.get("AD_LIB_ROOT") or "/Volumes/陈灿会展/广告视频"
HERE = os.path.dirname(os.path.abspath(__file__))
INDEX_PATH = os.path.join(os.path.dirname(HERE), "assets", "product_library_index.json")
INDEX_TTL = 12 * 3600
LIB_HINT = ("产品图库", "产品图", "图库", "素材图")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".tif", ".tiff")
VID_EXT = (".mp4", ".mov", ".m4v", ".avi", ".mkv")
SKIP_PREFIX = ("._", "._", ".")


def is_noise(name):
    return name.startswith(SKIP_PREFIX) or name in (".DS_Store",)


def norm(s):
    s = s.lower().strip()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", s)


def scan(root):
    """只扫两层（分类 -> 品牌），只判目录，尽量少 stat。"""
    out = {"root": root, "scanned_at": time.time(), "categories": {}, "flat": {}}
    try:
        top = sorted(e for e in os.listdir(root) if not is_noise(e))
    except OSError as ex:
        raise SystemExit(f"[index] 素材盘不可读：{root}（{ex}）——请确认 /Volumes/陈灿会展 已挂载")

    for c in top:
        cp = os.path.join(root, c)
        if not os.path.isdir(cp):
            continue
        children = sorted(e for e in os.listdir(cp) if not is_noise(e))
        brands, files = {}, []
        for ch in children:
            chp = os.path.join(cp, ch)
            try:
                if os.path.isdir(chp):
                    brands[ch] = {"lib": False, "lib_path": "", "imgs": []}
                    try:
                        grand = sorted(g for g in os.listdir(chp) if not is_noise(g))
                    except OSError:
                        grand = []
                    for g in grand:
                        if not os.path.isdir(os.path.join(chp, g)):
                            continue
                        if any(h in norm(g) for h in LIB_HINT) or any(h in g for h in LIB_HINT):
                            libp = os.path.join(chp, g)
                            try:
                                imgs = sorted(i for i in os.listdir(libp)
                                              if i.lower().endswith(IMG_EXT) and not is_noise(i))
                            except OSError:
                                imgs = []
                            brands[ch] = {"lib": True, "lib_path": libp, "imgs": imgs}
                            break
                    # 品牌目录自己直接放图（没有 产品图库/ 子目录时的兜底）
                    if not brands[ch]["lib"]:
                        direct = sorted(i for i in grand
                                        if i.lower().endswith(IMG_EXT) and not is_noise(i))
                        if direct:
                            brands[ch]["imgs"] = direct
                            brands[ch]["lib_path"] = chp
                else:
                    files.append(ch)
            except OSError:
                continue
        if brands:
            out["categories"][c] = {"brands": brands}
        else:
            imgs = [f for f in files if f.lower().endswith(IMG_EXT)]
            if imgs:
                out["flat"][c] = {"lib_path": cp, "imgs": imgs}
            elif children:
                out["flat"][c] = {"lib_path": cp, "imgs": []}
    return out


def load_index(force_rescan=False):
    if not force_rescan and os.path.exists(INDEX_PATH):
        try:
            d = json.load(open(INDEX_PATH, encoding="utf-8"))
            if time.time() - d.get("scanned_at", 0) < INDEX_TTL:
                return d, False
        except Exception:
            pass
    d = scan(ROOT_DEFAULT)
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    json.dump(d, open(INDEX_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return d, True


def score(name, text):
    """目录名 vs 文案：整名原样出现=1.0；否则滑窗模糊，但阈值卡紧（防"肌酸"这种品类词误命中品牌）。"""
    nn = norm(name)
    if not nn:
        return 0.0
    if nn in text:
        return 1.0
    if len(nn) < 3:
        return 0.0
    best, L = 0.0, len(nn)
    seen = set()
    for i in range(len(text)):
        for w in (L - 1, L, L + 1, L + 2):
            if w <= 0:
                continue
            seg = text[i:i + w]
            if not seg or seg in seen:
                continue
            seen.add(seg)
            r = difflib.SequenceMatcher(None, nn, seg).ratio()
            if r > best:
                best = r
            if best >= 0.98:
                return 1.0
    return best if best >= 0.72 else 0.0


def query(idx, text, topk=6):
    """品牌分是主项，品类分做加权：品牌命中 + 品类也命中 才满分。
    这样"米当卡"在 米糊/甜菜根粉/谷氨酰胺 下都不会冒出来抢位。"""
    t = norm(text)
    hits = []
    for cat, cv in idx.get("categories", {}).items():
        cs = score(cat, t)
        for brand, bv in cv["brands"].items():
            bs = score(brand, t)
            if bs <= 0:
                continue
            total = bs * (0.55 + 0.45 * cs)
            if total < 0.6:
                continue
            hits.append({
                "score": round(total, 3), "cat": cat, "brand": brand,
                "cat_score": round(cs, 3), "brand_score": round(bs, 3),
                "lib": bv["lib"], "lib_path": bv["lib_path"], "imgs": bv["imgs"],
                "archive_dir": os.path.join(ROOT_DEFAULT, cat, brand),
            })
    for cat, v in idx.get("flat", {}).items():
        s = score(cat, t)
        if s < 0.6:
            continue
        hits.append({"score": round(s, 3), "cat": "(顶层)", "brand": cat,
                     "cat_score": 0.0, "brand_score": round(s, 3),
                     "lib": bool(v["imgs"]), "lib_path": v["lib_path"],
                     "imgs": v["imgs"], "archive_dir": v["lib_path"]})
    hits.sort(key=lambda h: (-h["score"], not h["lib"], -len(h["imgs"])))
    return hits[:topk]


def export_imgs(src_dir, out_dir, limit=3):
    """挑产品图并导出为可直接喂 ImageGen 的 jpg（HEIC 用 sips 转，macOS 自带）。
    顺序：产品图库/ 里的图优先（按文件名序），其次目录里直接放的图。"""
    import shutil
    import subprocess
    lib_cands, other_cands = [], []
    for name in sorted(os.listdir(src_dir)):
        if is_noise(name):
            continue
        p = os.path.join(src_dir, name)
        if os.path.isdir(p):
            if any(h in name for h in LIB_HINT):
                for e2 in sorted(os.listdir(p)):
                    if not is_noise(e2) and e2.lower().endswith(IMG_EXT):
                        lib_cands.append(os.path.join(p, e2))
        elif name.lower().endswith(IMG_EXT):
            other_cands.append(p)
    cands = lib_cands + other_cands
    os.makedirs(out_dir, exist_ok=True)
    out = []
    for i, src in enumerate(cands[:limit], 1):
        ext = os.path.splitext(src)[1].lower()
        dst = os.path.join(out_dir, f"产品图_{i}.jpg")
        try:
            if ext == ".heic":
                subprocess.run(["sips", "-s", "format", "jpeg", src, "--out", dst],
                               check=True, capture_output=True)
            else:
                shutil.copy(src, dst)
            out.append((src, dst))
        except Exception as ex:
            print(f"[export] 失败 {src} ({ex})")
    return out


def main():
    global ROOT_DEFAULT
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", action="store_true", help="重建索引（外置盘约 30–60s）")
    ap.add_argument("--query", help="按文案片段匹配（品牌/产品名）")
    ap.add_argument("--list", help="直接列目录下的产品图，如 肌酸/米当卡")
    ap.add_argument("--export", help="把某目录的产品图导出成 产品图_1.jpg…（HEIC 自动转 jpg）")
    ap.add_argument("--out", default="/tmp/adwork/product_refs", help="--export 的输出目录")
    ap.add_argument("--limit", type=int, default=3, help="--export 最多导出几张")
    ap.add_argument("--root", default=ROOT_DEFAULT)
    args = ap.parse_args()

    ROOT_DEFAULT = args.root

    if args.scan:
        t0 = time.time()
        idx = scan(args.root)
        os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
        json.dump(idx, open(INDEX_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        nb = sum(len(v["brands"]) for v in idx["categories"].values())
        nl = sum(1 for v in idx["categories"].values() for b in v["brands"].values() if b["lib"])
        print(f"[index] 扫描完成 {time.time()-t0:.1f}s → {INDEX_PATH}")
        print(f"[index] 分类 {len(idx['categories'])} 个 / 品牌 {nb} 个 / 带产品图库 {nl} 个 / 顶层直放 {len(idx['flat'])} 个")
        return

    if args.list:
        p = args.list if os.path.isabs(args.list) else os.path.join(args.root, args.list)
        if not os.path.isdir(p):
            raise SystemExit(f"[list] 目录不存在：{p}")
        for e in sorted(os.listdir(p)):
            if is_noise(e):
                continue
            full = os.path.join(p, e)
            if os.path.isdir(full):
                if any(h in e for h in LIB_HINT):
                    print(f"[图库] {e}/")
                    for i in sorted(os.listdir(full)):
                        if not is_noise(i) and i.lower().endswith(IMG_EXT):
                            print(f"        {i}")
                continue
            if e.lower().endswith(IMG_EXT):
                print(f"[图]   {e}")
            elif e.lower().endswith(VID_EXT):
                print(f"[视]   {e}")
        return

    if args.export:
        p = args.export if os.path.isabs(args.export) else os.path.join(args.root, args.export)
        if not os.path.isdir(p):
            raise SystemExit(f"[export] 目录不存在：{p}")
        got = export_imgs(p, args.out, args.limit)
        if not got:
            print(f"[export] {p} 里没有可用图片 —— 去问用户")
        for src, dst in got:
            dim = ""
            try:
                r = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", dst],
                                   capture_output=True, text=True)
                ns = [ln.split(":")[-1].strip() for ln in r.stdout.splitlines() if "pixel" in ln]
                if len(ns) == 2:
                    w, h = int(ns[0]), int(ns[1])
                    ar = h / max(w, 1)
                    dim = f"  {w}x{h}"
                    if ar > 1.5:
                        dim += "  ← 竖长图，多半是详情/卖点图，别当产品参考喂生图"
            except Exception:
                pass
            print(f"[export] {os.path.basename(src)}  ->  {dst}{dim}")
        print("[export] ★ 逐张 Read 目视：实拍产品照（完整包装/白底/手持）才当产品参考喂 ImageGen；"
              "详情图只用来核对规格卖点。")
        return

    if args.query:
        idx, rescanned = load_index()
        if rescanned:
            print(f"[index] 缓存缺失/过期，已重扫（{len(idx['categories'])} 分类）")
        hits = query(idx, args.query)
        if not hits:
            print("[query] 没匹配到任何目录 —— 按『找不到就问用户』处理，不要自建文件夹")
            return
        for h in hits:
            tag = "有产品图库" if h["lib"] else "无产品图库"
            print(f"\n★ {h['score']:.2f}  {h['cat']} / {h['brand']}   [{tag}]")
            print(f"   归档目录: {h['archive_dir']}")
            print(f"   图片来源: {h['lib_path']}")
            if h["imgs"]:
                for i in h["imgs"][:12]:
                    print(f"     - {i}")
            else:
                print("     （没找到图片，去问问用户）")
        return

    ap.print_help()


if __name__ == "__main__":
    main()
