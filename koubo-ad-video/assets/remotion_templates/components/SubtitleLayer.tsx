import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * SubtitleLayer —— 字幕逐字弹入 + 重点词联动高亮（v17 新增）
 * ============================================================
 * 为什么要重做字幕：
 *   现有字幕是 ffmpeg 烧的静态 ASS（`subs.ass`），整句一次性淡入，
 *   **关键词和画面上的弹簧重点字是两套独立时间轴** ⇒ 观众眼睛要
 *   在「底部字幕」和「画面中央的重点字」之间来回跳，两者对不上。
 *
 * 本层做法：
 *   ① 整句 spring 弹入（替代 ASS 的 `fad` 线性淡入，有重量感）
 *   ② 重点词（与 KwOverlay 同源同 startSec）**同帧变色 + 轻微放大**
 *      ⇒ 观众看一处就够，字幕和画面重点字在时间上严格咬合
 *
 * ★时间来源：直接读本片 `s4/subs.ass` 的实测时间码，不是估算。
 * ★样式必须与 ASS 版一致（Main: 60px 白字黑边 / 底部居中），
 *   否则叠上去会与原 ASS 字幕重影 —— **本层是替换，不是叠加**。
 */

// v17: headless Chrome 下 loadFont 偶发不 resolve，delayRender 会永久阻塞整片。
// 改为「最多阻塞 3 秒」的看门狗，超时也放行（字体回退系统字体，画面照出）。
// v17: headless Chrome 下 loadFont 的 promise 偶发不 resolve，
// delayRender 会永久阻塞整片渲染。改为完全不等字体：
// 直接用系统黑体（PingFang/Heiti）渲染，画面照常输出。
const FONT_STACK = '"PingFang SC", "Heiti SC", "Microsoft YaHei", sans-serif';
const FONT = '"WenYue XinQingNianTi", "Heiti SC", sans-serif';
const WHITE = '#FFFFFF';
const GOLD = '#FFD400';

/** 一条字幕 */
type LineSpec = {
  startSec: number;
  endSec: number;
  text: string;
  /** 这句里要联动的重点词（必须是 text 的子串） */
  hi?: {word: string; atSec: number};
};

/**
 * ★本片实测时间码（来自 s4/subs.ass，2026-10-08）
 * ★重点词的 atSec 与 KwOverlay 的 startSec 严格一致 ⇒ 音画咬合
 */
export const LINES: LineSpec[] = [
  {startSec: 0.05, endSec: 3.31, text: '练薄肌的成本已经来到3个月19块9，今天我们讲',
   hi: {word: '19块9', atSec: 1.55}},
  {startSec: 3.31, endSec: 3.72, text: '肌酸'},
  {startSec: 3.72, endSec: 5.83, text: '肌酸算A类补剂里性价比高的那一档'},
  {startSec: 5.83, endSec: 7.42, text: '含金量确实不用多说'},
  {startSec: 7.42, endSec: 8.70, text: '但是买肌酸有很多坑'},
  {startSec: 8.70, endSec: 11.12, text: '很多牌子的报告只让你看含量达到4个9',
   hi: {word: '4个9', atSec: 9.93}},
  {startSec: 11.12, endSec: 13.92, text: '重金属微生物超不超标，你完全不知道下落',
   hi: {word: '超不超标', atSec: 11.50}},
  {startSec: 13.92, endSec: 17.27, text: '缘由也简单，这两项加上去，检测费立刻得抬一截',
   hi: {word: '检测费', atSec: 14.93}},
  {startSec: 17.27, endSec: 18.80, text: '爱兰纯这罐粉剂是另一回事'},
  {startSec: 18.80, endSec: 21.67, text: '出场前一批批先过检测，单子随货寄到你手上',
   hi: {word: '一批批', atSec: 18.76}},
  {startSec: 21.67, endSec: 22.52, text: '不藏着，喝着踏实'},
  {startSec: 22.52, endSec: 24.42, text: '关键19块9够你喝三个月',
   hi: {word: '三个月', atSec: 23.22}},
  {startSec: 24.42, endSec: 26.82, text: '这就是为什么它能卖出50多万单的这个销量',
   hi: {word: '50多万单', atSec: 25.02}},
  {startSec: 26.82, endSec: 28.39, text: '这配置与保障，可以随意对比',
   hi: {word: '随意对比', atSec: 27.48}},
  {startSec: 28.39, endSec: 30.92, text: '反正天天喝，趁现在有保障，有活动'},
  {startSec: 30.92, endSec: 32.56, text: '左下角链接直接存起来'},
];

