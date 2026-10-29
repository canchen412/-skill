---
name: cloud-tts
description: 云端配音（TTS API）全流程技能：选型、停顿控制、试听、接进出片链路、逐字时间戳。当用户说「云端配音」「配音不占内存」「TTS API」「换云端跑配音」「MiniMax 配音」「豆包配音」「配音太慢」「配音停顿」「AI感」「配音像不像真人」「CosyVoice」「百炼配音」「配音语速」「出片配音」时使用。含：①2026-10 查证的各家价格与推荐顺序（首推阿里云百炼，一家可试 CosyVoice/Qwen-TTS/MiniMax 三款）；②MiniMax 停顿与语气词控制（`<#0.35#>` 两条官方硬约束、20+ 语气词标签）；③`scripts/tts_prep.py` 文案→带精确停顿标注版（含合规自检）；④**`scripts/cloud_cosyvoice_ws.py` 百炼云端配音实操脚本**（原生 WebSocket 通路，一次拿全音频+**逐字时间戳**+srt，可替掉本地 whisper 对齐；已用真实音色实测通过）；⑤**`scripts/mk_tokens_cloud.py` 把逐字时间戳转成出片级 token**（vo_mapped_v2.json + shot_bounds.json + srt，`--check` 可验对齐质量，实测相似度 1.0000）；⑥**定稿音色 `cosyvoice-v3.5-plus` + `rate=1.1` 已固化在 `~/.workbuddy/secrets/dashscope.env`**（2026-10-16 灿爹拍板「就用我建的音色」）；⑦**关键实测结论：换云端后语速要设 1.1，不能沿用本地的 1.2**（本地 F5 是 7.32 字/秒，CosyVoice rate=1.0 只有 6.70）；⑧三条最容易出 AI 感的流程陷阱（逐句生成导致语气断裂、换行=段落切换、数字念错）。⑨**已接进出片流水线（2026-10-16）**：`koubo-ad-video/scripts/pipeline_run.py` 第 2 步的默认引擎就是本技能这两支脚本，出片时**不用手动跑任何配音命令**；**`--tts-engine` 默认值已是 `cloud`（技能成片一律走云端，缺 Key/脚本直接报错中止，不静默降级）**。⑩**`scripts/check_cloud_tts.py` 一键体检**（查配置 → 查脚本 → **真跑一次短句合成**，10 秒给结论、非零退出可当闸门；Key 轮换后/出片卡在配音时先跑它）。触发词：云配音、云端配音、TTS API、配音停顿、AI感、语气词、MiniMax、豆包配音、CosyVoice、百炼、配音选型、逐字时间戳、出片配音、配音体检、云端配音不可用。
agent_created: true
---

# 云端配音：选型 + 停顿控制 + 试听

> 起因：本地跑配音（8GB 机器）吃内存又慢，要搬云端。
> 本文结论 2026-10-16 查证，**价格与政策会变，落地前以官网为准**。

> ★**2026-10-16 更新**：出片流水线的配音引擎**默认值已改为 `cloud`**，并新增一键体检脚本
> `scripts/check_cloud_tts.py` —— 详见文末 **§7.8**。

## 〇、先记住三句话

1. **合成的钱可以忽略**（一条 300 字口播片 ¥0.02~0.07），选型别盯单价，盯「建音色的钱」和「像不像真人」。
2. **「AI 感」大半来自停顿不对**，而停顿是可控的 —— 别指望模型自己猜。
3. **换平台前先拿真实参考音频实测**，别人说像没用，你的耳朵才算。

---

## 一、推荐顺序

| 顺序 | 平台 | 为什么 |
|---|---|---|
| **1** | **阿里云百炼**（Model Studio） | 一家可试三款模型（CosyVoice / Qwen-TTS / 阿里托管 MiniMax）；国内直连 + 支付宝；建音色最便宜甚至免费；开通 90 天内送 1000 个音色额度 |
| 2 | 火山引擎 豆包声音复刻 2.0 | 中文自然度国产第一梯队、5 秒克隆（相似度 97.5%）、延迟最低；但音色槽位 ¥138/个且 1 年有效 |
| 3 | Fish Audio | 中文非常好、15 秒克隆、开源可商用；国内访问需条件 |
| ✗ | ElevenLabs | 英文天花板，中文三声有洋腔、需代理、最贵 —— 中文带货不推荐 |

