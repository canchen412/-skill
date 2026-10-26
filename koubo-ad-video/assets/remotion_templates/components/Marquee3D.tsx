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
 * 卷播 3D 透视卡列（Marquee3D）
 * ============================================================
 * 对标参考视频实测：卡片顶缘斜率 −0.215（明显透视），
 * 画面中心附近的卡接近正对（斜率 ≈0.001），越靠边倾斜越大
 * ⇒ 标准透视投影：rotateY = f(卡到画面中心的距离)
 *
 * 为什么必须用 Remotion 而不是 ffmpeg：
 *   ffmpeg 的 perspective 滤镜要对每帧、每张卡做四点变换，
 *   10+ 张卡 × 106 帧 = 1000+ 次透视计算，滤镜图会爆且极慢。
 *   Remotion 里就是一行 CSS `perspective() rotateY()`，浏览器 GPU 加速。
 *
 * 对位铁律（照旧）：
 *   ① 卷播主图 = 卡列最后一张
 *   ② 白框/卡心 必须对位（抽帧量像素，不信日志）
 */

const handle = delayRender('加载新青年体');
loadFont({family: 'WenYue XinQingNianTi', url: staticFile('新青年体.ttf'), weight: '800'})
  .then(() => continueRender(handle))
  .catch((e) => {
    console.error('字体加载失败：', e);
    continueRender(handle);
  });

/* ═══ 参数（对标参考视频 568×320 实测值换算到 1920×1080） ═══ */
export const M3D = {
  cardW: 560,          // 卡宽（与方案B一致）
  cardH: 560 * 1.28,   // 卡高：参考视频卡是竖的（宽高比≈0.55），这里用 1.28 略压
  pitch: 700,          // 节距（卡宽 + 140 间隙）⇒ 每屏 2.7 张
  boxW: 600,
  boxH: 560 * 1.28 + 40,
  boxCY: 520,          // 白框中心 y
  maxY: 34,            // ★ 画面边缘的 rotateY 上限（度）—— 实测反解≈18-24°，取 34 稍夸张
  perspective: 1500,   // CSS perspective 距离（px），越小透视越强
  durS: 3.53,          // 卷播滚动时长（配音到「姜黄素」）
  totalS: 4.80,        // 素材总时长
  focusAt: 3.53,       // 定格落点
  nScroll: 8,          // 滚动卡数
  heroIdx: -1,         // 主图（最后一张）
};

export type CardSpec = {src: string};

/**
 * 单卡：按它到画面中心的距离给 rotateY
 * @param offNorm 归一化偏心（0=正中，±1=画面边缘）
 */
const Card: React.FC<{
  spec: CardSpec; offNorm: number; scale: number; z: number; opacity: number;
}> = ({spec, offNorm, scale, z, opacity}) => {
  // ★ rotateY 符号：卡在画面**右侧**（offNorm>0）应绕 Y 轴**向左转**（露出右侧面），
  //   CSS rotateY 正值 = 右边缘远离观察者 = 左侧转出 ⇒ 用 `-offNorm * maxY`。
  const rotateY = -offNorm * M3D.maxY;
  // 亮度：越靠边越暗（模拟侧光），让中心卡更突出
  const dim = Math.abs(offNorm);
  const filter = `brightness(${1 - dim * 0.42}) saturate(${1 - dim * 0.2})`;

  return (
    <div
      style={{
        position: 'absolute',
        left: 960,
        top: M3D.boxCY,
        width: M3D.cardW,
        height: M3D.cardH,
        // ★★ perspective 必须写在**父容器**（Marquee3D 的 AbsoluteFill）上，
        //    写在卡片自己身上无效 —— 那样只是自旋，没有透视投影。
        //    这里卡片只做 translateX + scale + rotateY。
        transform: `translate(-50%, -50%) translateX(${offNorm * 760}px) ` +
                   `scale(${scale}) rotateY(${rotateY}deg)`,
        transformStyle: 'preserve-3d',
        backfaceVisibility: 'hidden',
        opacity,
        filter,
        zIndex: Math.round(z),
        boxShadow: '0 18px 46px rgba(0,0,0,0.65)',
        borderRadius: 8,
        overflow: 'hidden',
      }}
    >
      <Img src={staticFile(spec.src)} style={{width: '100%', height: '100%', objectFit: 'cover'}} />
    </div>
  );
};

/** 白框：★必须跟着中心卡一起 rotateY —— 否则矩形框套梯形卡必然错位（v1 实测踩过） */
const Box3D: React.FC<{
  scale: number; opacity: number; t: number; offNorm: number;
}> = ({scale, opacity, t, offNorm}) => {
  const {fps} = useVideoConfig();
  void fps;
  const rotateY = -offNorm * M3D.maxY;
  const rip = Math.sin(t * 9) * (1 - t) * 7;         // 落位后的水波余震
  return (
    <div
      style={{
        position: 'absolute',
        left: 960,
        top: M3D.boxCY + rip,
        width: M3D.boxW,
        height: M3D.boxH,
        transform: `translate(-50%,-50%) translateX(${offNorm * 760}px) ` +
                   `scale(${scale}) rotateY(${rotateY}deg)`,
        transformStyle: 'preserve-3d',
        opacity,
        border: `6px solid rgba(255,255,255,${0.55 + 0.45 * (1 - t)})`,
        borderRadius: 6,
        boxShadow: `0 0 ${28 * (1 - t) + 8}px rgba(255,255,255,${0.5 * (1 - t)})`,
        pointerEvents: 'none',
        zIndex: 900,
      }}
    />
  );
};

