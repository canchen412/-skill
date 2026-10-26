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
import {loadFont} from '@remotion/fonts';
import {delayRender, continueRender} from 'remotion';

/**
 * 卷播 · 鼠标点击涟漪版（MarqueeClick）
 * ============================================================
 * 爱兰纯 v12 时序（★照本片实测配音时间轴重锚，不是照抄姜黄素版）：
 *   0.00-2.75s  卷播滚动（指数减速），说到「今天我们讲」时**已经停住**
 *   2.75-3.13s  鼠标光标淡入（从右下划入）→ 移到主图中心
 *   3.13s       ★点击瞬间：光标按下 + 以点击点为中心的涟漪扩散
 *   3.13-3.97s  主图动态调大（弹簧放大到 2.76×，铺满全屏）
 *   3.97-4.40s  「肌酸」说完，涟漪收尾，画面保持放大 ⇒ 直接接镜2 黑板图
 *
 * ★ 为什么鼠标要在 3.13s 点：配音「今天我们讲」在 2.80-3.46s（token 段5尾~段6），
 *   点击落在 3.13s 正好卡在这句中间 ⇒ 听到「今天我们讲」看到鼠标点下去，
 *   「肌酸」（3.46-3.61s）时主图正在放大 —— 音画严格对齐。
 *
 * ★ 没有白框（灿爹要求去掉）。视觉焦点靠三样东西：
 *   ① 涟漪扩散（明确告诉观众「点了这里」）
 *   ② 主图放大（明确告诉观众「看这张」）
 *   ③ 侧光压暗（周围卡变暗，主图最亮）
 */

/** ★本次作为「镜1 完整画面」直接渲染 mp4（不走 overlay 叠层）
 *  ⇒ 必须不透明黑底；若改回叠层模式要改回 'transparent'
 */
const TRANSPARENT_BG = '#000000';

const handle = delayRender('加载新青年体');
loadFont({family: 'WenYue XinQingNianTi', url: staticFile('新青年体.ttf'), weight: '800'})
  .then(() => continueRender(handle))
  .catch((e) => {
    console.error('字体加载失败：', e);
    continueRender(handle);
  });

/* ═══ 时序（全部锚在配音实测时间上，别改） ═══ */
export const T = {
  scrollEnd: 1.40,    // ★卷播停住（s2/s3 钩子 0.05-2.35，重音「建模」开口前停）
  cursorIn: 1.40,     // 鼠标开始淡入
  clickAt: 1.80,      // ★点击瞬间（钩子句「建模」重音处）
  zoomDur: 0.55,      // 主图放大时长 ⇒ 2.35s 放大完成，定格 0.40s 再切
  totalS: 2.75,       // ★=拼接切点（语音缝 2.35-2.75 尾=镜2 语音起点，正片首镜音画全同步）
  focusAt: 1.40,      // 兼容旧字段
};

export const M = {
  cardW: 700,// ★16:9 横卡（灿爹拍板：轮播卡与主图统一 16:9）
  cardH: 394,          // 700 / (16/9) = 393.75 ⇒ 394
  pitch: 860,          // 卡宽 + 160 间隙
  cy: 520,            // 卡列中心 y
  maxY: 18,           // 边缘 rotateY（对标参考视频实测）
  perspective: 1500,
  nScroll: 7,         // 滚动卡数（滚动段 2.75s，比姜黄素版多一张）
  K_DECAY: 3.0,
};

export type CardSpec = {src: string};

/* ═══ Hero 三层卡（2026-10-09 灿爹拍板定稿）：底图 / 滑入字层 / 人物前景 ═══
 * 主图 zoom 后 0.30s 字「插进来」：从右快进慢停滑入 + 景深 1.35→1 收缩；
 * 人物 fg 遮字 → 3D 空间感（头只遮中字下缘一小块）。
 * ★时机约束：正式切点=钩子尾+0.40，zoom 窗共 0.95s——滑入并行于 zoom 尾段完成。
 * 依赖分层图 cards_dir/hero_bg.jpg + hero_fg.png（make_cutout 拆层）；
 * 未传 heroLabel 时退回普通 Card（老片子不受影响）。
 * ★2026-10-15 灿爹（烟酰胺21金维他 v1）：「烟酰胺三个大字要在人物后面，被遮挡一部分，
 *   形成 3D 景深；正中间，头部遮挡一小部分」⇒ ① labelTop/labelSize 参数化（config
 *   hero_label_top / hero_label_size 注入，默认为老片兼容值 16/132）；② 字加向下
 *   3 层暗金堆叠 = 挤出厚度，强化立体感。落位用 hero_fg.png 蒙版逐参扫描选定
 *   （size=100/top=16：字可见 67.8%，遮挡中 51.6% 来自头部，最贴合「头部遮挡一小部分」）。 */
