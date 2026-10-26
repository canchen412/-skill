import React from 'react';
import {registerRoot, Composition, AbsoluteFill} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';
import {KwOverlay, KwSpec} from './KwOverlay';
import {VizOverlay, VizSpec} from './DataViz';
import {ProductCardFx} from './ProductCardFx';

/**
 * 爱兰纯肌酸 v14 —— 动效附加层
 * ============================================================
 * 输出 1920×1080 透明 PNG 序列，由 ffmpeg overlay 到「已剥离重点字与产品卡」的底片。
 *
 * 三层自下而上：
 *   ① ProductCardFx  镜9 产品卡弹簧入场（替换 ffmpeg 的线性 slide）
 *   ② VizOverlay    镜5 CountUp 含量 99.99% 数字滚动
 *   ③ KwOverlay     镜6/8 警告、镜10/11 盖章、镜13/15 重锤
 *
 * ★坐标来源：pick_kw_pos.py 逐候选点实测「字要占的那块矩形」的边缘密度，
 *   取最平（最不压主体）者；同时排除底部字幕带 y>880 与右上产品卡矩形。
 * ★时间来源：kw_times.py 从配音 token 逐字符定位，起跳比开口早 0.06s。
 */

const h = delayRender('加载新青年体');
loadFont({
  family: 'WenYue XinQingNianTi',
  url: staticFile('新青年体.ttf'),
  weight: '800',
})
  .then(() => continueRender(h))
  .catch((e) => {
    console.error('字体加载失败：', e);
    continueRender(h);
  });

const FPS = 30;
const TOTAL = 1009;      // 全片 33.62s × 30fps + 1

/* ASS 颜色是 &H00BBGGRR（BGR 序），这里按 BGR 反着写 */
const WARN  = '&H004A6BFF';   // → #FF6B4A 警告橙红
const WHITE = '&H00FFFFFF';
const GOLD  = '&H00D4FF00';   // → #FFD400  价格重锤金

/* ── ② 数据可视化：镜5「达到4个9」 ──
 * ⚠️ 合规：只滚数字本身（99.99 + %），label 只写「含量」，
 *    不写「纯度认证 / 优质 / 顶级」等任何评价词。 */
const VIZ: VizSpec[] = [
  {
    kind: 'count', x: 300, y: 300,
    target: 99.99, decimals: 2, unit: '%', label: '含量',
    startSec: 11.14, durSec: 1.72,
    barWidth: 300, color: '#FFD400',
  },
];

/* ── ③ 重点字：全部换成 spring 变体 ── */
const KW: KwSpec[] = [
  // 镜6「超不超标」12.90-13.38s —— 低频闷击，压得住
  {startSec: 12.84, durSec: 1.54, text: '超不超标', x: 300, y: 660,
   assColor: WARN, anim: 'warn'},
  // 镜8「检测费」16.32-16.73s
  {startSec: 16.26, durSec: 1.46, text: '检测费', x: 1360, y: 760,
   assColor: WARN, anim: 'warn'},
  // 镜10「一批批」20.21-20.63s —— 盖章：砸下来有分量
  {startSec: 20.15, durSec: 1.47, text: '一批批', x: 300, y: 460,
   assColor: WHITE, anim: 'stamp', hollow: true},
  // 镜11「随货」21.89-22.12s
  {startSec: 21.83, durSec: 1.29, text: '随货', x: 300, y: 300,
   assColor: WHITE, anim: 'stamp', hollow: true},
  // 镜13「三个月」24.72-25.07s —— 重锤：从下顶起 + 中段复顶
  {startSec: 24.66, durSec: 1.40, text: '三个月', x: 1660, y: 760,
   assColor: GOLD, anim: 'price'},
  // 镜15「随便挑着比」27.43-28.03s
  {startSec: 27.37, durSec: 1.65, text: '随便挑着比', x: 420, y: 300,
   assColor: GOLD, anim: 'price'},
];

export const FxLayerC: React.FC = () => (
  <AbsoluteFill style={{background: 'transparent'}}>
    <ProductCardFx
      src="card_c.jpg" w={360} left={1520} top={110}
      startSec={18.15} durSec={3.55} from="right" radius={22}
    />
    <VizOverlay list={VIZ} />
    <KwOverlay list={KW} />
  </AbsoluteFill>
);

registerRoot(() => (
  <>
    <Composition
      id="FxLayerC"
      component={FxLayerC}
      durationInFrames={TOTAL}
      fps={FPS}
      width={1920}
      height={1080}
    />
  </>
));
