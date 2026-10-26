import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
  staticFile,
} from 'remotion';

/**
 * BreathLayer —— 静图镜位的「呼吸 + 飘尘」层（v17 新增）
 * ============================================================
 * 解决的问题：ffmpeg 的 `zoompan` 是**匀速**推拉，静图看久了发死；
 *   Remotion 的 spring() 是真阻尼谐振，有「冲一下再稳住」的呼吸感。
 *
 * 两个能力：
 *   ① Breath   非匀速缩放 + 极缓位移（替代 Ken Burns）
 *   ② Motes    前景飘尘粒子（呼应「粉末 / 营养品」品类调性）
 *
 * ★为什么不用视差分层（Parallax）：
 *   视差需要先把素材做主体/背景分割，本片静图是小绿人 IP 全身，
 *   分割边缘容易糊在头发和罐子上。宁缺勿糊 —— 呼吸 + 粒子已足够
 *   把「静止」变成「有生命」，视差不做。
 *
 * ★所有层必须 background: transparent（不透明黑会盖住成片）。
 */

/* ══════════════════════════════════════════
 * ① 呼吸：spring 驱动的缩放 + 位移
 * ══════════════════════════════════════════ */
const Breath: React.FC<{
  src: string;
  startSec: number; durSec: number;
  /** 缩放区间：[起始, 结束]，弹簧从起始冲向结束并微微回弹 */
  scaleFrom: number; scaleTo: number;
  /** 位移方向与幅度（px） */
  dx: number; dy: number;
  /** 0 = 推近, 1 = 拉远 */
  dir?: 0 | 1;
}> = ({src, startSec, durSec, scaleFrom, scaleTo, dx, dy}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sf = Math.round(startSec * fps);
  const df = Math.round(durSec * fps);
  const local = frame - sf;
  if (local < 0 || local > df) return null;

  // ★弹簧的「冲过头再回落」就是呼吸感的来源
  const p = spring({
    frame: local,
    fps,
    config: {damping: 26, stiffness: 42, mass: 1.35},   // 低刚度大阻尼 = 缓入缓出，不过冲
    durationInFrames: df,
  });
  const scale = scaleFrom + (scaleTo - scaleFrom) * p;

  // 位移与缩放同相位，但用**不同的相位偏移**，避免「整体平移」的机械感
  const p2 = spring({
    frame: local,
    fps,
    config: {damping: 30, stiffness: 30, mass: 1.6},
    durationInFrames: df,
  });
  const tx = dx * p2;
  const ty = dy * p2;

  // 末段轻微提亮，给「落定」一个视觉终点
  const bright = 1 + 0.06 * Math.sin((local / df) * Math.PI);

  return (
    <AbsoluteFill style={{background: 'transparent', overflow: 'hidden'}}>
      <Img
        src={staticFile(src)}
        style={{
          position: 'absolute',
          left: '50%',
          top: '50%',
          width: '100%',
          height: '100%',
          objectFit: 'cover',
          objectPosition: 'center top',
          // ★先位移再缩放：避免缩放把位移量也放大
          transform: `translate(calc(-50% + ${tx}px), calc(-50% + ${ty}px)) scale(${scale})`,
          filter: `brightness(${bright})`,
        }}
      />
    </AbsoluteFill>
  );
};

/* ══════════════════════════════════════════
 * ② 飘尘粒子
 * ══════════════════════════════════════════ */
type MoteSpec = {
  /** 起始 x 比例 0~1 */
  x: number;
  /** 起始 y 比例 0~1 */
  y: number;
  /** 半径 px */
  r: number;
  /** 下落速度 px/s */
  vy: number;
  /** 横漂速度 px/s */
  vx: number;
  /** 透明度 0~1 */
  o: number;
  /** 闪烁相位偏移 0~1 */
  ph: number;
};

const Motes: React.FC<{
  list: MoteSpec[];
  startSec: number; durSec: number;
  /** 粒子整体淡入淡出窗口（秒） */
  fade?: number;
}> = ({list, startSec, durSec, fade = 0.6}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sf = Math.round(startSec * fps);
  const df = Math.round(durSec * fps);
  const local = frame - sf;
  if (local < 0 || local > df) return null;

  const t = local / fps;
  const fadeIn = interpolate(t, [0, fade], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const fadeOut = interpolate(t, [durSec - fade, durSec], [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const op = Math.min(fadeIn, fadeOut);

  return (
    <AbsoluteFill style={{background: 'transparent', pointerEvents: 'none'}}>
      {list.map((m, i) => {
        // 垂直循环下落：越出底边就从顶部回来
        const span = 1080 + 80;
        const yRaw = m.y * 1080 + m.vy * t;
        const y = ((yRaw % span) + span) % span - 40;
        // 横漂 + 正弦摆动（让运动轨迹不是直线）
        const x = m.x * 1920 + m.vx * t + Math.sin(t * 0.7 + m.ph * 6.28) * 14;
        // 闪烁：每个粒子有自己的相位，不会整片一起闪
        const tw = 0.62 + 0.38 * Math.sin(t * 1.9 + m.ph * 6.28);

        return (
          <div
            key={i}
            style={{
              position: 'absolute',
              left: x,
              top: y,
              width: m.r * 2,
              height: m.r * 2,
              marginLeft: -m.r,
              marginTop: -m.r,
              borderRadius: '50%',
              // 柔边光晕（不是硬边圆点）——硬边会像脏点
              background: `radial-gradient(circle, rgba(255,252,235,${m.o * tw}) 0%, rgba(255,250,225,${m.o * tw * 0.42}) 45%, rgba(255,250,225,0) 72%)`,
              boxShadow: `0 0 ${m.r * 3}px rgba(255,246,214,${m.o * tw * 0.28})`,
              opacity: op,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};

/* ══════════════════════════════════════════
 * 汇总容器
 * ══════════════════════════════════════════ */
export type BreathSpec = {
  src: string;
  startSec: number; durSec: number;
  scaleFrom: number; scaleTo: number;
  dx: number; dy: number;
};

export type MoteSpecWrap = {
  startSec: number; durSec: number;
  list: MoteSpec[];
};

export const BreathLayer: React.FC<{list?: BreathSpec[]}> = ({list = []}) => (
  <AbsoluteFill style={{background: 'transparent'}}>
    {list.map((b, i) => (
      <Breath key={`b-${i}`} {...b} />
    ))}
  </AbsoluteFill>
);

export const MoteLayer: React.FC<{list?: MoteSpecWrap[]}> = ({list = []}) => (
  <AbsoluteFill style={{background: 'transparent'}}>
    {list.map((m, i) => (
      <Motes key={`m-${i}`} {...m} />
    ))}
  </AbsoluteFill>
);