**优先试 CosyVoice**：与本地链路同源，音色迁移最平滑（已发过片的系列感不容易断）。

---

## 二、停顿怎么控（最关键的技能点）

### 2.1 MiniMax 的停顿标记
```
<#0.35#>      停顿 0.35 秒，范围 [0.01, 99.99]，最多两位小数
```
**两条官方硬约束，踩了标记会被吞或节奏歪：**
1. 必须夹在**两个「可发音文本片段」之间** —— 不能贴行首、不能贴行尾
2. **不能连续使用**两个停顿标记

其他文本控制手段：
| 手段 | 效果 |
|---|---|
| 换行符 | **段落切换** ⚠️ 会带来额外长间隔，粘贴版千万别为了好看加换行 |
| `，` 逗号 | 模型自带约 0.2s 微停，一般不用再标 |
| `……` 省略号 | 强制较长的声学呼吸停顿 |
| 句末标点 | 不加标点可能丢最后一个音节 |

### 2.2 语气词标签（仅 `speech-2.8-hd` / `speech-2.8-turbo`）
`(breath)` `(emm)` `(sighs)` `(inhale)` `(exhale)` `(chuckle)` `(clear-throat)` `(sniffs)` `(gasps)` `(laughs)` `(pant)` `(coughs)` `(humming)` `(sniffs)` 等 20+ 个。
**是文本内容的一部分**，不要放进 `voice_setting.emotion`。

一句带货口播里加一两个 `(breath)` / `(emm)`，真人感会明显上一档 —— 但别滥用。

### 2.3 ⚠️ 长句会被加速甚至截尾
MiniMax 的字符密度约 **3.3 字/秒**：
| 片段时长 | 中文上限 |
|---|---|
| 10 秒 | 30–34 字 |
| 15 秒 | 45–50 字 |

超过 50 字的句子必须按语义拆成两段生成，否则模型压缩语速甚至截掉尾音。

---

## 三、三个最容易出「AI 感」的流程陷阱

### 陷阱 1：逐句生成 → 语气断裂（★ 但要分引擎，别一刀切）

「逐句配音再拼接」的副作用是**每句都重新起头的朗读感**，句与句语调不连贯，听感很「AI」。
但**能不能用整段生成，取决于引擎支不支持人工控停顿**——这一点必须分开看：

| 引擎 | 停顿靠什么 | 定稿做法 |
|---|---|---|
| MiniMax | `<#0.35#>` 标记可用 | **整段生成** + 标记控停顿 + 句子级时间戳切回分句 |
| 百炼 CosyVoice | 标记、SSML **都不可用** | **逐句合成 + 裁真静音拼接**（见下方 ★） |

**★百炼 CosyVoice 必须逐句（2026-10-29 灿爹验收拍板「以后就按这个标准配音」）**

同一个模型，句号处的停顿完全取决于「这段文本在本次请求里是不是结尾」：

| 用法 | 句号处停顿 |
|---|---|
| 一次喂整段 | **25ms**（「米糊。外胚」直接连读） |
| 一句一喂 | **380~510ms**，且是完整句末收尾 |

所以整段合成后**在音频上硬补停顿，必然切到字尾**——灿爹原话
「每句不管是短句还是长句，最后一一两个字都会卡一下」，就是这么来的（补了两次、被打回两次）。
改逐句合成后，句间停顿由「段末保留 + 段首保留」拼出来，一个字都不切，听感自然。

三条同时记住：
- **合成是确定性的**：同文本同 seed 逐样本完全一致 → 「再生成一次」不会有任何变化；
- **SSML 被拒**：`SSML text is not supported at the moment!`；
- **文本加空格 = 停顿消失**；重复标点「。。。」能拉长停顿但会破坏逐字对齐，禁用。

实现见 `koubo-ad-video/scripts/tts_split_cloud.py`，详细病历见该技能
「★★★★ 终版结论：停顿要逐句合成拿」一节。

### 陷阱 2：把换行当排版用
MiniMax 里**换行 = 段落切换**。为了好看在停顿标记后换行，会让每个停顿都变成超长段落间隔，节奏直接散掉。
本技能的 `tts_prep.py` 已按这条设计：**粘贴版保持原段落，另出一份「一句一行」的对读版供肉眼核对**。

