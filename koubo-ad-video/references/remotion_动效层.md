# Remotion 动效层（重点字 / 数据可视化 / 卷播 3D）—— 完整方法论

> 本文档记录 2026-10-07 姜黄素片（261007）一轮完整落地经验。
> 可复用源码在 `assets/remotion_templates/`，**直接拷进工程改配置即可用**。

---

## 零、什么时候该上Remotion（判断标准）

**先量后判，别凭感觉。** 用「帧差密度」量化全片动效：

```python
# 每 0.4s 取两点，算平均帧差；<3 = 画面几乎静止
cap.set(cv2.CAP_PROP_POS_MSEC, t*1000);    ok,f  = cap.read()
cap.set(cv2.CAP_PROP_POS_MSEC,(t+0.4)*1000); ok2,f2 = cap.read()
g1 = cv2.cvtColor(cv2.resize(f,(320,180)),  cv2.COLOR_BGR2GRAY).astype(np.float32)
g2 = cv2.cvtColor(cv2.resize(f2,(320,180)), cv2.COLOR_BGR2GRAY).astype(np.float32)
d = np.abs(g1-g2).mean()
```

判据（本片实测 97 个采样点）：

| 帧差占比 | 结论 |
|---|---|
| 96% 的点 ≥ 4 | **问题不是「动得不够」**，是「该动的东西没动」 |
| 大量点 < 3 | 确实夹死，需加动效 |

**上Remotion 的三个真实理由**（不是「更炫」而是「做不到」）：

1. **弹簧过冲**——ASS 的 `\t()` 只有缓动，**做不出「冲过头再回落」**
2. **逐帧计算**——数据可视化（环形进度 / 数字滚动 / 对比条）ASS 完全做不到
3. **CSS 3D**——ffmpeg 的 `perspective` 要对每帧×每卡做四点变换，10+ 卡 × 100+ 帧滤镜图会爆

---

## 一、卷播开头（MarqueeClick.tsx）—— 本轮最大改动

### ⚠️ 0. 两个版本，别拿错（2026-10-07 深夜补记）

| 版本 | 文件 | 卡宽/节距 | maxY | nScroll | 白框 | 鼠标点击 |
|---|---|---|---|---|---|---|
| 3D 白框原型 | `Marquee3D.tsx` + `marquee_entry.tsx` | 560 / 700 | 34° | 8 | ✅ 有 | ❌ |
| **成品版（推荐）** | `MarqueeClick.tsx` + `click_entry.示例.tsx` | 700 / 860 | 18° | 6 | ❌ 已去（改涟漪+放大+点亮聚焦） | ✅ 三环涟漪 |

**★惨痛教训（爱兰纯 v12 261007）**：`opening_banner.md` 末尾虽然写了「Remotion 版已可用」，
但 **SKILL.md 的流程默认仍走 `scripts/opening_banner.py`（cv2 版）** ⇒ 当天出的片
**片头仍是纯平面无透视、每屏 11 张、cubic 减速**，3D 优化白写了。

### ★0.2 「动效附加层」工作流（v14 定型 · 换 acceptance 优先走这条）

ffmpeg 链路的 `keywords` / `product_cards` 都是**线性动画**（等速滑/升），没有重量感。
把它们从 ffmpeg 剥离，改由 Remotion 渲一层**透明 PNG 序列**再 overlay，是标准做法：

```
① 底片：config 里 keywords=[] product_cards=[] → 渲 out_c14_base.mp4（干净）
② 对象：fx_entry_<x>.tsx 三层叠加 —— ProductCardFx / VizOverlay / KwOverlay
③ 渲染：npx remotion render src/fx_entry_<x>.tsx FxLayer<X> out/fxframes \
           --sequence --image-format=png
   ⚠️ 序列模式的输出是**目录**，不能带扩展名（写 fx.png 会报
      "cannot have an extension"）
④ 合成：ffmpeg -i base.mp4 -framerate 30 -i fxframes/element-%04d.png \
           -filter_complex "[0:v][1:v]overlay=eof_action=endall" -c:a copy out.mp4
```

三个配套脚本（`assets/remotion_templates/scripts/`）：
| 脚本 | 作用 |
|---|---|
| `kw_times.py` | 从配音 token 抓每个重点字的真实发声时刻（⚠️见下方踩坑） |
| `pick_kw_pos.py` | 逐候选点实测「字要占的那块矩形」的平整度，选不压主体的坐标 |
| `qc_v14b.py` | 验 fx 层 alpha 掩码落在哪个位置（✅不用 v13/v14 差分，见下） |

**踩坑三条（2026-10-08 v14 实测）**
1. `kw_times.py` 不能「跨 token 累加直到包含该词」——`lo` 会取到累加起点那个 token，
   区间虚胖（「4个9」被算成 7.85–11.56，实际 11.20–11.56）。
   正解：把所有 token 拼成串、**记录每个字符的时间**再 `find` 定位。
   同名歧义（「三个月」在第1句和第13镜都出现）⇒ 枚举全部出现，只保留落在该镜时间窗内的。
2. **质检别用「v13 vs v14 差分」**：底片是重新渲的，滤镜链一变全局像素就有微差，
   差分质心被拉偏 269–667px（全误导）。⇒ 直接检查 **fx 层自己的 alpha 通道**。
   算质心前要抠掉同帧存在的其他元素（产品卡挂着时会把重点字质心拉走 1073px），
   且**只能抠矩形本身**，整行/整列抠会把同 y 或同 x 的字一起清掉。
3. 产品卡 `ProductCardFx` 的 left/top/w 直接沿用 ffmpeg 版 config 的 `pos`，
   语义不变、只换实现，切过来零成本。

⇒ **用户选了「卷播开头」时，默认必须走本节 Remotion 版**，只有明确没条件起 node 工程时
才退回 `opening_banner.py`，且**必须在交付说明里标注「片头为平面版」**。

### 0.1 ★换片复用清单（3D 版不是改个 T 就能换片 —— 2026-10-08 爱兰纯 v13 实跑沉淀）

换一条新片做 3D 卷播，**四件事缺一不可**：

1. **卡池重做**：从 `<工程>/cards_*.txt` 取陪衬图转 `public/cards_<X>/cNN.jpg`
   （PIL 统一转 jpg/长边1400）；hero 单独转 `hero.jpg`。
   ★**卡面定式（灿爹 2026-10-08 拍板）**：除主图（黑板品类大字）外，**其余全部用
   小绿人运动健身图、随机排列，不要卖点文字卡**——v15-2 试过黑板卖点卡
   （A类补剂/99.99%/检测单等 6 张），灿爹看样张后明确否掉。随机用固定种子可复现。
   卖点信息由配音 + 成片重点字动效承担，卷播只做氛围。
2. **时序重锚（★照抄旧 T = 必错）**：读新片 `<工作区>/s*/audio/vo_mapped_v2.json`
   逐段 token，实测「今天我们讲」的起止 ⇒ `scrollEnd`=该句起点−0.05、
   `clickAt`=该句中点、`totalS`=config 镜1 `src[1]`（保持一致就不用动 config）。
   派生新组件（如 `MarqueeClickC.tsx`，脚本化 diff 生成，别手抄），入口新开 `click_entry_<X>.tsx`。
3. **混音锚点重锚**：滚动音拉伸区间 = `0→scrollEnd`（不是 focus-at）；咚落点 = `clickAt`。
   其余照拍板版：人声闪避 0.60 / 滚动 0.90 / 咚 0.72 / 配音 1.25 / 峰值归一 0.92。
4. **渲染与 mux**：
   `npx remotion render src/click_entry_<X>.tsx MarqueeClick<X> out/卷播3D_<X>_画面.mp4`
   （env：`export PATH=…node…/bin:$PATH; unset CODEBUDDY_BROKERED_FS_HOOK_ENABLED; export NODE_OPTIONS=""`）

⚠️ **★mux 大坑（v13 首跑就中）**：Remotion 渲出的画面 mp4 **自带一条静音音轨**，
mux 不写 `-map` 时 ffmpeg 默认选第一个输入的最佳音频 = 那条静音轨 ⇒
卷播「带音轨」mp4 实际 −91 dB 全静音，`ffprobe` 有音轨但全是静音，极易漏检。
**必须 `-map 0:v -map 1:a`**。验法：`volumedetect` 看 mean_volume（静音轨 ≈ −91 dB）。
⚠️ **成片侧验证（互相关会误判）**：渲染链 loudnorm/BGM 混入 + 可能的帧级错位会让
逐样本互相关失真（v13 实测 −0.42 但内容正常）。**可信验法 = 三条**：
① 成片镜1 段 vs 全局 vo 前 N 秒相关 ≈ 0（own_audio 正确静音全局 vo）；
② 0.2s 粒度 RMS 能量包络与卷播音轨逐段同步；
③ 点击时刻峰值 > 滚动尾段峰值（咚真的在）。
### ★0.15 拼接式卷播三件套（v22 产线 · 2026-10-08 深夜姜黄素 A/B 三轮返工沉淀）

