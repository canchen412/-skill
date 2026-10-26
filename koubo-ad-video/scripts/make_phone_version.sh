#!/usr/bin/env bash
# ★★★ 默认交付版生成（2026-10-15 灿爹拍板："客户都在手机上看，质量没必要那么高，渲染快、体积小就行"）
# 用法: make_phone_version.sh <输入1080P母版.mp4> [输出手机版.mp4]
# 效果: 218MB/47Mbps -> ~6MB, 1280x720, CRF24, faststart, 手机端画面损失肉眼难辨
# 位置: motion_finish + 真峰值补刀 之后、交付之前 跑这一步；交付给用户的就是这个手机版。
set -euo pipefail
SRC="$1"
OUT="${2:-${SRC%.mp4}_手机预览.mp4}"
ffmpeg -y -v error -i "$SRC" \
  -vf "scale=1280:720" \
  -c:v libx264 -crf 24 -preset slow -profile:v high -pix_fmt yuv420p \
  -c:a aac -b:a 128k \
  -movflags +faststart "$OUT"
echo "✅ $OUT ($(du -h "$OUT" | cut -f1))"
