#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""seg_resplice.py —— 配音段「替换 / 插入 / 删除 + 重排段号 + 刷新 segments.json + 重拼 mp3」

场景（2026-09-28 灿爹返工定）：配音有个别段读法不对（品牌名「21金维他」被读断、
「烟酰胺／走高含量路线」断句错位），已按 #72 的办法**合成出正确的候选段 wav**。
本脚本负责把候选段落**安全地**接回工程，无需重新加载模型（0 积分）。

为什么要脚本化（而不是手写 mv）：拆段会让**后续所有段号后移**，手写 `mv` 极易
顺序写错（必须先移最后一个）。本脚本的做法是「**先算出最终段序、再整体重写**」：
  ① 读 seg 目录现有 seg_NN.wav；
  ② 按 plan 生成最终段序（replace 覆盖内容、insert_after 在其后插入、drop 删除）；
  ③ 备份原 seg 到 `<seg>/../seg_bak_orig`（只备份一次）；
  ④ **先把结果全部落进临时目录，再清空 seg 目录、按新段序写回** —— 不涉及 mv 顺序；
  ⑤ 按 `text_tts.txt` 的句末标点切分，刷新 `segments.json`，并**断言段数 == wav 数**；
  ⑥ 可选 `--rebuild`：调 `cosyvoice_clone.py --rebuild-from` 重拼 mp3（秒级）。

plan.json 格式（key 为**原始段号**，即替换/插入前的位置）::

    {
      "1": {"replace": {"5": "/tmp/exp/s1p5_A.wav"},
            "insert_after": {"6": ["/tmp/exp/s1seg/seg_01.wav",
                                   "/tmp/exp/s1seg/seg_02.wav"]}},
      "2": {"replace": {"5": "/tmp/exp/s2p5_E.wav"}},
      "3": {"replace": {"5": "/tmp/exp/s3p5_A.wav"},
            "insert_after": {"7": ["/tmp/exp/s3p7seg/seg_01.wav",
                                   "/tmp/exp/s3p7seg/seg_02.wav"]}},
      "4": {"drop": [3]}
    }

用法：
  PY=/Users/mac/.workbuddy/binaries/python/envs/default/bin/python
  S=~/.workbuddy/skills/koubo-ad-video/scripts/seg_resplice.py

  $PY $S --work . --plan vo_fix_plan.json --dry-run          # 先看变更
  $PY $S --work . --plan vo_fix_plan.json --rebuild          # 落盘 + 重拼

落盘后**必跑全链**：whisper_small_align → align_vo → fix_vo_head → build_configs
→ shot_plan --config → 渲染 → 响度/真峰值复检（换段后电平会变，见 #72）。
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

VENV_PY = "/Users/mac/.workbuddy/models/cosyvoice/venv/bin/python"
SCRIPTS = os.path.dirname(os.path.abspath(__file__))


def list_segs(seg_dir):
    out = []
    for f in os.listdir(seg_dir):
        m = re.fullmatch(r"seg_(\d+)\.wav", f)
        if m:
            out.append((int(m.group(1)), os.path.join(seg_dir, f)))
    out.sort()
    return out


def build_new_order(base, plan_k, tag):
    """按 plan 生成最终段序 [(orig_idx or None, path)]，并校验 plan 的 key 合法。"""
    valid = {i for i, _ in base}
    rep = {int(k): v for k, v in (plan_k.get("replace") or {}).items()}
    ins = {int(k): v for k, v in (plan_k.get("insert_after") or {}).items()}
    drop = {int(x) for x in (plan_k.get("drop") or [])}
    for k in list(rep) + list(ins) + sorted(drop):
        if k not in valid:
            sys.exit(f"条{tag}: plan 里的段号 {k} 不在可用段号 {sorted(valid)} 中（段号按替换前计）")
    for k, v in ins.items():
        if not isinstance(v, list):
            sys.exit(f"条{tag}: insert_after 的 {k} 应为文件路径数组")

    order = []
    for i, p in base:
        if i in drop:
            continue
        order.append((i, rep.get(i, p), "替换" if i in rep else "保留"))
        for extra in ins.get(i, []):
            order.append((None, extra, "插入"))
    return order


