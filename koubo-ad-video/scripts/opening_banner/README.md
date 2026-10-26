# 带停顿配音（配合卷播片头 extend）

卷播当镜1 时，`extend: 1.15` 让画面在定格主图上多停 1.15s。**若配音不带这段停顿，画面停住、声音却继续 ⇒ 观感割裂**。本脚本生成配套的「带停顿配音 + 后移时间轴」。

## 用法
```
python3 mk_vo_pause.py s1 s2      # 参数是 config 所在的目录名（工程下）
```
前置：`s1/audio/vo.mp3`（配音）与 `s1/audio/vo_mapped.json`（align_vo 产物）必须已存在。

## 产物
- `gen/vo_with_pause_<sid>.mp3` —— 镜1 末字后插入 1.15s 静音
- `<sid>/audio/vo_mapped_pause.json` —— 后续 tokens 时间轴整体后移

然后把 config 指过去（**必须用绝对路径**）：
```json
"vo": "<工程绝对路径>/gen/vo_with_pause_s1.mp3",
"tokens": "<工程绝对路径>/s1/audio/vo_mapped_pause.json"
```

## 三个实现要点（都踩过坑）
1. **时间轴在 `vo_mapped.json` 的 `transcription[].offsets`（毫秒）**，不是 `timestamps`（那是 `"00:00:01.50"` 字符串形式，两处都要改）。
2. **插静音用 `atrim + anullsrc + concat`**，不要用 `adelay`（那是拉伸，会把后面的音频全拖慢）。
3. **插入点判定**：从 tokens 逐段累计实义字数，累计到「config 镜1 的字数」那一段的 `offsets.to` 就是末字时刻。

## 注意
- 卷播镜**不参与 pchars 核对**（tokens 含全片、phrases 排除卷播镜，字数天然差 12 字，属正常）。
- 改 phrases 后必须重跑 `align_vo.py` → `mk_vo_pause.py`，顺序不能反。
