#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
opening_pool.py — 小绿人开场视频池轮换器（0 积分防重复）

用法:
  列出池子:   python3 opening_pool.py --pool <池json> --list
  挑选开场:   python3 opening_pool.py --pool <池json> --durs 1.0,1.65 \
                --workdir <工程目录> --project <片名> [--dry-run]
    → 输出可直接粘进 config 的 phrases 条目（kind=prod），并在池里记账
  撤销记账:   python3 opening_pool.py --pool <池json> --undo --project <片名>

挑选逻辑: 按「段+变体」组合的历史使用次数升序挑选（没用过优先），同片两镜
不用同一段；变体五选一（none/mirror/slow08/fast12/reverse），非 none 变体
现场用 ffmpeg 加工出派生片段到 <workdir>/pool_derived/，返回派生文件条目。
"""
import argparse, json, os, random, subprocess, sys, datetime, time

FF = os.environ.get("FFMPEG_BIN", "ffmpeg")

TRANSFORMS = ["none", "mirror", "slow08", "fast12", "reverse"]


def seg_len(s):
    return s["out"] - s["in"]


def usable(seg, dur, variant):
    """该变体下，这段素材能否撑满 dur 秒"""
    need = {"none": dur, "mirror": dur, "reverse": dur,
            "slow08": dur * 0.8, "fast12": dur * 1.2}[variant]
    return seg_len(seg) >= need + 0.05 and "弃用" not in seg.get("mood", [])


def make_derived(src, seg, variant, dur, out_dir):
    """生成变体派生片段，返回 (文件路径, 实际时长)"""
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(src))[0]
    name = f"{base}_{seg['id']}_{variant}_{datetime.datetime.now():%H%M%S}.mp4"
    out = os.path.join(out_dir, name)
    ss, take = seg["in"], dur + 0.15
    if variant == "none":
        return None, None          # 直接引用原片，不生成
    vf = {"mirror": "hflip",
          "slow08": "setpts=PTS*1.25",
          "fast12": "setpts=PTS/1.2",
          "reverse": "reverse"}[variant]
    cmd = [FF, "-y", "-hide_banner", "-loglevel", "error",
           "-ss", str(ss), "-t", f"{take:.3f}", "-i", src,
           "-vf", vf, "-an", "-c:v", "libx264", "-preset", "veryfast",
           "-crf", "17", "-pix_fmt", "yuv420p", out]
    subprocess.run(cmd, check=True)
    real = dur if variant != "slow08" else dur
    return out, real


def pick(pool, durs, workdir, project, dry=False, seed=None, fresh_days=7.0):
    rnd = random.Random(seed)
    derived_dir = os.path.join(workdir, "pool_derived")
    combos = []  # (usage, seg, variant, src, clip, fresh)
    for clip in pool["clips"]:
        src = os.path.join(os.path.dirname(pool_path), clip["file"])
        if not os.path.exists(src):
            continue
        # 🆕 新片优先（2026-09-23 用户拍板）：入库越新折扣越大（线性衰减到
        #    fresh_days 天归零），保证用户刚放的新片最先曝光；老片无加成
        age_d = (time.time() - os.path.getmtime(src)) / 86400.0
        fresh = fresh_days > 0 and age_d <= fresh_days
        fresh_bonus = max(0.0, (fresh_days - age_d)) * 0.3 if fresh else 0.0
        for seg in clip.get("segments", []):
            if seg.get("mood") == ["弃用"] or "弃用" in seg.get("mood", []):
                continue
            for v in TRANSFORMS:
                u = sum(1 for r in seg.get("used", [])
                        if r.get("variant") == v)
                combos.append((u, seg, v, src, clip, fresh, fresh_bonus))
    entries, chosen, chosen_ids = [], [], []
    for dur in durs:
        cand = [c for c in combos
                if usable(c[1], dur, c[2])
                and (c[1]["id"], c[2]) not in chosen
                and c[1]["id"] not in chosen_ids]  # 同片两镜不用同一段（连变体都不救重复感）
        if not cand:
            print(f"!! 没有可用片段撑 {dur}s，请扩池", file=sys.stderr)
            sys.exit(1)
        cand.sort(key=lambda c: (c[0] - c[6], rnd.random()))
        u, seg, v, src, clip, fresh, fb = cand[0]
        chosen.append((seg["id"], v))
        chosen_ids.append(seg["id"])
        if v == "none":
            in_pt = round(min(seg["in"], seg["out"] - dur), 3)
            e = {"kind": "prod", "video": src, "src": [in_pt, in_pt + dur + 0.05]}
        else:
            f, _ = make_derived(src, seg, v, dur, derived_dir)
            e = {"kind": "prod", "video": f, "src": [0.0, dur + 0.05]}
        entries.append({"text": None, **e})
        tag = "🆕新片" if fresh else "    "
        print(f"  选中 {seg['id']} [{v}] 用量{u}{tag} —— {seg['action']}  ({clip['file']})",
              file=sys.stderr)
        if not dry:
            seg.setdefault("used", []).append(
                {"project": project, "variant": v,
                 "date": datetime.date.today().isoformat()})
    return entries


def main():
    global pool_path
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool", required=True)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--durs", help="逗号分隔各镜时长，如 1.0,1.65")
    ap.add_argument("--workdir", default=".")
    ap.add_argument("--project", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--undo", action="store_true")
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--fresh-days", type=float, default=7.0,
                    help="N 天内入库的视频优先曝光（默认 7，0=关闭）")
    a = ap.parse_args()
    pool_path = os.path.abspath(a.pool)
    pool = json.load(open(pool_path, encoding="utf-8"))

    if a.list:
        for clip in pool["clips"]:
            print(f"== {clip['file']} [{clip.get('quality','')}] {clip.get('scene','')}")
            for s in clip.get("segments", []):
                n = len(s.get("used", []))
                vs = ",".join(sorted({r["variant"] for r in s.get("used", [])})) or "-"
                print(f"   {s['id']} {s['in']:.2f}-{s['out']:.2f}s ×{n}[{vs}] {s['action']}")
        return

    if a.undo:
        n = 0
        for clip in pool["clips"]:
            for s in clip.get("segments", []):
                keep = [r for r in s.get("used", []) if r.get("project") != a.project]
                n += len(s.get("used", [])) - len(keep)
                s["used"] = keep
        json.dump(pool, open(pool_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"已撤销 {a.project} 的 {n} 条记录")
        return

    if not a.durs:
        ap.error("需要 --durs 或 --list")
    durs = [float(x) for x in a.durs.split(",")]
    entries = pick(pool, durs, a.workdir, a.project, a.dry_run, a.seed,
                   a.fresh_days)
    if not a.dry_run:
        json.dump(pool, open(pool_path, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"已记账: {a.project} ({len(durs)}镜)", file=sys.stderr)
    for i, e in enumerate(entries):
        if e.get("text") is None and i < len(durs):
            e.pop("text")
    print(json.dumps(entries, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
