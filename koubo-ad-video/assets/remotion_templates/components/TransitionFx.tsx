import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * TransitionFx —— 镜间转场（v18 修正版）
 * ============================================================
 * 现状问题：13 个镜位之间全是**硬切**，缺现代广告片的呼吸感。
 *
 * ★★★ v18 实测踩坑：实体色块幕布一定会出现「半遮半露的黑边」
 *   症状：转场瞬间画面**上半被黑块盖住、下半还亮着**，
 *         像「一块黑板从上往下压下来又弹走」，非常廉价。
 *   根因：幕布是一个 2300×1300 的**大矩形横扫**，
 *         横向上它从右往左推进 ⇒ 任意中间帧都只有部分区域被覆盖，
 *         而矩形的上下沿正好落在画面内 ⇒ 形成硬边。
 *   ⇒ **实体色块横扫这个思路本身就是错的**，别再试。
 *
 * ★★ v18 修正方案：全屏「压暗 + 扫光带」，不用实体色块。
 *   构成（全部覆盖整屏，永远不会有未遮盖的边）：
 *     ① 压暗层：黑色铺满1920×1080，透明度按 bell 曲线起落
 *     ② 扫光带：一条**斜向亮带**（深黄 → 透明渐变）横扫画面
 *     ③ 边缘辉光：亮带前缘一条高光线，是「现代感」的关键
 *   效果＝「画面暗下来 → 一道光扫过 → 亮起来」，比黑块自然得多。
 *
 * ★★ 时长铁律：转场是**覆盖式**的，不额外占用镜位时间。
 *   atSec ＝ 后一镜入点，幕布在 [atSec-durSec, atSec] 区间内起落，
 *   全片总时长与音画时间轴完全不动，不会让字幕和配音错位。
 */

export type TransKind = 'wipe' | 'slide' | 'fade' | 'slideL';

export type TransSpec = {
  kind: TransKind;
  /** 转场**结束**的时刻（秒）＝后一镜的入点 */
  atSec: number;
  /** 转场持续时长（秒），建议 0.30~0.45 */
  durSec: number;
  /** 转场方向（slide 系列用）：1 = 自右向左扫，-1 = 自左向右扫 */
  from?: {x: number; y: number};
  /** 压暗层峰值透明度（0~1） */
  dim?: number;
  /** 扫光带颜色 */
  color?: string;
};

/** 单个转场：全屏压暗 + 斜向扫光 */
const Trans: React.FC<{spec: TransSpec}> = ({spec}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sf = Math.round((spec.atSec - spec.durSec) * fps);
  const df = Math.max(1, Math.round(spec.durSec * fps));
  const local = frame - sf;
  if (local < 0 || local > df) return null;

  const p = local / df;              // 0 → 1
  const dim = spec.dim ?? 0.72;      // 压暗峰值

  // ① 压暗层：bell 曲线，中段最暗
  // ★ v19 修正：删掉原来的「斜向黄光扫带」——灿爹反馈「转场有黄黑影」。
  //   那条 #D4FF00 亮带横扫时本身就是一道**黄色斜影**，在暗画面上非常显眼。
  //   现在只保留全屏均匀压暗，干净、无任何色影。
  const dimOp = interpolate(p, [0, 0.42, 0.58, 1], [0, dim, dim, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <AbsoluteFill style={{background: 'transparent', zIndex: 3000, pointerEvents: 'none'}}>
      {/* ① 全屏压暗（铺满 1920×1080，绝不留未遮盖的边） */}
      <AbsoluteFill style={{background: '#000000', opacity: dimOp}} />
    </AbsoluteFill>
  );
};

export const TransitionFx: React.FC<{list?: TransSpec[]}> = ({list = []}) => (
  <AbsoluteFill style={{background: 'transparent'}}>
    {list.map((t, i) => (
      <Trans key={`t-${i}`} spec={t} />
    ))}
  </AbsoluteFill>
);