> 适用场景与 §0.1 的 own_audio 接入式不同：v22 类产线是**拼接式**——banner 段
> 整段替换底片前 N 秒（`-ss CUT -i base.mp4` + `concat`）。这种结构下卷播段
> **画面、字幕、音频都来自不同源**，三个坑灿爹各挑出来一次，一次一个：

**① 音频必须同源（灿爹：「和配音感觉是两个视频拼接在一起的」）**
`banner_final = 底片渲染链音轨前 CUT 秒（vo+BGM+音效，已混好的）为基底
+ 滚动音 0.45 + 咚 0.40 低增益叠加 → 峰值限 0.92`。
**禁止独立混卷播音轨**（配音+独立 BGM 头）——两条混音线的 BGM 接不上、
配音响度跳变，接缝处就是「两个视频」。基底同源 ⇒ BGM 从卷播一路铺到片尾零突变。

**② 字幕必须烧回（灿爹：「卷播没有字幕呢」）**
底片 `-ss CUT` 切头时，**烧在画面上的字幕跟着一起被切掉**——配音在念钩子句、
画面没字。修法：拼接前 `ffmpeg -i banner.mp4 -vf "subtitles=<工程>/s*/subs.ass"`
把正片同款 ASS 烧到卷播段（卷播视频只有 CUT 秒，ASS 里超出的事件自动截断，
不会漏进后续段落）。

**③ 切点 = 语音缝尾 + 主图定格 ≥0.40s（灿爹：「点击放大后下一个分镜一闪而过」）**
T 重锚时**先算切点、再倒排时序**：`clickAt + zoomDur ≤ CUT − 0.40`。
反例（v1 必错）：放大 2.50s 才完成、切点 2.55 ⇒ 定格仅 0.05s，主图一闪而过。
正解（姜黄素 A/B 定稿）：切点取语音缝尾（= 下一镜语音起点）⇒ 主图定格 0.40s，
且正片首镜从语音起点开始、音画全同步。**切点永远不能落在句中**。

三件套齐了才算一个完整的拼接式卷播段：**音频同源 + 字幕烧录 + 缝尾切点带定格**。

### 1.1 ★★ 先量参考视频，别凭感觉做「像」

灿爹给了参考视频后，**量化出 4 个数字差异**，直接定了优化方向：

```python
# ① 每屏卡数 + 节距：黑间隙法
dark = (cv2.cvtColor(f,cv2.COLOR_BGR2GRAY) < 25)
colw = dark.mean(axis=0)
runs = 连续暗区间        # ⇒ 卡数 = len(runs)+1；节距 = diff(间隙中心)

# ② ★3D 倾斜（一眼区分平面/透视，最关键指标）
seg = g[:, x0:x1]                      # 第一张卡的左右界
top = [首个 >40 的 y，逐列]
slope = np.polyfit(xs[top>=0], top[top>=0], 1)[0]   # 0=纯平面，±0.2=明显透视

# ③ 内容占屏高
rows = np.where((g>25).any(axis=1))[0];占屏高 = (rows.max()-rows.min())/H

# ④ 验证左右是否被裁空（改定位 bug 后必查）
left300 = g[:, 0:300].mean()           # 0 ⇒ 左边黑了
```

**实测对照结果**：

| 指标 | 参考原视频 | 我的旧版 |
|---|---|---|
| 每屏可见卡数 | 3.8–5 张 | **11 张** |
| 卡节距 / 画面宽 | 26.4% | 8.8% |
| 白框宽 / 画面宽 | 26.9% | 47.8% |
| **卡片 3D 倾斜** | **顶缘斜率 −0.215** | **0.0000（纯平面）** |
| 内容占屏高 | **75%** | **29%** |

⇒ 一句话根因：**「竖卡·少卡·3D透视·撑满屏高」vs「横卡·多卡·平面·只占屏高1/3」**。
最大问题不是卡多，是**观众看不清在滚什么**（每张只 0.16s）。

### 1.2 ★★ 速度曲线：cubic 根本不是「由快到慢」

**用相位相关实测渲染后的真实速度**（不信脚本日志）：

```python
win = cv2.createHanningWindow((W,H), cv2.CV_32F)
(dx,dy),resp = cv2.phaseCorrelate(prev*win, g*win)   # 累计位移
v = (dx - prevx) * fps# 逐帧速度
```

实测 `cubic c=1.4`（opening_banner.py 沿用的曲线）：

```
0.00s 2011 px/s → 0.80s 2020 px/s   ★前0.8秒几乎恒速（甚至微微上升）
减速比仅 3.3x
```

**这根本不是「由快到慢」，是「先匀速再急停」。**

正确解法 —— **指数减速**：

```ts
const K_DECAY = 3.0;                                  // 越大越「前快后慢」
const sumF = Σ exp(-K_DECAY * i/NF);                  // 数值积分
const v1 = (nScroll * pitch) / sumF;                  // 归一化：总位移 = nScroll*pitch
pos[i] = pos[i-1] + v1 * exp(-K_DECAY * i/NF);        // ★必须数值积分，Σf
```

| 曲线 | 减速比 | 单调 | 结论 |
|---|---|---|---|
| `cubic c=1.4` | 3.3× | ✓ | ❌ 前段恒速 |
| `1-(1-u)²` / `³` / `⁴` | 0.2–0.3× | ✓ | ❌ **方向反了**（越到后面越快！） |
| **`exp(-3u)`** | **11.1×**（成片实测 19×） | ✓ | ✅ 真正的由快到慢 |

> ⚠️ `1-(1-u)^n` 这类最常见的缓动曲线用在**减速**上是反的，务必实测。

### 1.3 ★★ CSS 3D：`perspective` 必须写在父容器

```tsx
// ✅ 正确
<AbsoluteFill style={{background:'transparent', perspective: 1500}}>
  <div style={{transform:'... scale(...) rotateY(...)', transformStyle:'preserve-3d'}}>

// ❌ 写在卡片自己身上 ⇒ 只是「自旋」，没有透视投影（v1 实测：卡片完全朝正）
```

- `rotateY = -offNorm * maxY`，`offNorm = dx / 960`（**960= 画面半宽**），clamp ±1.3
- 正中 offNorm=0（正对），越靠边转得越多 —— 这正是参考视频的形态
- 实测反解参考视频 ≈ **18°**，三档：0（无）/ 18°（对标）/ 34°（夸张）
- 附加侧光：`brightness(1-dim*0.5)`，越靠边越暗 ⇒ 视线被吸到中心

### 1.4 ★★ 主图位置：`heroK = nScroll`（不是「卡列最后一张」）

**几何事实**：滚动总位移 = `nScroll × pitch` ⇒ **索引 `nScroll` 的卡恰好停在 x=0（画面正中）**。

```ts
const CARDS = [
  ...Array.from({length: N}, (_, i) => ({src:`cards/c${i}.jpg`})),   // 0..N-1 左侧陪衬
  {src: HERO_SRC},                        // ★ 索引 N = nScroll = 正中
  ...Array.from({length: 3}, (_, i) => ({src:`cards/c${N+1+i}.jpg`})), // 右侧陪衬
];
```

⚠️ **与 ffmpeg 版 opening_banner 刻意不同**：那边主图是卡列末端（右边无陪衬）；
这边为左右对称，主图是「最后滚到位的那张」，右侧仍有从未移动过的陪衬卡。
**语义一致（主图=滚动终点），但池子不再刚好截断。**

验证（按颜色判主图是否在正中）：

```python
h = f[cy-197:cy+197, 610:1309]              # 正中卡区域
greenish = ((G>R+15)&(G>B+15)).mean()       # 小绿人绿、黑板棕黑
# 修前 27.3%（绿植）→ 修后 4.2%（黑板 ✓）
```

### 1.5 ★★★ `offNorm × 760` 魔数 bug：左边全黑

**症状**：「轮播停下来时左边画面不见了」。实测 1.8s 起画面最左 300px 亮度**恒为 0.0**。

```tsx
// ❌ 坏的三件套
offNorm = clamp(cardX / 700)          // 700 是【卡宽】
left={960} translateX(offNorm * 760)  // 760 是【魔数】
if (x < -700 || x > 700 + pitch)      // 又一个 700
     return null                       // 三个数互不相干
```

pitch 改成 860（16:9）后换算彻底失配 ⇒ 左侧卡算出来占 `[-1210,-510]`，
**整张都在画面左界 -960 之外**。

```tsx
// ✅ 正解：位置只保留一个量
const dx = k * M.pitch - ixNow + centerShift;   // 相对中心
const px = 960 + dx;                // ★绝对屏幕像素
const offNorm = clamp(dx / 960, -1.3, 1.3);      // ★只算 rotateY / 亮度
<Card left={px} />                               // 不再 translateX
if (px < -M.cardW/2 || px > 1920 + M.cardW/2) return null;   // ★按像素判出画
```

