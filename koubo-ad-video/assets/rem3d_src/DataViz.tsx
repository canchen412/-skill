import React from 'react';
import {
  AbsoluteFill,
  Easing,
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
 * ★ 2026-10-08：移除模块级 loadFont——本机 headless Chrome 的 FontFace
 *   加载（网络与 data URI）全部悬死；新青年体已装进系统（~/Library/Fonts），
 *   Chrome 原生识别，无需 loadFont。
 */

// ★2026-10-18：数字层优先系统黑体 —— ① 数字配黑体更清晰、更「仪表盘」；
//   ② headless 下 FontFace.load 偶发永久 pending，系统字体保证永不空字。
const FONT = '"ALC Sans Medium", "Heiti SC", sans-serif';
// ★2026-10-22 灿爹拍板「宋体好」：卡片标题走宋体（横细竖粗、有衬角），
//   行名/标签/数字仍走黑体 —— 小字宋体会被视频压缩糊掉，只放标题级。
const FONT_TITLE = '"ALC Serif Heavy", "Songti SC", serif';
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
  barWidth?: number; color?: string; fontSize?: number;
  /**
   * ★2026-10-18 灿爹定式：「动效组件不要遮住主体，在旁边起说明补充作用」。
   *   数据标签被迫落在**亮背景**（窗光 / 白墙 / 浅色货架）时，裸黄字对比不足会发糊；
   *   开 plate 铺一层半透明暗板（与 CompareBar 的暗板同一套语言），保证可读且不抢主体。
   *   默认关 —— 落在暗背景上时裸字更透气，老片行为完全不变。
   */
  plate?: boolean;
}> = ({x, y, target, unit, label, startFrame, durFrames, barWidth = 420, color = BLUE, fontSize = 108, plate = false}) => {
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
  const decimals = Number.isInteger(target) ? 0 : (String(target).split('.')[1] || '').length;
  const shown = (target * Math.min(1, p * 1.08)).toFixed(decimals);

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
      ...(plate ? {
        padding: '20px 12px 18px', boxSizing: 'border-box' as const,
        borderRadius: 18, background: 'rgba(8,11,16,0.52)',
        border: '1.5px solid rgba(255,255,255,0.15)',
        backdropFilter: 'blur(6px)',
        boxShadow: '0 10px 40px rgba(0,0,0,0.35)',
      } : {}),
    }}>
      <div style={{fontFamily: FONT, fontWeight: 800, fontSize, color, lineHeight: 1,
        textShadow: `0 0 30px ${color}55, -4px 0 0 #101010, 4px 0 0 #101010, 0 -4px 0 #101010, 0 4px 0 #101010`,
      }}>
        {shown}
        <span style={{fontSize: Math.round(fontSize * 0.48), marginLeft: 6}}>{unit}</span>
      </div>
      <div style={{fontFamily: FONT, fontSize: Math.round(fontSize * 0.37), color: WHITE, letterSpacing: 4, marginTop: 6}}>{label}</div>
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
  title?: string;
  startFrame: number; durFrames: number;
  barWidth?: number;
}> = ({x, y, rows, title, startFrame, durFrames, barWidth = 560}) => {
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
        {title ?? '数据对比'}
      </div>
      {rows.map((r, i) => {
        // 每行错开 5 帧 ⇒ 从上到下依次生长，像「依次揭示数据」
        const rp = spring({
          frame: Math.max(0, local - i * 5), fps,
          config: {damping: 17, stiffness: 130, mass: 1.0},
          durationInFrames: durFrames,
        });
        const w = barWidth * r.v * Math.min(1, rp * 1.05);
        // ★行 suffix（2026-10-13）：配方/含量类数值不是倍数，suffix 直显原文；
        //   不给 suffix 沿用旧语义「N.N×」（对比倍数）
        const val = (r as {suffix?: string}).suffix ?? `${r.v.toFixed(1)}×`;
        return (
          <div key={r.name} style={{marginBottom: 16}}>
            <div style={{display: 'flex', justifyContent: 'space-between',
              fontFamily: FONT, fontSize: 26, color: 'rgba(255,255,255,0.9)', marginBottom: 6}}>
              <span>{r.name}</span>
              <span style={{color: r.color}}>{val}</span>
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
 * 3b. 增减条 ShiftBar（2026-10-21 灿爹点名）
 *     「掉肌肉和掉脂肪这种，用动效层的数字滚动或者进度条来显示，更有高级感」。
 *     语义：条从 from 高度**缩/长到** to（CompareBar 是 0→v 的生长，方向相反）。
 *     刻意不标百分比 —— 这些是示意值不是实测，只给「掉光了 / 几乎不动 / 还在涨」
 *     这类标签，既不编数据又把方向讲清楚。
 *
 * ★★ 2026-10-21 灿爹二改（横条 → 竖条）：
 *     「做成竖条，因为竖条就是掉和增，它看起来就更好，体重掉和增看起来就更好」
 *     「进度条可以慢一点点，反正在这个分镜还没切到下个分镜之前掉到最底部就行了」
 *   ⇒ ① 竖排双柱（横向排列、纵向伸缩），掉 = 液面下沉、增 = 液面上涨，物理直觉一致；
 *      ② 缓动拉长到「几乎铺满整镜」：travel = durFrames - 0.30s，末尾留 0.3s 停稳再看一眼；
 *      ③ 缓动曲线 bezier(0.16,0.6,0.24,1)：起步不肉、越掉越慢、最后稳稳停住，
 *         比 spring 更像「真的在掉」而不是「弹簧弹一下」；
 *      ④ 起点处留一道 2px 刻度线，让「掉了多少」有参照，又不用写数字。
 * ═══════════════════════════════════════════════════════════ */
export const ShiftBar: React.FC<{
  x: number; y: number;
  rows: {name: string; from: number; to: number; color: string; tag?: string}[];
  title?: string;
  startFrame: number; durFrames: number;
  barWidth?: number;
}> = ({x, y, rows, title, startFrame, durFrames, barWidth = 440}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames + Math.round(0.4 * fps)) return null;

  const fadeIn = interpolate(local, [0, 6], [0, 1],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  // 淡出窗口压到 3 帧：动画在 durFrames-0.30s 就到底了，留 6 帧「停稳给人看」再快速淡掉
  // （旧版 6 帧淡出会把停稳态吃掉一半，只剩 3 帧，等于没停住）
  const fadeOut = interpolate(local, [durFrames - 5, durFrames + 1], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  // 归一化基准：允许「上涨」超过 100（增肌），超出的按同比例画
  const base = Math.max(100, ...rows.map((r) => Math.max(r.from, r.to)));

  // ★2026-10-22 灿爹「表格做大一点，才看得清肌肉和脂肪的字」⇒ 整体放大约 1.45 倍：
  //   柱 84×178 → 124×252，标题 31→44、行名 25→35、状态标签 24→34。卡片总尺寸约 491×485。
  const COL_W = 124;
  const COL_H = 252;
  const GAP = 68;
  // ★2026-10-22 灿爹「停留时间太短来不及看清」⇒ 掉到底后的静止段 0.30s → 0.45s，
  //   淡出也从 3 帧放宽到 5 帧（更柔，别「啪」一下没）。
  const travel = Math.max(12, durFrames - Math.round(0.45 * fps));

  return (
    <div style={{
      position: 'absolute', left: x, top: y,
      transform: 'translate(-50%,-50%)', opacity: fadeIn * fadeOut,
      width: barWidth,
      background: 'rgba(8,11,16,0.68)',
      border: '1.5px solid rgba(255,255,255,0.16)',
      borderRadius: 20, padding: '28px 24px 26px',
      backdropFilter: 'blur(6px)',
      boxShadow: '0 14px 46px rgba(0,0,0,0.42)',
    }}>
      {title && (
        <div style={{fontFamily: FONT_TITLE, fontSize: 44, color: WHITE, letterSpacing: 2,
          marginBottom: 20, textAlign: 'center', whiteSpace: 'nowrap'}}>
          {title}
        </div>
      )}
      <div style={{display: 'flex', justifyContent: 'center', alignItems: 'flex-end', gap: GAP}}>
        {rows.map((r, i) => {
          // ★曲线：中段近似匀速、两端收软（首版用 0.16/0.6 太前倾 —— 0.9s 就掉完，
          //   后面干等，看着反而急。灿爹要「慢一点点」，所以让液面均匀往下走）
          const w = interpolate(local - i * 5, [0, travel], [0, 1], {
            extrapolateLeft: 'clamp', extrapolateRight: 'clamp',
            easing: Easing.bezier(0.4, 0.0, 0.2, 1),
          });
          const v = r.from + (r.to - r.from) * w;
          const h = Math.max(0, COL_H * (v / base));
          const fromH = Math.max(0, Math.min(COL_H, COL_H * (r.from / base)));
          return (
            <div key={r.name} style={{display: 'flex', flexDirection: 'column', alignItems: 'center'}}>
              <div style={{fontFamily: FONT, fontSize: 35, color: 'rgba(255,255,255,0.96)',
                marginBottom: 11, letterSpacing: 2, whiteSpace: 'nowrap'}}>
                {r.name}
              </div>
              <div style={{
                position: 'relative', width: COL_W, height: COL_H, borderRadius: 15,
                background: 'rgba(255,255,255,0.10)',
                border: '1.5px solid rgba(255,255,255,0.14)', overflow: 'hidden',
              }}>
                {/* 起点刻度：不动，只当「掉了多少」的参照，不写数字 */}
                {Math.abs(r.from - r.to) > 6 && (
                  <div style={{
                    position: 'absolute', left: 0, right: 0, bottom: fromH, height: 2,
                    background: 'rgba(255,255,255,0.32)',
                  }} />
                )}
                <div style={{
                  position: 'absolute', left: 0, right: 0, bottom: 0, height: h,
                  background: `linear-gradient(0deg, ${r.color}, ${r.color}CC)`,
                  boxShadow: `0 0 22px ${r.color}77`,
                }} />
              </div>
              {r.tag && (
                <div style={{fontFamily: FONT, fontSize: 34, color: r.color, marginTop: 12,
                  letterSpacing: 1, whiteSpace: 'nowrap'}}>
                  {r.tag}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

/* ═══════════════════════════════════════════════════════════
 * 4. 价格锤 PriceSlam —— ★2026-10-14 灿爹判定「很 low」，已删除。
 *    原：26 根程序化放射线随帧旋转 + spring 砸落 + 落地 sin×指数衰减震屏。
 *    价格镜改用 KwOverlay 的 price 变体（重点字 + 中段复顶），
 *    需要更重的视觉时用 StampSeal 或 ffmpeg 侧的价格大字版。
 *    已同步移除：VizSpec.kind 的 'slam'、VizOverlay 分发分支、
 *    make_fx_spec.py 的 slams 锚定、.fx.json 的 slams 数组。
 * ═══════════════════════════════════════════════════════════ */

/* ═══════════════════════════════════════════════════════════
 * 5. 盖章 StampSeal（灿爹样片带选型 3 号，收编进库）
 *    红框章体从 2.6 倍旋转砸下 → 1 倍落定，认证/保障语义。
 * ═══════════════════════════════════════════════════════════ */
export const StampSeal: React.FC<{
  x: number; y: number; text: string;
  startFrame: number; durFrames: number;
  color?: string; fontSize?: number; rotate?: number;
}> = ({x, y, text, startFrame, durFrames, color = '#E8796A', fontSize = 130, rotate = -12}) => {
  const {fps} = useVideoConfig();
  const frame = useCurrentFrame();
  const local = frame - startFrame;
  if (local < 0 || local > durFrames) return null;

  const s = spring({frame: local, fps,
    config: {damping: 10, stiffness: 200, mass: 0.6},
    durationInFrames: Math.round(0.45 * fps)});
  const o = interpolate(local, [0, 6], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const fadeOut = interpolate(local, [durFrames - 6, durFrames], [1, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const drop = interpolate(local, [0, 10], [-70, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  return (
    <div style={{
      position: 'absolute', left: x, top: y, opacity: o * fadeOut,
      transform: `translate(-50%,-50%) translateY(${drop}px) scale(${interpolate(s, [0, 1], [2.6, 1])}) rotate(${rotate}deg)`,
      border: `14px solid ${color}`, borderRadius: 26, padding: '26px 64px',
      background: `${color}24`, fontFamily: FONT, fontWeight: 800,
      fontSize, color, whiteSpace: 'nowrap', lineHeight: 1,
      boxShadow: '0 12px 60px rgba(0,0,0,0.4)',
    }}>
      {text}
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
  kind: 'ring' | 'count' | 'bar' | 'stamp' | 'shift';
  /** 组件中心点 x / y（1920×1080 画布内的绝对像素） */
  x: number; y: number;
  /** 停留时长（秒）——淡出窗口是尾 0.17s，太短（<1.2s）会看不到落定态 */
  startSec: number; durSec: number;

  /* ring 专用 */
  r?: number; value?: number; label?: string; sub?: string; color?: string;
  /* count 专用 */
  target?: number; unit?: string; barWidth?: number;
  /* count 专用：亮背景上是否铺半透明暗板（见 CountUp 注释） */
  plate?: boolean;
  /* count 字号（108 默认；样片带 240 大字版传 240） */
  fontSize?: number;
  /* bar 专用 */
  rows?: {name: string; v: number; color: string}[];
  /** bar 标题（★ v22 合规：禁「吸收/效果/提升/见效/优于」，用中性词如「优惠对比」） */
  title?: string;
  /* stamp 专用：章体文字 */
  text?: string;
  /* stamp 章体倾角（默认 -12deg） */
  rotate?: number;
  /* shift 专用：增减条（from→to，不标数字，只给 tag 标签） */
  shiftRows?: {name: string; from: number; to: number; color: string; tag?: string}[];
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
            color={s.color} fontSize={s.fontSize} plate={s.plate} />;
        }
        if (s.kind === 'stamp') {
          return <StampSeal key={key} x={s.x} y={s.y} text={s.text!}
            startFrame={sf} durFrames={df} color={s.color} fontSize={s.fontSize} rotate={s.rotate} />;
        }
        if (s.kind === 'shift') {
          return <ShiftBar key={key} x={s.x} y={s.y} rows={s.shiftRows!} title={s.title}
            startFrame={sf} durFrames={df} barWidth={s.barWidth} />;
        }
        return <CompareBar key={key} x={s.x} y={s.y} rows={s.rows!}
          startFrame={sf} durFrames={df} barWidth={s.barWidth} title={s.title} />;
      })}
    </AbsoluteFill>
  );
};