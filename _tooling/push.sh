#!/bin/bash
# ============================================================
#  push.sh — 主号发布：体检 → 装进 git → 提交 → 推送
# ------------------------------------------------------------
#  用法：
#    bash push.sh "这次改了什么"          # 常规发布
#    bash push.sh "改了什么" --tag v1.2   # 同时打版本标签
#    bash push.sh --init                  # 首次：把 skills 目录变成 git 仓库
#    bash push.sh --check                 # 只体检，不提交
#
#  前提：~/.workbuddy/skills 已 git init 且配好 remote（--init 可代劳）
# ============================================================
set -uo pipefail

SKILLS="${WB_SKILLS:-$HOME/.workbuddy/skills}"
HERE="$(cd "$(dirname "$0")" && pwd)"
PRECHECK="$HERE/precheck.py"
MSG="${1:-}"
TAG=""

shift $(( $# > 0 ? 1 : 0 )) 2>/dev/null || true
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:-}"; shift 2 ;;
    --init) DO_INIT=1; shift ;;
    --check) ONLY_CHECK=1; shift ;;
    *) shift ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

PY="$(command -v python3 || echo /usr/bin/python3)"
[ -d "$SKILLS" ] || die "技能目录不存在: $SKILLS"

# ---------------- 0. 首次初始化 ----------------
if [ "${DO_INIT:-0}" = "1" ]; then
  say "▸ 初始化 git 仓库"
  cd "$SKILLS" || die "进不去 $SKILLS"
  if [ -d .git ]; then
    warn "已经是 git 仓库，跳过 init"
  else
    git init -q || die "git init 失败"
    cp "$HERE/templates/gitignore" .gitignore || die "缺少 templates/gitignore"
    ok "已创建 .gitignore"
    say ""
    say "现在需要你提供 GitHub 私有仓库地址（只读一次，之后免配）："
    printf '  remote url > '
    read -r REMOTE
    [ -n "$REMOTE" ] && git remote add origin "$REMOTE" && ok "remote 已设为 $REMOTE"
  fi
  say ""
  say "→ 下一步：bash push.sh \"首次导入\""
  exit 0
fi

# ---------------- 1. 体检（阻断项直接停） ----------------
say "▸ 第 1 步：体检"
"$PY" "$PRECHECK" --root "$SKILLS" --manifest --strict
RC=$?
if [ $RC -ne 0 ]; then
  say ""
  die "体检有阻断项，已停止。修完再跑。"
fi
[ "${ONLY_CHECK:-0}" = "1" ] && { ok "仅体检模式，结束"; exit 0; }

# ---------------- 2. 仓库状态 ----------------
cd "$SKILLS" || die "进不去 $SKILLS"
[ -d .git ] || die "还不是 git 仓库。先跑: bash push.sh --init"

if ! git remote get-url origin >/dev/null 2>&1; then
  die "未配置 remote origin。执行: git -C \"$SKILLS\" remote add origin <私有仓库URL>"
fi

[ -n "$MSG" ] || die "请给一句变更说明，例: bash push.sh \"修了配音停顿\""

# ---------------- 3. 暂存与复核 ----------------
say ""
say "▸ 第 2 步：暂存变更"
git add -A -- . ':!.DS_Store' 2>/dev/null || git add -A

STAGED=$(git diff --cached --name-only | wc -l | tr -d ' ')
if [ "$STAGED" = "0" ]; then
  ok "没有变更，无需发布"
  exit 0
fi

# 保险：git 里绝不该出现的路径
BAD=$(git diff --cached --name-only | grep -E 'node_modules/|\.runtime/|\.cache/|\.pyc$|\.ttc$|\.wav$|\.mp4$|\.zip$|bailian\.json$|\.env$' | head -20)
if [ -n "$BAD" ]; then
  warn "以下文件本不该入库，将自动撤出暂存区："
  echo "$BAD" | sed 's/^/     - /'
  echo "$BAD" | while IFS= read -r f; do git reset -q -- "$f" 2>/dev/null; done
  STAGED=$(git diff --cached --name-only | wc -l | tr -d ' ')
  [ "$STAGED" = "0" ] && { ok "撤出后无内容可提交"; exit 0; }
fi

say ""
say "▸ 第 3 步：本次变更预览"
git diff --cached --stat | tail -5
say ""
say "  涉及技能："
git diff --cached --name-only | cut -d/ -f1 | sort | uniq -c | sort -rn | sed 's/^/     /' | head -15

# ---------------- 4. 提交与推送 ----------------
say ""
say "▸ 第 4 步：提交并推送"
git commit -q -m "$MSG" || die "提交失败"
SHORT=$(git rev-parse --short HEAD)
ok "已提交 $SHORT: $MSG"

BRANCH=$(git rev-parse --abbrev-ref HEAD)
# ★ 实测坑（2026-10-26）：本机有 http_proxy=127.0.0.1:60401（WorkBuddy 沙箱代理），
#   它拒绝 CONNECT 到 github.com，报 "HTTP2 framing layer" / "CONNECT tunnel failed 502"。
#   必须清掉代理变量直连，否则永远推不上去。
PUSH_ENV=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy)
if "${PUSH_ENV[@]}" git -c http.version=HTTP/1.1 push -q origin "$BRANCH" 2>/tmp/wb_push_err; then
  ok "已推送到 origin/$BRANCH"
else
  warn "推送失败，错误如下："
  sed 's/^/     /' /tmp/wb_push_err
  say ""
  say "常见原因："
  say "  · 凭据过期 → gh auth login 或重新配置 PAT"
  say "  · 远端有新提交 → git pull --rebase origin $BRANCH 后重试"
  say "  · 单文件超限 → 跑 precheck.py 看 [2] 段"
  exit 1
fi

# ---------------- 5. 版本标签 ----------------
say ""
say "▸ 第 5 步：版本标签"
if [ -n "$TAG" ]; then
  git tag -f "$TAG" && "${PUSH_ENV[@]}" git -c http.version=HTTP/1.1 push -f -q origin "$TAG" && ok "已打标签 $TAG"
else
  AUTO="v$(date +%Y.%m.%d)-$(git rev-list --count HEAD)"
  if git tag -f "$AUTO" >/dev/null 2>&1 && "${PUSH_ENV[@]}" git -c http.version=HTTP/1.1 push -f -q origin "$AUTO" 2>/dev/null; then
    ok "已自动打标签 ${AUTO}（小号可用 --rollback $AUTO 回退）"
  else
    warn "打标签失败（不影响同步）"
  fi
fi

say ""
ok "发布完成。小号执行：bash pull.sh"
