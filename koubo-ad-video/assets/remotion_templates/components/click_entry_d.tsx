import React from 'react';
import {registerRoot, Composition} from 'remotion';
import {MarqueeClickD, T} from './MarqueeClickD';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';

const h = delayRender('加载新青年体');
loadFont({family: 'WenYue XinQingNianTi', url: staticFile('新青年体.ttf'), weight: '800'})
  .then(() => continueRender(h))
  .catch((e) => {
    console.error('字体加载失败：', e);
    continueRender(h);
  });

const FPS = 30;

/* ═══ 爱兰纯 v12 · 3D 卷播卡池 ═══
 *
 * ★主图位置铁律（Remotion 版，与 ffmpeg 版不同，别混）：
 *   滚动总位移 = nScroll × pitch ⇒ **索引 nScroll 的卡恰好停在画面正中**。
 *   ⇒ 主图插在索引 nScroll，**不是数组末尾**。
 *   语义与 ffmpeg 版一致（主图 = 滚动终点），只是这边要左右对称，
 *   所以主图右侧还有 3 张陪衬（它们从未参与位移）。
 *
 * ★ hero = 镜2_黑板宣言_肌酸（与 ffmpeg 平面版同一张，视觉连续）
 */
const N = 7;                        // = M.nScroll，主图索引
const RIGHT = 3;                    // 右侧陪衬张数

const CARDS = [
  ...Array.from({length: N}, (_, i) => ({src: `cards_c/c${String(i).padStart(2, '0')}.jpg`})),
  {src: 'cards_c/hero.jpg'},        // ★ 索引 7 = 主图，滚动终点，画面正中
  ...Array.from({length: RIGHT}, (_, i) => ({src: `cards_c/c${String(N + i).padStart(2, '0')}.jpg`})),
];

registerRoot(() => (
  <>
    <Composition
      id="MarqueeClickD" component={MarqueeClickD}
      durationInFrames={Math.ceil(T.totalS * FPS)} fps={FPS}
      width={1920} height={1080} defaultProps={{cards: CARDS}}
    />
    <Composition
      id="MCPreviewC" component={MarqueeClickD}
      durationInFrames={Math.ceil(T.totalS * FPS)} fps={FPS}
      width={1920} height={1080}
      defaultProps={{
        cards: CARDS,
        label: '爱兰纯时序：0-2.75s 滚动(指数减速) ｜ 2.75-3.13s 鼠标划入 ｜ 3.13s 点击+涟漪 ｜ 3.13-3.97s 主图放大',
      }}
    />
  </>
));
