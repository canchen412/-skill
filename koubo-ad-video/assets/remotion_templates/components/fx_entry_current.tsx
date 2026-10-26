import React from 'react';
import {registerRoot, Composition, AbsoluteFill} from 'remotion';
import {KwOverlay, KwSpec} from './KwOverlay';
import {VizOverlay, VizSpec} from './DataViz';
import {ProductCardFx} from './ProductCardFx';
import {BreathLayer, MoteLayer, BreathSpec, MoteSpecWrap} from './BreathLayer';
import {TransitionFx, TransSpec} from './TransitionFx';

/**
 * 科普带货片· 动效层总入口（v22 定型 ·跨项目参考）
 * ============================================================
 * 输出 1920×1080 透明 PNG 序列，由 ffmpeg overlay 到「已剥离字幕与
 * 所有动效」的干净底片上。
 *
 * ★★ 前提：底片字幕**关不掉**（build_ad_video.py 里
 *   `max_lines = max(1, int(cfg.get("sub_max_lines", 1)))` 强制下限 1，
 *   且字幕滤镜无条件进 filter_complex）⇒ 底片始终带 ASS 字幕。
 *   所以 v17 **不叠自己的字幕层**（会与 ASS 重影），
 *   改为「重点字时间与 ASS 关键词严格同帧」来实现联动 —— 见 KW[] 注释。
 *
 * z 序自下而上：
 *   ① BreathLayer    静图镜位的呼吸缩放（镜 3/6/9/12 补呼吸）
 *   ② MoteLayer      飘尘粒子（镜 9 摇粉场景加密）
 *   ③ VizOverlay     数据可视化（镜 4 / 10 / 11）
 *   ④ ProductCardFx  镜 7 产品卡弹簧滑入
 *   ⑤ KwOverlay      重点字 8 词（warn / stamp / price）
 *   ⑥ TransitionFx   四处镜间转场（z 3000，盖住一切）
 *
 * ★全部时间码来自本片实测：
 *   shot_bounds.json（13 镜起止）+ kw_times.json（8 词发声时刻）
 *   + subs.ass（16 条字幕时间码）
 */

/* ══════════════════════════════════════
 * ① 呼吸层：给近乎静止的静图镜位补动感
 * ══════════════════════════════════════
 * ★只挑「够长且画面偏暗」的镜位做呼吸：
 *   镜3 (5.51-6.70) 暗调 1.19s  —— 拉近，最有压迫感
 *   镜6 (13.60-16.20)惊讶脸 2.60s —— 斜向右推，呼应「抬一截」
 *   镜9 (21.35-22.20)摇粉 0.85s  —— 太短，只做极轻的呼吸
 *   镜12 (26.50-28.07)拉镜头 1.57s —— 拉远，收尾感
 *
 * ★镜 2/13 刻意不做呼吸：
 *   镜 2 底部要压字幕、镜 13 是收尾欢跳（人物跳动本身有动感），
 *   加呼吸会和人物动作打架，反而变乱。
 */
/**
 * BREATH —— ★★ v22 已整体停用（BreathLayer 会盖出黑闪，详见 JSX 处注释）
 * 保留定义仅作历史参考，**不要再启用**。
 * 若将来要做「静图动感」，正确做法是在**底片 Ken Burns 上调参**，
 * 或只加**局部前景元素**（如手部/罐体高光），绝不要叠全屏图。
 */
const BREATH: BreathSpec[] = [];

/* ══════════════════════════════════════
 * ② 飘尘粒子
 * ══════════════════════════════════════
 * ★固定种子手排坐标（不用 Math.random）—— 保证每次渲染完全一致，
 *   否则同一份 config 渲两次出来的粒子位置不同，无法做前后对比。
 *
 * 密度分配：镜 9 摇粉最多（24 粒，呼应「粉末翻滚」），
 *   镜 6 惊讶脸 12 粒（表情镜不能太花），镜 3/12 各 10 粒。
 */
