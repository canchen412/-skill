# 自建 fx 动效层（PIL 逐帧渲染 → prores alpha → overlay）

> 出处：2026-10-25 肌肉博士牡蛎锌 v2 定稿。灿爹一句「还有动效没有出来，出现数字的时候没有数字滚动的特效」，
> 用 Remotion 组件库补不上「想怎么画就怎么画」的形态，于是自建了一层。
> **零积分、逐帧可控、不依赖 rem3d 打包**。当 Remotion 组件不够用时的第一备选。

---

## 一、什么时候该自建、什么时候别自建

| 情形 | 走哪条 |
|---|---|
| 只用现成的 count / stamp / roll / mark / pop / shift | **别自建**，走 `DataViz.tsx`（`references/fx动效库.md`），改 spec 即可 |
| 要画组件库里没有的形态（整块横向压缩回弹、自定义描边、手写盖章角度、任意多边形） | 自建 |
| Remotion 打包反复失败 / webpack 缓存 EPERM 卡住、但片子急着出 | 自建，30 分钟能出一版 |
| 需要和底片做**逐帧像素级对齐**的动效 | 自建（帧号 = 时间 × fps，不会有 spring 曲线的意外） |

代价：字体排版、描边、缓动全要自己写。**中文字体必须先确认能用**（本片用 `assets/fonts/新青年体.ttf`）。

---

## 二、管线（三步，中间产物是带 alpha 的 mov）

```
fx_layer.py  ──PIL 逐帧画 RGBA──▶  rawvideo 管道 ──▶  fx/fx.mov (prores 4444 / yuva444p10le)
                                                              │
底片 final.mp4 ───────────────────────────────────────────────┴─▶ compose_fx.py ──▶ final_fx.mp4
                                                                   （overlay + 15 路音效 amix）
```

### 2.1 渲染层（fx_layer.py 骨架）

```python
cmd = [FF, "-y", "-v", "error",
       "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{W}x{H}", "-r", str(FPS), "-i", "-",
       "-c:v", "prores_ks", "-profile:v", "4444", "-pix_fmt", "yuva444p10le",
       "-vendor", "apl0", os.path.join(OUT, "fx.mov")]
p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
empty = np.zeros((H, W, 4), dtype=np.uint8)
for i in range(nf):
    t = i / FPS
    act = [e for e in items if e.t0 <= t <= e.t0 + e.dur]
    frame = empty.copy()
    for e in act:
        frame = e.draw(frame, t)          # 每个元素自己画自己，画完就叠下一层
    p.stdin.write(frame.tobytes())
p.stdin.close(); p.wait()
```

要点：
- **`-profile:v 4444` + `yuva444p10le` 是 alpha 能活下来的唯一组合**，写 `prores_ks` 但 profile 选错 = alpha 全丢（预览全黑）。
- `nf = round(total * FPS)`，`total` 要和底片严格一致（见 §四）。
- 每帧从 `empty` 起步再叠元素：**不要复用上一帧**，否则该消失的元素会留残影。
- 855 帧 1080p 实测 **20 秒**渲完（PIL + prores），比 Remotion 打包快一个量级。

### 2.2 合成层（compose_fx.py 骨架）

```python
fc = [f"[1:v]trim=end_frame={NFRAMES},setpts=PTS-STARTPTS[fx]",   # ★见 §四
      "[0:v][fx]overlay=0:0:format=auto:shortest=0[v]",
      "[0:a]volume=1.0,aresample=48000[a0]"]
labels = ["[a0]"]
for i, s in enumerate(sfx):
    ms = int(round(s["t"] * 1000))
    fc.append(f"[{i+2}:a]aresample=48000,adelay={ms}:all=1,volume={s['vol']}[s{i}]")
    labels.append(f"[s{i}]")
fc.append("".join(labels) +
          f"amix=inputs={len(labels)}:normalize=0:duration=first,"
          "alimiter=limit=0.95:level=false[a]")
```

- 音效按 `{"t": 秒, "file": 路径, "vol": 增益}` 逐条 `adelay` 后 `amix`，**`normalize=0` 必须写**（否则 ffmpeg 自动把所有音源除以路数，口播会掉一半音量）。
- 出口挂 `alimiter`：15 路叠上去真峰值会顶到 0 以上，`limit=0.95` 兜住（实测出口 -0.4 dBFS）。
- 单个音效按文件名做 `VOL_FIX` 修正：`10_震惊闷响.wav` 天生比其他响 6dB → 0.38；`kw_boom.wav` 长低频 → 0.45。

---

## 三、★字卡必须跟着画面走：换镜后一定要重排（本片踩到）

动效层是按**绝对时间**排的，画面一换，字卡就失配。

本片镜1-① 从「躺床刷手机」换成「胸肌膨胀」后，`阳气不足`（x=520 / y=292 / 142号）正好砸在**正在鼓起的胸肌**上——
0.83s 出现、0.22s 后才切走，那 0.2 秒整张字压在人身上。**字卡坐标是上一版构图量出来的，画面一换全部作废。**

解法：给元素加 `t0_add`，整体后移（**音效必须同步跟移，否则声画错位**）：

```python
# fx_layer.py main()
t0, _ = t_of(full, chars, d["anchor"])
t0 = t0 + float(d.get("t0_add", 0.0))     # ★ 音效也用这个 t0 排，才能一起走
e["t0"] = round(t0, 3)
```

```python
dict(text="阳气不足", anchor="阳气", x=520, y=292, size=142, dur=0.88, t0_add=0.22, ...)
```

