import React from 'react';
import {
  AbsoluteFill,
  interpolate,
  spring,
  useCurrentFrame,
  useVideoConfig,
} from 'remotion';

/**
 * 科普数据可视化层（DataViz Overlay）
 * ============================================================
 * 为什么单独做这个：
 *   科普带货的**核心是「用数据说服人」**，而现在片子里数据全是「嘴上说」
 *   ——「纯度高达98%」只是一句字幕 + 一个弹出的重点字，观众没有直观感受。
 *   ffmpeg 的 ASS 做不到：数字滚动、进度环、递增动画全是逐帧计算。
 *   Remotion 可以，而且 spring + interpolate 天然适合做缓动。
 *
 * 三种可视化组件（按数据类型分）：
 *   RingGauge  环形进度 —— 百分比类（98% 纯度）
 *   CountUp    数字滚动 —— 含量类（300mg / 10mg）
 *   CompareBar 对比条—— 「吸收不一样」这类对比
 *
 * 所有组件输出在 1920×1080 透明层，由 ffmpeg overlay 到正片。
 */

// v17: headless Chrome 下 loadFont 偶发不 resolve，delayRender 会永久阻塞整片。
// 改为「最多阻塞 3 秒」的看门狗，超时也放行（字体回退系统字体，画面照出）。
// v17: headless Chrome 下 loadFont 的 promise 偶发不 resolve，
// delayRender 会永久阻塞整片渲染。改为完全不等字体：
// 直接用系统黑体（PingFang/Heiti）渲染，画面照常输出。
const FONT_STACK = '"PingFang SC", "Heiti SC", "Microsoft YaHei", sans-serif';
const FONT = FONT_STACK;
const CY = '#D4FF00';      // 深黄主色（与成片重点字一致）
const BLUE = '#37A8FF';
const WHITE = '#FFFFFF';

/* ═══════════════════════════════════════════════════════════
 * 1. 环形进度：纯度 98%
 * ═══════════════════════════════════════════════════════════ */