const mkMotes = (n: number, seed: number, o: number, vy: number): {x: number; y: number; r: number; vy: number; vx: number; o: number; ph: number}[] => {
  // 确定性伪随机（mulberry32）—— 同 seed 必得同序列
  let s = seed;
  const rnd = () => {
    s |= 0; s = (s + 0x6D2B79F5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  return Array.from({length: n}, () => ({
    x: rnd(),
    y: rnd(),
    r: 1.6 + rnd() * 3.4,          // 1.6–5.0 px 柔光点
    vy: vy * (0.6 + rnd() * 0.8),  // 速度扰动 ±40%
    vx: (rnd() - 0.5) * 22,        // 横漂 −11 ~ +11 px/s
    o: o * (0.55 + rnd() * 0.45),  // 透明度扰动
    ph: rnd(),
  }));
};

const MOTES: MoteSpecWrap[] = [
  {startSec: 5.51,  durSec: 1.19, list: mkMotes(10, 1101, 0.30, 16)},
  {startSec: 13.60, durSec: 2.60, list: mkMotes(12, 2202, 0.26, 14)},
  {startSec: 21.35, durSec: 0.85, list: mkMotes(24, 3303, 0.42, 34)},   // ★摇粉：密、快、亮
  {startSec: 26.50, durSec: 1.57, list: mkMotes(10, 4404, 0.24, 12)},
];

/* ══════════════════════════════════════
 * ③ 数据可视化（v17 补强）
 * ══════════════════════════════════════
 * ⚠️ 合规：只做**产品客观参数**，不做效果对比。
 *   CompareBar 只对比「含量数值」这一项，不涉及任何功效宣称。
 *
 * ★durSec 全部 ≥1.2s（<1.2s 淡出窗口太短，抽帧看着像组件坏了）。
 *
 * ★★ 落位是量出来的，不是猜的（v17 首渲教训）：
 *   首版把三个可视化全放 x≈300~1080 / y≈300~380，结果：
 *     镜4 压在人物脸上、镜10「¥0.22/天」直接盖住罐身标签（完全不可读）。
 *   逐镜抽帧量过空白带后改到画面左上/右上（人物居中构图的两侧最空）。
 *   换素材后必须重量，别照抄这里的坐标。
 */
const VIZ: VizSpec[] = [
  // 镜4「含量达到4个9」：99.99% 计数（左上空白带）
  {kind: 'count', x: 330, y: 250, target: 99.99, decimals: 2, unit: '%',
   label: '含量', startSec: 9.93, durSec: 1.90, barWidth: 300, color: '#FFD400'},
  // ★v18 删除CompareBar（吸收对比）—— 灿爹拍板「容易违规」。
  //   且组件标题「吸收对比」本身就带功效暗示，属带货红线，双重不合规。
  // 镜10「19块9够你喝三个月」：★日均价视觉化 —— 放左上，不压罐身
  {kind: 'count', x: 340, y: 280, target: 0.22, decimals: 2, unit: '¥/天',
   label: '折合每天', startSec: 23.22, durSec: 1.85, barWidth: 380, color: '#D4FF00'},
  // 镜11「卖出50多万单」：销量计数器（左上）
  {kind: 'count', x: 360, y: 290, target: 500000, decimals: 0, unit: '单',
   label: '累计销量', startSec: 25.02, durSec: 1.80, barWidth: 460, color: '#FFD400'},
];

/* ══════════════════════════════════════
 * ④ 重点字 —— ★v18 大幅精简（灿爹 2026-10-08 拍板）
 * ══════════════════════════════════════
 * ★铁律：「有动效就不用重点字弹出了」。
 *   同一个信息只能有一种表现方式，两个都上＝重复＝噪音。
 *   本轮删掉 4 个与数据可视化/其他动效重复的重点字：
 *     「三个月」   → 镜10 已有「¥0.22/天」日均价滚动
 *     「50多万单」 → 镜11 已有「累计销量」计数器
 *     「检测费」   → 镜6 画面本身是惊讶脸，语义已足
 *     「随意对比」 → 属 CTA 前夕的口播内容，字幕已承载
 *   保留 3 个：**没有替代动效**的纯语义强调。
 */
const KW: KwSpec[] = [
  // 镜5「重金属微生物超不超标」—— 质疑语气，全片核心转折点
  {startSec: 11.50, durSec: 1.57, text: '超不超标', x: 1520, y: 300,
   assColor: '&H004A6BFF', anim: 'warn'},
  // ★v19 删除「一批批」重点字 —— 灿爹拍板「一批批这里音效不要」。
  //   重点字一出现就自带弹簧音（渲染器按 KwSpec 自动配音效），
  //   所以「字 + 音」必须一起删，只删音效字还在、或只删字音还在，都不对。
  // 「随货」保留：它是同句里唯一的认证落点，不带音效。
];

/* ══════════════════════════════════════
 * ⑤ 镜间转场 —— ★★ v21 已整体停用
 * ══════════════════════════════════════
 * 灿爹拍板「把今天优化的镜间转场去掉，应该是它出现的黑影」，
 * 实测确认判断正确：压暗幕布在黑底镜位会读作「画面突然暗一下」，
 * 配合 bell 曲线＝「黑影闪一下」。宁可不加，也不要黑影。
 * ⇒ TRANS 置空，13 镜恢复原生硬切（底片本身按shot_bounds 精确切，无缝）。
 *
 * 若将来要重启，务必满足两条：
 *   ① 不压暗（dim≤0.15），只做极轻微的亮度过渡；或干脆用镜位内元素动效衔接
 *   ② 验收：黑底镜位（镜2/3/6）转场帧的灰度均值必须与相邻帧差 <8
 */
const TRANS: TransSpec[] = [];

const FPS = 30;
const TOTAL = 1000;   // 33.33s，冗余 32 帧，overlay 端 eof_action=endall 截断

export const FxLayerCurrent: React.FC = () => (
  <AbsoluteFill style={{background: 'transparent'}}>
    {/* ★★★ v22 删除呼吸层（BreathLayer）—— 灿爹报障「一个分镜里面突然黑一下」。
     *
     *   实测根因（用 Pillow 扫全部 1000 帧的 alpha 通道定位）：
     *     ① BreathLayer 把**分镜原图整张铺满 1920×1080** 盖在底片上；
     *     ② PNG 在这些帧**丢掉了 alpha 通道**（模式从 RGBA 变成 RGB，
     *        左上角像素是近黑的 (1,1,1)）⇒ 变成一张**不透明的黑图**；
     *     ③ 结果：镜2/3/7/9/12 的入点各闪一次黑屏（alpha从 0 瞬间跳到 255）。
     *
     *   ★设计层面的错：底片本身已有 Ken Burns（zoompan）做推拉，
     *     再叠一层全屏图 = 双重缩放 + 覆盖 ⇒ 必然出黑边与黑闪。
     *     Ken Burns 已够，不该再叠全屏图层。
     *
     *   保留 MoteLayer（飘尘粒子）——它是前景零散小点，不铺满、不遮挡主体。
     */}
    {/* ① 飘尘粒子（保留） */}
    <MoteLayer list={MOTES} />
    {/* ② 飘尘粒子 */}
    <MoteLayer list={MOTES} />
    {/* ③ 数据可视化 */}
    <VizOverlay list={VIZ} />
    {/* ★v18 删除 ProductCardFx —— 灿爹拍板「图片不能作为卡片弹出」。
     *   两个问题：① 镜7 画面主体本身就是产品粉剂（右下角大罐），
     *     再弹一张同款小卡片＝同物重复，观众只会觉得画面脏；
     *   ② 卡片从右侧滑入时压在人物头部右上方，与字幕/重点字三层叠加，出场观感乱。
     *   ⇒产品镜就让产品本身在画面里，用呼吸层做动感即可，不加卡片。*/}
    {/* ④ 重点字 */}
    <KwOverlay list={KW} />
    {/* ★★ v21 删除整个转场层 —— 灿爹拍板「把今天优化的镜间转场去掉，
     *   应该是它出现的黑影」。
     *   实测确认：v18.1 加的压暗幕布即使去掉黄光带、在黑底镜位
     *   （镜2/3/6 黑板宣言图）仍会读作「画面突然暗一下」，
     *   加上 0.34~0.42s 的 bell 曲线，观感就是「黑影闪一下」。
     *   ⇒ 宁可不加转场，也不要有黑影。TRANS 已清空，13 镜恢复硬切。*/}
  </AbsoluteFill>
);

registerRoot(() => (
  <Composition
    id="FxLayerCurrent"
    component={FxLayerCurrent}
    durationInFrames={TOTAL}
    fps={FPS}
    width={1920}
    height={1080}
  />
));