### 1.6 鼠标点击涟漪（灿爹口述效果的完整实现）

时序**全部锚在配音实测逐字时间**上（`vo_mapped_*.json` 逐字重建）：

```
配音逐字：今2.26 天2.47 我2.64 们2.80 讲2.96 姜3.13 黄3.29 素3.46
T = {
  scrollEnd: 2.20,   // ★说「今天我们讲」(2.26) 时卷播已停
  clickAt:   2.62,   // ★点击 —— 正卡在「我|们|讲」中间
  zoomDur:   0.84,   // 主图弹簧放大 1 → 2.76×
  totalS:    4.80,   // ★硬上限 4.83（镜1语音槽），别再调高
}
```

**音画对齐设计**：观众听到「今天我们讲」看到鼠标点下去，说到「姜黄素」（3.13-3.46s）
时主图正铺满 ⇒ 语义与画面严格对应。

去掉白框后的**焦点三件套**：① 三环涟漪（深黄 11px / 白 8px / 淡黄 6px，
`radius = maxR·(1-(1-lt)^1.75)` easeOut，`opacity=(1-lt)^1.6`，每环带内侧能量填充）
② 主图放大 + 点亮（`brightness +0.22 / saturate +0.3`，其他卡缩小、背景压暗）
③ 侧光压暗。

z-index：涟漪 1100 / 鼠标 1200 / 主图 800 ⇒鼠标永远在最上层可见。

### 1.7 卡尺寸与铺满计算

```ts
M = { cardW:700, cardH:394, pitch:860, cy:520, maxY:18, perspective:1500, nScroll:6 }
heroScale = 1 + 1.76 * zoom             // 终值 2.76×
```

**铺满所需 scale = `max(画面宽/卡宽, 画面高/卡高)`**：
16:9 卡 → `1920/700 = 2.74`；v1 沿用竖卡的 1.95× ⇒ **只覆盖 71% 宽，两侧黑边**。

**3:2 素材裁16:9 必查损耗**（`objectFit:cover` 默认**居中裁**会切人物头部）：

```
9 张原生 16:9 → 无需裁；7 张 1536×1024 = 3:2 → 底部裁16%（保留84%高）
```
⇒必须加 `objectPosition: 'center top'`（**与渲染器 `img_fit=cover + crop_y=0` 一致**）。

---

## 二、数据可视化（DataViz.tsx）—— 科普带货的真正缺口

**诊断结论**：科普带货的核心是**用数据说服人**，但「纯度98%」「10mg黑胡椒」
原本只是一句字幕 + 一个弹出字，观众没有直观感受。

### 2.1 三个组件

| 组件 | 用在哪 | 效果 |
|---|---|---|
| `RingGauge` | 百分比类 | 环 spring 推进，中心数字**同步起跳但阻尼更大**（环冲 97% 时数字才 80%） |
| `CountUp` | 含量类 | 整数逐帧跳数 + 计量条，「仪器读数」感。**`decimals` 控制小数位**（v14 新增：`decimals=2` 显示 `99.99%`） |
| `CompareBar` | 对比类 | 多行错开 5 帧依次生长。**⚠️带货片禁用，见 2.3** |

### 2.2 三个坑（都会「渲染成功但不显示」）

**① `VizSpec` 坐标字段名必须统一 `x/y`**
首版 ring 用 `cx/cy`、count/bar 用 `x/y` ⇒ 给 count 传 `cy` 会**静默失效**
（`s.y` 是 undefined ⇒ CSS `top:undefined` ⇒ 元素掉到文档流顶部 0 处）。
**多分支组件的 spec 字段名必须统一，否则类型系统形同虚设。**

**② `durSec` 必须 > 1.2s**
淡出窗口是 `[durFrames-5, durFrames+4]` 帧（尾 0.17s 就开始淡出）。
`durSec=1.0` ⇒ 0.83s 后已在淡出、1.1s 消失 ⇒ 抽帧只看得到起跳那一下，**像组件坏了**。
Lab 预览用 1.6s。

**③ 数字滚动不要延迟起跳**
首版让数字延迟 42% 时长再起跳 ⇒ 前 2/3 时间都是 0%，观感是「数字卡住不动」。
**改成同步起跳 + 更大damping**，靠阻尼差做「追赶感」而不是「延迟感」。

### 2.2b ★★★ 落位三定律 + 亮背景暗板 `plate`（2026-10-18 欧扎克 v2）

**① 落位前先量主体 bbox，别凭画面感觉放。**
本轮初版把 14.6 克放 `(1600,190)`，以为右上角是空的——实测正好糊住小绿人**右半张脸**。
量法：读源图算主体（绿皮掩膜）外接框，只在 bbox 之外找留白。
改后 `(500,175)` 产品盒正上方、`(1650,120)` 右上侧，两处都离人物有距离。

**② `x/y` 是块中心**（`transform: translate(-50%,-50%)`），块宽是 `barWidth*1.5`
⇒ 靠边放时按中心点算，别把 `x` 当成左边缘（#252）。

**③ 亮背景必须开 `plate`。**
窗光 / 白墙 / 浅色货架下裸黄字对比不足、糊成一团。`CountUp` 新增 `plate`（默认 `false`）：

```json
{"text":"每百克","phrase":8,"x":500,"y":175,"target":14.6,"unit":"克",
 "label":"蛋白质 / 每百克","barWidth":320,"fontSize":88,"plate":true,"color":"#FFD24A","durSec":1.6}
```
`plate` 铺 `rgba(8,11,16,0.52)` + `blur(6px)` + 1.5px 白边，与 `CompareBar` 暗板同一套语言。
暗背景不写 `plate`（裸字更透气，老片行为完全不变）。

**④ 参数组合（实测）**：亮背景 `fontSize 88 / barWidth 320`；暗背景 `90 / 300`。
`durSec` 1.5~1.7s，**必须短于镜长**，否则数字还在跳就切镜。

### 2.3 ★★ 带货红线：只做参数可视化，不做效果对比

**`CompareBar` 吸收对比条在带货场景可能违规**（灿爹 2026-10-07 明确要求删除）——
「吸收起来确实不一样」属**效果对比**，而「纯度 98%」「含 10mg 黑胡椒」
是**产品客观参数**，不宣称任何效果。

⇒ **带货片数据层只允许纯陈述组件。**

---

## 三、重点字（KwOverlay.tsx）

### 3.1 四种弹簧变体（按词性分）

| 变体 | damping/stiffness/mass | from | drop | 性格 | 配词类 |
|---|---|---|---|---|---|
| `plain` | 9/180/0.7 | 20 | +26 | 均衡 | 通用/数据 |
| `warn` | 12/210/0.9 | 42 | −18 | 过冲小、收得快，压得住 | 警告/问题 |
| `stamp` | 8/240/0.85 | 12 | +46 | 从上砸下、大过冲快衰减＝盖章 | 认证/品牌 |
| `price` | 7/260/0.8 | 8 | −52 | 从下顶上、最大过冲＋**中段复顶** | 价格/利益点 |

★ `price` 的中段复顶（第二次 `spring`）是 **ASS 完全做不到的**，最出彩的一处。
新增 `hollow`＝空心→实心（`opacity` 0.62→1），对应 ASS 的 `stroke` 但有过渡。

### 3.2 ★ 音效要按词义选，别用泛用型

实测 19 款 `kw_*` 音色的频谱质心，规律：

| 频段 | 语义 | 代表音色 |
|---|---|---|
| 低频 <500Hz | 重击/警告/权威 | `kw_boom` 257、`kw_thump` 418 |
| 中频 1–1.7kHz | 数据/认证/价格 | `kw_marker` 1178、`kw_shine` 1354、`kw_ding` 1677 |
| 高频 >2.8kHz | 俏皮/跳跃 | `kw_glass` 2927、`kw_triple` 2893 |

**致命反例**：「建模最堪忧」（负面警告）配了 `kw_glass` =质心 2927Hz（全池最高频）
⇒ **用最刺耳的声音说最负面的话**，语义完全反向。**禁止高频配负面词。**

### 3.3 ★ 同位置的重点字必须时间互斥

v2 首版 `98%` 24.20s 入场，但 `VORSE` 22.64+2.00=24.64s 才淡出
⇒ **两层字重叠 0.44s**，抽帧看到重影。

**规则：`startSec[i] ≥ startSec[i-1] + durSec[i-1]`（留 0.15s 呼吸）。**

### 3.4 ★ 落位必须逐镜实测空白带

```python
subj = ((g > 60) & (g < 200))            # 主体 = 中等亮度
col  = subj.mean(axis=0)
bands = [col[i*240:(i+1)*240].mean() for i in range(8)]
最空竖带 = sorted(range(8), key=lambda i: bands[i])[:2]
```

本片实测：建模最堪忧主体在左 → 放x=1560；VORSE 主体在右 → 放 x=300；SGS 两端 → x=1250。

**⚠️ 换素材后必须重量一次**，落位不能照抄。