const SubLine: React.FC<{spec: LineSpec}> = ({spec}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const sf = Math.round(spec.startSec * fps);
  const ef = Math.round(spec.endSec * fps);
  const local = frame - sf;
  if (local < 0 || local > ef - sf) return null;

  // 整句弹入：spring 从下方顶上来
  const rise = 26;
  const p = spring({
    frame: local,
    fps,
    config: {damping: 15, stiffness: 190, mass: 0.7},
    durationInFrames: Math.round(0.34 * fps),
  });
  const y = (1 - p) * rise;

  // 淡入 4 帧、淡出 6 帧（与 ASS 的 fad(120,180) 视觉等效但带弹簧）
  const op = interpolate(
    local,
    [0, 4, Math.max(5, ef - sf - 6), ef - sf],
    [0, 1, 1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}
  );

  // 重点词：到点后弹一下 + 变金
  const hiT = spec.hi ? (frame - Math.round(spec.hi.atSec * fps)) / fps : -1;
  const hiOn = spec.hi && hiT >= 0 && hiT < 2.2;
  const hiP = hiOn
    ? spring({
        frame: Math.max(0, Math.round(hiT * fps)),
        fps,
        config: {damping: 8, stiffness: 300, mass: 0.55},
        durationInFrames: Math.round(0.3 * fps),
      })
    : 0;
  const hiScale = 1 + 0.22 * hiP;
  const hiColor = hiOn ? GOLD : WHITE;

  // 把重点词切成前后两段
  let pre = spec.text;
  let post = '';
  if (spec.hi) {
    const i = spec.text.indexOf(spec.hi.word);
    if (i >= 0) {
      pre = spec.text.slice(0, i);
      post = spec.text.slice(i + spec.hi.word.length);
    }
  }

  const textShadow = `-3px 0 0 #101010, 3px 0 0 #101010, 0 -3px 0 #101010, 0 3px 0 #101010, 0 0 7px rgba(0,0,0,0.95)`;

  return (
    <div style={{
      position: 'absolute',
      left: 0,
      right: 0,
      bottom: 58,                    // 与 ASS MarginV 58 对齐
      display: 'flex',
      justifyContent: 'center',
      opacity: op,
      transform: `translateY(${y}px)`,
    }}>
      <div style={{
        fontFamily: FONT,
        fontSize: 60,
        fontWeight: 800,
        color: WHITE,
        letterSpacing: 1,
        lineHeight: 1.25,
        maxWidth: 1800,              // 1920 − 左右各 60，与 ASS 一致
        textAlign: 'center',
        textShadow,
        whiteSpace: 'nowrap',
      }}>
        {pre}
        {spec.hi && (
          <span style={{
            color: hiColor,
            display: 'inline-block',
            transform: `scale(${hiScale})`,
            transformOrigin: 'center bottom',
            textShadow: hiOn
              ? `0 0 22px rgba(255,212,0,0.75), ${textShadow}`
              : textShadow,
          }}>{spec.hi.word}</span>
        )}
        {post}
      </div>
    </div>
  );
};

export const SubtitleLayer: React.FC = () => (
  <AbsoluteFill style={{background: 'transparent'}}>
    {LINES.map((l, i) => (
      <SubLine key={`s-${i}`} spec={l} />
    ))}
  </AbsoluteFill>
);
