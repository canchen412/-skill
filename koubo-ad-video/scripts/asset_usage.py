# -*- coding: utf-8 -*-
"""素材使用台账：扫全部成片 config，统计每张分镜图/视频被用了几次、首次/末次使用。

用途（2026-09-30 灿爹要求「优先选用未使用过的视频素材和新加入的图片」）：
  选材前先跑本脚本 → 挑素材时把「次数 0（从未用过）」和「入库时间新」的排在前面。

用法:
  python asset_usage.py [--root /Users/mac/WorkBuddy] [--out <图库>/_素材使用台账.json] [--top N]
"""
import argparse, glob, json, os, re, time
from collections import defaultdict

SKIP_NAME = (".bak",)
SKIP_CFG = ("config_timi.json", "config_C_v2.json")

# ★2026-10-03 修：工程内分片子目录的命名样式（s1/s2…、a/b/c/d、v1/v2、01/02…）
#   这些目录名不是「工程名」，遇到要再上溯一级，否则 films 里只会留下 "a"/"b" 这类无信息量的字母。
_SUBDIR_RE = re.compile(r"(?:s\d+|v\d+|[a-z]|\d{1,2})", re.I)


def _film_name(p):
    """从 config 路径推出「工程名」，用于台账 films 字段。"""
    parts = p.split("/")
    d = parts[-2]
    if len(parts) >= 3 and _SUBDIR_RE.fullmatch(d):
        return parts[-3]
    return d

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="/Users/mac/WorkBuddy")
    ap.add_argument("--out", default="/Volumes/陈灿会展/广告视频/分镜图库/小绿人/_素材使用台账.json")
    ap.add_argument("--top", type=int, default=0, help="只打印前 N 条（0=全部）")
    a = ap.parse_args()

    use = defaultdict(lambda: {"n": 0, "films": [], "first": None, "last": None, "kind": None})
    seen = set()
    for p in sorted(glob.glob(a.root + "/**/config*.json", recursive=True)):
        b = os.path.basename(p)
        if any(s in b for s in SKIP_NAME) or b in SKIP_CFG or "backup" in p:
            continue
        if any(k in b for k in ("单行字幕", "超重字验证")):
            continue
        try:
            c = json.load(open(p, encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(c, dict) or not isinstance(c.get("phrases"), list):
            continue
        # ★2026-10-03 修：原来 key=(proj, basename)，而 proj 在 a/b/c/d 结构下会退化成单字母
        #   ⇒ 261002/a/config.json 与 261003/a/config.json 撞成同一个 key，**后一个工程整条被去重丢弃**
        #   （实测 261003 四条片的素材一次都没进台账）。改按绝对路径去重，同一文件天然唯一。
        proj = _film_name(p)
        key = os.path.abspath(p)
        if key in seen:
            continue
        seen.add(key)
        mt = os.path.getmtime(p)
        for ph in c["phrases"]:
            # ★2026-10-03 修：imgvid 镜的 src 直接就是 mp4（**不带 video 字段**），
            #   原逻辑只认 ph["video"] ⇒ 所有视频分镜素材 uses 恒为 0。
            #   而 #159 要求「优先选用未使用过的视频素材」，0 次会被反复优先挑中（明明用过）。
            f = None; k = None
            kind = ph.get("kind"); src = ph.get("src"); vid = ph.get("video")
            if kind == "imgvid":
                f = vid or (src[0] if isinstance(src, list) and src else src)
                k = "video"
            elif isinstance(vid, str):
                f, k = vid, "video"
            elif isinstance(src, list) and vid:
                f, k = vid, "video"
            elif isinstance(src, str):
                f = src
                k = "video" if src.lower().endswith((".mp4", ".mov")) else "img"
            if not f or not isinstance(f, str):
                continue
            n = os.path.basename(f)
            e = use[n]; e["n"] += 1; e["kind"] = k
            if proj not in e["films"]:
                e["films"].append(proj)
            e["first"] = mt if e["first"] is None else min(e["first"], mt)
            e["last"] = mt if e["last"] is None else max(e["last"], mt)

    # ★2026-09-30 补：扫图库全量素材，把「存在但从未出现在任何成片 config」的也记为 0 次
    #   （只从 config 反推会漏掉所有没用过的素材，无法回答「哪些新图还没用上」）
    POOL = a.out.rsplit("/", 1)[0]
    if os.path.isdir(POOL):
        for root, dirs, files in os.walk(POOL):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            rel = os.path.relpath(root, POOL)
            # 跳过备份/判废/分层/归档等非可选素材目录
            if any(seg in rel for seg in ("_旧版备份", "_旧画风", "_判废", "_分层",
                                          "_定妆图历史", "_无眼版备份", "_他品污染隔离",
                                          "_灿爹归档", "_新生成", "_品牌绑定")):
                continue
            for f in files:
                if f.startswith(".") or f.endswith(".bak"):
                    continue
                if not f.lower().endswith((".png", ".jpg", ".jpeg", ".mp4", ".mov")):
                    continue
                if f not in use:
                    mp = os.path.join(root, f)
                    try:
                        mt = os.path.getmtime(mp)
                    except Exception:
                        mt = time.time()
                    e = use[f]
                    e["n"] = 0; e["films"] = []
                    e["kind"] = "video" if f.lower().endswith((".mp4", ".mov")) else "img"
                    e["first"] = mt; e["last"] = mt
                    e["added"] = mt
    # ★2026-10-01 血泪保护：扫到 0 条成片，几乎可以肯定是 --root 传错。
    #   --root 是「成片工程目录」（默认 /Users/mac/WorkBuddy），**不是图库**！
    #   不加保护的话，会用一个「全是 0 次」的台账覆盖掉真实用量数据（本次已踩坑，355→清零）。
    if len(seen) == 0:
        print("\n⚠️  扫到 0 条成片 —— --root 传错了。")
        print("    --root 必须指向【成片工程目录】（默认 /Users/mac/WorkBuddy），不是图库目录。")
        print("    已中止，**没有**覆盖 " + a.out)
        return {}

    rec = {}
    for n, e in sorted(use.items(), key=lambda kv: -kv[1]["n"]):
        rec[n] = {"uses": e["n"],
                  # ★2026-10-03：8 → 20。旧值会把「被 8 部以上片用过」的老素材，把最新几部全截掉，
                  #   台查看「这素材最近被哪些片用了」时看不到新工程（261003 就因此显示缺项）。
                  "films": e["films"][:20],
                  "first_used": time.strftime("%Y-%m-%d", time.localtime(e["first"])) if e["first"] else None,
                  "last_used": time.strftime("%Y-%m-%d", time.localtime(e["last"])) if e["last"] else None,
                  "kind": e["kind"]}
    json.dump({"updated": time.strftime("%Y-%m-%d %H:%M"), "films_scanned": len(seen),
               "assets": rec}, open(a.out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"扫 {len(seen)} 条成片 → {len(rec)} 个素材 → {a.out}")
    rows = list(rec.items())
    if a.top:
        rows = rows[:a.top]
    for n, r in rows:
        tag = r["last_used"] or r.get("added_date") or "未用过"
        print(f"  {r['uses']:>3}×  {tag}  {n}")
    return rec

if __name__ == "__main__":
    main()
