import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * 重点字字体说明
 * ============================================================
 * 「文悦新青年体 (非商用) W8」为商业字体。
 * ★ 2026-10-08：本机 headless Chrome 的 loadFont / FontFace 全部悬死
 *   （网络 fetch 与 data URI 均超时），改为**安装进系统字体目录**
 *   （~/Library/Fonts/新青年体.ttf），Chrome 原生识别，不再需要 loadFont。
 *   换机渲染时需重装该字体，否则重点字回退 sans-serif。
 */

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
  /** ★2026-10-14：behind 字的 3D 景深三件套（不写用默认） */
  /** 失焦半径 px：字在人物之后 ⇒ 离镜头更远 ⇒ 应微微虚化，2~3 最自然 */
  behindBlur?: number;
  /** 绕 Y 轴旋转（deg，负=右侧向后退） */
  behindYaw?: number;
  /** 绕 X 轴旋转（deg，正=顶部向后仰） */
  behindPitch?: number;
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
    // ★2026-10-22 换宋体后 +1px：宋体横画细，4px 边在 152 号字上显得发飘
    : `-5px 0 0 #101010, 5px 0 0 #101010,
       0 -5px 0 #101010, 0 5px 0 #101010,
       -5px -5px 0 #101010, 5px 5px 0 #101010,
       -5px 5px 0 #101010, 5px -5px 0 #101010,
       0 0 9px #101010, 0 0 20px rgba(0,0,0,0.78)`;
  // ★ behind 模式（2026-10-14 灿爹「重点字要有 3D 景深 + 被人物遮住一点」）
  //   v1 只有「压暗+降饱和」，是个躺平的平面字，再抠掉一块 ⇒ 观感是「字烂了」。
  //   2026-10-14 重做：真 3D = 三层叠加
  //     ① 空间朝向：perspective + rotateY/rotateX，字不再正对镜头（transformStyle: preserve-3d）
  //     ② 挤出厚度：主字下方按 translateZ 逐层堆叠副本（1 层 = 4px 深，共 9 层 ≈ 36px 立体实体）
  //     ③ 景深失焦：blur —— 字在人物之后 ⇒ 离镜头更远 ⇒ 应当微微虚化（这条最出效果）
  //   遮挡由 ffmpeg 双层合成落地（pipeline fx 段）：mask 由 Vision 显著性逐帧生成，
  //   天然跟随运镜和第一镜的动态视频，不再是写死椭圆。
  const BEHIND_LAYERS = 9;
  const behindShadow = [...Array(BEHIND_LAYERS).keys()].map(i =>
    `${2 + i * 0.6}px ${1 + i * 0.35}px ${3 + i}px rgba(0,0,0,${0.5 - i * 0.04})`).join(', ');
  const behindFilter = spec.behind
    ? `brightness(0.86) saturate(0.88) blur(${spec.behindBlur ?? 2.2}px)` : '';

  return (
    <div
      style={{
        position: 'absolute',
        left: spec.x,
        top: spec.y,
        // 先位移再缩放，避免缩放把位移量也放大
        // ★behind：追加 perspective + rotateY/rotateX 建立 3D 空间（SCENE 朝向），
        //   preserve-3d 让子层的 translateZ 真正有「深度」而不是被拍平。
        transform: spec.behind
          ? `translate(-50%, calc(-50% - ${rise}px)) rotateY(${spec.behindYaw ?? -20}deg) `
            + `rotateX(${spec.behindPitch ?? 7}deg) scale(${(scale / 100) * 0.96})`
          : `translate(-50%, calc(-50% - ${rise}px)) scale(${scale / 100})`,
        ...(spec.behind ? {perspective: 1100, transformStyle: 'preserve-3d' as const} : {}),
        opacity,
        textShadow,
        // ★ behind（2026-10-14 灿爹点名「重点字3D景深、被人物遮住一点」）：
        //   ① zIndex 沉底（同层内压到 viz 与普通重点字之下）
        //   ② 压暗降饱和 + 失焦虚化 —— 融进背景且处在景深之外
        //   ③ 真正被人物/产品遮住靠 ffmpeg 双层合成 + Vision 显著性蒙版
        //      （pipeline_run.py fx 段，2026-10-14 落地）
        ...(spec.behind ? {zIndex: 0, filter: behindFilter} : {}),
      }}
    >
      {/* 挤出层：主字后面叠 N 层同字，逐层后退 translateZ + 变暗 ⇒ 侧面厚度 */}
      {spec.behind ? [...Array(BEHIND_LAYERS).keys()].reverse().map(i => (
        <span
          key={`ex${i}`}
          style={{
            position: 'absolute', left: 0, top: 0,
            fontFamily: '"ALC Serif Heavy", serif',
            fontSize: fs, fontWeight: 400, lineHeight: 1, letterSpacing: 2,
            whiteSpace: 'nowrap',
            transform: `translateZ(${-4 * (i + 1)}px)`,
            color: '#2A1A06',
            opacity: 0.92 - i * 0.055,
            textShadow: 'none',
          }}
        >{spec.text}</span>
      )) : null}
      <span
        style={{
          position: 'relative',
          fontFamily: '"ALC Serif Heavy", serif',
          fontSize: fs,
          fontWeight: 400,
          color: css,
          whiteSpace: 'nowrap',
          lineHeight: 1,
          letterSpacing: 2,
          transform: spec.behind ? 'translateZ(0)' : undefined,
          textShadow: spec.behind ? `${behindShadow}, 0 0 26px rgba(0,0,0,0.45)` : undefined,
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