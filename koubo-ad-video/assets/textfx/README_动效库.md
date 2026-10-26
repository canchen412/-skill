# 动效库 · 文字 4 款 + 图片 15 款（2026-10-10 灿爹拍板入库）

全部由 PIL 逐帧渲染（8GB 机器零依赖、不吃内存），**缓动曲线与 GSAP 同名曲线 1:1**，
以后要进网页直接抄 GSAP 写法，要进视频直接调渲染器。

## 目录内容

- `特效_*.mp4` —— 文字动效 demo（4 款）
- `图效_*.mp4` —— 图片动效 demo（15 款，演示图 `demo_小绿人健身房.jpg`）
- `../scripts/textfx_render.py` —— 文字动效渲染器（可 CLI 可 import）
- `../scripts/imgfx_render.py` —— 图片动效渲染器（可 CLI 可 import）

## 文字动效（textfx_render.py）

| 特效 | 机制 | GSAP 写法 | 默认音效 | 适用 |
|---|---|---|---|---|
| `pop` 弹入 | scale .4→1 过冲 + 快速浮现 | `from(scale:.4, ease:"back.out(1.7)")` | kw_spring | 价格钩子、主推品 |
| `slide` 滑入 | 整字从画外滑到位 power3.out | `from(x:-620, ease:"power3.out")` | kw_snap | 常规字幕句 |
| `rotate` 旋转进场 | rotate −24°→0 + scale 1.7→1 | `from(rotation:-24,scale:1.7,ease:"back.out(1.4)")` | kw_shine | 贴纸感强调 |
| `drop` 落下弹跳 | translateY 落下 bounce.out + 落点阴影 | `from(y:-400, ease:"bounce.out")` | kw_drop(触地时) | 结论句砸落 |

```bash
# CLI 例
python3 scripts/textfx_render.py --fx pop --text "个位数一瓶" --size 200 --color pink --out out.mp4
# 可选：--bg grid|black|green（绿幕抠像用） --dir left|right --dur 2.4 --in-at 0.3 --sfx 名 --sfx-at 秒
```

```python
# import 例（成片管线里直接画在某一镜上）
from textfx_render import FX, text_layer, make_canvas
ctx = {"layer": text_layer("个位数一瓶", 200, (255,45,105), 13), "in_at": 镜内秒, "dir": "left"}
FX["pop"](镜图层, t, ctx)
```

## 图片动效（imgfx_render.py）

**进场 6**：`fade_up` 淡入上浮 / `pop` 缩放弹入 / `clip_wipe` 擦除揭示 / `circle_reveal` 圆形揭示 / `blur_in` 模糊聚焦 / `rotate_in` 旋转落位
**循环 5**（无音效）：`breathe` 呼吸脉冲 / `float` 悬浮漂浮 / `shake` 左右摇晃 / `glow` 辉光闪烁 / `parallax` 双层视差
**高级 3**：`glitch` 故障风（爆点前信号干扰）/ `sweep` 扫光遮罩 / `fade_out_blur` 虚化退场
**组合 1**：`combo` 图弹入＋价格标签 spring 甩入＋角标补刀（带货最常用，双音效 pop_in2+kw_pop）

> 3D 翻转**未入库**（灿爹 2026-10-10 拍板）。

```bash
# CLI 例
python3 scripts/imgfx_render.py --fx combo --img 产品图.jpg --out out.mp4 \
    --price "¥9.9/瓶" --corner "第二瓶半价"
# 可选：--bg grid|black|green --size 640 --dur 2.4 --in-at 0.3 --sfx 名 --sfx-at 秒 --no-label
```

```python
# import 例
from imgfx_render import FX, prep_img
ctx = {"img": prep_img("产品图.jpg", (960, 640)), "in_at": 0.3,
       "price": "¥9.9/瓶", "corner": "第二瓶半价"}
FX["combo"](镜图层, t, ctx)
```

## 实现备忘（改代码前必读）

- **GSAP 曲线对照**：`ease_out_back`≙`back.out(1.7)`（k 参数可调 1.4/1.8/2.0）；`ease_out_bounce`≙`bounce.out`；`ease_out`≙`power3.out`；`ease_in_out`≙`power2.inOut`。bounce.out **首次触地在 p=1/2.75≈0.364**，drop 的音效默认打在这个时刻。
- **循环类从 t=0 即动**，无 in_at；`k = 0.5 - 0.5*cos(2π t/T)` 就是 sine.inOut 往复。
- **辉光做法**：提取图层 A 通道→GaussianBlur→染色→垫图下；图片 demo 统一加了圆角+白边（`round_img`），辉光沿圆角边出。
- **glitch**：复制层 R↔B 通道对调（模拟 hue-rotate）+ x 抖 0.05s 步进 + 低 alpha 叠回。
- **parallax**：背景 cover 放大暗化反向漂，前景圆角图框+投影同向漂。
- **sweep**：mask 是 2.5 倍图宽的横条，44%~56% 一条软亮带（带中心 25% 不透明），mask 从右扫到左。
- **音效规则**：`adelay=X|X,apad=pad_dur=2` + `-t 显式时长`（**禁用 -shortest**，会被音效截断片长）；combo 双音效用 amix normalize=0。
- 组合款角标 x 用 `W - 文字宽 - 70`，别写死偏移（会出画）。