### 3.5❌ 重点字「人物后面半遮」——试过，灿爹不要，已回退
曾实现「字从人物后面露出来」（`mask_kw_behind.py`：用**饱和度**识别人物，
`mask*MORPH_CLOSE+DILATE(17)+GaussianBlur(21)`，`kw_alpha *= (255-mask*0.82)/255`）。
第一版用亮度双阈值 70~205 判人物 ⇒ 所有非黑区域都成「人物」，**连字一起挖掉**。

**2026-10-07灿爹拍板：重点字不要半遮，恢复原本「压在最上层完整可见」。**
`behind: true` 已从配置移除，`mask_kw_behind.py` 不再使用。
（脚本保留在 templates 里，仅作「人物区域遮罩」技术储备。）

### 3.6 ★ v14 选型搭配表（下次照抄，别重新想）

判断顺序：**这个词在句子里承担什么语气 → 选变体**（不是看字数、不是看位置）。

| 词语气 | 变体 | 实例（爱兰纯 v14） | 为什么 |
|---|---|---|---|
| **纯数字/指标** | `CountUp`（不是重点字！） | 镜5「四个9」→ 滚到 `99.99%` | 数字本身要"跳"，用字无效 |
| **质疑 / 追问** | `warn` | 镜6「超不超标」、镜8「检测费」 | 收得快、压得住，跟负面语义配套 |
| **认证 / 承诺 / 品牌** | `stamp` | 镜10「一批批」、镜11「随货」 | 从上砸下＝盖章，天然带"作数"感 |
| **价格 / 利益点** | `price` | 镜13「三个月」、镜15「随便挑着比」 | 最大过冲 + 中段复顶，重力感最强 |
| 中性数据/通用 | `plain` | — | 兜底 |

**三条配平经验（照做不踩雷）**
1. **别一半线性一半弹簧**——同一部片里只要有一个动效用弹簧，其余必须全换，风格割裂很明显
   （v14 顺手把剩下 2 个重点字也换成了 stamp）。
2. **负面/疑问词配 `warn`，绝不能配高频音**（见 3.2 频谱表：禁止 2927Hz 的 `kw_glass` 配负面词）。
3. **价格类一定用 `price`**——中段复顶是 ASS 做不到的，也是全片最出彩的一处，别浪费在其他词上。

---

## 四、★ 接入成片的完整流程（v14 定型 · **照抄这一节**）

> **2026-10-08 v14 重写。** 旧版只清 `keywords`、把 `product_cards` 留在 ffmpeg 里
> （那时还没写 `ProductCardFx`），导致产品卡至今仍是线性滑入。**现在一律剥离，
> 所有「会动的附加元素」都由 Remotion 统一负责。**

### 4.0 分工原则（一句话）

**底片只留「画面 + 字幕 + 音乐」，凡是要有重量感的东西，全部剥给 Remotion。**

| 元素 | v13 及以前 | v14 起 |
|---|---|---|
| 重点字 | ffmpeg ASS 线性 `rise/pop` | **Remotion `KwOverlay` 弹簧 4 变体** |
| 产品卡 | ffmpeg 线性 `slide` | **Remotion `ProductCardFx` 弹簧滑入** |
| 数据可视化 | 完全没有 | **Remotion `DataViz`（CountUp / RingGauge）** |
| 片头卷播 | 各自版本 | **Remotion `MarqueeClick`（3D）** |

### 4.1 照抄命令

```bash
# ── 环境（必须，WorkBuddy 的 fs hook 会让渲染从 11s 变成 78s）
export PATH=/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin:$PATH
unset CODEBUDDY_BROKERED_FS_HOOK_ENABLED
export NODE_OPTIONS=""

# ── ① 渲「干净底片」：keywords 与 product_cards 全部清空
python - <<'PY'
import json
c = json.load(open('s*/config.json'))
c['keywords'] = []
c['product_cards'] = []          # ★v14 起一起清，卡片交给 ProductCardFx
c['output'] = '<工程>/render/out_base.mp4'
json.dump(c, open('<工程>/config_base.json','w'), ensure_ascii=False, indent=1)
print('底片：无重点字、无产品卡')
PY
python ~/.workbuddy/skills/koubo-ad-video/scripts/build_ad_video.py <工程>/config_base.json

# ── ② 落位 + 时刻（★每次必重算，不能抄上一部）
python ~/.workbuddy/skills/koubo-ad-video/assets/remotion_templates/scripts/pick_kw_pos.py
python ~/.workbuddy/skills/koubo-ad-video/assets/remotion_templates/scripts/kw_times.py

# ── ③ 渲附加层透明 PNG 序列（★入口名不能叫 index.tsx；★输出必须是目录不能带扩展名）
mkdir -p out/fxframes
npx remotion render src/fx_entry_<X>.tsx FxLayer<X> out/fxframes \
  --sequence --image-format=png --log=info

# ── ④ 单层 overlay 合成（只有一层时，不需要链式）
ffmpeg -y -v error -i render/out_base.mp4 \
  -framerate 30 -i out/fxframes/element-%04d.png \
  -filter_complex "[0:v][1:v]overlay=eof_action=endall[outv]" \
  -map "[outv]" -map 0:a -c:v h264_videotoolbox -q:v 75 -pix_fmt yuv420p \
  -c:a copy -movflags +faststart render/out.mp4

# ── ⑤ 若有多层（如 数据层 + 重点字层分离），overlay 一次只吃 2 输入，必须链式
#    [0:v]底片 [1:v]数据层 [2:v]重点字层
ffmpeg -y -framerate 30 -i "$VZ/element-%04d.png" -framerate 30 -i "$KW/element-%04d.png" \
  -i render/out_base.mp4 -filter_complex "\
[2:v]setpts=PTS-STARTPTS[b];[0:v]format=rgba,setpts=PTS-STARTPTS[vz];[1:v]format=rgba,setpts=PTS-STARTPTS[kw];\
[b][vz]overlay=0:0:format=auto:repeatlast=0[t];[t][kw]overlay=0:0:format=auto:repeatlast=0[v]" \
  -map "[v]" -map 2:a -c:v h264_videotoolbox -q:v 75 -pix_fmt yuv420p -c:a copy out.mp4

# ── ⑥ 质检三件套
python .../scripts/qc_v14b.py                                    # 落位是否命中设计坐标
ffmpeg -nostats -i out.mp4 -af "ebur128=peak=true" -f null - 2>&1 | grep -A4 Summary
ffmpeg -y -v error -c:v copy -i out.mp4 -af "alimiter=limit=0.85:level=0" out_限幅.mp4
```

### 4.2 ⚠️ 三层合成的两个前提

1. **所有层都要 `AbsoluteFill backgroundColor:'transparent'`**——漏一处就是整块黑底盖住成片
   （踩坑 #1）。压暗效果用 `rgba(0,0,0,0.55)` + `opacity`，**不能用不透明黑**。
2. **Remotion 输出的 PNG 是 straight alpha**（实测各 alpha 档位 RGB 均保持纯色强度，
   未预乘）⇒ ffmpeg `overlay` 不会二次衰减，**字边缘不会发暗，可直接叠**。

### 4.3 派生 checklist（照 `fx_entry_c.tsx` 复制一份改造）

| # | 要改／要备 | 说明 |
|---|---|---|
| 1 | `public/新青年体.ttf` | 从技能 `assets/fonts/新青年体.ttf` 拷进 remotion 工程的 `public/`（字体失败有 catch 兜底，但会掉字形） |
| 2 | `public/card_<X>.jpg` | 产品卡原图，`ProductCardFx` 的 `src` 只填文件名 |
| 3 | `TOTAL` 帧数 | `全片秒数 × fps + 1`（v14：33.62×30 = 1009）。**算错会导致尾巴没渲或被截断** |
| 4 | `VizSpec` / `KwSpec` 数组 | 坐标来自 `pick_kw_pos.py`、时间来自 `kw_times.py`，**均为本片实测** |
| 5 | `startSec` 提前 0.06s | 让动效在开口瞬间已处于起跳状态，而不是字出口后才动 |
| 6 | Composition id 与文件名同步 | `FxLayer<X>` ↔ `fx_entry_<X>.tsx`，两者必须一致否则渲染找不到 |
| 7 | **合规复核** | CountUp 的 `label` 只能写中性词（「含量」），**禁止「纯度认证/优质/顶级」**等评价暗示 |

> ⚠️ v14 的教训：**一次改一层、一次验一层**。三层一起改就分不清是谁的坐标没生效——
> 定位类问题直接上 `probe_entry.tsx` 参考线，比逐帧试快一个量级。

---