### 陷阱 3：数字念错
带货文案里全是「19块9」「SGS」「2999.7mg」。**必须开 `text_normalization: true`**，否则价格/成分数字容易念错，一听就出戏。

---

## 四、工具：文案 → 带精确停顿的标注版

```bash
SK=~/.workbuddy/skills/cloud-tts/scripts/tts_prep.py

# 文案 → 标注版（默认 koubo 预设：句末 0.35s / 冒号 0.25s / 定场句 0.45s）
python3 $SK --text 文案.txt --out ./tts_out

# 想给逗号也加停顿
python3 $SK --text 文案.txt --out ./tts_out --comma 0.15

# 慢节奏旁白预设
python3 $SK --text 文案.txt --out ./tts_out --preset narration

# 单独检查一段带标记的文本是否合规
python3 $SK --check "今天我们讲，肌酸。<#0.45#>先算账："
```

输出四个文件：
| 文件 | 用途 |
|---|---|
| `<名>_mm.txt` | **粘这个** —— 保持原段落，标记内嵌，直接贴进体验页或 API |
| `<名>_mm_lines.txt` | 一句一行，**只供肉眼核对，别拿去粘贴** |
| `<名>_mm_seg.json` | 分段 + 每段建议的 `emotion` / `speed` |
| `<名>_mm_report.md` | 停顿明细 + 数字检测 + **硬性自检** + 软提示 |

脚本会自动做的事：
- 按标点+灿爹口吻卡的角色（钩子/定场/算账/品类地位/盲区/品牌事实/交权/CTA）切句，给每段配 `emotion`/`speed` 建议
- 强制自检官方两条硬约束（越界、连续标记、行首标记）
- 长句超 34/50 字告警
- 列出数字，提醒开 `text_normalization`

---

## 五、试听流程（不写代码，10 分钟出结论）

1. 开通**阿里云百炼** → 找「语音合成」，进在线体验页
2. 用 `tts_prep.py` 生成标注版，**整段粘贴**（不要一句一句粘 —— 那就复现了陷阱 1）
3. 先跑 CosyVoice v3.5-flash，再换 v3.5-plus，再换 MiniMax speech-2.8-hd，同一段对比
4. **用真实参考音频克隆音色**，不要用预置音色 —— 要判断的是「克隆得像不像」
5. 判断标准（按重要性）：
   - 停顿位置对不对、有没有「匀速平铺」
   - 语气词/呼吸有没有、长句会不会赶
   - 数字念对没有
   - 和你已有成片的音色接不接得上

---

## 六、坑

- **音色会被回收**：阿里侧音色 1 年未使用自动删；MiniMax 快速克隆音色需在 168 小时内用一次才保留。长期用要定期跑一次。
- **免费额度多数限个人非商用**。带货是纯商用，必须走付费额度。
- **情绪标签整段只认第一个** —— 要换情绪必须切段生成，`_mm_seg.json` 里给的就是切段建议。
- **火山音色槽位 ¥138/个且 1 年有效** —— 固定 1–2 个音色其实不贵，但别一次建一堆。
- **不同平台底层模型不同，音色不可能完全一样**。换平台后要留一段过渡期。

详见 `references/试听与参数速查.md`。

---

## 七、★ 接进出片链路（2026-10-16 实测跑通，并**已写进 `pipeline_run.py`**）

> **★ 出片时不用手动跑本节任何命令**：`koubo-ad-video/scripts/pipeline_run.py` 第 2 步的
> 默认引擎就是云端（`--tts-engine auto`，见该技能 §「口播配音」）。本节的两支脚本是
> **流水线内部调用**的（`tts_cloud()`），手动跑只在「试听 / 单段重配 / 排查对齐」时用。
> ★ 同步给别的机器（小号）时：技能包里有这两支脚本，但 **API Key 与音色 ID 不会跟着走** ——
> 要在那台机器上同样建一份 `~/.workbuddy/secrets/dashscope.env`（内容照 §7.5；音色 ID 是绑账号的，
> 换账号得用同一段参考音频重新 `create_voice`）。

> 结论先行：**换云端后语速要设 `rate ≈ 1.1`，别再沿用本地的 1.2。**
> 实测（同一份 248 字稿）：本地 F5 = 7.32 字/秒；CosyVoice rate=1.0 = 6.70（慢 8%）；
> **rate=1.1 = 7.37（差 0.7%，听感一致）**。1.2 是 F5 时代的补偿值，CosyVoice 基准本来就快，1.2 会快出 15%。