`dur` 同时要收短（1.16 → 0.88），保证**退场落在本镜切点之前**，不越界到下一镜的干净画面上。

> **规矩**：任何一次「换素材 / 换机位 / 换生图」，都要重新抽 0.1s 步进的成片帧，逐个重点字核对是否压主体。
> 不能因为"上一版过了"就跳过 —— 上一版过的是上一版的构图。

---

## 四、★动效层与底片必须帧数对齐（会多出一帧黑场）

| | 帧数 | 时长 |
|---|---|---|
| `fx.mov`（`round(28.5×30)`） | 855 | 28.5000s |
| 底片 `final.mp4`（实际） | **854** | 28.4667s |

`overlay=shortest=0` 不会因动效层更长而截断，结果成片尾巴多 1 帧「只有半透明动效、没有画面」的**黑场**。

`-t 28.4667` 也救不了：`trim=duration=28.4667` 里第 855 帧 PTS 正好等于 duration，边界含等号 ⇒ 还是 855 帧。

**唯一可靠写法**：
```python
NFRAMES = int(round(float(底片实测时长) * 30))
fc = [f"[1:v]trim=end_frame={NFRAMES},setpts=PTS-STARTPTS[fx]", ...]
```
并且把出口 `-t` 从硬编码改成实测时长（曾写死 28.5，换了素材后底片变成 28.4667 就错位）。

验收：`ffprobe -count_frames` 数成片与底片帧数，**必须相等**。

---

## 五、★音效入轨校验：看能量包络差，别看波形相减

「渲染成功 ≠ 音轨进了成片」是技能里的老坑（卷播段踩过）。静音段听不出来，肉眼也看不出，必须量化。

### ❌ 不要用波形相减
```python
amix=inputs=2:weights='1 -1'   # 成片 - 底片
```
AAC 编码差异 + `alimiter` 让残差 RMS 高达 **0.41**（比信号本身还大），毫无信噪比，会误判「音效没进」。

### ✅ 用 20ms RMS 包络差
```python
def env(p):                       # 解码成单声道 wav 后算包络
    w = wave.open(p); x = np.frombuffer(w.readframes(w.getnframes()), np.int16)/32768.0
    win = int(48000*0.02); nw = len(x)//win
    return np.sqrt((x[:nw*win].reshape(nw,win)**2).mean(axis=1))

A, B = env(成片), env(底片)        # D = A - B 就是音效层贡献的能量
for s in sfx_plan:
    c = int(s["t"]/0.02)
    pk = D[c-2:c+10].max()                                     # 该点邻域增量
    base = np.median(np.abs(D[max(0,c-30):c-5]))               # 前 0.5s 基线
    判定 = pk > 0.004 and pk/base > 2.0
```

本片实测 15 路：
- **14 路明确检出**（增量是基线的 2.2~8.4 倍）；
- `kw_charge`（18.08s，数字滚动蓄力，`vol=0.4`）只有 1.40 倍 —— 它本身就轻、且那 0.2 秒口播正响。
  判据要允许这种例外：**绝对值 ≥0.028 且紧跟一个明确检出的落定音（`kw_marker` @18.60s）⇒ 判在轨**。

> 判定阈值是经验值（pk>0.004 / ratio>2.0），不同片子音量不同要重标；但**"两音配对、轻的那个算在轨"**这个逻辑通用。

---

## 六、本片最终参数（可直接抄的配方）

| 项 | 值 |
|---|---|
| 画布 / 帧率 | 1920×1080 / 30fps，855 帧 |
| 字体 | `assets/fonts/新青年体.ttf` |
| 元素数 | 11 个字卡（pop×4 / stamp×3 / bloom×2 / slide×1 / tag×1） |
| 数字滚动 | `8mg锌` 0→8（num_dur 0.52s）、`囤3瓶` 0→3（0.62s） |
| 音效 | 15 路（每字卡 1~2 条，蓄力音 + 落定音配对） |
| 渲速 | fx 层 20s / 合成 25s |
| 成片响度 | -10.5 LUFS，真峰值 -0.4 dBFS |

---

## 七、踩过的坑（速查）

1. **alpha 全丢** → profile 必须是 `4444`、`yuva444p10le`。
2. **残影** → 每帧从 `zeros` 起步，别复用上一帧 buffer。
3. **字压人** → 换画面必重排（§三）。
4. **尾部黑场** → `trim=end_frame`（§四）。
5. **口播被压到一半** → `amix` 漏写 `normalize=0`。
6. **真峰值顶破** → 出口 `alimiter=limit=0.95:level=false`。
7. **音效"看起来"进了其实没进** → 用包络差验（§五），别靠听。
8. **黄底上出现"黑方块"** → 不是渲染 bug，是**字本身笔画太重**：`囤`（囗+屯）在 `新青年体` 重磅下
   囗的外框和内部笔画糊成一整块实心方块，肉眼读成"缺字/豆腐块"（2026-10-25 版本四实拍确认，
   字体 cmap 里**有这个字**，别去查缺字表浪费时间）。
   判据：黄底黑字卡里，凡"全包围/半包围结构 + 内部件笔画密"的字（囤、国、图、圆、黑、墨）都要换词，
   或把该字从字卡里去掉只留数字（该片最终改成 `3瓶` 0→3 滚动）。
9. **单字数字滚动宽度会跳** → 0→8 / 0→3 / 0→2 都只占一位数，宽度恒定，**不会抖**；
   一旦目标值跨到两位数（0→12），必须走 `band_final_w` 固定宽度居中那条分支。
