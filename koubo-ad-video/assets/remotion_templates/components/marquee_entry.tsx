import React from 'react';
import {registerRoot, Composition, AbsoluteFill} from 'remotion';
import {Marquee3D, M3D, CardSpec} from './Marquee3D';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';

/**
 * 入口
 * ⚠️ 不能叫 index.tsx（CLI 会把 basename 当 composition id）
 */

const h = delayRender('加载新青年体');
loadFont({family: 'WenYue XinQingNianTi', url: staticFile('新青年体.ttf'), weight: '800'})
  .then(() => continueRender(h))
  .catch((e) => {
    console.error('字体加载失败：', e);
    continueRender(h);
  });

const FPS = 30;

/* 卡池：17 张，主图（最后一张）压最后 —— 铁律 */
const CARDS: CardSpec[] = Array.from({length: 17}, (_, i) => ({
  src: `cards/c${String(i).padStart(2, '0')}.jpg`,
}));

/* 三档对比页：无 3D / 弱 3D / 强 3D —— 一屏看透视强度差别 */
const ComparePage: React.FC<{maxY: number; label: string}> = ({maxY, label}) => {
  const old = M3D.maxY;
  M3D.maxY = maxY;
  void old;
  return (
    <AbsoluteFill style={{background: '#000'}}>
      <Marquee3D cards={CARDS} label={label} />
    </AbsoluteFill>
  );
};

const No3D: React.FC = () => <ComparePage maxY={0} label="无 3D（纯平面，当前 ffmpeg 版的效果）" />;
const Weak3D: React.FC = () => <ComparePage maxY={18} label="弱 3D（rotateY 边缘 18°，对标参考视频实测值）" />;
const Strong3D: React.FC = () => <ComparePage maxY={34} label="强 3D（rotateY 边缘 34°，更夸张）" />;

registerRoot(() => (
  <>
    <Composition
      id="Marquee3D" component={Marquee3D}
      durationInFrames={Math.ceil(M3D.totalS * FPS)} fps={FPS}
      width={1920} height={1080} defaultProps={{cards: CARDS}}
    />
    <Composition id="No3D" component={No3D}
      durationInFrames={Math.ceil(M3D.totalS * FPS)} fps={FPS} width={1920} height={1080} defaultProps={{}} />
    <Composition id="Weak3D" component={Weak3D}
      durationInFrames={Math.ceil(M3D.totalS * FPS)} fps={FPS} width={1920} height={1080} defaultProps={{}} />
    <Composition id="Strong3D" component={Strong3D}
      durationInFrames={Math.ceil(M3D.totalS * FPS)} fps={FPS} width={1920} height={1080} defaultProps={{}} />
  </>
));