### 7.1 四个脚本分工

| 脚本 | 干什么 | 什么时候用 |
|---|---|---|
| `scripts/cloud_cosyvoice.py` | 建音色（`create_voice`）+ 简单合成（走 dashscope SDK） | 第一次建音色 |
| **`scripts/cloud_cosyvoice_ws.py`** | **合成 + 逐字时间戳 + srt**（走原生 WebSocket） | **出片主力** |
| **`scripts/mk_tokens_cloud.py`** | **逐字时间戳 → 出片级 token**（`vo_mapped_v2.json` + `shot_bounds.json` + srt） | **合成完紧接着跑** |
| **`scripts/check_cloud_tts.py`** | **一键体检**（配置 / 脚本 / 真跑一次合成） | **出片前、Key 轮换后、配音卡住时**（见 §7.8） |

★ 后两支由 `koubo-ad-video/scripts/pipeline_run.py` 的 `tts_cloud()` **自动串联**（见 §7.7）；
第四支是**独立体检**，不被流水线调用，出问题时先手动跑它。

**为什么出片要用 WS 那个**：dashscope SDK 的 `SpeechSynthesizer` **只回音频、不暴露词级时间戳**
（源码里那两个 `timestamp` 只是首包延迟统计）。而逐字时间戳正是能替掉本地 whisper 对齐的东西。

```bash
source ~/.workbuddy/secrets/dashscope.env       # 含 Key + 定稿音色，见 7.5

# ① 合成（一次拿全：wav + mp3 + words.json + srt）
#    ★ 音色/模型/语速已固化，只给文案和输出路径即可
python3 ~/.workbuddy/skills/cloud-tts/scripts/cloud_cosyvoice_ws.py \
    --text-file 文案.txt --out ./s1/audio/vo

# ② 先验对齐质量（不写文件），再看结论
python3 ~/.workbuddy/skills/cloud-tts/scripts/mk_tokens_cloud.py \
    --words ./s1/audio/vo.words.json --phrases phrases.txt --check

# ③ 出片级 token
python3 ~/.workbuddy/skills/cloud-tts/scripts/mk_tokens_cloud.py \
    --words ./s1/audio/vo.words.json --phrases phrases.txt --out-dir ./s1/audio
```

### 7.2 WS 通路的协议要点（踩过的坑）

- **音频走二进制帧**，不是 base64。收集时 `isinstance(raw, bytes)` 要单独收，别当 JSON 解析（否则音频 0 字节）。
- 事件流：`task-started` → `result-generated`（多次）→ `task-finished` / `task-failed`。
- 打开时间戳：`run-task` 的 `parameters` 里加 **`word_timestamp_enabled: true`**。
- 时间戳在 `payload.output.sentence.words`，字段 `{text, begin_index, end_index, begin_time, end_time}`，**单位毫秒**。
- `result-generated` 会多次推送同一句的**累计更新**，取词数最多的那一份才完整。
- ★ **文字会被规范化后再对齐**：`19块9` → `十九块九`。所以 `begin_index` 是按 `normalized_text` 算的，做字幕要用 `normalized_text`，**别拿原文案去索引**。

### 7.3 音色是绑模型的

`create_voice(target_model=...)` 建出来的音色**不能跨模型用**。要比 CosyVoice / Qwen / MiniMax，
得用**同一段参考音频各建一次**。

### 7.4 已知前提

- 参考音频：WAV(16bit)/MP3/M4A，≥16kHz，≤10MB，推荐 **10–20 秒**连续清晰朗读，不能有 BGM/噪声/他人声。
- 建音色**必须公网可访问的音频**（走 SDK 时给 `file://` 由 SDK 自动传 OSS）。
- 服务须**开通百炼**（控制台点一下；需实名）。

### 7.5 ★ 定稿音色（2026-10-16 灿爹拍板「就用我建的音色」）

固化的地方：`~/.workbuddy/secrets/dashscope.env`（与 API Key 同一文件，别每次手输 ID）。

