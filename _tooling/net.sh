#!/bin/bash
# ============================================================
#  net.sh — GitHub 出口自动探测（被 push.sh / pull.sh / sync-up.sh source）
# ------------------------------------------------------------
#  背景（2026-10-26 实测）：
#    WorkBuddy 沙箱把 http_proxy 指向自己的内部代理，该代理拒绝
#    CONNECT github.com → 报 "HTTP2 framing layer" / "CONNECT failed 502"。
#    但机器上若开着 Clash Verge 之类的客户端（实测 127.0.0.1:7897），
#    从它出去是通的（github.com 200 / 0.76s）。
#  所以不能一律"清代理"，而要"先找能通的代理，找不到再直连"。
#
#  产出的全局变量：
#    WB_GIT   —— 数组，git 命令前缀（含 env 与 http.version）
#    WB_ROUTE —— 描述，如 "proxy:7897" 或 "direct"
#
#  用法：
#    source "$(dirname "$0")/net.sh"
#    wb_probe_route
#    "${WB_GIT[@]}" git ls-remote origin
#    -- 或直接：wb_git ls-remote origin
# ============================================================

# 常见代理客户端端口（Clash Verge 7897 / ClashX 7890 / Surge 6152 / V2Ray 1087 / 通用 1080）
WB_PROXY_TRIES="${WB_PROXY_TRIES:-7897 7899 7890 7891 1087 1086 1080 6152 8889 10000 8123}"

# 探测一次结果缓存，避免重复计时
WB_ROUTE="${WB_ROUTE:-}"
WB_GIT=()

wb_probe_route() {
  [ -n "$WB_ROUTE" ] && return 0

  # 允许用户强制指定：WB_PROXY=http://127.0.0.1:7897 或 WB_PROXY=none
  case "${WB_PROXY:-}" in
    none|direct)
      WB_GIT=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
                  -u ALL_PROXY -u all_proxy git -c http.version=HTTP/1.1)
      WB_ROUTE="direct(forced)"
      return 0 ;;
    http*)
      WB_GIT=(env http_proxy="$WB_PROXY" https_proxy="$WB_PROXY" \
                  HTTP_PROXY="$WB_PROXY" HTTPS_PROXY="$WB_PROXY" git -c http.version=HTTP/1.1)
      WB_ROUTE="proxy:${WB_PROXY##*:}(forced)"
      return 0 ;;
  esac

  local port
  for port in $WB_PROXY_TRIES; do
    # 端口没监听就跳过，省时间
    if ! nc -z -G 1 127.0.0.1 "$port" >/dev/null 2>&1; then
      continue
    fi
    if curl -sS -o /dev/null --max-time 4 -x "http://127.0.0.1:$port" \
         https://github.com >/dev/null 2>&1; then
      WB_GIT=(env http_proxy="http://127.0.0.1:$port" https_proxy="http://127.0.0.1:$port" \
                  HTTP_PROXY="http://127.0.0.1:$port" HTTPS_PROXY="http://127.0.0.1:$port" \
                  git -c http.version=HTTP/1.1)
      WB_ROUTE="proxy:$port"
      return 0
    fi
  done

  # 都没通就试直连（某些网络直连本来就通）
  WB_GIT=(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
              -u ALL_PROXY -u all_proxy git -c http.version=HTTP/1.1)
  WB_ROUTE="direct"
  return 0
}

# 便利封装：wb_git <git 子命令...>
wb_git() {
  wb_probe_route
  "${WB_GIT[@]}" git "$@"
}

# 打印当前出口
wb_route_info() {
  wb_probe_route
  printf 'GitHub 出口: %s\n' "$WB_ROUTE"
}