const SlideLabel: React.FC<{clickF: number; labelTop: number; labelSize: number; text: string}> =
  ({clickF, labelTop, labelSize, text}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  const local = frame - (clickF + Math.round(0.30 * fps));   // ★zoom 中段起滑（帧制变量不加小数秒！）
  if (local < 0) return null;
  const slide = spring({frame: local, fps,
    config: {damping: 19, stiffness: 165, mass: 1.0},
    durationInFrames: Math.round(0.42 * fps)});
  const tx = (1 - slide) * 620;
  const sc = 1.35 - 0.35 * Math.min(1, slide * 1.05);
  const op = Math.min(1, slide * 2.6);
  return (
    <div style={{position: 'absolute', left: 0, top: labelTop, width: '100%', zIndex: 2,
      display: 'flex', justifyContent: 'center', gap: 16,
      transform: `translateX(${tx}px) scale(${sc})`,
      transformOrigin: '50% 60%', opacity: op, pointerEvents: 'none'}}>
      {Array.from(text).map((ch, i) => (
        <span key={i} style={{
          fontFamily: "'WenYue XinQingNianTi', sans-serif", fontWeight: '800',
          fontSize: labelSize, lineHeight: 1, color: '#F2C14E',
          // ★2026-10-15：前 3 层＝向下挤出的暗金厚度（3D 立体），中间 4 层＝黑描边，最后＝投影
          textShadow: '0 2px 0 #8A4A08, 0 4px 0 #7A3D05, 0 6px 0 #6A3203, '
            + '0 0 2px #000, 3px 3px 0 #000, 2px -2px 0 #000, -2px 2px 0 #000, -2px -2px 0 #000, '
            + '0 10px 26px rgba(0,0,0,0.9)',
          display: 'inline-block'}}>{ch}</span>
      ))}
    </div>
  );
};

const HeroCard: React.FC<{px: number; scale: number; z: number; focusT: number;
  clickF: number; heroLabel: string; heroBg: string; heroFg: string;
  labelTop?: number; labelSize?: number}> =
  ({px, scale, z, focusT, clickF, heroLabel, heroBg, heroFg,
    labelTop = 16, labelSize = 132}) => (
  <div style={{
    position: 'absolute', left: px, top: M.cy,
    width: M.cardW, height: M.cardH,
    transform: `translate(-50%,-50%) scale(${scale})`,
    zIndex: z, borderRadius: 8, overflow: 'hidden',
    boxShadow: `0 ${20 + focusT * 46}px ${70 + focusT * 80}px rgba(0,0,0,${0.7 + focusT * 0.2})`,
  }}>
    <Img src={staticFile(heroBg)}
      style={{position: 'absolute', inset: 0, width: '100%', height: '100%',
        objectFit: 'cover', objectPosition: 'center top'}} />
    <SlideLabel clickF={clickF} labelTop={labelTop} labelSize={labelSize} text={heroLabel} />
    <Img src={staticFile(heroFg)}
      style={{position: 'absolute', inset: 0, width: '100%', height: '100%',
        objectFit: 'cover', objectPosition: 'center top', zIndex: 3}} />
  </div>
);

/* ══════════════════════════════════════
 * 鼠标光标（SVG箭头 + 点击时的按下反馈）
 * ══════════════════════════════════════ */