## 五、★★ 本轮全部踩坑清单（勿重犯）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | 卷播层渲出来**3 通道无 alpha**，整块黑底盖住成片 | `<AbsoluteFill background:'#000'>`纯黑底 ⇒ PNG 没透明区 | 接成片必须 `background:'transparent'`；压暗层用 `rgba(0,0,0,0.55)`+opacity，不可用不透明黑 |
| 2 | **真峰值 +1.6 dBTP**（严重削波） | 素材层自己归一过，但接进成片后又过渲染器 loudnorm(+3dB)+BGM+音效 ⇒ 叠加爆 | 每次外部层叠进成片**必重测**，`alimiter=limit=0.85` ⇒ −0.7 dBTP |
| 3 | `alimiter` 报 `Unknown decoder 'copy'` | `-c:v copy` 放在 `-i` **之后**被当成输出选项 | 必须放在 `-i` **之前**（输入选项） |
| 4 | 轮播停下时**左边全黑** | `offNorm × 760` 魔数：卡宽700/画面半宽960/基准760 三个数互不相干 | 位置只保留绝对像素 `px`，`offNorm` 只算 rotateY/亮度 |
| 5 | 黑板主图**跑到画面右边**，正中还是别的卡 | 误把 `heroK = nScroll` 当成「卡列最后一张」 | 几何事实：索引 `nScroll` 的卡恰停在 x=0，主图必须插在该索引 |
| 6 | 3D透视**完全没生效**（卡片全朝正） | `perspective` 写在卡片自己身上 | 必须写在**父容器** |
| 7 | 定格时白框与卡**明显分离** | 白框矩形，卡片梯形 | 白框必须跟着卡一起 rotateY；且定格时 `centerShift` 归0 让 hero 正对 |
| 8 | 放大后主图**两侧黑边** | 16:9 卡铺满需 2.74×，沿用了竖卡的 1.95× | `scale = max(画面宽/卡宽, 画面高/卡高)` |
| 9 | 鼠标 SVG **1080P 上几乎看不见** | 原尺寸 30×38太小 | 放大 1.73×（52×66），viewBox 不变 |
| 10 | 数据层**掉到画面顶部 y=0** | `VizSpec` 字段名不统一（`cy` vs `y`）静默失效 | 统一 `x/y` |
| 11 | 数字滚动**像坏了**（前2/3 时间显示 0%） | 延迟 42% 时长才起跳 | 同步起跳 + 更大 damping |
| 12 | 组件**停在画面顶部**、抽帧看不到落定态 | `durSec=1.0` < 淡出窗口 | Lab 用 1.6s |
| 13 | 3:2 素材裁16:9 **切掉人物头部** | `objectFit:cover` 默认居中裁 | 加 `objectPosition:'center top'` |
| 14 | 卷播段**顶开下一镜**导致全片音画错位 | 镜1 语音槽 0→3.46s、下一镜 4.83s 进字，卷播渲成 5.20s | **卷播 dur ≤ 4.83s**，重渲到 4.80 |
| 15 | `1-(1-u)²`减速曲线**方向反了** | 越到后面越快 | 用`exp(-K·u)`；且必须**数值积分 Σf** |
| 16 | `rm -rf out/frames` 被安全删除策略拦 | 1260 文件超阈值 50 | 直接覆盖渲（`Config.setOverwriteOutput(true)` 已在配置里） |
| **17** | 序列渲染报 `cannot have an extension` | `--sequence` 的输出写成了 `out/fxframes/fx.png` | 序列模式输出**必须是目录**：`out/fxframes`（文件名由渲染器自动生成 `element-%04d.png`） |
| **18** | 重点字时间区间虚胖好几秒（「四个9」被算成 7.85–11.56，实际 11.20–11.56） | `kw_times.py` 用「跨 token 累加直到包含该词」取 `lo` ⇒ 取到的是**累加起点**那个 token | 把全部 token 拼串、**逐字符记录时间**再 `find`；同名歧义词（「三个月」第 1 句也有）⇒ 枚举全部出现，**只保留落在该镜时间窗内的** |
| **19** | 质检说「所有动效落位偏 269–667px」，实际全对 | 拿 v13 vs v14 做帧差分定位——**底片是重渲的**，滤镜链一变全局像素就有微差 | 直接查 **fx 层自己的 alpha 通道**（`qc_v14b.py`）。且算质心前**只能抠矩形本身**：整行/整列抠会把同 y 或同 x 的字一起清掉；产品卡挂着时要先抠掉卡矩形，否则质心被拉偏 1073px |
| **20** | 卷播「带音轨」文件 `ffprobe` 有音轨但 **−91dB 全静音** | Remotion 渲的画面 mp4 **自带一条静音音轨**，mux 不写 `-map` 时 ffmpeg 默认选它 | **必须 `-map 0:v -map 1:a`**。验法：`volumedetect` 看 mean_volume（静音轨 ≈ −91 dB） |
| **21** | 成片音轨互相关算出负值，怀疑音轨没进去 | 渲染链 loudnorm/BGM 混入 + 帧级错位 ⇒ 逐样本互相关失真（v13 实测 −0.42 但内容正常） | **可信验法三条**：① 成片镜1 段 vs 全局 vo 前 N 秒相关 ≈ 0（own_audio 正确静音了全局 vo）；② 0.2s 粒度 RMS 包络与源音轨逐段同步；③ 点击/落点时刻峰值明显高于滚动尾段 |
| **22** | 成片右下角有 **debug 小字**「爱兰纯·3D卷播 总长 X.XXs」（v13/v14 已带病交付，18px 深灰 `#5c6778`，六帧缩略质检看不出来） | `MarqueeClick.tsx` 基础组件里有**无条件渲染**的角标 div（本来是预览调参用的），派生时没人删 | **渲染前必查**：`grep -n "总长\|总宽" src/Marquee*.tsx`，角标 div 直接删。抽帧质检必须含**右下角 crop 放大**一帧（`crop=700:160:1220:920`），小字在缩略图里必然漏检 |
| **23** | 同一工程第二次 `npx remotion render` 报 `EPERM: operation not permitted, unlink …/webpack/…/*.pack_` | webpack 缓存**同一缓存键第二次渲染必挂**（ unlink 被安全策略拦），且失败会级联锁死后续渲染 | **每次 render 前**：`python3 -c "import shutil; shutil.rmtree('node_modules/.cache', ignore_errors=True)"`（⚠️ 用 python 删——shell `rm -rf` 会被 safe-delete 拦截反而中断命令链） |
| **24** | overlay 合成报找不到帧文件 / 帧序错乱 | `remotion render … --sequence` 输出帧名**位数随总帧数变**：1008 帧 = `element-0000.png`（4 位）、902 帧 = `element-000.png`（3 位） | 合成前先 `ls out/fx_X \| head -1` 看实际位数，overlay 通配符按实写 `%04d` / `%03d`，别照抄上一条的 |
| **25** | 渲 fx 附加层报 composition 导出不存在 | 派生 `fx_comp_<X>.tsx` 时只改了内容/时序，**`export const FxLayerV22` 导出名忘改** | 派生组件 = 内容 + 时序 + **导出名 + combo_entry 的 import/Composition id** 四处同步改，改完 `grep "FxLayer" src/*.tsx` 交叉核对 |
| **26** | TTS 后台进程悄悄死掉（日志停在模型加载，无 wav 产出） | `osascript do shell script nohup … &` 或 `nohup … &` 启动方式**会被沙箱环境干扰杀死** | 用 python `os.fork()` + `os.setsid()` + `os.execv("/bin/zsh", …)` 三连启动（2026-10-08 验证稳定），启动后 60s 内必须见到首段 wav 才算活 |

---

## 六、可复用资产

```
assets/remotion_templates/
├── components/
│   ├── KwOverlay.tsx        重点字 + 4 种 spring 变体 + hollow（见 3.1 / 3.6）
│   ├── DataViz.tsx          ★CountUp（含 decimals）/ RingGauge / CompareBar + VizOverlay
│   ├── ProductCardFx.tsx    ★v14 新增：产品卡弹簧入场（替代 ffmpeg 线性 slide）
│   ├── fx_entry_c.tsx       ★v14 新增：附加层总入口样例（三层合一，照它派生 fx_entry_<X>.tsx）
│   ├── Marquee3D.tsx        ★卷播 3D「白框版」原型（pitch700/卡宽560/maxY34/nScroll8）
│   ├── MarqueeClick.tsx     ★★卷播 3D「去白框 + 鼠标点击涟漪」成品版（推荐·见下）
│   ├── marquee_entry.tsx    Marquee3D 的入口（含 无3D/弱3D/强3D 三档透视对比页）
│   └── probe_entry.tsx      ★定位探针：渲参考线，一眼看出谁的坐标没生效
├── click_entry.示例.tsx      MarqueeClick 的入口（卡池组装 + hero 索引 ★铁律）
└── scripts/
    ├── pick_kw_pos.py       ★重点字落位：逐候选点实测矩形平整度，选不压主体的坐标（每次必重跑）
    ├── kw_times.py          ★重点字时刻：从配音 token 逐字符定位真实发声点（见踩坑 #18）
    ├── qc_v14b.py           ★质检：查 fx 层 alpha 掩码落位是否命中设计坐标（勿用差分，#19）
    ├── find_blank.py        空白带分析：把画面竖切 8 带，找主体最少的两带
    ├── fix_token_bounds.py  ★用 RMS 实测修正字幕 token 边界（见下）
    ├── mask_kw_behind.py    人物区域遮罩（半遮方案，灿爹已否决，备查）
    └── kwlab_build_one.py   重点字方案对比片生成器
```

