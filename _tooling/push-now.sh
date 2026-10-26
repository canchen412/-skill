#!/bin/bash
# ============================================================
#  push-now.sh — 用本机 token 文件完成首次推送（token 不进命令行/不进 history）
# ------------------------------------------------------------
#  凭据来源（按优先级）：
#    1. $WB_TOKEN_FILE            指定路径
#    2. ~/.wb-git-token           默认（chmod 600）
#    3. macOS 钥匙串              git credential-osxkeychain
#
#  token 通过 GIT_ASKPASS 传给 git，不出现在 ps / shell history 里。
#  推送完成后按提示可选删除 token 文件。
# ============================================================
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="${WB_REPO:-$HOME/wb-skills}"
TOKEN_FILE="${WB_TOKEN_FILE:-$HOME/.wb-git-token}"
GIT_USER="${WB_GIT_USER:-canchen412}"
BRANCH="${WB_BRANCH:-main}"
FORCE=0
for a in "$@"; do
  case "$a" in
    --force|-f) FORCE=1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

cd "$REPO" || die "进不去 $REPO"
[ -d .git ] || die "$REPO 不是 git 仓库"

source "$HERE/net.sh"
wb_probe_route
say "GitHub 出口: $WB_ROUTE"
say "仓库:       $REPO"
say "远程:       $(git remote get-url origin)"
say "分支:       $BRANCH"
say ""

# ---------------- 凭据准备 ----------------
ASKPASS=""
TOKEN=""
cleanup() {
  [ -n "$ASKPASS" ] && rm -f "$ASKPASS"
}
trap cleanup EXIT INT TERM

if [ -f "$TOKEN_FILE" ]; then
  TOKEN=$(tr -d ' \t\r\n' < "$TOKEN_FILE")
  [ -n "$TOKEN" ] || die "token 文件是空的: $TOKEN_FILE"

  ASKPASS=$(mktemp /tmp/wb_askpass.XXXXXX) || die "创建 askpass 失败"
  cat > "$ASKPASS" <<'ASKEOF'
#!/bin/sh
case "$1" in
  *[Uu]sername*|*[Ll]ogin*) printf '%s\n' "$WB_GIT_ASK_USER" ;;
  *)                        printf '%s\n' "$WB_GIT_ASK_TOKEN" ;;
esac
ASKEOF
  chmod 700 "$ASKPASS"

  export WB_GIT_ASK_USER="$GIT_USER"
  export WB_GIT_ASK_TOKEN="$TOKEN"
  export GIT_ASKPASS="$ASKPASS"
  export GIT_TERMINAL_PROMPT=0
  ok "凭据来源: ${TOKEN_FILE}（经 askpass 传递，不在命令行暴露）"
else
  warn "找不到 ${TOKEN_FILE}，尝试用钥匙串/现有凭据"
  export GIT_TERMINAL_PROMPT=0
fi

# ---------------- 先探远端（区分：仓库不存在 / 无权限 / 正常） ----------------
say ""
say "▸ 第 1 步：探测远端仓库"
if wb_git ls-remote --heads origin >/tmp/wb_lsremote 2>/tmp/wb_lsremote_err; then
  if [ -s /tmp/wb_lsremote ]; then
    ok "远端可访问，已有分支："
    sed 's/^/     /' /tmp/wb_lsremote | head -5
    REMOTE_EMPTY=0
  else
    ok "远端可访问，但仓库还是空的（首次推送）"
    REMOTE_EMPTY=1
  fi
else
  warn "探测失败，输出如下："
  sed 's/^/     /' /tmp/wb_lsremote_err | head -6
  say ""
  say "常见原因："
  say "  · 仓库还没在 GitHub 上创建 → 先去建（Private 即可，不要勾 README）"
  say "  · token 无权限 → 需要 repo（classic）或 Contents:Read+Write（fine-grained）"
  say "  · token 过期/写错 → 重新生成，注意不要带多余空格"
  say "  · 仓库名以连字符开头，URL 里必须完整保留 /-skill.git"
  exit 1
fi

# ---------------- 推送 ----------------
say ""
say "▸ 第 2 步：推送 $BRANCH"
PUSH_FLAGS=()
[ "$FORCE" = "1" ] && { PUSH_FLAGS=(-f); say "  （--force：覆盖远端已有历史）"; }
if wb_git push ${PUSH_FLAGS[@]+"${PUSH_FLAGS[@]}"} -u origin "$BRANCH" 2>/tmp/wb_push_out; then
  ok "推送成功"
  sed 's/^/     /' /tmp/wb_push_out | tail -8
else
  warn "推送失败："
  sed 's/^/     /' /tmp/wb_push_out | tail -12
  say ""
  say "若提示 non-fast-forward / fetch first："
  say "  · 远端有别人的提交 → 先 cd $REPO && git pull --rebase origin $BRANCH"
  say "  · 远端只有建仓库时自动生成的 README（无价值）→ 用 --force 覆盖"
  say "    bash _tooling/push-now.sh --force"
  exit 1
fi

# ---------------- 校验 ----------------
say ""
say "▸ 第 3 步：校验远端"
if wb_git ls-remote --heads origin >/tmp/wb_lsremote2 2>/dev/null; then
  REMOTE_SHA=$(awk '{print $1}' /tmp/wb_lsremote2 | head -1)
  LOCAL_SHA=$(git rev-parse HEAD)
  say "  远端 HEAD: ${REMOTE_SHA:0:12}"
  say "  本地 HEAD: ${LOCAL_SHA:0:12}"
  if [ "$REMOTE_SHA" = "$LOCAL_SHA" ]; then
    ok "本地与远端完全一致"
  else
    warn "SHA 不一致，请复查"
  fi
fi

# ---------------- 标签 ----------------
AUTO="v$(date +%Y.%m.%d)-$(git rev-list --count HEAD)"
if git tag -f "$AUTO" >/dev/null 2>&1 && wb_git push -f -q origin "$AUTO" 2>/dev/null; then
  ok "已打标签 ${AUTO}（小号可用 --rollback $AUTO 回退）"
fi

# ---------------- 收尾 ----------------
say ""
say "▸ 第 4 步：清理"
if [ -n "$TOKEN" ]; then
  say "  token 仍保存在 $TOKEN_FILE"
  say "  用完请删除：  rm -f $TOKEN_FILE"
  say "  或去 GitHub 撤销该 token（Settings → Developer settings → Tokens）"
else
  ok "未使用 token 文件，无需清理"
fi

say ""
ok "完成。仓库地址: $(git remote get-url origin | sed 's|\.git$||')"
