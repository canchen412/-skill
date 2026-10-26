import React from 'react';
import {AbsoluteFill} from 'remotion';
import {KwOverlay, KwSpec} from './KwOverlay';
import {VizOverlay, VizSpec} from './DataViz';
import {ExplainOverlay, ExplainSpec} from './Explain';

/**
 * 流水线 · Remotion fx 附加层（通用版）
 * ============================================================
 * ★ 灿爹拍板：重点字视觉**全部走 Remotion**（弹簧真物理动效 + 数字滚动），
 *   不再走 build_ad_video 的 ASS 大字（config keywords 一律传 []）。
 *   音效仍由 build_ad_video 的 trigger_sfx 出（PNG 序列无声）。
 *
 * spec 由 make_fx_spec.py 从文案侧车 .fx.json + vo token 实测生成：
 *   fx_spec_{name}.json = { totalFrames, kw, viz, explain }
 *
 * ★ 2026-10-14 新增第三层 explain（科普段纠偏动效）：
 *   kw     重点字      —— 弹一下就消失，用于指认单个词
 *   viz    数据可视化  —— 数字/含量/占比，回答「多少」，产品段用
 *   explain 纠偏动效  —— **只有 Highlight 一个组件**（kind='mark'）
 *                       划掉错误说法 + 荧光笔点出正确说法，回答「怎么说」
 *
 *   ★ 出片后定稿（2026-10-14 灿爹拍板）：explain 层原本有 CheckRoll / WordPop，
 *     看完成片后只保留 Highlight，其余两个组件已下架。
 *     原因见 references/科普句式动效库.md 第三节「密度铁律」。
 *
 * 落位铁律：不遮主体人物/产品（四角+两侧安全区），配色跟画面走（低饱和暖色系）。
 * ★ 密度铁律（优先于所有 spec 规则）：同一时刻屏幕上最多 1–2 行大字；
 *   动效层只说关键词、不重复字幕完整句；补充说明类内容一律不上画面。
 * 科普段落位另见 references/科普句式动效库.md 的边缘密度实测表。
 */
export const FxLayerJ: React.FC<{
  kw: KwSpec[];
  viz: VizSpec[];
  explain?: ExplainSpec[];
}> = ({kw, viz, explain = []}) => (
  <AbsoluteFill style={{background: 'transparent'}}>
    <VizOverlay list={viz} />
    <ExplainOverlay list={explain} />
    <KwOverlay list={kw} />
  </AbsoluteFill>
);