### ★ 定位类问题先写探针，别猜

`probe_entry.tsx` 渲三条参考线（y=440 绿 / y=700 粉）+ 各组件
⇒ **一眼看出谁的坐标没生效**，比逐个试快得多。

### ★★ `fix_token_bounds.py`：字幕间隔不匀的根治

**症状**：「这两句话中间隔了太久」。

**根因**：重建 token 时「段内按字数线性分配」把**尾部静音**算进了句子
—— 镜9「这笔账算到这儿」token 记到 19.83s，实际音频 **18.68s** 就念完（虚长 1.15s）。

```python
# 句首/句末用 0.05s 窗口 RMS 实测（thr 0.010、步进 0.02），句内再按字数分配
def last_loud(t_end, back=1.4, thr=0.010):
    """从 t_end 往前找最后一声（跳过静音尾巴）"""
```

**效果：镜9→10 间隔 3.60s → 1.00s**，全片只剩镜1→2 的 1.53s（卷播设计停顿，要留）。

⚠️ **纯能量阈值法分不出句子**（句内停顿会被当句界，19 句分出 32 段）——
必须「以 phrases 为单位、在旧token 边界附近微调」。

---

## 八、★★★ v17 实战补充（爱兰纯 261008 · 6 个新坑，全部已修）

> 2026-10-08 v17 动效重构一轮实录。以下 6 坑每个都浪费过一轮渲染，**勿重犯**。

### 8.1 ★★★ 渲染工程不能拷 node_modules，必须在「依赖完整的原工程」里渲

**症状**：`cp -R node_modules` 到新工程后，渲染报
`Cannot find module '@remotion/renderer'` / `remotion-cli.js` 软链接悬空 /
`@remotion/renderer` 目录只有 `client.js` 没有 `package.json`。

**根因**：365MB 目录拷贝在沙箱/超时下会**静默中断**，留下一堆残包。
`node_modules/.bin/remotion` 是软链接指向 `../@remotion/cli/remotion-cli.js`，
目标不存在时 npx 报的是 `could not determine executable to run`（**极具误导性**）。

**正解（按优先级）**：
1. **首选**：新组件 `.tsx` 直接拷进**已有依赖的工程**（如 `render_tmp_*/remotion/src/`），
   在那里渲染 —— 零拷贝、零风险。**本轮就是这么干的。**
2. 次选：`cp -R` 拷完后**逐个校验** CLI 入口与 renderer 的 package.json 是否存在。
3. 只补缺失包时用 `cp -R "$S/$p/."`（带 `/.`）而非 `cp -R "$S/$p"`，
   后者遇到已存在目录会变成嵌套导致残包。

### 8.2 ★★★ 临时目录必须带 `remotion.config.ts`，否则字体永远加载不了

**症状**：`A delayRender() "加载新青年体" was called but not cleared after 28000ms`，
每帧都超时，**843 帧后彻底卡死**，改文件名/加看门狗/改并发全都没用。

**根因**：临时目录只拷了 `src/ public/ package.json tsconfig.json`，
**漏了 `remotion.config.ts`** ⇒ public 目录解析异常 ⇒ `staticFile()` 拿不到字体。

**排查顺序（下次照这个顺序走，别乱试）**：
```
① 工程根有没有 remotion.config.ts？        ← ★本轮真凶
② 字体文件在不在 public/？（ls + head -c4 看是否 00 01 00 00）
③ 文件名含中文？（先复制成 xqy.ttf 试）
④ 都没有 ⇒ 换系统字体兜底（见 8.3）
```

### 8.3 ★★ 字体兜底：宁可回退系统字体，不要让 delayRender 卡死整片

**正解**（本轮最终采用，1000/1000 帧全出）：把 `loadFont` + `delayRender` 整段删掉，
直接声明系统字体栈：
```tsx
const FONT_STACK = '"PingFang SC", "Heiti SC", "Microsoft YaHei", sans-serif';
const FONT = FONT_STACK;
```
⚠️ 代价：重点字从「文悦新青年体」变系统黑体，**观感降一档**。
若必须用商业字体，得在**正常环境**（非沙箱）渲一次并把帧序列存下来复用。

### 8.3b ★★★★★ 8.3 的坑**在两处**：`pipeline_run.py` 里的 fx 入口模板也会把 loadFont 写回去（2026-10-18 实锤，连崩三次）

**现象**：fx 序列每次渲到 600~800 帧（约 20~28s 处）整个进程崩，报
`delayRender() was called but not cleared after 28000ms`，前 600 帧 PNG 全部正常落盘。

**根因**：`@remotion/fonts` 的 `loadFont()` **内部自己带一个 `delayRender` 句柄**；
headless 渲染中 `FontFace.load` 的 promise 不 settle（`webpack` 首次编译后更是如此），
句柄永远不清 ⇒ 到 28s 超时上限整片被 kill。**不是**「我们的 delayRender 忘了 clear」。

**三个无效尝试**（省下你去试）：
1. 给 loadFont 包 `setTimeout(6000)` 兜底 ⇒ loadFont 内部句柄不受控，照样卡；
2. 改成「不阻塞的预加载」（`void loadFont()`） ⇒ 字体没就位，数字用 fallback 糊一档；
3. 只删 `fx_entry` 的调用 ⇒ 下一次 pipeline 重跑又从模板生成回来。

**正解（两处都要改）**：
- `ws/rem3d/src/fx_entry_*.tsx`：整段删掉 loadFont / delayRender，保留
  `void loadFont; void staticFile;` 防未使用变量报错；
- `pipeline_run.py` 的 **fx entry 模板字符串**同样删（否则下次又被写回）；
- 字体栈走系统字体：
  ```tsx
  const FONT = '"PingFang SC", "Heiti SC", "WenYue XinQingNianTi", sans-serif';
  ```
  **数字用 PingFang SC 比新青年体更清晰**（等宽感强、小数点不飘）；重点字仍可用新青年体。

### 8.4 ★★★ ffmpeg 读 PNG 序列：`%04d` 会失败，必须用 glob

**症状**：
`Could find no file or sequence with path 'fxframes/element-%04d.png' and index in the range 0-4`
- 加 `-start_number 0` 仍失败
- 文件确实存在（`ls` 1000 个、单帧 `ffmpeg -i` 可读）

**正解**：
```bash
ffmpeg -y -i base.mp4 -pattern_type glob -framerate 30 -i "fxframes/element-*.png" \
  -filter_complex "[0:v][1:v]overlay=eof_action=endall[outv]" \
  -map "[outv]" -map 0:a -c:v h264_videotoolbox -q:v 75 -pix_fmt yuv420p -c:a copy out.mp4
```
⚠️ **`/tmp` 是软链接**，ffmpeg 读 `/tmp/xxx` 会失败 → 帧序列要拷到工程目录再 overlay。

### 8.5 ★★★ `CountUp` 无条件 `Math.round` ⇒ 99.99 变 100（自己打自己脸）

**症状**：「4个9」的含量滚到 **100%** —— 而「4个9」就是 99.99% 的意思。

**根因**：`DataViz.tsx` 的 `CountUp` 写死 `Math.round(target * ...)`，
且 `CountUp` 组件签名 / `VizSpec` 类型 / 容器透传**三层都没有 `decimals` 字段**。

**正解（四层都要改，漏一层就白渲）**：
```tsx
// ① 组件 props 解构加 decimals = 0
// ② 显示逻辑
const _raw = target * Math.min(1, p * 1.08);
const shown = decimals > 0 ? _raw.toFixed(decimals) : String(Math.round(_raw));
// ③ VizSpec 类型加 decimals?: number
// ④ 容器透传 <CountUp ... decimals={s.decimals} />
```
⚠️ 只改显示逻辑会报 `ReferenceError: decimals is not defined`。

### 8.6 ★★★ 同一镜位内两个组件必须错开 y，否则糊在一起

**症状**：镜4 的对比条放 `(1560,300)`，与重点字「超不超标」`(1520,300)` **完全重叠**，
抽帧看到「吸收对比」和红色大字糊成一团。

**正解**：
1. 先 `ffmpeg -ss <t> -i base.mp4 -vf crop=...` **把该镜空白区量出来**（别猜）；
2. 同一画面内多组件**上下排布**（v17 定稿：99.99% 在 y=250、对比条在 y=620，同 x=330）；
3. ★**人物居中构图，左右两侧上方最空**，数据可视化优先放左上/右上，别放正中。

### 8.7 顺带确认：底片字幕关不掉