const Cursor: React.FC<{x: number; y: number; clickT: number}> = ({x, y, clickT}) => {
  // 点击后 6 帧内快速缩小再回弹（模拟按压）
  const press = clickT >= 0
    ? interpolate(clickT, [0, 3, 9], [1, 0.82, 1], {extrapolateRight: 'clamp'})
    : 1;
  const ring = clickT >= 0 ? interpolate(clickT, [0, 2, 14], [0, 1, 0],
    {extrapolateRight: 'clamp'}) : 0;
  const rr = interpolate(clickT, [0, 14], [10, 52], {extrapolateRight: 'clamp'});

  return (
    <div style={{
      position: 'absolute', left: x, top: y, zIndex: 1200,
      transform: `scale(${press})`,
      pointerEvents: 'none',
    }}>
      {/* 点击扩散的小白圈（紧贴指针，给「点到了」的即时反馈） */}
      {ring > 0 && (
        <div style={{
          position: 'absolute', left: -6, top: -6,
          width: 12, height: 12, borderRadius: '50%',
          border: `2px solid rgba(255,255,255,${ring * 0.9})`,
        }} />
      )}
      <svg width={52} height={66} viewBox="0 0 30 38"
        style={{filter: 'drop-shadow(0 3px 9px rgba(0,0,0,0.95))'}}>
        {/* ★放大 1.73×：30×38 的箭头在 1080P 上太小看不见，实测必须放大 */}
        <path d="M2 2 L2 30 L9 24 L14 36 L19 34 L14 22 L24 22 Z"
          fill="#FFFFFF" stroke="#000000" strokeWidth="1.8" strokeLinejoin="round" />
      </svg>
      {clickT >= 0 && rr > 12 && (
        <div style={{
          position: 'absolute', left: -4, top: -4,
          width: rr, height: rr, borderRadius: '50%',
          border: `2px solid rgba(255,255,255,${Math.max(0, 1 - rr / 52) * 0.5})`,
        }} />
      )}
    </div>
  );
};

/* ══════════════════════════════════════
 * 涟漪：以点击点为中心的扩散圆环（3 环递进）
 * ══════════════════════════════════════ */
const Ripple: React.FC<{cx: number; cy: number; t: number; maxR: number}> = ({cx, cy, t, maxR}) => {
  if (t < 0 || t > 1.35) return null;
  // ★ 三环加强：线宽×1.5、加一圈实心能量填充、扩散更快更远
  const RINGS = [
    {delay: 0.00, dur: 1.05, w: 11, c: 'rgba(212,255,0,1.00)', fill: 0.10},
    {delay: 0.11, dur: 0.96, w: 8, c: 'rgba(255,255,255,0.95)', fill: 0.07},
    {delay: 0.22, dur: 0.84, w: 6, c: 'rgba(212,255,0,0.75)', fill: 0.05},
  ];
  return (
    <AbsoluteFill style={{pointerEvents: 'none', zIndex: 1100}}>
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        {RINGS.map((r, i) => {
          const lt = (t - r.delay) / r.dur;
          if (lt < 0 || lt > 1) return null;
          // easeOutQuad 扩散（比 v1 快、更「甩」出去）
          const radius = maxR * (1 - Math.pow(1 - lt, 1.75));
          // 平方衰减但保底可见
          const op = Math.pow(1 - lt, 1.6);
          if (radius < 2 || op < 0.015) return null;
          return (
            <g key={i}>
              <circle cx={cx} cy={cy} r={radius} fill="none"
                stroke={r.c} strokeWidth={r.w * (1 - lt * 0.55)} opacity={op} />
              {/* 内侧能量填充，让涟漪有厚度 */}
              <circle cx={cx} cy={cy} r={radius} fill={r.c} opacity={op * r.fill * 3} />
            </g>
          );
        })}
        {/* 点击瞬间的实心闪点（给「点到了」的即时冲击） */}
        {t < 0.28 && (
          <circle cx={cx} cy={cy} r={30 + t * 300} fill="rgba(255,255,255,0.85)"
            opacity={(1 - t / 0.28) * 0.7} />
        )}
      </svg>
    </AbsoluteFill>
  );
};

/* ══════════════════════════════════════
 * 单卡
 * ══════════════════════════════════════ */