def main():
    ap = argparse.ArgumentParser(description="配音段替换/插入/删除 + 重排 + 刷新 segments.json + 重拼")
    ap.add_argument("--work", default=".", help="工程目录（含 s1/s2/s3；默认当前目录）")
    ap.add_argument("--plan", required=True, help="plan.json（见文件头格式）")
    ap.add_argument("--seg-gap", type=float, default=0.22, help="段间静音秒（默认 0.22）")
    ap.add_argument("--speed", type=float, default=1.2, help="写入 segments.json 的 speed 字段（默认 1.2）")
    ap.add_argument("--backup-name", default="seg_bak_orig", help="段目录备份名（默认 seg_bak_orig）")
    ap.add_argument("--rebuild", action="store_true", help="落盘后调 cosyvoice_clone --rebuild-from 重拼 mp3")
    ap.add_argument("--dry-run", action="store_true", help="只打印变更计划，不改任何文件")
    args = ap.parse_args()

    work = os.path.abspath(args.work)
    plan = json.load(open(args.plan, encoding="utf-8"))

    print(f"工程 {work}")
    summary = []
    for tag, plan_k in plan.items():
        seg_dir = os.path.join(work, f"s{tag}", "seg")
        if not os.path.isdir(seg_dir):
            sys.exit(f"条{tag}: 找不到段目录 {seg_dir}")
        base = list_segs(seg_dir)
        if not base:
            sys.exit(f"条{tag}: {seg_dir} 里没有 seg_NN.wav")
        order = build_new_order(base, plan_k, tag)

        missing = [p for _, p, _ in order if not os.path.exists(p)]
        if missing:
            sys.exit(f"条{tag}: 以下源文件不存在：\n  " + "\n  ".join(missing))

        # 校验段数 == text_tts 切分段数
        tts_path = os.path.join(work, f"s{tag}", "text_tts.txt")
        text = open(tts_path, encoding="utf-8").read()
        joined = text.replace("\r", "").replace("\n", "")
        sents = [p.strip() for p in re.split(r"(?<=[。！？!?；;])", joined) if p.strip()]
        if len(sents) != len(order):
            print(f"⚠️ 条{tag}: text_tts 切出 {len(sents)} 段 ≠ 结果 {len(order)} 段 —— "
                  f"若采用「逗号升级句号」拆段法，请确认 text_tts.txt 已同步改为拆段写法")
            for i, s in enumerate(sents, 1):
                print(f"     text {i:2d}. {s}")
            sys.exit(1)

        changes = [(i, os.path.basename(src), kind) for i, src, kind in order if kind != "保留"]
        summary.append((tag, len(base), len(order), changes))

    print("\n===== 变更计划 =====")
    for tag, nb, na, changes in summary:
        print(f"条{tag}: {nb} 段 → {na} 段")
        for i, name, kind in changes:
            print(f"    [{kind}] 原段号 {i} ← {name}")

    if args.dry_run:
        print("\n（dry-run，未改动任何文件）")
        return

    for tag, plan_k in plan.items():
        seg_dir = os.path.join(work, f"s{tag}", "seg")
        base = list_segs(seg_dir)
        order = build_new_order(base, plan_k, tag)

        bak = os.path.join(work, f"s{tag}", args.backup_name)
        if not os.path.isdir(bak):
            shutil.copytree(seg_dir, bak)
            print(f"条{tag}: 原段已备份 → {bak}")

        tmp = os.path.join(work, f"s{tag}", "_seg_tmp")
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        for n, (_, src, _) in enumerate(order, 1):
            shutil.copy2(src, os.path.join(tmp, f"seg_{n:02d}.wav"))
        for f in list_segs(seg_dir):
            os.remove(f[1])                     # 只删 seg_NN.wav，保留 segments.json 等
        for n in range(1, len(order) + 1):
            shutil.move(os.path.join(tmp, f"seg_{n:02d}.wav"), os.path.join(seg_dir, f"seg_{n:02d}.wav"))
        os.rmdir(tmp)

        # 刷新 segments.json
        text = open(os.path.join(work, f"s{tag}", "text_tts.txt"), encoding="utf-8").read()
        joined = text.replace("\r", "").replace("\n", "")
        sents = [p.strip() for p in re.split(r"(?<=[。！？!?；;])", joined) if p.strip()]
        json.dump({"seg_gap": args.seg_gap, "speed": args.speed,
                   "segments": [{"i": i, "text": s} for i, s in enumerate(sents, 1)]},
                  open(os.path.join(seg_dir, "segments.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        got = len(list_segs(seg_dir))
        assert got == len(sents), f"条{tag}: 落盘后 wav {got} 段 ≠ 文本 {len(sents)} 段"
        print(f"条{tag}: {len(sents)} 段已落盘，segments.json 已刷新")

    if args.rebuild:
        print("\n===== 重拼 mp3 =====")
        env = dict(os.environ)
        env.pop("PYTHONPATH", None)
        for tag in plan.keys():
            out = os.path.join(work, "audio", f"s{tag}.mp3")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            r = subprocess.run([VENV_PY, os.path.join(SCRIPTS, "cosyvoice_clone.py"),
                                "--rebuild-from", os.path.join(work, f"s{tag}", "seg"),
                                "--seg-gap", str(args.seg_gap), "--out", out],
                               env=env, capture_output=True, text=True)
            if r.returncode != 0:
                print(f"条{tag}: ❌ 重拼失败\n{r.stdout[-500:]}\n{r.stderr[-500:]}")
                sys.exit(1)
            dur = subprocess.run(["/Users/mac/.local/bin/ffprobe", "-v", "error",
                                  "-show_entries", "format=duration", "-of", "csv=p=0", out],
                                 capture_output=True, text=True).stdout.strip()
            print(f"条{tag}: {out}  {dur}s")
        print("\n★ 接下来必跑：whisper_small_align → align_vo → fix_vo_head → build_configs "
              "→ shot_plan --config → 渲染 → 响度/真峰值复检")


if __name__ == "__main__":
    main()
