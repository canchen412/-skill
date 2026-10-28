#!/bin/bash
# ============================================================
#  link-assets.sh — 两台 Mac 的路径对齐（让技能里的硬编码原样生效）
# ------------------------------------------------------------
#  背景：主号（Mac mini）上素材盘是
#          /Volumes/陈灿会展/广告视频/...
#        但 SMB 只共享了「广告视频」这一层，另一台 Mac 挂载后是
#          /Volumes/广告视频/...
#        ⇒ 差一层。技能里 45 处 /Volumes/陈灿会展/广告视频/xxx 全部失效。
#
#  本脚本在小号上建一个符号链接，把两层路径接起来，技能一个字都不用改。
#
#  用法：
#    bash link-assets.sh              # 自动检测并修复（会要 sudo）
#    bash link-assets.sh --check      # 只看现状，不改
#    bash link-assets.sh --home       # 额外把 /Users/<主号名> 链到当前用户（谨慎）
#    bash link-assets.sh --unlink     # 撤销本次建立的链接
# ============================================================
set -uo pipefail

# 主号上的原始路径结构（改这里即可适配别的盘）
REMOTE_VOLUME="陈灿会展"        # 主号上的卷名
SHARE_NAME="广告视频"            # SMB 共享名（= 主号上该卷下的子目录名）
MAIN_USER="mac"                  # 主号用户名（决定 /Users/xxx 硬编码）

CHECK_ONLY=0; LINK_HOME=0; UNLINK=0
for a in "$@"; do
  case "$a" in
    --check)  CHECK_ONLY=1 ;;
    --home)   LINK_HOME=1 ;;
    --unlink) UNLINK=1 ;;
  esac
done

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
die()  { printf '❌ %s\n' "$*"; exit 1; }

TARGET_DIR="/Volumes/$REMOTE_VOLUME"
TARGET_LINK="$TARGET_DIR/$SHARE_NAME"
ME="$(whoami)"

say "▸ 路径对齐检查"
say "  主号期望路径: $TARGET_LINK"
say "  当前用户:     $ME"
say ""

# ---------- 撤销 ----------
if [ "$UNLINK" = "1" ]; then
  if [ -L "$TARGET_LINK" ]; then
    sudo rm -f "$TARGET_LINK" && ok "已删除链接 $TARGET_LINK"
    sudo rmdir "$TARGET_DIR" 2>/dev/null && say "  已清理空目录 $TARGET_DIR"
  else
    warn "$TARGET_LINK 不是符号链接，未动"
  fi
  [ "$LINK_HOME" = "1" ] && [ -L "/Users/$MAIN_USER" ] && \
    sudo rm -f "/Users/$MAIN_USER" && ok "已删除 /Users/$MAIN_USER 链接"
  exit 0
fi

# ---------- 1. 找素材源 ----------
say "【1】定位素材源"
SRC=""
if [ -d "$TARGET_LINK" ] && [ ! -L "$TARGET_LINK" ]; then
  ok "本机就是素材盘所在机器（$TARGET_LINK 真实存在），无需对齐"
  SRC=""   # 已经是原生路径
elif [ -L "$TARGET_LINK" ]; then
  DEST=$(readlink "$TARGET_LINK")
  if [ -d "$DEST" ]; then
    ok "链接已存在且有效: $TARGET_LINK → $DEST"
    SRC="$DEST"
  else
    warn "链接存在但已断开: $TARGET_LINK → ${DEST}（素材盘没挂载？）"
    SRC=""
  fi
else
  # 找挂载的共享卷（可能带 -1 / " 1" 后缀）
  for cand in "/Volumes/$SHARE_NAME" "/Volumes/$SHARE_NAME-1" "/Volumes/$SHARE_NAME 1"; do
    if [ -d "$cand" ]; then SRC="$cand"; break; fi
  done
  if [ -z "$SRC" ]; then
    warn "没找到共享卷。先在 Finder 里连接主号："
    say "     Finder → 前往 → 连接服务器 → smb://<主号IP>"
    say "     或用命令行挂载："
    say "       mkdir -p ~/mnt/advideo"
    say "       mount_smbfs //guest@<主号IP>/$SHARE_NAME ~/mnt/advideo"
    say "     挂好之后重跑本脚本。"
    die "缺素材源"
  fi
  ok "找到共享卷: $SRC"
fi

# ---------- 2. 建符号链接 ----------
if [ -n "$SRC" ] && [ "$SRC" != "$TARGET_LINK" ]; then
  say ""
  say "【2】建立路径对齐链接"
  say "  $TARGET_LINK"
  say "     ↓ 指向"
  say "  $SRC"
  if [ "$CHECK_ONLY" = "1" ]; then
    warn "check 模式，不实际创建"
    say "  实际执行命令："
    say "    sudo mkdir -p \"$TARGET_DIR\""
    say "    sudo ln -sfn \"$SRC\" \"$TARGET_LINK\""
  else
    if [ -e "$TARGET_DIR" ] && [ ! -d "$TARGET_DIR" ]; then
      die "$TARGET_DIR 已存在且不是目录，请手工处理"
    fi
    sudo mkdir -p "$TARGET_DIR" || die "建目录失败（需要 sudo）"
    sudo ln -sfn "$SRC" "$TARGET_LINK" \
      && ok "已创建: $TARGET_LINK → $SRC" \
      || die "创建链接失败"
    # 验证
    if [ -d "$TARGET_LINK/分镜图库" ] || [ -f "$TARGET_LINK/_归档规则.md" ]; then
      ok "验证通过：技能所需的子目录可见"
    elif [ -d "$TARGET_LINK" ]; then
      warn "链接可访问，但没看到「分镜图库」——确认共享内容是否是广告视频目录"
    fi
  fi
fi

# ---------- 3. 用户名路径对齐（可选）----------
say ""
say "【3】用户名路径（技能里有 /Users/$MAIN_USER 硬编码）"
if [ "$ME" = "$MAIN_USER" ]; then
  ok "当前用户就是 ${MAIN_USER}，87 处硬编码天然有效，无需处理"
elif [ "$LINK_HOME" = "1" ]; then
  if [ -L "/Users/$MAIN_USER" ]; then
    ok "已存在 /Users/$MAIN_USER → $(readlink /Users/$MAIN_USER)"
  elif [ -e "/Users/$MAIN_USER" ]; then
    die "/Users/$MAIN_USER 是真目录（真的有这个用户），不能覆盖。改用参数化方案。"
  else
    say "  将创建: /Users/$MAIN_USER → $HOME"
    if [ "$CHECK_ONLY" = "1" ]; then
      warn "check 模式，不实际创建"
    else
      sudo ln -sfn "$HOME" "/Users/$MAIN_USER" \
        && ok "已创建（注意：这是全局别名，会影响所有以 /Users/$MAIN_USER 开头的路径）" \
        || die "创建失败"
    fi
  fi
else
  warn "当前用户是 $ME ≠ 主号 $MAIN_USER"
  say "     技能里 87 处 /Users/$MAIN_USER/... 会失效。两个选择："
  say "       a) sudo 别名:  bash link-assets.sh --home   （一条链接全解决，但影响全局）"
  say "       b) 参数化:     把技能里的 /Users/$MAIN_USER 改成 \${HOME}（长期正确，但要改文件）"
fi

# ---------- 4. 汇总 ----------
say ""
say "────────────────────────────────────────"
if [ "$CHECK_ONLY" = "1" ]; then
  say "（check 模式，未改动任何东西）"
else
  say "完成后跑一遍：bash _tooling/setup-mac.sh --check"
  say "然后重启 WorkBuddy。"
fi
