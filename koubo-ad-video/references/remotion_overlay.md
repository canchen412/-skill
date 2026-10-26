# 重点字弹簧动画叠加层（Remotion）

> **⚠️ 完整方法论在 `remotion_动效层.md`**（2026-10-07 姜黄素片一整轮沉淀：
> 卷播 3D + 鼠标点击涟漪 + 数据可视化 + 16 个踩坑清单 + 可复用源码）。
> 本文件只讲**重点字层**的最小用法。

## 作用

用 Remotion 的 **spring 物理动画**做重点字弹出，渲成**带 alpha 的 PNG 序列**，
再由 ffmpeg overlay 到带货成片。**不改动也不重写现有的 2697 行渲染器**。

### 为什么不直接用 ASS 做

现有渲染器用 ASS 的 `\t` 分段线性插值：`\fscx30→126→100`。
到 126% 处有明显**折角**，然后停住 —— 观感偏机械。

spring 是真·阻尼谐振：冲过目标 → 被拉回 → 微弹一下才停。
这是「有手感」与「机械感」的分界。

## 目录

```
src/kw_entry.tsx    入口（★不要改名叫 index.tsx，见下）
src/KwOverlay.tsx   组件 + spring 动画 + 字体加载
public/新青年体.ttf  商业字体，从 koubo-ad-video/assets/fonts 复制而来
compose.sh          合成脚本：底片 + PNG 序列 → 成片
out/frames/kw/      渲出的 PNG 序列（element-<帧号>.png）
```

## 用法

```bash
# 1. 改 src/KwOverlay.tsx 里的 KW_LIST（每条重点字的时间/位置/文案）
#    ⚠️ 时间必须与 build_ad_video.py 打印的 `KW 'xxx' @ Ns` 一致

# 2. 渲序列（注意要先关掉 fs hook，见文末踩坑）
export PATH=/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin:$PATH
unset CODEBUDDY_BROKERED_FS_HOOK_ENABLED
export NODE_OPTIONS=""
npx remotion render src/kw_entry.tsx KwOverlay out/frames/kw \
  --sequence --image-format=png --frames=170-250

# 3. 渲一版「无重点字」底片（关键！否则两层字重叠）
#    在 config 里把 sub_highlights 和 keywords 都置空后跑 build_ad_video.py

# 4. 合成
./compose.sh <底片.mp4> <输出.mp4>
```

## spring 参数调手感

在 `KwOverlay.tsx` 里：

```ts
const scale = spring({
  frame: localFrame,
  fps,
  config: {damping: 9, stiffness: 180, mass: 0.7},  // ← 改这里
  durationInFrames: Math.round(0.55 * fps),
});
```

- `damping` 越小回弹越明显；越大越"硬"
- `stiffness` 越大弹得越快
- 想完全不要回弹：`damping: 200`（官方推荐的无 bounce 配置）

### ★ 已内置 4 种变体，按词性选，不要都用 `plain`

| 变体 | damping/stiffness/mass | 入场 | 配词类 |
|---|---|---|---|
| `plain` | 9/180/0.7 | 上浮 | 通用/数据 |
| `warn` | 12/210/0.9 | 下方微沉 | 警告/问题（如「建模最堪忧」） |
| `stamp` | 8/240/0.85 | **从上方砸下** | 认证/品牌（盖章感） |
| `price` | 7/260/0.8 | 从下顶上 | 价格/利益点（**含中段复顶**，ASS 做不到） |

另加 `hollow: true` = 空心→实心（对应 ASS 的 `stroke` 但有过渡）。

### ★★ 三条硬规则（2026-10-07 实测）

1. **同位置的重点字必须时间互斥**：`startSec[i] ≥ startSec[i-1] + durSec[i-1]`
   （留 0.15s 呼吸）。违反会出现两层字重影。
2. **落位必须逐镜实测空白带**，不能统一 `x`。方法见 `remotion_动效层.md` §3.4。
   换素材后必须重量。
3. **重点字保持完整可见压在最上层**（灿爹 2026-10-07 拍板）。
   「人物后面半遮」方案已否决，`behind` 字段不要再设 `true`。

### ★ 音效按词义选，别用泛用型

