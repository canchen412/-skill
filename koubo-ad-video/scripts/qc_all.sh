#!/bin/bash
# ★一键质检 qc_all.sh（2026-10-09）—— 一条命令跑完成片五项检查
# 用法: bash qc_all.sh <成片.mp4> <workdir> [卷播时长秒]
# 输出: PASS/FAIL 表（人工只需最后看抽帧图）
FF=/Users/mac/.local/bin/ffmpeg
FP=/Users/mac/.local/bin/ffprobe
PIECE="$1"; WD="$2"; BANNER_DUR="${3:-0}"
PASS=0; FAIL=0
say() { if [ "$1" = "ok" ]; then echo "  ✓ $2"; PASS=$((PASS+1)); else echo "  ✗ $2"; FAIL=$((FAIL+1)); fi; }

echo "════ 质检 $PIECE ════"
# 1 时长
DUR=$($FP -v error -show_entries format=duration -of csv=p=0 "$PIECE")
echo "1) 时长 ${DUR}s"

# 2 顶开（渲染链拦截记录）
OPEN=$(grep -c "顶开" "$WD/filter_debug.txt" 2>/dev/null); OPEN=${OPEN:-0}
say ok "2) 顶开 $OPEN 次"

# 3 响度（Integrated LUFS，带货标准 -14±2.5）
LUFS=$($FF -nostats -i "$PIECE" -af "ebur128=peak=true" -f null - 2>&1 | grep -A3 "Summary" | grep "I:" | head -1 | grep -oE "[-0-9.]+ LUFS" | grep -oE "^[-0-9.]+")
OKL=$(python3 -c "print('ok' if -17 < float('$LUFS') < -10 else 'no')" 2>/dev/null || echo no)
say "$OKL" "3) 响度 ${LUFS} LUFS"

# 4 镜1 音轨非静音（防静音轨顶掉配音）
VOL=$($FF -v info -t "${BANNER_DUR:-3.7}" -i "$PIECE" -map 0:a -af volumedetect -f null - 2>&1 | grep -oE "mean_volume: [-0-9.]+" | head -1 | grep -oE "[-0-9.]+")
OKV=$(python3 -c "print('ok' if float('$VOL' or -99) > -60 else 'no')" 2>/dev/null || echo no)
say "$OKV" "4) 镜1音轨 ${VOL} dB"

# 5 抽帧（含右下角 crop——角标必须人工扫一眼）
$FF -y -v error -i "$PIECE" -vf "select='eq(n\,62)+eq(n\,340)+eq(n\,700)+eq(n\,950)',scale=640:360" -fps_mode vfr -frames:v 4 /tmp/qc_f_%d.png 2>/dev/null
$FF -y -v error -ss 2.4 -i "$PIECE" -frames:v 1 -vf "crop=700:160:1220:920" /tmp/qc_corner.png 2>/dev/null
$FF -y -v error -i /tmp/qc_f_1.png -i /tmp/qc_f_2.png -i /tmp/qc_f_3.png -i /tmp/qc_f_4.png -filter_complex "[0][1][2][3]xstack=inputs=4:layout=0_0|w0_0|0_h0|w0_h0" /tmp/qc_grid.png 2>/dev/null
echo "  👁 5) 抽帧 /tmp/qc_grid.png + 右下角 /tmp/qc_corner.png（人工扫角标）"

echo "════ 结果: PASS=$PASS FAIL=$FAIL ════"
[ "$FAIL" -eq 0 ]