export const Marquee3D: React.FC<{cards: CardSpec[]; label?: string}> = ({cards, label}) => {
  const frame = useCurrentFrame();
  const {fps} = useVideoConfig();
  void fps;

  const N = cards.length;
  const NF = Math.round(M3D.focusAt * fps);              // 定格帧号
  const TOTAL = Math.round(M3D.totalS * fps);

  // 位置：从左外侧滑入 → 定格在画面正中（★必须归0，让 hero 卡 offNorm=0 正对镜头，
  //   否则卡是斜的、矩形白框套不上；v1 实测因为定格在 +120 导致框卡错位）
  const centerShift = interpolate(
    frame,
    [0, NF * 0.55, NF],
    [-900, 0, 0],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'}
  );

  // 定格后放大
  const zoom = interpolate(frame, [NF, NF + Math.round(1.0 * fps)], [1, 1.9],
    {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

  // 滚动位移（★全程渐慢 = 指数减速，v(u)=v1·e^(-K·u)）
  const NF1 = NF;
  // ★★ 为什么用指数而不是 cubic（实测对比，v1 实测踩过）：
  //   cubic c=1.4  → 减速比仅 3.3x，且**前 0.8s 几乎恒速**（2011→2020 px/s），
  //                   观感就是「先匀速再急停」，正是文档里记的「前段恒速=太快」的坑。
  //   指数 e^(-3u)  → 减速比 11.1x，首帧就是最��，之后一路明显变慢 ⇒ 真正的「由快到慢」。
  const K_DECAY = 3.0;
  const _sumf = (() => {
    let s = 0;
    for (let u = 0; u < 1; u += 1 / NF1) s += Math.exp(-K_DECAY * u);
    return s;
  })();
  const v1 = (M3D.nScroll * M3D.pitch) / _sumf;
  const offStop = M3D.nScroll * M3D.pitch;

  let acc = 0;
  const pos: number[] = [];
  for (let fi = 0; fi < NF1; fi++) {
    acc += v1 * Math.exp(-K_DECAY * (fi / NF1));
    pos.push(acc);
  }
  const ixNow = frame < NF1 ? pos[Math.min(frame, NF1 - 1)] : offStop;

  /** 卡 k 的屏幕 x（相对画面中心）与归一化偏心 */
  const cardX = (k: number) => k * M3D.pitch - ixNow + centerShift;
  const offOf = (k: number) =>
    Math.max(-1.25, Math.min(1.25, cardX(k) / 700));

  // 白框跟随「定格时刻正好在中心的卡」—— 滚动段框正在过的卡，定格段框 hero 卡
  const focusK = frame < NF1
    ? Math.round((ixNow - centerShift) / M3D.pitch)          // 此刻正在中心的那张
    : M3D.nScroll;                                          // 定格后框 hero
  const boxOff = Math.max(-1.25, Math.min(1.25,
    (frame < NF1 ? cardX(focusK) / 700 : 0)));

  return (
    // ★★★ perspective 放在这里（父容器）—— 这是 3D 透视生效的关键。
    //    参考视频实测顶缘斜率 −0.215 ⇒ perspective 1500 这个档。
    <AbsoluteFill style={{background: '#000', perspective: M3D.perspective}}>
      {cards.map((c, k) => {
        const x = cardX(k);
        if (x < -700 || x > 700 + M3D.pitch) return null;
        const offNorm = offOf(k);
        const isHero = frame >= NF1 && k === M3D.nScroll;
        const scale = isHero ? zoom : 1;
        const z = isHero ? 800 : Math.round(1000 - Math.abs(offNorm) * 400);
        const op = frame < NF1 ? 1 : Math.max(0, 1 - (frame - NF1 - 30) / 20);
        return (
          <Card key={k} spec={c} offNorm={offNorm} scale={scale} z={z} opacity={op} />
        );
      })}
      <Box3D
        scale={frame < NF1 ? interpolate(frame, [0, NF1 * 0.4], [1.25, 1], {extrapolateRight: 'clamp'}) : zoom}
        opacity={frame < NF1
          ? interpolate(frame, [0, 8], [0, 1], {extrapolateRight: 'clamp'})
          : Math.max(0, 1 - (frame - TOTAL + 12) / 12)}
        t={Math.min(1, Math.max(0, (frame - NF1 + 26) / 40))}
        offNorm={boxOff}
      />
      {label && (
        <div style={{
          position: 'absolute', left: 0, top: 40, width: '100%', textAlign: 'center',
          color: '#8a94a6', fontFamily: 'monospace', fontSize: 22,
        }}>{label}</div>
      )}
    </AbsoluteFill>
  );
};

function easeInOutCubic(u: number): number {
  const c = 1.4;                    // 与 opening_banner.py 同档，全程渐慢
  return c * u * u * u + (1 - c) * u * u;
}