import React from 'react';
import {registerRoot, Composition} from 'remotion';
import {MarqueeClick, T} from './MarqueeClick';
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

/* ═══ 卡池构造：★主图必须落在「滚动结束时正好到画面正中」那张 ═══
 *
 * 滚动总位移 = nScroll × 节距 ⇒ **索引 nScroll 的卡恰好停在 x=0（画面正中）**。
 * 所以主图要插在索引 nScroll 处，**不是数组末尾**。
 * （v1 踩过：heroK 取 nScroll=6，但黑板在索引 16 ⇒ 主图停在右边，正中还是别的卡。）
 *
 * 灿爹拍板：「停下来的时候，主图一定要在中间，两边是轮播图」
 *   ⇒ 主图 = 索引 nScroll（正中）；左边 0…nScroll-1 陪衬，右边 nScroll+1… 陪衬。
 *
 * ⚠️ 与 ffmpeg 版 opening_banner 的「主图 = 卡列最后一张」不同：
 *   那边主图是卡列末端（右侧无陪衬）。这边要左右对称 ⇒ 主图是「**最后滚到位的那张**」，
 *   卡池在它右侧仍有余量。语义一致（主图=滚动终点），只是池子不再刚好截断。
 *   ★ 但仍满足「主图是最后一个参与滚动位移的卡」—— 右侧的卡从未移动过。
 */
const HERO_SRC = 'cards/c16.jpg';           // 小绿人指黑板·姜黄素大字（1536×864 原生16:9）
const N = 6;                                // = M.nScroll，主图索引

const CARDS = [
  ...Array.from({length: N}, (_, i) => ({src: `cards/c${String(i).padStart(2, '0')}.jpg`})),
  {src: HERO_SRC},                          // ★ 索引 6 = 主图，滚动终点，画面正中
  ...Array.from({length: 3}, (_, i) => ({src: `cards/c${String(N + 1 + i).padStart(2, '0')}.jpg`})),
];

registerRoot(() => (
  <>
    <Composition
      id="MarqueeClick" component={MarqueeClick}
      durationInFrames={Math.ceil(T.totalS * FPS)} fps={FPS}
      width={1920} height={1080} defaultProps={{cards: CARDS}}
    />
    <Composition
      id="MCPreview" component={MarqueeClick}
      durationInFrames={Math.ceil(T.totalS * FPS)} fps={FPS}
      width={1920} height={1080}
      defaultProps={{
        cards: CARDS,
        label: '时序：0-2.20s 滚动(指数减速) ｜ 2.20-2.62s 鼠标划入 ｜ 2.62s 点击+涟漪 ｜ 2.62-3.46s 主图放大',
      }}
    />
  </>
));