```bash
export COSYVOICE_MODEL='cosyvoice-v3.5-plus'
export COSYVOICE_VOICE_ID='cosyvoice-v3.5-plus-candi-be84af59211943aca1048d7b1d941265'
export COSYVOICE_RATE='1.1'
```
`cloud_cosyvoice_ws.py` / `cloud_cosyvoice.py` 会自动读这三项为默认值 —— **调用时不用再传**
（文件里还留了 bailian / hahengdali / flash 三组备选，注释切换即可）。

**音色要 bind 模型**：账号里同名音色有多个版本，`candi` 那条只绑 `cosyvoice-v3.5-plus`。
用 `list_voices` 查当前清单：
```python
from dashscope.audio.tts_v2 import VoiceEnrollmentService
VoiceEnrollmentService().list_voices(prefix="")
```

### 7.6 ★ 出片级 token 生成（`mk_tokens_cloud.py`）

**和 F5 版 `mk_tokens_f5.py` 的根本区别**：F5 版句内是「按字数比例猜时间」，本版句内也是实测值。

**为什么要「规范化对齐」**（这是最容易踩的坑）：
云端 TTS 送进去的不是原文，是它自己转写过的文本，三类变化都会改字数：

| 原文 | 云端转写 | 字数变化 |
|---|---|---|
| `19块9` | `十九块九` | 3 → 5 |
| `99.9%` | `百分之九十九点九` | 5 → 8 |
| `SGS` | `sgs` | 不改字数（仅大小写） |

所以脚本先对原文做**同样的规范化**，再用 `difflib` 对齐，最后把时间映射回原镜位。
**字幕显示仍用原文字形**（「19块9」，不是「十九块九」）。

其他实现要点：
- 云端会把**标点和字母凑成一个词元**（实测到 `，sgs`），所以标点必须**逐字符**过滤，整词元判定会漏。
- 大小写比较时统一小写（云端对 `SGS` 转小写、却把孤立的 `A` 留成大写，不统一）。
- 镜界取「该镜首字 begin、末字 end」，全是实测值，不推导。
- **`--check` 是出片前的安全闸**：相似度 < 0.98 或有未映射字符 → 返回非零，**别拿它出片**。
- `--debug` 打印差异块，用来定位规范化规则还缺什么（`99.9%` 就是这么发现的）。
- 无 `--phrases` 时按整篇切 token（兜底）。
- 输出 `systeminfo: "cloud-cosyvoice-words"` 便于和 F5 产物区分。

实测：248 字长稿（11 镜）与 158 字短稿**对齐相似度均为 1.0000，零未映射字符**。

实测数据与同稿 A/B 记录见 `references/云端配音实测_261016.md`。

### 7.7 ★ 出片流水线里的调用契约（`pipeline_run.py` 的 `tts_cloud()`）

**产物名与本地 F5 路线完全一致**，所以下游 `build_ad_video` / `make_fx_spec` / 卷播切点**零改动**：

| 文件 | 谁写 | 谁读 |
|---|---|---|
| `audio/vo.wav` | `cloud_cosyvoice_ws.py` | build_ad_video 混音、qc |
| `audio/durations.json` | `mk_tokens_cloud.py` | pipeline：`len(durs)==n` 断言、轮播切点 `durs[定场句镜]["end"]` |
| `audio/vo_mapped_v2.json` | `mk_tokens_cloud.py` | build_ad_video（字幕 + 镜界）、make_fx_spec（重点字锚点） |
| `audio/shot_bounds.json` | `mk_tokens_cloud.py` | 镜界对照 / 复核 |
| `audio/vo.words.json` | WS 脚本 | `mk_tokens_cloud` 的输入、流水线**必读词校验** |

**流水线里写死的三条（改 `pipeline_run.py` 时别碰）**：
1. 合成文本**必须与 `phrases.txt` 逐字一致**，是对齐闸能成立的前提。两种模式的喂法不同，别弄混：
   - **`split`（默认，2026-10-29 起）**：`tts_split_cloud.py` 把 `phrases.txt` **一行一次**送合成，
     段间靠裁真静音 + 拼接得停顿；**不能**再 `"".join` 成一段，否则句末收尾全被压成 25ms；
   - `mono`（老行为）：`"".join(phrases)` 写 `audio/text_tts_src.txt`，不换行、不加标记、不做替换。
2. 先 `--check` 再出件：相似度 <0.98 或有未映射字符 ⇒ **抛错中止**。
3. **不套** `poly_map`（同音字替换）/ `norm_map`（品牌词读法）—— 两者都会改字数、对齐必挂。代价是出片后要人工听读音。