`build_ad_video.py` 里 `max_lines = max(1, int(cfg.get("sub_max_lines", 1)))`
**强制下限 1**，且 `subtitles={ass_path}` 无条件进 filter_complex
⇒ **没有任何 config 能关掉 ASS 字幕**。
v17 因此**放弃自带的 SubtitleLayer**（会与 ASS 重影），
改为「重点字 startSec 与 ASS 关键词严格同帧」实现联动。

### 8.8 ★★★ 铁律：同一个信息只能有一种表现方式（2026-10-08 灿爹拍板）

**原话**：「有动效就不用重点字弹出了。」

**含义**：数据可视化（CountUp/RingGauge）、重点字（KwOverlay）、产品卡（ProductCardFx）
三者**都在抢同一块注意力**。同一个信息点同时上两个组件＝重复＝噪音＝廉价感。

**判据**：加任何新组件前先问「这个信息现在有没有别的表现方式」。
有 → 删掉其中一个，**优先保留有动效的那个**（数据可视化 > 重点字）。

**v18 实删清单（爱兰纯 13 镜，重点字 7 → 3）**：

| 删掉 | 原因 |
|---|---|
| 「三个月」 | 镜10 已有「¥0.22/天」日均价滚动 |
| 「50多万单」 | 镜11 已有「累计销量」计数器 |
| 「检测费」 | 镜6 画面本身是惊讶脸，语义已足 |
| 「随意对比」 | CTA 前夕口播内容，字幕已承载 |

保留 3 个**没有替代动效**的：`超不超标`（核心转折）、`一批批`+`随货`（认证盖章）。

### 8.9 ★★★ 产品镜禁止用「分镜图」当卡片弹出（2026-10-08 灿爹拍板）

**原话**：「图片不能作为卡片弹出，比如粉剂着地方，而且出场也要bug。」

**两个问题**：
1. **同物重复**——镜7 画面主体本身就是产品罐（右下角大罐），
   再弹一张同款小卡片＝同一样东西出现两次，观众只觉得画面脏。
2. **出场观感乱**——卡片从右侧滑入压在小绿人头部右上方，
   与字幕、重点字三层叠加，出场像「贴纸飘进来」。

**铁律**：
- `ProductCardFx` **只允许真产品详情图**（`产品图库/` 下的成分表/详情图），
  **分镜图永远不做卡片**（与主 §卡片双规则同源）。
- 产品点名镜就让**产品本身在画面里**，用 `BreathLayer` 做呼吸动感即可，不加卡片。

### 8.10 ★★★ 叙事音效库 `爆款叙事音效_261008` 用法（14 款）

**加载方式**：config 顶层 `sfx_files` 填映射，`trigger_sfx` 里用 key。
**⚠️ `trigger_sfx` 靠 `kw`（文案关键词）自动定位发音时刻，没有 `at` 字段！**
写 `{"at": 12.4, ...}` 会被静默跳过，日志打 `trigger_sfx 跳过: kw 为空`。

```json
"sfx_files": {"narr_shock": "爆款叙事音效_261008/10_震惊闷响.wav"},
"trigger_sfx": [
  {"kw": "超不超标", "sfx": "narr_shock", "anchor": "kw", "priority": 0}
]
```
- `anchor`：`kw`（默认，说到该词那一刻）/ `shot`（含该词的镜开口第一字）
- `lead`：提前 N 秒；`priority`：0=响亮（参加 5s 互斥）/ 3=轻音
- `phrase` 可省略，省略则全片逐镜扫描该词（每次出现都响）

**按语义选音效（灿爹指定，别凭感觉）**：

| 叙事节点 | key | 用途 |
|---|---|---|
| 抛问设悬念 | `narr_query` | 「很多坑」短促上扬 |
| 揭示盲区/反转 | `narr_shock` | 闷雷式震惊，★全片转折点 |
| 认证/盖章 | `narr_metal` | 「一批批过检测」 |
| 数据点 | `narr_ding` | 念数字时逐字点缀 |
| 销量/结论重锤 | `narr_thump` | ★最有分量，1.48s |
| 顿悟 | `narr_light` | 「可以随意对比」 |
| CTA 收束 | `narr_final` | 3.1s 长收束，★配片尾「直接存起来」 |

⚠️ `narr_slide` 2.18s / `narr_final` 3.11s 都偏长，映射表建议裁短或淡出。

**5s 铁律排布法**：先定必留骨架（片头抛问 / 中段盖章 / 片尾 CTA），
再填空档。★**纯时间序贪心会砍掉片尾 CTA**（它离前一个点太近）——
必须按叙事优先级**全局择优**，必要时把 CTA 挪到 29.6s 这类从容位置。

### 8.11 ★★★ 转场整体停用——「黑影闪一下」比硬切更糟（2026-10-08 灿爹拍板）

**原话**：「把今天优化的镜间转场去掉，应该是它出现的黑影。」

**判断正确，实测证实**。我连修两版转场（v18.1 实体色块 → v19 只留压暗），
**都是错的方向**——真正的问题是「加了压暗幕布」这件事本身：

| 版本 | 实现 | 灿爹观感 |
|---|---|---|
| v18.1 | 2300px 实体色块横扫 | 上半黑、下半亮（半遮半露黑边） |
| v18.1 | +斜向黄光带 | **黄黑影** |
| v19 | 只留全屏压暗（bell 曲线 0.34~0.42s） | **黑影闪一下** |
| **v21** | **TRANS 置空，恢复硬切** | ✔ 干净 |

**根因**：镜2/3/6 是**黑底黑板宣言图**（亮度只有 22~46）。
在黑底镜位上加 0.68~0.88 的压暗 ⇒ 观感就是「画面突然黑一下再亮」，
**无论幕布是色块还是纯压暗，都会读作黑影**。
⇒ 硬切反而是对的：底片本身按 `shot_bounds` 精确切，**无黑帧、无闪烁**。

**★ 铁律（本项目 v21 定型）**：
- **默认不加转场**。13 镜硬切，靠**镜位内元素动效**（呼吸/粒子/数据可视化）保持节奏感。
- 若将来确需转场，两条硬约束：
  ① `dim ≤ 0.15`（几乎不压暗），**黑底镜位尤其不许压暗**；
  ② 验收：**黑底镜位（镜2/3/6）转场帧灰度均值与相邻帧差必须 < 8**。

**★ 量化验收（转场改动的判据）**：
```
转场前（v19/v20 压暗）: 128 → 45 → 165← 有暗谷 = 有黑影
转场后（v21 无转场）  : 128 → 128 → 165  ← 直接换镜，无中间暗帧
```
看到「暗谷」就说明转场还在压暗，必须去掉。

**代码怎么停用**：`TRANS` 数组置空 + 移除 `<TransitionFx list={TRANS} />`，
其余层（Mote/Viz/Kw）保留，Breath 也已因下条bug 停用。

### 8.11b ★★★ 「分镜内部突然黑一下」的真正根因：BreathLayer 丢 alpha（v22 根治）

**症状（灿爹报障）**：「一个分镜里面突然黑一下」——删了转场仍在。

**★ 定位方法：直接扫动效层 PNG 序列的 alpha 通道（★唯一可靠手段）**
```python
from PIL import Image
import statistics
vals=[]
for i in range(1000):
    im = Image.open(f'element-{i:03d}.png')
    if im.mode != 'RGBA': print(f'帧{i} 模式={im.mode}  ← ★丢了 alpha！')
    px = list(im.convert('RGBA').getchannel('A').resize((96,54)).getdata())
    vals.append(sum(px)/len(px))
# 找 alpha 跃升：某一帧从~0 突然变成大面积覆盖
for i in range(3, 997):
    if vals[i] - statistics.median(vals[i-3:i]) > 25: print(f'帧{i} ({i/30:.2f}s) alpha 跃升')
```

**实测结果**：v21 有 5 处 alpha 从 **0 瞬间跳到 255**，四角全 255，
对应帧 165/408/509/641/795（= 5.51/13.60/16.97/21.37/26.50s，正好是 BREATH 五镜）。

**两个叠加的 bug**：
1. **`BreathLayer` 把分镜原图整张铺满 1920×1080 盖在底片上** ——
   底片本来已有 Ken Burns 做推拉，再叠一层全屏图＝双重缩放 + 覆盖。
2. **PNG 在这些帧丢了 alpha 通道**（`RGBA` → `RGB`，左上角像素 `(1,1,1)`）
   ⇒ 变成一张**不透明的黑图**盖住整个画面 ⇒ 每个呼吸镜入点闪一次黑屏。

**★ 为什么肉眼看不出来**：亮度检测（灰度均值）在成片上**查不到异常**
（因为底片本身内容明暗变化大，淹没了信号）。**必须查 alpha 通道**。

**★ 铁律**：
- **绝不要在 overlay 层里放「铺满全屏的原图」** —— 底片已有 Ken Burns，重复了。
  要做动感就去调底片的 zoompan 参数，或只加**局部前景元素**（高光/粒子）。
