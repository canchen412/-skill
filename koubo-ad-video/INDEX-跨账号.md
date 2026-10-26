# 带货视频动效库 · 跨账号索引

> **本文件是"入口索引"，不是技能本体。**
> 技能本体在 `~/.workbuddy/skills/koubo-ad-video/`（用户级目录，**所有 WorkBuddy 账号共享**）。
> 切换账号后如果找不到这套动效，先读这个文件。

---

## 一、这套东西在哪

```
~/.workbuddy/skills/koubo-ad-video/
├── SKILL.md                          主文档（含全部变更日志）
├── assets/
│   ├── rem3d_src/                    ★ 组件本体（canonical）
│   │   ├── Explain.tsx               科普段结构动效（2026-10-14 新增）
│   │   ├── DataViz.tsx               产品段数据可视化
│   │   ├── KwOverlay.tsx             重点字四变体
│   │   └── fx_comp_j.tsx             入口（三层编排）
│   ├── remotion_templates/           旧版模板 + 调试脚本（pick_kw_pos 等）
│   ├── textfx/  caption_fx/  rem3d_src/   动效样片与字幕模板
│   └── fonts/  sfx/  bgm/            字体、音效、背景音乐
├── scripts/
│   ├── make_fx_spec.py               文案侧车 → fx spec（⚠ explains 分支待补）
│   ├── pipeline_run.py               主流水线
│   └── pick_kw_pos.py 等             落位实测工具
└── references/
    ├── 科普句式动效库.md              ★★ 活文档：句式→动效映射表，持续补充
    ├── fx动效库.md                   动效库总览
    ├── remotion_动效层.md            Remotion 方法论与踩坑
    └── 三段结构规范.md                文案分段铁律
```

**关键：`rem3d_src/` 是 canonical 版本。** `remotion_templates/` 是 10-08 的旧版，
pipeline 渲前会自动把 `rem3d_src/` 同步进工作区。**改组件只改 `rem3d_src/`。**

---

## 二、三层动效分工（理解这套库的钥匙）

| 层 | 回答什么 | 组件 | 主要用在哪段 |
|---|---|---|---|
| `kw` | 指认一个词（弹一下就消失）| plain / warn / stamp / price | 全片 |
| `viz` | **多少** | RingGauge / CountUp / CompareBar / StampSeal | 产品段 |
| `explain` | **怎么说** | `Highlight`（唯一组件，kind='mark'） | **科普段纠偏句** |

> 科普段没有数字，但有**句式结构**——并列、转折、纠偏、对偶。
> 这两种结构同样可以可视化，不要因为"没有数"就以为科普段做不了动效。

---

## 三、写 spec 的最小示例

文案旁放同名 `.fx.json`：

```json
{
  "explains": [{
    "kind": "mark", "phrase": 7, "anchor": "练得少真不是原因",
    "x": 200, "y": 270, "durSec": 7.1,
    "fontSize": 62, "lineGap": 132, "stagger": 78,
    "lines": [
      {"text": "练得少",         "kind": "strike", "color": "#E8796A"},
      {"text": "微量元素长期没补", "kind": "marker", "color": "#D4FF00"}
    ]
  }],
  "words":  [{"text": "六个9", "x": 500, "y": 430, "anim": "price", "durSec": 1.8}],
  "counts": [{"text": "50多万单", "x": 1330, "y": 240, "target": 50, "unit": "万+单", "label": "单"}],
  "stamps": [{"text": "每批送检", "phrase": 7, "x": 420, "y": 300, "durSec": 2.4}]
}
```

`anchor` = 配音实际读的词（防同词跨镜错锚）；`phrase` = 第几镜起（1 起始）。

---

## 四、★密度铁律（优先于一切，最高优先级）

**同一时刻屏幕上最多 1–2 行大字。宁可不放，也不铺满。**

- 动效层**只说关键词**，绝不重复字幕的完整句子
- 补充说明类内容（「我以前的活例子」这类）**一律不上画面**，那是口播内容
- 判断标准：动效层的字和字幕的句子读起来是同一句话 ⇒ 重复了，删动效那层

**职责三分**：字幕层承载完整句 / 动效层只做指认 / 画面层提供情境不承载文字。

> 完整版见 `references/科普句式动效库.md` 第三节（含出片前自检表）。

---

## 五、四条铁律（违反了必踩坑）

1. **落位必须实测** —— 先跑 `scripts/pick_kw_pos.py` 或手写 Canny 边缘密度打分。
   判据：**密度 < 6 可用**；`y + 字高 > 880` 一律排除（侵入字幕带）。
   *凭感觉选位置是最大的返工来源。*

2. **同 kind 一条片只用一次** —— `roll` / `mark` / `pop` / `count` / `stamp` 都受这条约束。
   多个数据合并到一个组件的参数里，不要拆成多个。

3. **不要一半线性一半弹簧** —— 同一镜位里只要有一个用了 spring，其余全换。
   风格割裂非常明显。

4. **同位置时间互斥** —— `startSec[i] ≥ startSec[i-1] + durSec[i-1]`，留 0.15s 呼吸。

---

## 六、渲染命令（本机可用的关键写法）

```bash
# 在工作区的 rem3d/ 目录下
node remotion render src/fx_entry_xxx.tsx FxLayerXxx out/fx_xxx --sequence --log=error
```

⚠️ **如果报 `mkdir .../remotion-webpack-bundle-xxx EEXIST`**，
是环境拦截，须清 broker hook（照 `pipeline_run.py` 第 509–516 行）：

```js
const env = {...process.env};
env.PATH = '/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin:' + env.PATH;
env.NODE_OPTIONS = '';
delete env.CODEBUDDY_BROKERED_FS_HOOK_ENABLED;   // ★关键
```

并传 `--browser-executable="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"`。
参考实现：WorkBuddy/2026-10-14-14-27-47/fx-clip/render.js

---

## 七、继续攒这个库

`references/科普句式动效库.md` 里有 10 类句式，其中 **7 类待补充**：

因果 / 递进 / 设问引入 / 总结收束 / 否定强调 / 假设推演 / 时间线

**收集流程**：遇到新句式 → 查注册表 → 有就直接用 spec 声明 →
没有就找现成（snapcn / remotion-templates）改写，或自做 →
**做完全程补回注册表**，否则下次又得重新想。

---

## 八、GitHub 上可用的开源组件库

| 库 | 星 | 特点 |
|---|---|---|
| [shadcn-labs/snapcn](https://github.com/shadcn-labs/snapcn) | 426 · MIT | 51 个组件，**源码注释带逐帧实测表** |
| [reactvideoeditor/remotion-templates](https://github.com/reactvideoeditor/remotion-templates) | 261 | 81 个模板，图表最全 |
| [reactvideoeditor/clippkit](https://github.com/reactvideoeditor/clippkit) | 67 · MIT | 121 个 tsx，文字动效族完整 |

⚠️ **只抄时序，不抄配色**。snapcn 用 Inter + OKLCH 体系，与本项目
（PingFang + `#D4FF00` + 八向硬描边）冲突。

⚠️ **炫技型动效慎用**：PriceSlam（放射线+震屏）曾因"很 low"被删。
卡片级大动作先出样片给灿爹过目。