export const RingGauge: React.FC<{
  cx: number; cy: number; r: number;
  value: number;          // 目标值 0~1
  label: string; sub: string;
  startFrame: number; durFrames: number;
  color?: string;
}> = ({cx, cy, r, value, label, sub, startFrame, durFrames, color = CY}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames + Math.round(0.5 * fps)) return null;

  // 进度：spring 推进（不是线性 ⇒ 有「冲上去再稳住」的劲）
  const p = spring({
    frame: local,
    fps,
    config: {damping: 16, stiffness: 120, mass: 1.0},
    durationInFrames: durFrames,
  });
  const shown = value * Math.min(1, p * 1.06);

  // 圆环：dasharray 做进度
  const C = 2 * Math.PI * r;
  const dash = C * shown;

  // 中心数字：★与环**同步起跳**，靠 damping 差制造「数字追着环跑」的追赶感。
  //   （首版让数字延迟 42% 时长再起跳 ⇒ 前 2/3 时间数字都是 0，观感是「卡住不动」，
  //     数字仿佛坏了。damping 更大 = 更平稳无过冲，环先冲到 97% 时数字才 80%，
  //     视觉上有先后层次，但全程都在动。）
  const nP = spring({
    frame: local,
    fps,
    config: {damping: 22, stiffness: 190, mass: 1.15},
    durationInFrames: Math.max(8, Math.round(durFrames * 1.05)),
  });
  const shownNum = Math.round(value * 100 * Math.min(1, nP * 1.02));

  const fadeOut = interpolate(local, [durFrames - 6, durFrames + 4], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  // 入场缩放
  const sc = 0.72 + 0.28 * spring({
    frame: local, fps,
    config: {damping: 14, stiffness: 190, mass: 0.8},
    durationInFrames: Math.round(0.4 * fps),
  });

  return (
    <div style={{
      position: 'absolute', left: cx, top: cy,
      transform: `translate(-50%,-50%) scale(${sc})`,
      opacity: fadeOut,
      width: r * 2.6, height: r * 2.6,
      display: 'flex', alignItems: 'center', justifyContent: 'center',
    }}>
      <svg width={r * 2.6} height={r * 2.6} style={{position: 'absolute', left: 0, top: 0, overflow: 'visible'}}>
        <circle cx={r * 1.3} cy={r * 1.3} r={r} fill="none"
          stroke="rgba(255,255,255,0.13)" strokeWidth={r * 0.135} />
        <circle cx={r * 1.3} cy={r * 1.3} r={r} fill="none"
          stroke={color} strokeWidth={r * 0.135} strokeLinecap="round"
          strokeDasharray={`${dash} ${C}`}
          transform={`rotate(-90 ${r * 1.3} ${r * 1.3})`}
          style={{filter: `drop-shadow(0 0 ${r * 0.16}px ${color}88)`}} />
      </svg>
      <div style={{position: 'relative', textAlign: 'center', lineHeight: 1}}>
        <div style={{
          fontFamily: FONT, fontWeight: 800, fontSize: r * 0.62,
          color, letterSpacing: -2,
          textShadow: `0 0 ${r * 0.2}px ${color}66, -3px 0 0 #101010, 3px 0 0 #101010, 0 -3px 0 #101010, 0 3px 0 #101010`,
        }}>{shownNum}<span style={{fontSize: r * 0.34}}>%</span></div>
        <div style={{
          fontFamily: FONT, fontSize: r * 0.2, color: WHITE, marginTop: r * 0.08,
          letterSpacing: 3, opacity: 0.95,
        }}>{label}</div>
        <div style={{
          fontFamily: FONT, fontSize: r * 0.155, color: 'rgba(255,255,255,0.62)', marginTop: r * 0.04,
        }}>{sub}</div>
      </div>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════
 * 2. 数字滚动 + 计量条：300mg 含量
 * ═══════════════════════════════════════════════════════════ */
export const CountUp: React.FC<{
  x: number; y: number;
  target: number; unit: string; label: string;
  startFrame: number; durFrames: number;
  barWidth?: number; color?: string;
  /** ★v17 新增：小数位数。0（默认）= 整数跳数；2 = 显示 99.99 / 0.22 */
  decimals?: number;
}> = ({x, y, target, unit, label, startFrame, durFrames, barWidth = 420, color = BLUE, decimals = 0}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames + Math.round(0.4 * fps)) return null;

  // 数字用整数滚动（逐帧跳数，像老式计数器），比 spring 更「数据感」
  const p = spring({
    frame: local, fps,
    config: {damping: 18, stiffness: 150, mass: 0.9},
    durationInFrames: durFrames,
  });
  // ★v17 修：原来无条件 Math.round，把 99.99 舍成 100 ——
  //   「4个9」是本片的卖点数字，滚到 100% 等于自己打自己的脸。
  //   改：有 decimals 就走 toFixed（99.99%），没有才取整（500000 单）。
  const _raw = target * Math.min(1, p * 1.08);
  const shown = decimals > 0 ? _raw.toFixed(decimals) : String(Math.round(_raw));

  const fadeOut = interpolate(local, [durFrames - 5, durFrames + 3], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const sc = 0.86 + 0.14 * spring({
    frame: local, fps,
    config: {damping: 15, stiffness: 200, mass: 0.7},
    durationInFrames: Math.round(0.3 * fps),
  });
  const bw = barWidth * Math.min(1, p * 1.05);

  return (
    <div style={{
      position: 'absolute', left: x, top: y,
      transform: `translate(-50%,-50%) scale(${sc})`,
      opacity: fadeOut, textAlign: 'center', width: barWidth * 1.5,
    }}>
      <div style={{fontFamily: FONT, fontWeight: 800, fontSize: 108, color, lineHeight: 1,
        textShadow: `0 0 30px ${color}55, -4px 0 0 #101010, 4px 0 0 #101010, 0 -4px 0 #101010, 0 4px 0 #101010`,
      }}>
        {shown}
        <span style={{fontSize: 52, marginLeft: 6}}>{unit}</span>
      </div>
      <div style={{fontFamily: FONT, fontSize: 40, color: WHITE, letterSpacing: 4, marginTop: 6}}>{label}</div>
      {/* 计量条：底槽 + 填充 */}
      <div style={{
        marginTop: 16, width: barWidth, height: 14, borderRadius: 7,
        background: 'rgba(255,255,255,0.16)', overflow: 'hidden', marginLeft: 'auto', marginRight: 'auto',
      }}>
        <div style={{
          width: bw, height: '100%', borderRadius: 7,
          background: `linear-gradient(90deg, ${color}, ${color}cc)`,
          boxShadow: `0 0 18px ${color}88`,
        }} />
      </div>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════
 * 3. 对比条：「吸收起来确实不一样」—— 姜黄素 vs 空瓶吸收曲线
 * ═══════════════════════════════════════════════════════════ */
export const CompareBar: React.FC<{
  x: number; y: number;
  rows: {name: string; v: number; color: string}[];
  startFrame: number; durFrames: number;
  barWidth?: number;
}> = ({x, y, rows, startFrame, durFrames, barWidth = 560}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames + Math.round(0.4 * fps)) return null;

  const fadeOut = interpolate(local, [durFrames - 5, durFrames + 3], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <div style={{
      position: 'absolute', left: x, top: y,
      transform: 'translate(-50%,-50%)', opacity: fadeOut,
      width: barWidth * 1.25,
      background: 'rgba(8,11,16,0.62)',
      border: '1.5px solid rgba(255,255,255,0.16)',
      borderRadius: 18, padding: '26px 30px',
      backdropFilter: 'blur(6px)',
    }}>
      <div style={{fontFamily: FONT, fontSize: 34, color: WHITE, letterSpacing: 3, marginBottom: 20}}>
        {/* ★v22 合规修正：原文写「吸收对比」——
           「吸收」是功效暗示，属带货红线，平台合规风险高。
           改为中性的「成分对比」——只陈述客观参数，不宣称效果。
           ⚠️ 任何对比类组件的标题都必须过这道：禁用吸收/效果/提升/见效。*/}
        成分对比
      </div>
      {rows.map((r, i) => {
        // 每行错开 5 帧 ⇒ 从上到下依次生长，像「依次揭示数据」
        const rp = spring({
          frame: Math.max(0, local - i * 5), fps,
          config: {damping: 17, stiffness: 130, mass: 1.0},
          durationInFrames: durFrames,
        });
        const w = barWidth * r.v * Math.min(1, rp * 1.05);
        return (
          <div key={r.name} style={{marginBottom: 16}}>
            <div style={{display: 'flex', justifyContent: 'space-between',
              fontFamily: FONT, fontSize: 26, color: 'rgba(255,255,255,0.9)', marginBottom: 6}}>
              <span>{r.name}</span>
              <span style={{color: r.color}}>
                {/* v17 修：原硬编码 toFixed(1) 把 0.9999 显示成 1.0，
                    跟左边「含量 100%」自相矛盾。按值域自动选精度。*/}
                {r.v >= 0.999 ? (r.v * 100).toFixed(2) : (r.v * 100).toFixed(1)}%
              </span>
            </div>
            <div style={{width: barWidth, height: 24, borderRadius: 12,
              background: 'rgba(255,255,255,0.12)', overflow: 'hidden'}}>
              <div style={{width: w, height: '100%', borderRadius: 12,
                background: `linear-gradient(90deg, ${r.color}, ${r.color}88)`,
                boxShadow: `0 0 16px ${r.color}66`}} />
            </div>
          </div>
        );
      })}
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════
 * 汇总容器
 * ═══════════════════════════════════════════════════════════
 * ★★ 坐标字段名的坑（踩过）：ring 用 cx/cy，而 count/bar 用 x/y。
 *   三种字段名不统一 ⇒ 写 spec 时把 cy 传给 count/bar 会**静默失效**
 *   （s.y 为 undefined ⇒ CSS top:undefined ⇒ 元素掉到文档流顶部 0 处，
 *    渲染成功、不报错，只有抽帧才看出来 —— 探针页三种都用对字段名才一次过）。
 *   ⇒ 统一成 x/y（中心点），ring 也改读 s.x/s.y。
 */
export type VizSpec = {
  kind: 'ring' | 'count' | 'bar';
  /** 组件中心点 x / y（1920×1080 画布内的绝对像素） */
  x: number; y: number;
  /** 停留时长（秒）——淡出窗口是尾 0.17s，太短（<1.2s）会看不到落定态 */
  startSec: number; durSec: number;

  /* ring 专用 */
  r?: number; value?: number; label?: string; sub?: string; color?: string;
  /* count 专用 */
  target?: number; unit?: string; barWidth?: number;
  /** ★v17 新增：小数位数（99.99 / 0.22 需要 2；500000 单用 0） */
  decimals?: number;
  /* bar 专用 */
  rows?: {name: string; v: number; color: string}[];
};

export const VizOverlay: React.FC<{list?: VizSpec[]}> = ({list = []}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  void frame;
  return (
    <AbsoluteFill style={{background: 'transparent'}}>
      {list.map((s, i) => {
        const sf = Math.round(s.startSec * fps);
        const df = Math.round(s.durSec * fps);
        const key = `${s.kind}-${i}`;
        if (s.kind === 'ring') {
          return <RingGauge key={key} cx={s.x} cy={s.y} r={s.r!} value={s.value!}
            label={s.label!} sub={s.sub!} startFrame={sf} durFrames={df} color={s.color} />;
        }
        if (s.kind === 'count') {
          return <CountUp key={key} x={s.x} y={s.y} target={s.target!} unit={s.unit!}
            label={s.label!} startFrame={sf} durFrames={df} barWidth={s.barWidth}
            color={s.color} decimals={s.decimals} />;
        }
        return <CompareBar key={key} x={s.x} y={s.y} rows={s.rows!}
          startFrame={sf} durFrames={df} barWidth={s.barWidth} />;
      })}
    </AbsoluteFill>
  );
};