import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';

/**
 * 重点字字体加载
 * ============================================================
 * ★为什么必须显式 loadFont：
 *   「文悦新青年体 (非商用) W8」是**商业字体，没装进系统**。
 *   ffmpeg 那侧靠 `subtitles=xx.ass:fontsdir=<技能>/assets/fonts` 加载，
 *   Remotion 走 headless Chrome 只认系统已注册字体 ⇒ 不 loadFont 会静默回退。
 *   loadFont 返回 Promise，必须用 delayRender 阻塞首帧。
 */
const handle = delayRender('加载新青年体');
loadFont({
  family: 'WenYue XinQingNianTi',
  url: staticFile('新青年体.ttf'),
  weight: '800',
})
  .then(() => continueRender(handle))
  .catch((e) => {
    console.error('新青年体加载失败，将回退系统字体：', e);
    continueRender(handle);
  });

/** ASS 的 &H00BBGGRR（BBGGRR 是 BGR）→ CSS #RRGGBB */
export const assColorToCss = (ass: string): string => {
  const h = ass.replace(/[^0-9A-Fa-f]/g, '');
  const bgr = h.slice(2, 8);
  const b = bgr.slice(0, 2);
  const g = bgr.slice(2, 4);
  const r = bgr.slice(4, 6);
  return `#${r}${g}${b}`.toUpperCase();
};

/**
 * ★ 动效变体：三类语义 × 不同弹簧物理
 * ============================================================
 * ASS 的 \t() 只能分段线性插值（\fscx30→126→100 在 126% 处有明显折角），
 * Remotion 的 spring() 是真阻尼谐振 —— 冲过头、被拉回、微弹一下才停。
 * 不同 damping/stiffness 给出完全不同的「性格」：
 *
 *   warn    低频闷击感：过冲小（1.04）、回弹快、末段有一次下压 —— 稳、压迫、不轻浮
 *   stamp   认证盖章感：从上方砸下、过冲大（1.18）后 3 次快速衰减 —— 有分量、干脆
 *   price   价格重锤感：从下方顶上来、过冲最大（1.22）再回落 + 中段再顶 —— 最有冲击力
 */
export type KwAnim = 'warn' | 'stamp' | 'price' | 'plain';

export type KwSpec = {
  /** 该重点字在成片里的绝对起始时间（秒） */
  startSec: number;
  /** 停留时长（秒） */
  durSec: number;
  text: string;
  /** ASS 坐标（5 号对齐的 \pos） */
  x: number;
  y: number;
  /** ASS 颜色（&H00BBGGRR），这里转成 CSS 的 #RRGGBB */
  assColor: string;
  fontSize?: number;
  anim?: KwAnim;
  /** 入场方向（px，正=从上往下砸，负=从下往上顶） */
  dropPx?: number;
  /** 描边粗细（0=无描边；用于 stroke 变体的空心→实心） */
  hollow?: boolean;
  /**
   * ★ behind：字画在「人物后面半遮住」的效果（灿爹 2026-10-07 要的）。
   *   实现：zIndex 压到 0（卡片是 800~1000）⇒ 被人物/产品挡住下半截，
   *   只露出上半截 —— 文字「从人物后面露出来」的高级感。
   *   ⚠️ 必须在「有透明区域」的层上做；接成片时若重点字层在最上面，
   *   就要靠「底片本身有人物」+ 本层 zIndex=0 才能被遮住。
   */
  behind?: boolean;
};

/** 各变体的弹簧参数与运动曲线 */
const ANIM: Record<KwAnim, {
  damping: number; stiffness: number; mass: number;
  durS: number; from: number; drop: number; pulse?: boolean;
}> = {
  // 警告：过冲小、收得快，压得住
  plain: {damping: 9, stiffness: 180, mass: 0.7, durS: 0.55, from: 20, drop: 26},
  warn:   {damping: 12, stiffness: 210, mass: 0.9, durS: 0.50, from: 42, drop: -18},
  // 盖章：砸下 + 大过冲 + 快速衰减
  stamp:  {damping: 8, stiffness: 240, mass: 0.85, durS: 0.62, from: 12, drop: 46},
  // 重锤：从下顶上 + 最大过冲 + 中段复顶
  price:  {damping: 7, stiffness: 260, mass: 0.8, durS: 0.72, from: 8, drop: -52, pulse: true},
};

