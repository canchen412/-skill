#!/bin/bash
# ============================================================
#  pull.sh — 小号同步：拉取 → 判脏 → 备份 → 覆盖 → 提示重启
# ------------------------------------------------------------
#  用法：
#    bash pull.sh                 # 增量同步（推荐日常用）
#    bash pull.sh --dry-run       # 只看会改哪些技能，不动任何文件
#    bash pull.sh --prune         # 同步并删除仓库里已移除的文件（先备份）
#    bash pull.sh --rollback v1.2 # 回退到某个标签
#    bash pull.sh --status        # 本地与仓库的差异一览
#
#  设计原则：小号是「只读消费者」。
#    · 仓库 clone 到独立目录（默认 ~/wb-skills），不改 ~/.workbuddy/skills 的 git 状态
#    · 同步前先把本地改动备份，覆盖永不丢东西
#    · 默认不删除本地多出的文件（node_modules / 自装技能 / 素材都不受影响）
# ============================================================
set -uo pipefail

SKILLS="${WB_SKILLS:-$HOME/.workbuddy/skills}"
REPO="${WB_REPO:-$HOME/wb-skills}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="$(command -v python3 || echo /usr/bin/python3)"
PRECHECK="$HERE/precheck.py"
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP_ROOT="$HOME/.workbuddy/skills-backup"

MODE="sync"; DRY=0; PRUNE=0; ROLLBACK=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)  DRY=1; shift ;;
    --prune)    PRUNE=1; shift ;;
    --status)   MODE="status"; shift ;;
    --rollback) MODE="rollback"; ROLLBACK="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

# 同步时一律不要碰这些：本地原生依赖 / 大素材 / 本机产物
RSYNC_EXCLUDES=(
  --exclude='.git' --exclude='.gitignore'
  --exclude='node_modules' --exclude='.runtime' --exclude='.cache'
  --exclude='__pycache__' --exclude='*.pyc'
  --exclude='*.wav' --exclude='*.mp4' --exclude='*.mov'
  --exclude='*.ttc'
  # 注意：不要排除 *.ttf —— 仓库里保留了「新青年体.ttf」等字幕渲染必需字体
  --exclude='.DS_Store' --exclude='._*'
)

# ---------------- 0. 准备仓库 ----------------
say "▸ 仓库: $REPO"
say "▸ 技能: $SKILLS"
say ""

if [ ! -d "$REPO/.git" ]; then
  die "本地还没有仓库副本。首次请先 clone：
    git clone <你的私有仓库URL> \"$REPO\"
  然后重跑本脚本。"
fi

cd "$REPO" || die "进不去 $REPO"

# ---------------- 回滚模式 ----------------
if [ "$MODE" = "rollback" ]; then
  [ -n "$ROLLBACK" ] || die "用法: bash pull.sh --rollback <标签名>"
  git rev-parse "$ROLLBACK" >/dev/null 2>&1 || die "标签不存在: ${ROLLBACK}（git tag 可查看全部）"
  say "▸ 回退到 $ROLLBACK"
  git checkout -q "$ROLLBACK" -- . 2>/dev/null || git checkout -q "$ROLLBACK"
  ok "工作区已切到 $ROLLBACK"
  MODE="sync"
  say ""
fi

# ---------------- 拉取 ----------------
if [ "$MODE" = "sync" ] && [ "$DRY" = "0" ]; then
  say "▸ 第 1 步：拉取远端"
  source "$HERE/net.sh"
  wb_probe_route
  say "  GitHub 出口: $WB_ROUTE"
  BEFORE=$(git rev-parse --short HEAD)
  if wb_git pull --ff-only -q 2>/tmp/wb_pull_err; then
    AFTER=$(git rev-parse --short HEAD)
    if [ "$BEFORE" = "$AFTER" ]; then
      ok "已是最新（${AFTER}）"
    else
      ok "更新 $BEFORE → $AFTER"
      git log --oneline "$BEFORE..$AFTER" | sed 's/^/     /' | head -10
    fi
  else
    warn "拉取失败："
    sed 's/^/     /' /tmp/wb_pull_err
    say ""
    say "若提示本地有改动 → 说明你在小号上直接改过仓库副本。"
    say "  处理: git -C \"$REPO\" stash  # 暂存本地改动后重跑"
    exit 1
  fi
  say ""
fi

# ---------------- 判脏 ----------------
say "▸ 第 2 步：比对本地技能与仓库指纹"
[ -f "$REPO/MANIFEST.json" ] || die "仓库缺 MANIFEST.json — 主号需要先跑 bash push.sh"