const Card: React.FC<{
  spec: CardSpec; px: number; offNorm: number; scale: number; z: number;
  opacity: number; isHero: boolean; focusT: number;
}> = ({spec, px, offNorm, scale, z, opacity, isHero, focusT}) => {
  const rotateY = -offNorm * M.maxY;
  const dim = Math.abs(offNorm);
  // 主图被点亮：focusT 0→1 时亮度回升、饱和度提升
  const lift = isHero ? focusT : 0;
  const filter = `brightness(${1 - dim * 0.5 + lift * 0.22}) saturate(${1 - dim * 0.24 + lift * 0.3})`;

  return (
    <div style={{
      // ★ px 是「卡片中心的绝对屏幕 x」（0=画面左边缘，1920=右边缘），
      //   不再经过 offNorm*760 这种中间换算 —— 那个魔数跟 pitch 不匹配，
      //   实测导致定格时左侧卡片落到画面外、左边全黑。
      position: 'absolute', left: px, top: M.cy,
      width: M.cardW, height: M.cardH,
      transform: `translate(-50%,-50%) scale(${scale}) rotateY(${rotateY}deg)`,
      transformStyle: 'preserve-3d',
      backfaceVisibility: 'hidden',
      opacity,
      filter,
      zIndex: Math.round(z),
      boxShadow: isHero
        ? `0 ${20 + lift * 46}px ${70 + lift * 80}px rgba(0,0,0,${0.7 + lift * 0.2})`
        : '0 14px 36px rgba(0,0,0,0.6)',
      borderRadius: 8,
      overflow: 'hidden',
    }}>
      <Img src={staticFile(spec.src)}
        style={{
          width: '100%', height: '100%',
          objectFit: 'cover',
          // ★ 与渲染器 img_fit=cover + crop_y=0 一致：**顶部对齐**。
          //   3:2 素材（c08-c14）裁成 16:9 时，居中裁会切掉人物头部，
          //   顶部对齐只裁底部 16%（实测保留 84% 高，主体完好）。
          objectPosition: 'center top',
        }} />
    </div>
  );
};

/* ══════════════════════════════════════
 * 主组件
 * ══════════════════════════════════════ */