export const KwWord: React.FC<{spec: KwSpec; globalFrame: number}> = ({spec, globalFrame}) => {
  const {fps} = useVideoConfig();
  const localFrame = globalFrame - Math.round(spec.startSec * fps);
  const localDur = Math.round(spec.durSec * fps);
  if (localFrame < 0 || localFrame > localDur) return null;

  const a = ANIM[spec.anim ?? 'plain'];
  const springDur = Math.round(a.durS * fps);

  // ---- 缩放：从 a.from 起跳，spring 自然过冲回落 ----
  const springRaw = spring({
    frame: localFrame,
    fps,
    config: {damping: a.damping, stiffness: a.stiffness, mass: a.mass},
    durationInFrames: springDur,
  });
  // spring() 返回 0→1 的进度，映射到 [from%, 100%]：过冲量由 spring 的物理决定
  let scale = a.from + (100 - a.from) * springRaw;

  // price 变体：中段再顶一下（第二次冲击），这是 ASS 完全做不到的
  if (a.pulse && localFrame > springDur) {
    const t2 = localFrame - springDur;
    const p2 = spring({
      frame: t2,
      fps,
      config: {damping: 14, stiffness: 320, mass: 0.6},
      durationInFrames: Math.round(0.34 * fps),
    });
    scale += 7 * p2 * (1 - Math.min(1, t2 / (0.34 * fps)));   // 顶起后衰减
  }

  // ---- 透明度：4 帧淡入，末 6 帧淡出 ----
  const opacity = interpolate(
    localFrame,
    [0, 4, Math.max(5, localDur - 6), localDur],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}
  );

  // ---- 位置：入场位移，落定后归零 ----
  const drop = spec.dropPx ?? a.drop;
  const rise = interpolate(localFrame, [0, Math.round(0.26 * fps)], [drop, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });

  const fs = spec.fontSize ?? 132;
  const css = assColorToCss(spec.assColor);
  // 阴影：非 hollow 用描边式硬阴影；hollow（stroke 变体）改成外发光，突出「空心→实心」
  const textShadow = spec.hollow
    ? `0 0 18px rgba(255,255,255,0.55), 0 0 42px rgba(255,255,255,0.28),
       -3px 0 0 #101010, 3px 0 0 #101010, 0 -3px 0 #101010, 0 3px 0 #101010`
    : `-4px 0 0 #101010, 4px 0 0 #101010,
       0 -4px 0 #101010, 0 4px 0 #101010,
       -4px -4px 0 #101010, 4px 4px 0 #101010,
       -4px 4px 0 #101010, 4px -4px 0 #101010,
       0 0 8px #101010, 0 0 18px rgba(0,0,0,0.75)`;
  // ★ behind 模式：字在人物后面 ⇒ 压暗 + 降饱和，与背景融在一起（不是「贴在上面」）
  const behindFilter = spec.behind ? 'brightness(0.78) saturate(0.7)' : '';

  return (
    <div
      style={{
        position: 'absolute',
        left: spec.x,
        top: spec.y,
        // 先位移再缩放，避免缩放把位移量也放大
        transform: `translate(-50%, calc(-50% - ${rise}px)) scale(${scale / 100})`,
        opacity,
        textShadow,
      }}
    >
      <span
        style={{
          fontFamily: '"WenYue XinQingNianTi", sans-serif',
          fontSize: fs,
          fontWeight: 800,
          color: css,
          whiteSpace: 'nowrap',
          lineHeight: 1,
          letterSpacing: 2,
          // hollow 变体：文字本体半透明 ⇒ 视觉上就是「空心」，spring 到位后转实
          // ★ 0.34 在深底上几乎看不见（实测 v2 全糊），提到 0.62 才读得出「空心」
          opacity: spec.hollow ? interpolate(localFrame, [0, springDur, springDur + 5], [0.62, 0.62, 1],
            {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}) : 1,
        }}
      >
        {spec.text}
      </span>
    </div>
  );
};

export const KwOverlay: React.FC<{list?: KwSpec[]}> = ({list = []}) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{background: 'transparent'}}>
      {list.map((spec, i) => (
        <KwWord key={`${spec.text}-${i}`} spec={spec} globalFrame={frame} />
      ))}
    </AbsoluteFill>
  );
};