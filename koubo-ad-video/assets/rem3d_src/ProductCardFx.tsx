import React from 'react';
import {Img, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import {staticFile} from 'remotion';

/**
 * 产品卡弹簧入场
 * ============================================================
 * 为什么舍掉 build_ad_video 的 product_cards：
 *   ffmpeg 那一版 anim:"slide" 是**线性位移**（等速滑到位就停），
 *   看着像「推出来的贴纸」，没有重量感、不吸引眼睛。
 *   Remotion 的 spring 是真阻尼谐振 ⇒ 冲过头一点、被拉回、微弹停住，
 *   观众会本能地注意到那一下「落定」——这正是产品点名镜要的效果。
 *
 * 参数与 ffmpeg 版保持一致（left/top/w 直接沿用 config），
 * 这样切过来 Config 语义不变，只是|||||换了实现。
 */
export const ProductCardFx: React.FC<{
  /** public/ 下的图片路径 */
  src: string;
  w: number;
  /** 左上角坐标 */
  left: number;
  top: number;
  startSec: number;
  durSec: number;
  /** 入场方向：'right' 从画右滑入（默认） */
  from?: 'right' | 'left' | 'bottom';
  radius?: number;
}> = ({src, w, left, top, startSec, durSec, from = 'right', radius = 20}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const startFrame = Math.round(startSec * fps);
  const durFrames = Math.round(durSec * fps);
  const local = frame - startFrame;
  if (local < 0 || local > durFrames + Math.round(0.35 * fps)) return null;

  // 入场位移：spring 从偏移量收到 0（过冲 → 回弹 → 停住）
  const enter = spring({
    frame: local,
    fps,
    config: {damping: 11, stiffness: 170, mass: 0.9},
    durationInFrames: Math.round(0.75 * fps),
  });
  const dist = from === 'right' ? w + 60
    : from === 'left' ? -(w + 60)
    : 0;
  const distY = from === 'bottom' ? 220 : 0;
  const dx = dist * (1 - enter);
  const dy = distY * (1 - enter);

  // 轻微缩放：0.9 → 1，配合 spring 让「落定」更实
  const sc = 0.90 + 0.10 * spring({
    frame: local,
    fps,
    config: {damping: 13, stiffness: 200, mass: 0.8},
    durationInFrames: Math.round(0.6 * fps),
  });

  // 淡出：尾 8 帧
  const opacity = interpolate(
    local,
    [0, 3, durFrames - 8, durFrames],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}
  );

  return (
    <div
      style={{
        position: 'absolute',
        left,
        top,
        width: w,
        transform: `translate(${dx}px, ${dy}px) scale(${sc})`,
        opacity,
        borderRadius: radius,
        overflow: 'hidden',
        boxShadow: '0 10px 34px rgba(0,0,0,0.5), 0 0 0 1.5px rgba(255,255,255,0.14)',
        transformOrigin: 'center',
      }}
    >
      <Img src={staticFile(src)} style={{width: '100%', display: 'block'}} />
    </div>
  );
};
