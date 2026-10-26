import React from 'react';
import {registerRoot, Composition, AbsoluteFill} from 'remotion';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';
import {KwOverlay, KwSpec} from './KwOverlay';
import {VizOverlay, VizSpec} from './DataViz';
import {ProductCardFx} from './ProductCardFx';

/**
 * 爱兰纯肌酸 v15 —— 动效附加层（13 镜）
 * ============================================================
 * 输出 1920×1080 透明 PNG 序列，由 ffmpeg overlay 到「已剥离重点字与产品卡」的底片。
 *
 * 三层自下而上：
 *   ① ProductCardFx  镜7 产品卡弹簧入场（点名镜起挂 2 镜，覆盖镜7-8）
 *   ② VizOverlay    镜4 CountUp 含量 99.99% 数字滚动
 *   ③ KwOverlay     镜5/6 警告、镜8 盖章×2、镜10/11/12 重锤
 *
 * ★坐标来源：pick_kw_pos_d.py 逐候选点实测（13 镜版）。
 * ★时间来源：kw_times_d.py 从配音 token 逐字符定位，起跳比开口早 0.06s。
 * ★TOTAL 给 1040（34.67s）冗余 30 帧，overlay eof_action=endall 自然截断。
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
const TOTAL = 1040;      // 冗余缓冲，overlay 端 endall 截断

/* ASS 颜色是 &H00BBGGRR（BGR 序），这里按 BGR 反着写 */
const WARN  = '&H004A6BFF';   // → #FF6B4A 警告橙红
const WHITE = '&H00FFFFFF';
const GOLD  = '&H00D4FF00';   // → #FFD400  价格重锤金

/* ── ② 数据可视化：镜4「含量达到4个9」 ──
 * ⚠️ 合规：只滚数字本身（99.99 + %），label 只写「含量」，
 *    不写「纯度认证 / 优质 / 顶级」等任何评价词。 */
const VIZ: VizSpec[] = [
  {
    kind: 'count', x: 300, y: 300,
    target: 99.99, decimals: 2, unit: '%', label: '含量',
    startSec: 9.93, durSec: 1.65,
    barWidth: 300, color: '#FFD400',
  },
];

/* ── ③ 重点字：7 词全部 spring 变体 ──
 * ★镜8 两词（一批批 / 随货）位置错开（300,300 与 300,460），时间自然错开 */
const KW: KwSpec[] = [
  // 镜5「重金属微生物超不超标」11.56-12.08s
  {startSec: 11.50, durSec: 1.57, text: '超不超标', x: 300, y: 760,
   assColor: WARN, anim: 'warn'},
  // 镜6「检测费立刻得抬一截」14.99-15.39s
  {startSec: 14.93, durSec: 1.45, text: '检测费', x: 1360, y: 760,
   assColor: WARN, anim: 'warn'},
  // 镜8「出场前一批批先过检测」18.82-19.22s —— 盖章
  {startSec: 18.76, durSec: 1.44, text: '一批批', x: 300, y: 300,
   assColor: WHITE, anim: 'stamp', hollow: true},
  // 镜8「单子随货寄到你手上」20.01-20.24s —— 同镜第二词，位置错开
  {startSec: 19.95, durSec: 1.28, text: '随货', x: 300, y: 460,
   assColor: WHITE, anim: 'stamp', hollow: true},
  // 镜10「19块9够你喝三个月」23.28-23.62s —— 重锤
  {startSec: 23.22, durSec: 1.39, text: '三个月', x: 1660, y: 760,
   assColor: GOLD, anim: 'price'},
  // 镜11「卖出50多万单的销量」25.08-25.57s —— 重锤
  {startSec: 25.02, durSec: 1.54, text: '50多万单', x: 560, y: 660,
   assColor: GOLD, anim: 'price'},
  // 镜12「可以随意对比」27.54-28.04s —— 重锤
  {startSec: 27.48, durSec: 1.54, text: '随意对比', x: 420, y: 300,
   assColor: GOLD, anim: 'price'},
];

export const FxLayerD: React.FC = () => (
  <AbsoluteFill style={{background: 'transparent'}}>
    <ProductCardFx
      src="card_c.jpg" w={360} left={1520} top={110}
      startSec={17.00} durSec={3.90} from="right" radius={22}
    />
    <VizOverlay list={VIZ} />
    <KwOverlay list={KW} />
  </AbsoluteFill>
);

registerRoot(() => (
  <>
    <Composition
      id="FxLayerD"
      component={FxLayerD}
      durationInFrames={TOTAL}
      fps={FPS}
      width={1920}
      height={1080}
    />
  </>
));