VERIFY_JSON=$("$PY" "$PRECHECK" --root "$SKILLS" --verify-against "$REPO/MANIFEST.json" 2>/dev/null)
DIRTY_LIST=$(printf '%s' "$VERIFY_JSON" | "$PY" -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: print(""); raise SystemExit
print(" ".join(x["skill"] for x in d.get("dirty",[])))
' 2>/dev/null)

if [ -n "$DIRTY_LIST" ]; then
  warn "本地被改过的技能: $DIRTY_LIST"
else
  ok "本地技能与仓库一致或缺失（无冲突）"
fi

if [ "$MODE" = "status" ]; then
  say ""
  say "▸ 完整差异："
  ENV_WB_SKILLS="$SKILLS" "$PY" "$PRECHECK" --root "$SKILLS" --verify-against "$REPO/MANIFEST.json" 2>&1 | sed 's/^/  /'
  exit 0
fi

# ---------------- 备份 ----------------
BACKUP_DIR=""
if [ -n "$DIRTY_LIST" ]; then
  BACKUP_DIR="$BACKUP_ROOT/$STAMP"
  say ""
  say "▸ 第 3 步：备份本地改动 → $BACKUP_DIR"
  if [ "$DRY" = "1" ]; then
    say "  （dry-run，跳过实际备份）"
  else
    mkdir -p "$BACKUP_DIR"
    for s in $DIRTY_LIST; do
      [ -d "$SKILLS/$s" ] && cp -R "$SKILLS/$s" "$BACKUP_DIR/$s" 2>/dev/null && say "     ✓ $s"
    done
    ok "备份完成，随时可从这里取回本地改动"
  fi
elif [ "$PRUNE" = "1" ]; then
  BACKUP_DIR="$BACKUP_ROOT/$STAMP"
  [ "$DRY" = "1" ] || mkdir -p "$BACKUP_DIR"
fi

# ---------------- 同步 ----------------
say ""
say "▸ 第 4 步：同步技能文件"
[ -d "$SKILLS" ] || { [ "$DRY" = "1" ] || mkdir -p "$SKILLS"; }

SYNCED=0
for d in "$REPO"/*/; do
  name=$(basename "$d")
  [ "$name" = ".git" ] && continue
  [ -f "$d/SKILL.md" ] || continue        # 只同步真正的技能（跳过 _tooling 等）
  if [ "$DRY" = "1" ]; then
    DIFF=$(rsync -ain --out-format='%n' "${RSYNC_EXCLUDES[@]}" "$d" "$SKILLS/$name/" 2>/dev/null | grep -v '/$' | wc -l | tr -d ' ')
    [ "$DIFF" != "0" ] && say "     会更新 ${name}（$DIFF 个文件）"
    SYNCED=$((SYNCED+1))
    continue
  fi
  DEL_FLAG=""
  [ "$PRUNE" = "1" ] && DEL_FLAG="--delete"
  if rsync -a $DEL_FLAG "${RSYNC_EXCLUDES[@]}" "$d" "$SKILLS/$name/" 2>/dev/null; then
    SYNCED=$((SYNCED+1))
  else
    warn "同步失败: $name"
  fi
done

if [ "$DRY" = "1" ]; then
  say ""
  ok "dry-run 结束，共检查 $SYNCED 个技能，未改动任何文件"
  exit 0
fi
ok "已同步 $SYNCED 个技能"

# ---------------- 收尾 ----------------
say ""
say "▸ 第 5 步：收尾"
# 把工具脚本也放到技能目录旁，方便下次直接用
mkdir -p "$SKILLS/_wb-sync" 2>/dev/null
cp "$HERE"/*.sh "$HERE"/*.py "$SKILLS/_wb-sync/" 2>/dev/null
[ -f "$REPO/MANIFEST.json" ] && cp "$REPO/MANIFEST.json" "$SKILLS/.sync-manifest.json" 2>/dev/null

say ""
ok "同步完成"
say ""
say "  ⚠️  必须重启 WorkBuddy，技能列表才会刷新。"
say "      技能缓存 .skill-list-cache.json 靠目录监听更新，"
say "      有时不重启会看到旧版本或干脆看不到新技能。"
say ""
say "  版本：$(git -C "$REPO" describe --tags --always 2>/dev/null || git -C "$REPO" rev-parse --short HEAD)"
say "  回退：bash pull.sh --rollback <标签>"
[ -n "$BACKUP_DIR" ] && say "  备份：$BACKUP_DIR"
