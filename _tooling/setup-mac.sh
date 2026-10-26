#!/bin/bash
# ============================================================
#  setup-mac.sh — 小号首次同步后装环境（git 不管环境，这步必须手工做一次）
#  用法: bash setup-mac.sh [--check]
# ============================================================
set -uo pipefail

SKILLS="${WB_SKILLS:-$HOME/.workbuddy/skills}"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

say()  { printf '%s\n' "$*"; }
ok()   { printf '✅ %s\n' "$*"; }
warn() { printf '⚠️  %s\n' "$*"; }
miss() { printf '❌ 缺 %s\n' "$*"; }

say "▸ WorkBuddy 技能环境自检"
say ""

NEED_INSTALL=0

# ---------- 1. 命令行工具 ----------
say "【1】命令行工具"
check_bin() {
  local b="$1" hint="$2"
  if command -v "$b" >/dev/null 2>&1; then
    ok "$b  $($b -version 2>&1 | head -1 | cut -c1-40)"
  else
    miss "$b   →  $hint"
    NEED_INSTALL=1
  fi
}
check_bin ffmpeg  "brew install ffmpeg"
check_bin ffprobe "brew install ffmpeg"
command -v node >/dev/null 2>&1 && ok "node  $(node -v)" || { warn "node 未装 → Remotion 系技能需要: brew install node"; NEED_INSTALL=1; }
command -v git  >/dev/null 2>&1 && ok "git   $(git --version | awk '{print $3}')" || { miss "git"; NEED_INSTALL=1; }

# ---------- 2. Python ----------
say ""
say "【2】Python"
PY=""
for c in "$HOME/.workbuddy/binaries/python/versions/"*/bin/python3 "$(command -v python3)"; do
  [ -x "$c" ] && PY="$c" && break
done
if [ -n "$PY" ]; then
  ok "python $("$PY" -V 2>&1)  →  $PY"
else
  miss "python3"
  NEED_INSTALL=1
fi

# ---------- 3. Python 包 ----------
# 清单来自对 29 个技能全部 .py 的 AST import 静态扫描结果
say ""
say "【3】Python 依赖（按技能实际 import 扫描得出）"
if [ -n "$PY" ]; then
  # import名:pip名:哪些技能用
  PKGS="
PIL:Pillow:koubo-ad-video,xueyuan-koubo
numpy:numpy:koubo-ad-video,xueyuan-koubo
cv2:opencv-python-headless:koubo-ad-video,xueyuan-koubo
cryptography:cryptography:totorosir-*
Crypto:pycryptodome:totorosir-*
pypinyin:pypinyin:koubo-ad-video
soundfile:soundfile:koubo-ad-video
edge_tts:edge-tts:xueyuan-koubo
mlx:mlx:koubo-ad-video
Quartz:pyobjc-framework-Quartz:koubo-ad-video
Foundation:pyobjc-framework-Cocoa:koubo-ad-video
Vision:pyobjc-framework-Vision:koubo-ad-video
"
  MISSING_PIP=""
  echo "$PKGS" | while IFS=: read -r mod pipname users; do
    [ -z "${mod:-}" ] && continue
    if "$PY" -c "import $mod" >/dev/null 2>&1; then
      ok "$mod"
    else
      warn "$mod 未装  →  pip install $pipname   （${users}）"
    fi
  done
  # 汇总一条可直接执行的命令（只列缺的）
  for pair in "PIL:Pillow" "numpy:numpy" "cv2:opencv-python-headless" \
              "cryptography:cryptography" "Crypto:pycryptodome" \
              "pypinyin:pypinyin" "soundfile:soundfile" "edge_tts:edge-tts"; do
    m="${pair%%:*}"; p="${pair##*:}"
    "$PY" -c "import $m" >/dev/null 2>&1 || MISSING_PIP="$MISSING_PIP $p"
  done
  if [ -n "$MISSING_PIP" ]; then
    say ""
    say "  一键安装缺的包："
    say "    \"$PY\" -m pip install$MISSING_PIP"
    NEED_INSTALL=1
  fi
fi

# ---------- 4. 技能声明的 requirements ----------
say ""
say "【4】技能自带 requirements.txt"
FOUND_REQ=0
while IFS= read -r req; do
  FOUND_REQ=1
  skill=$(echo "$req" | sed "s|$SKILLS/||;s|/requirements.txt||")
  say "  · $skill"
  [ "$CHECK_ONLY" = "1" ] || "$PY" -m pip install -q -r "$req" 2>/dev/null \
    && ok "    已装" || warn "    安装失败，手工跑: $PY -m pip install -r $req"
done < <(find "$SKILLS" -maxdepth 2 -name requirements.txt 2>/dev/null)
[ "$FOUND_REQ" = "0" ] && say "  （无，跳过）"

# ---------- 5. 凭据 ----------
say ""
say "【5】凭据"
if [ -f "$HOME/.workbuddy/bailian.json" ]; then
  ok "bailian.json 存在（cloud-tts / koubo 云端配音需要）"
else
  warn "bailian.json 缺失 → cloud-tts 与 koubo 的云端配音不可用"
  say "     从主号拷: ~/.workbuddy/bailian.json → 本机同路径（勿提交到 git）"
fi

# ---------- 6. 外部素材盘 ----------
say ""
say "【6】外部素材盘（koubo-ad-video 等 C 档技能强依赖）"
if [ -d "/Volumes/陈灿会展" ]; then
  ok "/Volumes/陈灿会展 已挂载"
else
  warn "/Volumes/陈灿会展 未挂载"
  say "     → koubo-ad-video / xueyuan-koubo-ad-video / motiongit-templates 可加载但跑不通"
  say "     → 这类技能本质上不适合跨机同步，见方案.md §七"
fi

# ---------- 7. Remotion node_modules ----------
say ""
say "【7】Remotion 系依赖（node_modules 不进 git，需本机重装）"
for d in "$SKILLS"/*/; do
  if [ -f "$d/package.json" ]; then
    name=$(basename "$d")
    if [ -d "$d/node_modules" ]; then
      ok "$name  node_modules 已存在"
    else
      warn "$name 缺 node_modules"
      say "     → cd \"$d\" && npm install（motiongit-templates 约 626MB，慢）"
    fi
  fi
done

# ---------- 汇总 ----------
say ""
say "────────────────────────────────────────"
if [ "$NEED_INSTALL" = "1" ]; then
  warn "有缺失项。装完再重启 WorkBuddy 测试技能。"
  exit 1
fi
ok "环境看起来齐全。别忘重启 WorkBuddy。"