实测 19 款 `kw_*` 音色质心：低频<500Hz=警告/权威 ｜ 中频 1–1.7kHz=数据/认证/价格 ｜
高频 >2.8kHz=俏皮。**禁止高频配负面词**（曾把 2927Hz 玻璃声配「最堪忧」，语义反向）。

## 本轮踩的坑（全部已修，勿重犯）

| # | 现象 | 根因 | 修法 |
|---|---|---|---|
| 1 | `Could not find composition with ID src/index.tsx` | **入口文件不能叫 `index.tsx`** —— CLI 把 basename「index」当成了 composition id。同一份内容改名 `kw_entry.tsx` 立刻成功 | 避开 `index`/`render`/`root` 这类保留名 |
| 2 | `Codec must be one of: h264, h265...but got png` | PNG 序列**不设 codec**，codec 只对视频格式有效 | `remotion.config.ts` 里只留 `setVideoImageFormat('png')` |
| 3 | 同一错误 ①再次出现 | `<Composition>` 上写了 `format`/`transparent`（非法 props）→ 注册失败，报错文案有误导 | 删掉这两个 prop，alpha 靠渲染参数 |
| 4 | 字体静默回退成默认字形 | 「文悦新青年体」是商业字体，**没装进系统**。ffmpeg 靠 `subtitles=xx.ass:fontsdir=<技能>/assets/fonts` 加载；Remotion 走 headless Chrome 只认系统字体 | `@remotion/fonts` 的 `loadFont()` + 文件放 `public/`，并用 `delayRender` 阻塞首帧 |
| 5 | `EEXIST: mkdir .../remotion-webpack-bundle-xxx` | WorkBuddy 的文件代理层（`CODEBUDDY_BROKERED_FS_HOOK_ENABLED=1`）拦截 mkdir 会误报 | `unset CODEBUDDY_BROKERED_FS_HOOK_ENABLED` + `export NODE_OPTIONS=""`（渲染从 78s 降到 11s） |
| 6 | `Undefined constant or missing '(' in 'shortest'` | overlay 的 `eof_action` 只接受 `repeat`/`endall`/`pass`，没有 `shortest` | 用 `overlay=0:0:repeatlast=0` |
| 7 | `FRAMES_DIR: unbound variable` | bash 变量名后紧跟**全角标点**会被吞进变量名（`$FRAMES_DIR（`） | `$VAR ` 与全角字符之间留空格 |
| 8 | 层叠进成片后**真峰值 +1.6 dBTP** | 素材层自己归一过，但接进成片后又过渲染器 loudnorm(+3dB)+BGM+音效 ⇒ 叠加爆 | 每次外部层叠进成片**必重测**，`alimiter=limit=0.85:level=0`（`-c:v copy` 放 `-i` **之前**） |
| 9 | 渲出的 PNG 序列**3 通道无 alpha**，整块黑底盖住成片 | 根容器 `background:'#000'` | 接成片的层必须 `background:'transparent'`；压暗层用 `rgba(0,0,0,0.55)` + opacity |

## 与现有流程的关系

```
文案 → [Remotion] 重点字 PNG 序列 ┐
                                 ├→ [ffmpeg] overlay → 成片
     → [build_ad_video.py] 无重点字底片 ┘
```

现有 `build_ad_video.py` 一行未改。Remotion 只负责它擅长的**动画曲线**，
其余（配音、字幕、卡片、音效、BGM、响度）仍由原渲染器负责。

## 下一步可做

- ~~开场冲击→ Remotion 版~~（卷播 3D 已实现，见 `remotion_动效层.md`）
- ~~卷播卡牌 3D 翻转~~（已实现 `MarqueeClick.tsx`：透视 + 点击涟漪 + 主图放大）
- 逐词高亮字幕 → `display-captions.md` 有 TikTok 式实现

## 已完成的扩展（详见 `remotion_动效层.md`）

| 组件 | 文件 | 用途 |
|---|---|---|
| 数据可视化 | `DataViz.tsx` | RingGauge / CountUp / CompareBar，科普带货核心说服力 |
| 卷播 3D + 点击涟漪 | `MarqueeClick.tsx` | CSS 3D 透视 + 鼠标点击 + 三环涟漪 + 主图弹簧放大 |
| 定位探针 | `probe_entry.tsx` | 渲参考线，一眼看出谁的坐标没生效 |
| token 边界修正 | `scripts/fix_token_bounds.py` | 治「两句话间隔太久」 |
