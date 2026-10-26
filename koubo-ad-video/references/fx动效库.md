# fx 动效库（2026-10-09 灿爹样片带选型收编）

> 样片带 9 段选型结论：收编 2 数字滚动 / 3 盖章，其余（描线圈/星光/检测勾/残影/彩纸/光圈）灿爹不满意，**不得入片**。
> 组件本体：`assets/rem3d_src/DataViz.tsx`（canonical，pipeline 渲前自动同步进 `W/rem3d/src/`）。
>
> ★2026-10-14 **价格锤（PriceSlam / slams）已删除** —— 灿爹判定「很 low」。
> 价格镜改走 KwOverlay 的 `price` 变体（重点字 + 中段复顶）或 counts 数字滚动。

## 一、两个动效与 spec 字段

全部走既有 fx PNG 序列管线（v22 定式：`--sequence` 渲 `element-XXXX.png` → ffmpeg overlay 底片），在 `.fx.json` 侧车里声明，`make_fx_spec.py` 用 vo token 实测锚定时刻，归并进 `viz` 数组（与 count 同链渲染）。

### 1. count 数字滚动（0→目标值 + 计量条）——已在库，本轮加 fontSize
```json
"counts": [{"text": "50多万单", "phrase": 11, "x": 1330, "y": 240,
            "target": 50, "unit": "万+单", "label": "一单一单喝出来的",
            "color": "#F2C14E", "barWidth": 620, "fontSize": 240, "durSec": 3.4}]
```
- `fontSize` 默认 108；样片带 240 大字版直接传 240（unit/label 字号按 0.48/0.37 比例跟随）。
- target 传**数值**（50 不是 "50万"），单位进 `unit`。
- **`plate`（2026-10-18 新增，默认 false）**：亮背景（窗光/白墙/浅货架）下裸黄字会糊，开
  `"plate": true` 铺一层 `rgba(8,11,16,0.52)` + `blur(6px)` + 白边暗板（与 CompareBar 同语言）。
  暗背景不写 —— 裸字更透气。
- **★ 落位（灿爹 2026-10-18 定式「动效组件不要遮住主体，在旁边起说明补充作用」）**：
  先算主体 bbox（绿皮掩膜外接框），只在框外找留白。实测亮背景 `fontSize 88 / barWidth 320`、
  暗背景 `90 / 300`；`durSec` 1.5~1.7s 且**必须短于镜长**。
  块宽是 `barWidth*1.5`、`x/y` 是**中心点**，靠边放按中心点算。

### 2. count 的小数（2026-10-18 实测）
`target: 14.6` 自动出 1 位小数（`decimals` 由 target 推断，`Number.isInteger` 判定），
`unit: "克"` ⇒ 显示 `14.6克`。**文案侧也写「14.6克」**，别写 `14.6g`（TTS 会念「G」）。

### 3. stamp 盖章（红框章体旋转砸下）
```json
"stamps": [{"text": "每批送检", "phrase": 7, "x": 460, "y": 780,
            "color": "#E8796A", "fontSize": 130, "rotate": -12, "durSec": 2.4}]
```
| 字段 | 必填 | 默认 | 说明 |
|---|---|---|---|
| text | ✓ | — | 章体文字（认证/保障语义：每批送检/A类认证/批批检测） |
| anchor | 建议 | text | 显示文字与配音读法不同时用（显示「每批送检」配音说「先送检」→ `"anchor":"先送检"`） |
| color | | #E8796A 砖红 | 章框+章文同色 |
| rotate | | -12 | 章体倾角（deg） |

物理：spring(damping 10/stiffness 200) 从 2.6 倍旋转缩到 1 倍 + 上方 -70px 砸下，6 帧淡入。

## 二、落位与配色铁律（灿爹 2026-10-09 拍板）
1. **不遮主体**：人物/产品罐体所在区域禁放——用四角+两侧安全区，写完侧车必须抽帧目视。
2. **配色跟画面走**：低饱和暖色系（金 #F2C14E / 奶白 #FFE9C8 / 砖红 #E8796A / 柔绿 #A8D8A0），禁高饱和荧光色。
3. **信息唯一性**（v22 铁律不变）：同句已有 count/stamp 大动效时不再配 words 重点字弹出。
4. **音效**由 `.kw.json` 的 `trigger_sfx` 逐词分配（PNG 序列无声），count 配蓄满类（kw_charge）、stamp 配盖章类（kw_marker）；价格镜用 KwOverlay `price` 变体时配中频类（kw_marker/kw_ding）。

## 三、机制备忘（踩坑）
- canonical 组件在 `assets/rem3d_src/`（KwOverlay/DataViz/fx_comp_j），pipeline_run.py fx 分支渲前自动同步（内容有变才覆盖，日志打「动效库同步」）。
- `--sequence` 漏传会静默出 alpha 全丢的 mp4；PNG 命名位数随总帧数自适应（<1000 帧是 element-%03d，整片 1000+ 帧是 %04d，pipeline 的 ffmpeg 用 %04d 恰好匹配整片）。
- webpack 旧缓存 EPERM：DataViz 改动后渲染前清 `render_tmp_261007/remotion/node_modules/.cache/webpack`（pipeline 已内置清 rem3d/node_modules/.cache，符号链接场景需清真实路径）。
- 验证样例（实测 2026-10-09）：stamp/count 两 kind 同渲 140 帧，alpha 36.4%，两动效全部显影、互不遮挡。（原三 kind 样例含 slam，slam 已于 2026-10-14 删除。）

## 四、★科普段结构动效（2026-10-14 新增第三层 explain）

DataViz 回答「**多少**」（产品段），Explain 回答「**怎么说**」（科普段）。
起因：灿爹指出「科普段动效方式单一、数字很少」——症结是没把**句式当结构**看。

| 组件 | spec kind | 对应句式 |
|---|---|---|
| `CheckRoll` | `roll` | 并列/列举（三个理由、四点区别） |
| `Highlight` | `mark` | 认知纠偏（不是A而是B）、痛点递进 |
| `WordPop` | `pop` | 转折（可…但是）、对偶（越X越Y） |

**完整句式映射表（含 7 类待补充句式）见 [科普句式动效库](科普句式动效库.md)** —— 那是活文档，遇到新句式持续补充。

落位仍必须跑 Canny 边缘密度实测（判据 <6 可用，y+字高>880 排除）。
