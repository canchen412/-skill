# F5-TTS 本地产线（MLX 版 · 2026-10-09 定型）

> ★**2026-10-16 起本产线不再是默认**：出片默认走**云端 CosyVoice**（约 10 秒、零本机内存，
> 见 `cloud-tts` 技能与 SKILL.md §口播配音）。**本文只在 `--tts-engine local` 回退时适用**
> —— 即云端缺 Key / 断网 / 云端合成出问题要对照本地时。

★**本地首选产线**（2026-10-09 灿爹拍板：先用 F5，效果不行再退 CosyVoice 双进程）。
**速度：比 CosyVoice CPU 快约 3 倍；内容零幻觉零重复**（非自回归架构）。

## 环境（已部署）
- venv: `/Users/mac/.workbuddy/models/mlx-audio/venv`（含 f5-tts-mlx + jieba 源码直拷 + 全依赖）
- 模型已缓存，跑时必须 `export HF_HUB_OFFLINE=1`（否则 HF 校验 502）
- 修复过的 patch：f5_tts_mlx/cfm.py 的 mx.random tuple→list + int(dur)（mlx 0.32 API）

## 合成命令
```bash
export HF_HUB_OFFLINE=1
python -m f5_tts_mlx.generate --text "正文（数字已归一化）" \
  --ref-audio <参考24kHz mono wav> --ref-text "<与音频严格逐句对应的转录>" \
  --output out.wav --estimate-duration true
```

## 七条硬定式（缺一不可，踩过的坑）
1. **ref_text 与参考音频严格逐句对应**，尾句必须完整+句号收尾——
   音频截半句而转录全句 ⇒ 模型在每段开头「补念」尾句（幽灵）。
2. **文本归一化（2026-10-09 通用化，`f5_batch.py norm()` 自动做）**：
   阿拉伯数字/拉丁字母是乱读+漏读高发区——实测「单粒300mg」整段乱语、
   「VORSE」读成 V2CE、38 字长句开头 8 字整段被吞。规则链：
   ① config `norm_map`（品牌词，长词优先，如 VORSE→沃斯）→ ② 百分比（98%→百分之九十八）
   → ③ 数字+单位（300mg→三百毫克；★结尾用 `(?![A-Za-z])` 不用 `\b`，中文汉字也是
   `\w`，「10mg黑胡椒」的 g|黑 不构成词边界）→ ④ 剩余数字→中文 → ⑤ 剩余字母串逐字谐音
   （SGS→艾斯吉艾斯）。**norm_map 别带上下文词**（SGS→「艾斯吉艾斯检测」会撞上原句
   「权威检测」读两遍）。polish 裁幽灵的 head 也必须用归一化后文本取前6字
   （whisper 转录的是发音「三百毫克」，拿「300mg」匹配永远失败→幽灵裁不掉）。
3. **`--estimate-duration true` 必带**：duration predictor 输出 0.04s 异常，
   启发式按文本估时正常。
4. **合成后必跑 `scripts/polish_f5.py in.wav out.wav "正文前6字"`**：
   F5 稳定在开头泄露参考首词 1 字（重roll/垫静音均无效），
   用 whisper -ml 1 字级时间戳定位正文起点，**起点前移 0.03s** 裁剪
   （灿爹 2026-10-09 三裁点对比拍板：狠裁 0.08s 最自然），
   尾部按能量裁剪 +0.12s。
5. **情感对齐法**：参考音频用「本地 CosyVoice3 高情感输出段」
   （非原始平静样本）——F5 情感完全由参考音频带入。
6. **whisper 转写只做初筛**：繁体/同音字（截→解、十九块九→时就快）
   会拉低机器相似度造成误判，终审交给灿爹耳朵。
7. **单句 ≤32 字（split_long 居中拆）**：38 字长句实测整段漏读开头——
   长句是 F5 漏读/乱读第一高发区，流水线切镜时必须拆。

## 参考音频规范
mono / 24kHz / 16bit PCM / 5-12s / 尾句完整。
转换：`ffmpeg -i in.wav -ar 24000 -ac 1 -sample_fmt s16 out.wav`