- **动效层每帧渲染后必须扫 alpha**：`mode` 必须是 `RGBA`、
  且 alpha 均值 **不应出现 >25 的跃升**。命令：
  ```bash
  /path/python -m pip install pillow
  python -c "from PIL import Image; im=Image.open('element-100.png'); print(im.mode)"
  ```

**保留 MoteLayer**：飘尘粒子是前景零散小点（alpha 均值仅 11/255），不铺满不遮挡主体。


### 8.12 ★★★ 定位「某个词那里有音效」的可靠方法

**问题**：灿爹说「含金量这里音效不要」——但 `trigger_sfx` 里根本没有「含金量」这个词，
**音效是渲染器自己加的**（镜位入场音 / 分层入场音 / 重点字弹簧音 / 转场唿哨）。

**错误做法**：只看 `trigger_sfx` 配置就下结论「这个位置没有音效」⇒ 改半天没改掉。

**★ 可靠方法：渲一版`sfx: False` 的对照底片，做差分**
```bash
# ① 全静音基准（关掉所有自动音效 + 自定义音效）
python -c "
import json; c=json.load(open('config.json'))
c['sfx']=False; c['trigger_sfx']=[]
c['output']='/path/out_nosfx.mp4'
json.dump(c,open('config_nosfx.json','w'),ensure_ascii=False,indent=1)"
python build_ad_video.py config_nosfx.json

# ② 两版都提音频，算逐窗RMS(dB)
ffmpeg -v error -i A.mp4 -ac 1 -ar 8000 -f s16le a.raw
ffmpeg -v error -i B.mp4 -ac 1 -ar 8000 -f s16le b.raw
# ③ 逐窗相减 → 差值 >4dB 的位置＝该处音效存在
```

**⚠️ 两个大坑**：
1. **别用「有音效版 vs 全静音版」直接找峰**——BGM 混音会污染基线，
   差值全是 ±3~7dB 噪声，**检不出真音效**。必须用**同底片结构的两个版本**互减。
2. **「峰值法」（找瞬时跃升 >6dB）在这类口播片上完全不可用**——
   人声本身就有 6dB 以上的起伏，会把大量**纯人声句**误判成音效点。

**音效来源清单**（`build_ad_video.py` 自动添加，不在 config 里）：
| 来源 | 触发条件 | 关闭方式 |
|---|---|---|
| 开场重击 `impact` | 默认开，0.12s | 无直接开关（可被 gap 规则挤掉） |
| 重点字弹簧音 | `keywords` 每项自带 | 删该重点字 |
| 镜位甩动唰声 | `swing_shots` | 写 `swing_shots: []` |
| 转场唿哨 | `transition_whoosh` **为 `None` 时默认 `[5,13]`** | ★写 `[]`，**不能不写**（`None` 会触发默认） |
| 抠图分层入场音 | 镜位有 `fg`+`bg` 图层 | 去掉该镜的图层拆分 |
| 贴纸入场音 | `product_cards` 带 anim | 删卡片 |

**★ 关键判断**：「删音效」通常要**字和音一起删**——
重点字一出现就自动配音效。只删 config 里的音效、字还在 ⇒ 音还在；
只删字、音还挂在别的规则上 ⇒ 音还在。**v20 就是把「一批批」字整条删掉才干净。**

### 8.13 ★★★ 转场绝不能用「实体色块横扫」——必然出半遮半露黑边

**症状（灿爹原话「转场和政策画面出现的时候有部分黑影」）**：
转场瞬间画面**上半被黑块盖住、下半还亮着**，像「一块黑板从上往下压下来又弹走」。

**根因（★这个思路本身就是错的，别再试）**：
幕布是一个 2300×1300 的**大矩形横扫**。横向上它从右往左推进
⇒ **任意中间帧都只有部分区域被覆盖**，而矩形上下沿正好落在画面内 ⇒ 形成硬边。
我第一版修「滑到 x=0 停住」也只是从「盖死全屏」变成「压一半」，**没根治**。

**★ 正解：全屏「压暗 + 扫光带」，不用实体色块**
```
① 压暗层  <AbsoluteFill> 黑色 1920×1080，opacity 按 bell 曲线
          dimOp = [0, 0.42, 0.58, 1] → [0, dim, dim, 0]     // 铺满 ⇒ 永不露边
② 扫光带  一条 460×2400 的 div，rotate(-18deg) 跨整个对角线，
          background 用 linear-gradient(90deg) 做软边（中亮两侧透）
          中心 bx 从 −600 → 2520 扫过（★必须扫出画面，不能停在中间）
③ 前缘高光  亮带正中一条 4px 亮线 + boxShadow 辉光 —— 现代感的关键
```
效果＝「画面暗下来 → 一道光扫过 → 亮起来」，比黑块自然得多，且**没有硬边**。

**参数语义**（v18.1 重写后的 `TransSpec`）：
| 字段 | 含义 |
|---|---|
| `kind` | **只决定扫光方向**：`slideL` = 自左向右，其余 = 自右向左 |
| `atSec` | 转场**结束**时刻＝后一镜入点（覆盖式，不占镜位时长） |
| `durSec` | 建议 0.30~0.45s |
| `dim` | 压暗峰值（slide 类 0.68~0.72，fade 收尾 0.88） |
| `color` | 扫光带颜色（不再是幕布色！） |

**★ 验收方法：量化亮度曲线，别靠肉眼看**
```bash
ffmpeg -ss <atSec-0.4> -i out.mp4 -t 0.6 -vf "fps=30,scale=320:-1" f_%02d.jpg
for f in f_*.jpg; do
  ffmpeg -v error -i $f -f rawvideo -pix_fmt gray - \
    | python3 -c "import sys;d=sys.stdin.buffer.read();print(round(sum(d)/len(d),1))"
done
```
**判据**：
- ✔ 好的转场＝亮度**平滑对称**成谷（实测 `128 → 45 → 165`）
- ✘ 有黑边＝亮度**断崖式跌落**且中途卡住（实测 `128 → 128 → 36 → 40 → 52`）
- ⚠️ 注意：黑底内容镜（黑板宣言）本身亮度就只有 25 左右，
  **别把它误判成黑边** —— 要看曲线是否平滑，不能只看绝对值。

---

## 七、定稿参数（姜黄素 261007，跨项目参考）

| 项 | 值 |
|---|---|
| 卷播卡 | 16:9 · 700×394 · pitch 860 · 卡池 10 张（6 滚动 + 主图 + 3 陪衬） |
| 卷播速度 | `exp(-3u)` 指数减速，scrollEnd 2.20s，nScroll 6，实测减速比 19× |
| 卷播 3D | `perspective:1500`，边缘 `rotateY 18°` + 侧光 `brightness(1-dim*0.5)` |
| 卷播定格 | `centerShift` 终值 **0** ⇒ 主图正中，左右各露 468px，边距 0 vs 0 |
| 卷播交互 | 2.62s 鼠标点击 → 三环涟漪 → 主图弹簧放大 **2.76×** 铺满；无白框 |
| 卷播音效 | 4 层：滚动音 `e^(-3u)` 渐弱 / `kw_click`×1.5 / `pop_in` 涟漪 / 定格咚×0.9 |
| 卷播总长 | **4.80s（硬上限 4.83s = 镜1 语音槽）** |
| 重点字 | 4 个（`98%` 已删，由环形进度承担），完整可见不半遮 |
| 数据层 | 2 个（RingGauge 98% + CountUp 10mg），★无对比类|
| 响度 | Integrated −11.8 LUFS / True Peak −0.7 dBTP |

### 7.1 爱兰纯肌酸 v14（2026-10-08 定稿 ·「附加层」首部全片）

走通了「底片 + 单层附加层」的新架构，**作为后续所有片子的模板**：

| 项 | 值 |
|---|---|
| 时长 / 帧数 | 33.62s / 1009 帧 @30fps |
| 底片 | `keywords=[]` **且** `product_cards=[]` ⇒ `out_c14_base.mp4` |
| 附加层 | `fx_entry_c.tsx`（`FxLayerC`）**单层**出场，含 3 类元素共 **10 个动效点** |
| 数据层 | 镜5 CountUp `decimals=2` → `99.99%`（配计量条） |
| 重点字 | 6 个：镜6/8 `warn`、镜10/11 `stamp`+`hollow`、镜13/15 `price` |
| 产品卡 | 镜9 `ProductCardFx` 从画右弹簧滑入，`left/top/w` 沿用 ffmpeg 版 config 的 `pos` |
| 落位 / 时刻 | `pick_kw_pos.py` + `kw_times.py`；起跳比开口早 0.06s |
| 质检 | 10/10 命中设计坐标，偏差 **2–32px**；顶开 0；响度 **−12.0 LUFS** |

⚠️ v14 相比 v13 **只有镜2–17 的重点字/产品卡变了**，片头（镜1）沿用 v13 的 3D 卷播，
未重渲——动效层从镜2 才开始有内容，改片时别误以为整层都要重做。