export const MarqueeClickD: React.FC<{cards: CardSpec[]; label?: string, heroLabel?: string; heroBg?: string; heroFg?: string; heroLabelTop?: number; heroLabelSize?: number}> = ({cards, label, heroLabel, heroBg, heroFg, heroLabelTop, heroLabelSize}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  void fps;

  const NF = Math.round(T.scrollEnd * fps);          // 滚动结束帧
  const F_CLICK = Math.round(T.clickAt * fps);       // 点击帧
  const TOTAL = Math.round(T.totalS * fps);

  /* ── 位置：滚动段从左外滑入，scrollEnd 时主图停在正中 ──
  * ★★ 灿爹拍板：「停下来的时候，主图一定要在中间，两边是轮播图」
  *   ⇒ centerShift 终值必须 0（主图死钉在 x=960 画面正中）
  *   上一版为了填左边空档挪到 +430（HERO_X），这版不要——构图对称优先。
  *   左边有卡是靠 px 定位算对了（v2 修的 bug），不需要挪主图来补。
  */
  const centerShift = interpolate(frame, [0, NF], [-900, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  /* ── 指数减速位移 ── */
  const _sumf = (() => {
    let s = 0;
    for (let u = 0; u < 1; u += 1 / NF) s += Math.exp(-M.K_DECAY * u);
    return s;
  })();
  const v1 = (M.nScroll * M.pitch) / _sumf;
  const offStop = M.nScroll * M.pitch;
  const pos: number[] = [];
  let acc = 0;
  for (let fi = 0; fi < NF; fi++) {
    acc += v1 * Math.exp(-M.K_DECAY * (fi / NF));
    pos.push(acc);
  }
  const ixNow = frame < NF ? pos[Math.min(frame, NF - 1)] : offStop;

  const cardX = (k: number) => k * M.pitch - ixNow + centerShift;
  // ★ 归一化基准必须用「画面半宽」= 960，不是魔数 760。
  //   offNorm 语义= 卡到画面中心的距离 / 画面半宽 ⇒|offNorm|≥1 已在画外，=0 正中。
  const offOf = (k: number) =>
    Math.max(-1.3, Math.min(1.3, cardX(k) / 960));

  /* ── 点击后：主图弹簧放大 ── */
  const zoom = spring({
    frame: frame - F_CLICK,
    fps,
    config: {damping: 14, stiffness: 120, mass: 1.05},
    durationInFrames: Math.round(T.zoomDur * fps),
  });
  const focusT = Math.min(1, Math.max(0, (frame - F_CLICK) / (T.zoomDur * fps * 0.6)));

  /* ── 鼠标：从右下划入 → 移到主图中心 ── */
  const CUR_IN = Math.round(0.24 * fps);            // 淡入时长
  const cursorFrom = {x: 1660, y: 890};
  const cursorTo = {x: 960, y: M.cy};
  const cT = interpolate(frame, [F_CLICK - Math.round(0.42 * fps), F_CLICK],
    [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const curX = cursorFrom.x + (cursorTo.x - cursorFrom.x) * cT;
  const curY = cursorFrom.y + (cursorTo.y - cursorFrom.y) * cT;
  const curOp = interpolate(frame, [F_CLICK - Math.round(0.44 * fps), F_CLICK - Math.round(0.44 * fps) + CUR_IN],
    [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  /* ── 点击后鼠标淡出（画面已经聚焦主图，鼠标退场） ── */
  const curOut = interpolate(frame, [F_CLICK + Math.round(0.42 * fps), F_CLICK + Math.round(0.72 * fps)],
    [1, 0], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  const heroK = M.nScroll;
  const rippleT = (frame - F_CLICK) / fps;

  return (
    // ★ perspective 写在父容器—— 3D 透视生效的关键
    // ★ 背景必须 transparent：渲成 PNG 序列交给 ffmpeg overlay 时，
    //   纯黑底会把整块黑色矩形叠到成片上（实测 3 通道、无 alpha 就是这个症状）。
    //   自己看效果时可以用 prop `preview` 临时开黑底。
    <AbsoluteFill style={{background: TRANSPARENT_BG, perspective: M.perspective}}>
      {/* 背景纯黑 + 点击后的整体压暗，让主图更突出 */}
      <AbsoluteFill style={{
        // 压暗层：半透明黑（不透明黑会盖住后面所有卡）
        background: 'rgba(0,0,0,0.55)',
        // ★2026-10-15 修（烟酰胺v1实测）：原 zIndex=900 > HeroCard 的 800 ⇒ 点击后
        //   「烟酰胺」大字被这层一起压暗 55%，金色 (242,193,78) 掉到 (109,87,35)，
        //   逐帧抽样只见涟漪环、不见亮字。降到 750：滚动停止后**所有非 hero 卡
        //   z ≤ 642**（|offNorm|≥0.896），仍全部被压暗；hero(800) 浮在压暗层之上，
        //   字保持亮金。压暗层语义不变（只是不再误伤主图）。
        opacity: focusT, zIndex: 750, pointerEvents: 'none',
      }} />

      {cards.map((c, k) => {
        const dx = cardX(k);                       // 相对画面中心的偏移
        const px = 960 + dx;                       // ★绝对屏幕 x
        if (px < -M.cardW / 2 || px > 1920 + M.cardW / 2) return null;
        const offNorm = offOf(k);
        const isHero = k === heroK;
        // 主图从点击开始放大；其他卡保持 1×，并随 focusT 缩小一点（进一步聚焦）
        // ★ 16:9 卡铺满 1080P 需 scale = 1920/700 = 2.74（横向铺满，纵向等比溢出）
        //   v1 用 1.95 ⇒ 只覆盖 71% 宽 ⇒ 两侧黑边，实测才发现。
        const scale = isHero
          ? 1 + 1.76 * zoom
          : 1 - 0.10 * focusT;
        const z = isHero ? 800 : Math.round(1000 - Math.abs(offNorm) * 400);
        if (isHero && heroLabel && heroBg && heroFg) {
          return <HeroCard key={k} px={px} scale={scale} z={z} focusT={focusT}
            clickF={F_CLICK} heroLabel={heroLabel} heroBg={heroBg} heroFg={heroFg}
            labelTop={heroLabelTop} labelSize={heroLabelSize} />;
        }
        return (
          <Card key={k} spec={c} px={px} offNorm={offNorm} scale={scale} z={z}
            opacity={1} isHero={isHero} focusT={isHero ? focusT : 0} />
        );
      })}

      {/* ★ 涟漪（无白框，靠它指示点击位置） */}
      <Ripple cx={cursorTo.x} cy={cursorTo.y} t={rippleT} maxR={760} />

      {/* ★ 鼠标 */}
      {curOp > 0.01 && curOut > 0.01 && (
        <div style={{opacity: Math.min(curOp, curOut)}}>
          <Cursor x={curX} y={curY} clickT={frame - F_CLICK} />
        </div>
      )}

      {label && (
        <div style={{
          position: 'absolute', left: 0, top: 36, width: '100%', textAlign: 'center',
          color: '#8a94a6', fontFamily: 'monospace', fontSize: 22, zIndex: 2000,
        }}>{label}</div>
      )}
      <div style={{position: 'absolute', left: 0, top: 0, opacity: 0, pointerEvents: 'none'}}>{TOTAL}</div>
    </AbsoluteFill>
  );
};