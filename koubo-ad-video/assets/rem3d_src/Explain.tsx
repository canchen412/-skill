import React from 'react';
import {
  AbsoluteFill,
  Img,
  interpolate,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * Explain Layer · 科普段「认知纠偏」动效（2026-10-14 定稿）
 * ============================================================
 * 本层只保留一个组件：Highlight（两行纠偏）。
 *
 * ★ 为什么只留这一个（2026-10-14 灿爹拍板）：
 *   做了 CheckRoll（勾选框滚词）、Highlight（纠偏）、WordPop（逐词弹跳）三个，
 *   出片后灿爹看完说：「练得少 + 微量元素长期没补」这个效果可以留下，
 *   其他的暂时不用。其余效果就此下架。
 *   ⇒ **本层只保留 Highlight**，CheckRoll / WordPop 已移除（2026-10-14）。
 *
 * ★ 为什么它留下来了：
 *   科普带货的核心表达是「**指出对错**」——「练得少真不是原因」「那才是短板」。
 *   这种纠偏语义光靠字幕讲不清，观众听得出转折、看不出错在哪。
 *   划掉 + 荧光笔两个动作能把「错在哪、对在哪」直接标出来。
 *   而且它天然只占**两行**，符合密度铁律（见 references/科普句式动效库.md 第三节）。
 *
 * 典型用法：
 *   练得少          → strike（划掉错误归因）
 *   微量元素长期没补  → marker（点出真正短板）
 *
 * 时序参数来源（GitHub 开源库 shadcn-labs/snapcn 的 text-highlight，6 种强调预设）：
 *   本组件取其中 marker（荧光笔）+ strikethrough（划掉）两种。
 *   ★ 只抄时序，视觉全部按本项目规范重做（PingFang + #D4FF00 + 八向硬描边），
 *     未引入 snapcn 的 Inter / OKLCH 体系。
 */

export const FONT_STACK = '"PingFang SC", "Heiti SC", "Microsoft YaHei", system-ui, sans-serif';
export const CY = '#D4FF00';
export const WHITE = '#FFFFFF';
export const SEAL = '#E8796A';
const INK = '#101010';

const CLAMP = {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'} as const;

/** 八向硬描边 —— 压在动漫底片任何位置都读得出（与 KwOverlay 一致） */
const stroke = (px = 4) => [
  `${-px}px 0 0 ${INK}`, `${px}px 0 0 ${INK}`,
  `0 ${-px}px 0 ${INK}`, `0 ${px}px 0 ${INK}`,
  `${-px}px ${-px}px 0 ${INK}`, `${px}px ${px}px 0 ${INK}`,
  `${-px}px ${px}px 0 ${INK}`, `${px}px ${-px}px 0 ${INK}`,
  `0 0 ${px * 2}px ${INK}`, `0 0 ${px * 4.5}px rgba(0,0,0,0.78)`,
].join(', ');

/* ═══════════════════════════════════════════════════════════
 * Highlight —— 认知纠偏（★本层唯一组件★）
 *    kind: strike     划掉错误说法（默认红 #E8796A）
 *          marker     荧光笔点出正确说法（默认黄 #D4FF00）
 *          underline  下划线强调
 * ═══════════════════════════════════════════════════════════ */
export type MarkKind = 'strike' | 'marker' | 'underline';

export type ExplainLine = {
  text: string;
  kind: MarkKind;
  /** 覆盖默认色（strike 默认砖红、marker 默认深黄） */
  color?: string;
};

export type HighlightProps = {
  /** 每行左上角的 x（中心点），y 为第一行的中心 */
  x: number; y: number;
  lines: ExplainLine[];
  startFrame: number; durFrames: number;
  /** 行间距（像素） */
  lineGap?: number;
  fontSize?: number;
  /** 行间入场错开（帧）—— 拉开间隔让观众逐行读，不要一起糊上来 */
  stagger?: number;
};

export const Highlight: React.FC<HighlightProps> = ({
  x, y, lines, startFrame, durFrames,
  lineGap = 132, fontSize = 62, stagger = 78,
}) => {
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames) return null;

  const op = interpolate(local, [0, 5, durFrames - 10, durFrames], [0, 1, 1, 0], CLAMP);

  return (
    <div style={{position: 'absolute', inset: 0, fontFamily: FONT_STACK, opacity: op}}>
      {lines.map((ln, i) => {
        const lo = local - i * stagger;
        // 标记擦出：错开一点，落在字出现之后
        const wipe = interpolate(lo, [6, 26], [0, 1], CLAMP);
        // 整行入场：从下方轻抬
        const rise = interpolate(lo, [0, 10], [18, 0], CLAMP);
        const col = ln.color ?? (ln.kind === 'strike' ? SEAL : CY);
        return (
          <div key={i} style={{
            position: 'absolute', left: x, top: y + i * lineGap - fontSize * 0.6,
            fontWeight: 800, fontSize, color: WHITE, whiteSpace: 'nowrap',
            textShadow: stroke(5),
            opacity: interpolate(lo, [0, 6], [0, 1], CLAMP),
            transform: `translateY(${rise}px)`,
          }}>
            {ln.text}
            {ln.kind === 'marker' && (
              <span style={{
                position: 'absolute', left: -14, right: -14, bottom: -4, height: '38%',
                background: col, borderRadius: 6, zIndex: -1,
                transform: `scaleX(${wipe})`, transformOrigin: 'left',
              }}/>
            )}
            {ln.kind === 'underline' && (
              <span style={{
                position: 'absolute', left: 0, right: 0, bottom: -14, height: 7,
                background: col, borderRadius: 4,
                transform: `scaleX(${wipe})`, transformOrigin: 'left',
              }}/>
            )}
            {ln.kind === 'strike' && (
              <span style={{
                position: 'absolute', left: -8, right: -8, top: '54%', height: 7,
                background: col, borderRadius: 4,
                transform: `scaleX(${wipe})`, transformOrigin: 'left',
              }}/>
            )}          </div>
        );
      })}
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════
 * 汇总容器 —— 与 VizOverlay 同构，spec 由 make_fx_spec 归并进 explain 数组
 * ═══════════════════════════════════════════════════════════ */
export type ExplainSpec = {
  kind: 'mark';
  x: number; y: number;
  startSec: number; durSec: number;
  lines: ExplainLine[];
  fontSize?: number; lineGap?: number; stagger?: number;
};

export const ExplainOverlay: React.FC<{list?: ExplainSpec[]}> = ({list = []}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  void frame;
  return (
    <AbsoluteFill style={{background: 'transparent'}}>
      {list.map((s, i) => (
        <Highlight
          key={`${s.kind}-${i}`}
          x={s.x} y={s.y} lines={s.lines}
          startFrame={Math.round(s.startSec * fps)}
          durFrames={Math.round(s.durSec * fps)}
          fontSize={s.fontSize ?? 62}
          lineGap={s.lineGap ?? 132}
          stagger={s.stagger ?? 78}
        />
      ))}
    </AbsoluteFill>
  );
};
