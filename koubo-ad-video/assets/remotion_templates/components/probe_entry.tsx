import React from 'react';
import {registerRoot, Composition, AbsoluteFill, useCurrentFrame} from 'remotion';
import {VizOverlay, VizSpec} from './DataViz';
import {loadFont} from '@remotion/fonts';
import {staticFile, delayRender, continueRender} from 'remotion';

/**
 * 定位探针：三个组件各画一条水平参考线，验证 y 坐标是否生效。
 * 用法：渲 VizProbe → 抽帧量亮线所在行，应分别落在 y=440 / 440 / 700 附近。
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

const SPECS: VizSpec[] = [
  {kind: 'ring', x: 300, y: 440, r: 120, value: 0.98,
   label: 'RING', sub: 'cy=440', startSec: 0, durSec: 1.5, color: '#D4FF00'},
  {kind: 'count', x: 900, y: 440, target: 10, unit: 'mg', label: 'COUNT',
   startSec: 0, durSec: 1.5, barWidth: 280, color: '#FFB020'},
  {kind: 'bar', x: 1500, y: 700,
   rows: [{name: 'A', v: 1.0, color: '#8A94A6'}, {name: 'B', v: 2.4, color: '#D4FF00'}],
   startSec: 0, durSec: 1.5, barWidth: 340},
];

const Probe: React.FC = () => {
  const frame = useCurrentFrame();
  void frame;
  return (
    <AbsoluteFill style={{background: '#101319'}}>
      {/* 参考线：y=440 绿线、y=700 粉线 */}
      <div style={{position:'absolute', left:0, top:440, width:1920, height:1.5, background:'#0f0'}} />
      <div style={{position:'absolute', left:0, top:700, width:1920, height:1.5, background:'#f0f'}} />
      <div style={{position:'absolute', left:0, top:6, width:1920, textAlign:'center',
        color:'#8a94a6', fontFamily:'monospace', fontSize:22}}>
        绿线 y=440 / 粉线 y=700 ｜ 三个组件应分别贴住对应线
      </div>
      <VizOverlay list={SPECS} />
    </AbsoluteFill>
  );
};

registerRoot(() => (
  <Composition id="VizProbe" component={Probe}
    durationInFrames={60} fps={30} width={1920} height={1080} defaultProps={{}} />
));