#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
precheck.py — WorkBuddy 技能仓库上线前体检 / MANIFEST 生成

用法:
    python3 precheck.py                       # 体检当前技能目录
    python3 precheck.py --root ~/.workbuddy/skills
    python3 precheck.py --manifest            # 同时写出 MANIFEST.json
    python3 precheck.py --strict              # 有 ERROR 则退出码 1（给 push.sh 用）

检查项:
    1. 体积与文件数（GitHub 单文件 100MB 硬限 / 50MB 警告 / 仓库建议 <1GB）
    2. 不该入库的大目录（node_modules / .runtime / .cache）
    3. 嵌套 git 仓库（会变成空 gitlink）
    4. 凭据泄露（API Key / token / 私钥）
    5. 跨机不兼容的绝对路径（/Volumes/ 素材盘、别的用户名 home）
    6. 技能完整性（缺 SKILL.md / name 与目录名不一致 / frontmatter 缺失）
    7. 生成 MANIFEST.json（每技能内容指纹，供 pull 判脏 + 回滚校验）
"""

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------- 排除规则（必须与 templates/gitignore 保持一致）----------
SKIP_DIRS = {
    "node_modules", ".runtime", ".cache", "dist", "build", "out", ".next",
    ".parcel-cache", "__pycache__", ".venv", "venv", ".git",
    "work", "tmp", "output", "_旧版备份",
}
SKIP_SUFFIX = {
    ".pyc", ".pyo", ".node", ".wasm", ".ttc", ".wav", ".mp4", ".mov",
    ".mkv", ".zip", ".tar.gz", ".7z", ".log",
}
SKIP_NAMES = {
    ".DS_Store", ".skill-list-cache.json", "bailian.json",
    ".model_invocation_to_override_migration.json",
    ".disable_to_model_invocation_migration.json",
    "_bm_skillid_migration.json",
    "skills-full-latest.zip", "team-toolkit-latest.zip",
}
# 允许的例外：压缩后的 BGM
ALLOW_SUFFIX = {".m4a", ".mp3"}

MAX_FILE_HARD = 100 * 1024 * 1024   # GitHub 硬限制
MAX_FILE_WARN = 50 * 1024 * 1024    # GitHub 警告
REPO_WARN = 900 * 1024 * 1024       # 仓库建议上限

SECRET_PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{20,}"), "OpenAI/通用 sk- 密钥"),
    (re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}['\"]"), "硬编码 api_key"),
    (re.compile(r"(?i)(access[_-]?key[_-]?secret|secret[_-]?key)\s*[=:]\s*['\"][^'\"]{12,}['\"]"), "硬编码 secret"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "GitHub PAT"),
    (re.compile(r"ghp_[A-Za-z0-9]{30,}"), "GitHub token"),
    (re.compile(r"-----BEGIN (RSA |OPENSSH |EC )?PRIVATE KEY-----"), "私钥"),
    (re.compile(r"(?i)bearer\s+[A-Za-z0-9\-_\.]{30,}"), "Bearer token"),
]

ERRORS, WARNS, INFOS = [], [], []


def err(m):  ERRORS.append(m)
def warn(m): WARNS.append(m)
def info(m): INFOS.append(m)


def section(title: str):
    print(f"\n{title}")


def ok(msg: str):
    print(f"    {msg} ✓")


def should_skip(p: Path, rel: Path) -> bool:
    parts = set(rel.parts[:-1])
    if parts & SKIP_DIRS:
        return True
    name = p.name
    if name in SKIP_NAMES or name.startswith("._"):
        return True
    low = name.lower()
    if low.endswith(".tar.gz"):
        return True
    suf = p.suffix.lower()
    if suf in ALLOW_SUFFIX:
        return False
    if suf in SKIP_SUFFIX:
        return True
    return False


def walk(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            rel = p.relative_to(root)
            if should_skip(p, rel):
                continue
            yield p, rel


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def skill_dir_of(rel: Path):
    return rel.parts[0] if len(rel.parts) > 1 else None


def compute_fingerprints(root: Path):
    """返回 {skill: {files, bytes, sha}} —— 与 MANIFEST 同一算法"""
    files = list(walk(root))
    per = {}
    for p, rel in files:
        sd = skill_dir_of(rel)
        if not sd or sd.startswith("_") or sd.startswith("."):
            continue
        d = per.setdefault(sd, {"files": 0, "bytes": 0, "_h": hashlib.sha256()})
        d["files"] += 1
        d["bytes"] += p.stat().st_size
        d["_h"].update(str(rel).encode())
        d["_h"].update(sha256_file(p).encode())
    return {k: {"files": v["files"], "bytes": v["bytes"], "sha": v["_h"].hexdigest()[:16]}
            for k, v in per.items()}


def verify(root: Path, manifest_path: Path) -> int:
    """比对本地目录与仓库 MANIFEST，报告 脏/缺/新 的技能"""
    try:
        mf = json.loads(manifest_path.read_text("utf-8"))
    except Exception as e:
        print(f"❌ 读不了 MANIFEST: {e}")
        return 2
    repo = mf.get("skills", {})
    local = compute_fingerprints(root)

    dirty, missing, extra, same = [], [], [], []
    for s, meta in repo.items():
        if s not in local:
            missing.append(s)
        elif local[s]["sha"] != meta["sha"]:
            dirty.append((s, meta["sha"], local[s]["sha"]))
        else:
            same.append(s)
    for s in local:
        if s not in repo:
            extra.append(s)

    print(json.dumps({
        "dirty": [{"skill": s, "repo": r, "local": l} for s, r, l in dirty],
        "missing": missing,
        "extra_local": extra,
        "identical": same,
    }, ensure_ascii=False, indent=2))

    if dirty:
        print(f"\n⚠️  {len(dirty)} 个技能在本地被改过（同步前会先备份）:", file=sys.stderr)
        for s, r, l in dirty:
            print(f"    · {s}  本地={l}  仓库={r}", file=sys.stderr)
    if missing:
        print(f"\n▸ 本地还没有这 {len(missing)} 个技能（首次同步正常）", file=sys.stderr)
    if extra:
        print(f"\n▸ 本地独有（仓库里没有，同步不会动它们）: {', '.join(extra)}", file=sys.stderr)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/.workbuddy/skills"))
    ap.add_argument("--manifest", action="store_true", help="写出 MANIFEST.json")
    ap.add_argument("--manifest-out", default=None, help="MANIFEST 输出路径")
    ap.add_argument("--verify-against", default=None, help="与仓库 MANIFEST 比对，输出脏技能 JSON")
    ap.add_argument("--strict", action="store_true", help="有 ERROR 时退出码 1")
    args = ap.parse_args()

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"❌ 目录不存在: {root}")
        return 2

    if args.verify_against:
        return verify(root, Path(args.verify_against).expanduser().resolve())

    print(f"▸ 体检目标: {root}")
    print("=" * 68)

    files = list(walk(root))
    total = sum(p.stat().st_size for p, _ in files)
    print(f"\n[1] 体积与文件数")
    print(f"    将入库: {len(files)} 个文件 / {total / 1024 / 1024:.1f} MB")
    if total > REPO_WARN:
        err(f"总体积 {total/1024/1024:.0f}MB 超过 GitHub 建议上限(1GB)，请继续精简")
    else:
        info(f"体积安全（GitHub 建议 <1GB，当前 {total/1024/1024:.1f}MB）")

    # 大文件
    big = [(p, rel, p.stat().st_size) for p, rel in files if p.stat().st_size > MAX_FILE_WARN]
    if big:
        print(f"\n[2] ⚠️ 超大单文件（>50MB）")
        for p, rel, sz in sorted(big, key=lambda x: -x[2]):
            tag = "ERROR" if sz > MAX_FILE_HARD else "WARN"
            print(f"    [{tag}] {sz/1024/1024:.1f}MB  {rel}")
            (err if sz > MAX_FILE_HARD else warn)(f"超大文件 {rel} ({sz/1024/1024:.1f}MB)")
    else:
        print(f"\n[2] 无 >50MB 单文件 ✓")

    # 未清理的重目录
    section("[3] 重量级依赖/缓存目录（应被 .gitignore 排除）")
    import subprocess
    heavy_found = False
    for d in ("node_modules", ".runtime", ".cache", "dist", "build"):
        for h in root.glob(f"**/{d}"):
            if not h.is_dir():
                continue
            try:
                kb = int(subprocess.run(["du", "-sk", str(h)], capture_output=True,
                                        text=True, timeout=60).stdout.split()[0])
            except Exception:
                continue
            if kb < 10 * 1024:
                continue
            heavy_found = True
            rel = h.relative_to(root)
            print(f"    · {rel}  {kb/1024:.0f}MB")
            warn(f"大目录 {rel} ({kb/1024:.0f}MB) 已被 .gitignore 排除 — 不要用 git add -f 强加")
    if not heavy_found:
        ok("无残留的依赖目录")

    # 嵌套 git
    section("[4] 嵌套 git 仓库")
    nested = [g for g in root.glob("**/.git") if g.is_dir() and g.parent != root]
    if nested:
        for g in nested:
            rel = g.parent.relative_to(root)
            print(f"    [ERROR] {rel} 内含 .git（会变成空 gitlink，小号 clone 后是空目录）")
            err(f"嵌套仓库 {rel} — 处理: 删其 .git 后作为普通目录提交（rm -rf '{g}'），或改用 submodule")
    else:
        ok("无嵌套仓库")

    def _skip_docfile(rel):
        r = str(rel)
        return r.startswith("_") or r.startswith(".") or ("/" not in r and r.lower().endswith((".md", ".json", ".txt")))

    # 凭据
    section("[5] 凭据扫描")
    textsuf = {".md", ".py", ".sh", ".json", ".yaml", ".yml", ".js", ".ts", ".txt", ".cfg", ".ini", ".env"}
    leaked = 0
    for p, rel in files:
        if p.suffix.lower() not in textsuf or _skip_docfile(rel):
            continue
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            txt = p.read_text("utf-8", errors="ignore")
        except Exception:
            continue
        for pat, label in SECRET_PATTERNS:
            if pat.search(txt):
                print(f"    [ERROR] {label} → {rel}")
                err(f"疑似凭据泄露 [{label}] → {rel}")
                leaked += 1
    if not leaked:
        ok("无明文凭据")

    # 绝对路径
    section("[6] 跨机不兼容的绝对路径")
    textsuf2 = textsuf
    vol_hits, home_hits = {}, {}
    me = Path.home().name
    placeholder = re.compile(r"^(\.\.\.|<.*>|\$\{?.*\}?|xxx|XXX|你的.*|你|用户名|user|username|用户名|示例.*)$")

    def clean(v: str):
        v = v.strip().strip("`'\"")
        if not v or placeholder.match(v) or v.startswith("$") or "/" in v:
            return None
        # 纯中文占位（如「你」「卷名」）+ 含中文但非真实卷名的，只保留已知真实卷
        if re.search(r"[你卷名示例]", v) and len(v) <= 4:
            return None
        # 过滤正则元字符：脚本里的正则源码（如 /Volumes/([^/\s]）会被误当路径
        if re.search(r"[()\[\]{}\\$|*+?^]", v):
            return None
        return v

    for p, rel in files:
        if p.suffix.lower() not in textsuf2 or _skip_docfile(rel):
            continue
        try:
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            txt = p.read_text("utf-8", errors="ignore")
        except Exception:
            continue
        for v in set(re.findall(r"/Volumes/([^/\s`'\"）)，,、]+)", txt)):
            vv = clean(v)
            if vv:
                vol_hits.setdefault(vv, set()).add(str(rel))
        for u in set(re.findall(r"/Users/([^/\s`'\"）)，,、]+)", txt)):
            uu = clean(u)
            if uu and uu != me:
                home_hits.setdefault(uu, set()).add(str(rel))
    if vol_hits:
        for v, where in sorted(vol_hits.items(), key=lambda x: -len(x[1])):
            print(f"    [WARN] /Volumes/{v} → {len(where)} 个文件"
                  f"（小号无此盘则技能可装不可跑）")
            warn(f"依赖外接卷 /Volumes/{v} — {len(where)} 个文件引用")
    if home_hits:
        for u, where in sorted(home_hits.items(), key=lambda x: -len(x[1])):
            print(f"    [WARN] /Users/{u} → {len(where)} 个文件（本机是 /Users/{me}）")
            warn(f"写死 /Users/{u} — {len(where)} 个文件引用，跨机需改写")
    if not vol_hits and not home_hits:
        ok("无跨机路径依赖")

    # 技能完整性
    section("[7] 技能完整性")
    skills = sorted({d.name for d in root.iterdir() if d.is_dir() and d.name not in SKIP_DIRS})
    skills = [s for s in skills if not s.startswith(".") and not s.startswith("_")]
    bad = []
    for s in skills:
        md = root / s / "SKILL.md"
        if not md.exists():
            print(f"    [ERROR] {s}/ 缺 SKILL.md（WorkBuddy 不会识别为技能）")
            err(f"技能 {s} 缺 SKILL.md，不会被识别")
            bad.append(s)
            continue
        head = md.read_text("utf-8", errors="ignore")[:800].replace("\\", "")
        m = re.search(r"^name:\s*(.+)$", head, re.M)
        if not m:
            print(f"    [ERROR] {s}/SKILL.md frontmatter 缺 name 字段")
            err(f"技能 {s} frontmatter 缺 name")
            bad.append(s)
        else:
            nm = m.group(1).strip().strip('"').strip("'")
            if nm != s:
                print(f"    [WARN] {s}: name='{nm}' ≠ 目录名（以目录名为准，建议统一）")
                warn(f"{s}/SKILL.md name 与目录名不一致")
    if not bad:
        ok(f"{len(skills)} 个技能结构完整")


    # MANIFEST
    if args.manifest:
        print(f"\n[8] MANIFEST")
        per = {}
        for p, rel in files:
            sd = skill_dir_of(rel)
            if not sd or sd.startswith("_") or sd.startswith("."):
                continue
            d = per.setdefault(sd, {"files": 0, "bytes": 0, "_h": hashlib.sha256()})
            d["files"] += 1
            d["bytes"] += p.stat().st_size
            d["_h"].update(str(rel).encode())
            d["_h"].update(sha256_file(p).encode())
        out = {
            "schema": 1,
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source_root": str(root),
            "skill_count": len(per),
            "total_files": len(files),
            "total_bytes": total,
            "skills": {k: {"files": v["files"], "bytes": v["bytes"], "sha": v["_h"].hexdigest()[:16]}
                       for k, v in sorted(per.items())},
        }
        mf = (Path(args.manifest_out).expanduser().resolve()
              if args.manifest_out else (root / "MANIFEST.json"))
        mf.parent.mkdir(parents=True, exist_ok=True)
        mf.write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n", "utf-8")
        print(f"    已写出 {mf}（{len(per)} 个技能指纹）")
        sample = ", ".join(f"{k}@{v['sha'][:8]}"
                           for k, v in list(out["skills"].items())[:4])
        print(f"    条目: {sample} …")

    # 汇总
    print("\n" + "=" * 68)
    for m in INFOS:
        print(f"  ✓ {m}")
    for m in WARNS:
        print(f"  ⚠️  {m}")
    for m in ERRORS:
        print(f"  ❌ {m}")
    print(f"\n结论: {len(ERRORS)} 个阻断项 / {len(WARNS)} 个警告")

    if ERRORS:
        print("→ 阻断项必须先处理，否则不要 push。")
        return 1 if args.strict else 0
    print("→ 可以上线。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
