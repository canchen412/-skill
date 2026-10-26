#!/bin/bash
# ============================================================
#  sync-up.sh — 主号发布：~/.workbuddy/skills → ~/wb-skills → GitHub
# ------------------------------------------------------------
#  用法：
#    bash _tooling/sync-up.sh "这次改了什么"
#    bash _tooling/sync-up.sh "改了什么" --dry-run     # 只看会改哪些文件
#    bash _tooling/sync-up.sh "加了个技能" --add foo   # 先把 foo 加进白名单
#
#  白名单：仓库根的 skills.list，一行一个技能名（# 开头为注释）
#  只同步白名单里的技能，其余技能留在本机不上传
# ============================================================
set -uo pipefail

SRC="${WB_SKILLS:-$HOME/.workbuddy/skills}"
REPO="${WB_REPO:-$HOME/wb-skills}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
LIST="$REPO/skills.list"

MSG="${1:-}"; shift $(( $# > 0 ? 1 : 0 )) 2>/dev/null || true
DRY=0; ADD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1; shift ;;
    --add) ADD="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

[ -d "$REPO/.git" ] || die "仓库不存在: $REPO"
[ -d "$SRC" ] || die "技能源不存在: $SRC"

# ---------- 加白名单 ----------
if [ -n "$ADD" ]; then
  [ -d "$SRC/$ADD" ] || die "技能不存在: $SRC/$ADD"
  [ -f "$SRC/$ADD/SKILL.md" ] || die "$ADD 缺 SKILL.md，不是有效技能"
  grep -qxF "$ADD" "$LIST" 2>/dev/null || echo "$ADD" >> "$LIST"
  ok "$ADD 已加入白名单（${LIST}）"
  [ -z "$MSG" ] && { say "→ 再跑一次带说明的同步"; exit 0; }
fi

[ -f "$LIST" ] || die "缺少白名单 ${LIST}（首行写技能名，一行一个）"

# ---------- 复制 ----------
say "▸ 从 $SRC 同步白名单技能 → $REPO"
[ "$DRY" = "1" ] && say "  （dry-run，只统计不改文件）"
say ""

"$PY" - "$SRC" "$REPO" "$LIST" "$DRY" <<'PYEOF'
import os, shutil, fnmatch, sys
from pathlib import Path

src_root, repo_root, list_file, dry = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4] == "1"
src_root, repo_root = Path(src_root), Path(repo_root)

names = [l.strip() for l in Path(list_file).read_text("utf-8").splitlines()
         if l.strip() and not l.strip().startswith("#")]

EXCL_DIRS = {"__pycache__", "node_modules", ".runtime", ".cache", ".git", "venv", ".venv",
             "work", "tmp", "output", "_旧版备份"}
EXCL_PAT  = ["*.pyc","*.pyo","*.ttc","*.wav","*.mp4","*.mov","*.mkv",
             "*.bak*","*~","*.orig",".DS_Store","._*",".?*.??????","*.log","*.zip"]

def skip(name):
    if name in EXCL_DIRS: return True
    return any(fnmatch.fnmatch(name, p) for p in EXCL_PAT)

total_new = total_skip = 0
for name in names:
    s_dir = src_root / name
    if not s_dir.is_dir():
        print(f"  ⚠️  跳过（不存在）: {name}")
        continue
    if not (s_dir / "SKILL.md").exists():
        print(f"  ⚠️  跳过（缺 SKILL.md）: {name}")
        continue
    d_dir = repo_root / name
    n_new = n_skip = 0
    for dirpath, dirnames, filenames in os.walk(s_dir):
        dirnames[:] = [d for d in dirnames if not skip(d)]
        rel = Path(dirpath).relative_to(s_dir)
        if not dry:
            (d_dir / rel).mkdir(parents=True, exist_ok=True)
        for f in filenames:
            if skip(f):
                n_skip += 1
                continue
            s = Path(dirpath) / f
            d = d_dir / rel / f
            # 内容相同则跳过
            if d.exists() and d.stat().st_size == s.stat().st_size:
                try:
                    if d.read_bytes() == s.read_bytes():
                        continue
                except Exception:
                    pass
            if not dry:
                shutil.copy2(s, d)
            n_new += 1
    total_new += n_new; total_skip += n_skip
    print(f"  {'[dry] ' if dry else ''}{name}: {'将更新' if dry else '更新'} {n_new} 个文件，排除 {n_skip} 个")

print(f"\n合计: {'将更新' if dry else '已更新'} {total_new} 个文件，排除 {total_skip} 个（.wav/.ttc/.bak/缓存）")
PYEOF

[ "$DRY" = "1" ] && { say ""; ok "dry-run 结束"; exit 0; }
say ""

# ---------- 体检 ----------
say "▸ 体检"
"$PY" "$REPO/_tooling/precheck.py" --root "$REPO" --manifest --strict
[ $? -ne 0 ] && die "体检有阻断项，已停止"

# ---------- 提交 ----------
say ""
cd "$REPO" || die "进不去 $REPO"
if [ -z "$(git status --porcelain)" ]; then
  ok "没有变化，无需发布"
  exit 0
fi

[ -n "$MSG" ] || die "请给一句变更说明：bash _tooling/sync-up.sh \"改了什么\""

say "▸ 变更预览"
git add -A
git diff --cached --stat | tail -3
say ""

git commit -q -m "$MSG" || die "提交失败"
ok "已提交 $(git rev-parse --short HEAD): $MSG"

# ---------- 推送 ----------
say ""
say "▸ 推送"
# 本机沙箱代理会拦 github（CONNECT 502），必须清代理直连
source "$HERE/net.sh"
wb_probe_route
say "  GitHub 出口: $WB_ROUTE"
if wb_git push -q origin main 2>/tmp/wb_push_err; then
  ok "已推送到 GitHub"
  AUTO="v$(date +%Y.%m.%d)-$(git rev-list --count HEAD)"
  git tag -f "$AUTO" >/dev/null 2>&1 && \
    wb_git push -f -q origin "$AUTO" 2>/dev/null && \
    ok "已打标签 $AUTO"
else
  warn "推送失败（本地提交已完成，不影响）"
  sed 's/^/     /' /tmp/wb_push_err 2>/dev/null | head -6
  say ""
  say "  最常见原因："
  say "    · 网络连不上 GitHub → 在你自己的终端里跑，别在沙箱里跑"
  say "    · 没认证 → 用 PAT: https://github.com/settings/tokens (勾 repo)"
  say "  手工推送："
  say "    cd $REPO && git push origin main"
  exit 1
fi