**为什么 `tail_keep` 用默认 1.0**：云端镜内 token 是逐字实测，末 token 就该收到「末字实测结束」；F5 版的 0.88 留白是「按字数比例猜」时代的防压音尾手段，云端不需要照搬。

**镜界与画面对齐怎么成立的**（免疑）：build_ad_video 是拿 token 流按每镜字数贪心切分，每镜画面从**本镜首字时刻**延续到**下一镜首字时刻** —— 所以云端句间停顿天然被前一镜画面吸收，不需要额外补 gap（F5 那 0.22s 拼接缝在这里不存在）。

### 7.8 ★ 出片默认引擎 = 云端（`--tts-engine cloud`），并配一键体检

**2026-10-16 灿爹第二次拍板「写进技能里，每次技能成片就好用它」** ⇒ `pipeline_run.py` 的
`--tts-engine` **默认值已由 `auto` 改成 `cloud`**：日常出片不传任何参数就是云端；云端不可用时
**直接报错中止**，不再静默降级去啃本地 8GB 内存。

**三档语义（记住这张表）**：

| `--tts-engine` | 云端可用 | 云端不可用 | 谁用 |
|---|---|---|---|
| **`cloud`（默认）** | 走云端 | **直接报错中止**，并打印修复步骤 | 日常出片（什么都不用传） |
| `auto` | 走云端 | 告警后退回本地 F5 | 应急：云端临时挂了也要出片 |
| `local` | 忽略 | 走本地 F5（~8GB / ~2.5 分钟） | 显式要求本地路线时 |

**为什么默认不再"温柔回退"**：原 `auto` 在探测不到 Key/脚本时告警后降级到本地 F5 —— 但换云端
本就是为了省内存，降级恰好回到要消灭的情形；而且降级只是变慢、不报错，很容易一条片渲完才发现
跑的是老路线。**宁可不跑，也不降级。**

**引擎自检提前到"读配置之前"**：缺 Key 时 **0.1 秒**就报错退出，不再切完镜、写完 `phrases.txt`
才说。`--skip-tts`（复用已有配音）**不校验**，允许云端临时不可用时续跑渲画面。

**体检脚本 `scripts/check_cloud_tts.py`** —— 出片前、Key 轮换后、怀疑音色被回收时跑一次：

```bash
$PY ~/.workbuddy/skills/cloud-tts/scripts/check_cloud_tts.py
$PY ~/.workbuddy/skills/cloud-tts/scripts/check_cloud_tts.py --text "今天这条视频" --quiet
```

它做三件事，**第 3 件是真跑一次短句合成（不是 ping）**：
① 查 `~/.workbuddy/secrets/dashscope.env` 是否含 Key/模型/音色/语速（缺 `COSYVOICE_VOICE_ID` 会提示音色可能不对）；
② 查 `cloud_cosyvoice_ws.py` / `mk_tokens_cloud.py` 是否在位；
③ 实跑合成，验 Key 有效 + 音色可用 + 逐字时间戳通路通。
任一项不过 → **非零退出**，可直接当闸门接进别的脚本。

**为什么必须真跑**：Key 失效、额度耗尽、音色被回收（阿里侧 1 年未用自动删）这三件事 ——
恰好是出片中途最常见的翻车点 —— 在"读配置"阶段**全都看不出来**，只有真发一次合成请求才暴露。

实测输出：
```
✓ 1/3 配置：Key=sk-ws-H.PYDR…Rp1KlQ（115 字符）  model=cosyvoice-v3.5-plus  rate=1.1
✓ 2/3 脚本：cloud_cosyvoice_ws.py + mk_tokens_cloud.py 均在位
✓ 3/3 合成成功（2.2s）：音频 2.82s / 135316 字节，逐字时间戳 18 词
结论：✅ 云端配音可用。出片直接用默认参数即可，无需任何手动步骤。
```

**踩过的坑（脚本里已修）**：体检脚本读 env 后必须把 **`DASHSCOPE_API_KEY` 也灌进 `os.environ`**
（只灌 model/voice/rate 不够），否则第 3 步的子进程继承不到环境，会误报「缺 API Key」——
看起来像 Key 坏了，其实只是自己没把 Key 传下去。**这个坑写同类脚本时会反复遇到。**
