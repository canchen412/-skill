# 环境准备与技术细节（本机 macOS / Intel i9 实测）

## ffmpeg
- 静态二进制已装在 `~/.workbuddy/binaries/ffmpeg/`（ffmpeg + ffprobe，Intel 版来自 osxexperts.net）。
- 本机 pip 装 numpy / imageio-ffmpeg 等大包会被系统杀掉（exit 137 / SIGTERM），**不要用 pip 装二进制类包**，用 curl 下静态包。edge-tts、pypinyin 等纯 Python 小包可以装。
- venv: `/Users/mac/.workbuddy/binaries/python/envs/default/bin/python`

## whisper.cpp 语音对齐（字幕卡点核心）
- ★**2026-09-19 已迁到持久目录，不再随 /tmp 被清**（旧文档写 /tmp，每次会话都要重装，浪费时间）：
  ```bash
  W=/Users/mac/.workbuddy/models/whisper.cpp   # main + models/ggml-base.bin 都在这里
  $W/main -m $W/models/ggml-base.bin -f vo16.wav -l zh -oj --max-len 4 -bs 1 -bo 1 -t 16
  ```
- 真丢了才重装（新版需要 cmake、本机没有，**必须 v1.6.2** 旧版自带 Makefile）：
  ```bash
  cd /Users/mac/.workbuddy/models && git clone --depth 1 --branch v1.6.2 https://github.com/ggml-org/whisper.cpp.git
  cd whisper.cpp && make -j4    # 产物 ./main
  ```
  - git 协议可能被代理挡（502 / HTTP2 错误），加重试或 `git -c http.version=HTTP/1.1`；curl 拉 tarball 也行。
- 模型：
  ```bash
  curl -L -o models/ggml-base.bin "https://hf-mirror.com/ggerganov/whisper.cpp/resolve/main/ggml-base.bin"
  ```
  - 注意仓库名是 `ggerganov/whisper.cpp`（`ggml-org/...` 会 404）。校验：文件约 148MB，头 4 字节 "lmgg"。
- **另一把更细的尺子（★2026-09-19 新增，诊断断句专用）**：cosyvoice venv 里有 `openai-whisper`
  （`small` 权重已缓存在 `~/.cache/whisper/small.pt`），
  `whisper.load_model("small").transcribe(wav, word_timestamps=True, language="zh", fp16=False)`
  能给出**逐字**时间戳（中文通常一字一词），比 `-ml 4` 的 4 字组精确一个量级
  —— `scripts/qc_vo_pause.py` 就是用它定位"停顿落在哪两个字之间"。
  ⚠️ 用它必须先 `export PATH=/Users/mac/.workbuddy/binaries/ffmpeg:$PATH`（whisper 内部要调 ffmpeg）。
  ⚠️ 自己用 numpy 读音频时，ffmpeg 输出务必带 `-f f32le` 或写成标准 wav 用 `wave` 模块读
  —— 漏了就会把 44 字节 wav 头当浮点读，数据整体错位、断点检测全错（2026-09-19 踩过）。
- 转音频并识别（`--max-len 4` 给逐字时间戳，`-oj` 出 JSON；**`-bs 1 -bo 1` 必须加，默认 5 beams 慢 10 倍**）：
  ```bash
  ffmpeg -i 口播.mp3 -ar 16000 -ac 1 vo16.wav
  ./main -m ggml-base.bin -f vo16.wav -l zh -oj --max-len 4 -bs 1 -bo 1 -t 16
  ```
- 产物 `vo16.wav.json` 结构：`transcription[].offsets.from/to`（毫秒）+ `text`。
- **不要直接按字符数贪心消费**——whisper 常把多字节字符拆坏成 U+FFFD（读 JSON 需 `errors="replace"`），字数与文案不符就会整体漂移（实测 246 vs 243 就错位 3 字）。用 `scripts/align_vo.py` 拼音强制对齐。
- **结尾漏句是常态**（实测最后一句 6 个字整轨识别不到，但音频里确实有：`ffmpeg -ss 35.5 ... volumedetect` 量到 mean -21dB 不是静音）。切尾部单独识别后 `--extra=tail.wav.json:35.5` 补进来。

## 对齐算法要点
- **首选拼音强制对齐**（`scripts/align_vo.py`）：文案与识别文本 → pypinyin → difflib 序列比对 → 乱码/插入字跳过、缺字按前后时间线性插值 → 输出与文案严格同字数同字形的 JSON。
- 需要 pypinyin：`/Users/mac/.workbuddy/binaries/python/envs/default/bin/pip install pypinyin`。
- 旧的「按非标点字符数贪心消费」仅在识别无乱码时可用；**必须保留字母和数字**（B、16mg、8瓶）。
- whisper 会输出繁体+同音字（"烟酰胺"→"延下安"、"性价比"→"性价力"），只看时间不看文字正确性；对齐后逐句打印"听到的字"人工核对同音是否对位。

## 产品图贴图（2026-09-16 新增）
- 需求形态：达人只给**产品图**（没有产品视频）时，口播提到产品内容就把产品图贴到分镜画面上，参考片（蜘蛛侠风绿肌猛男 32.5s）就是这么做的——产品罐/成分表以白底卡片贴在右上角。
- shot 配置：
  ```json
  {"kind":"img","src":"分镜图.png",
   "over":{"src":"产品图.png","w":620,"pos":"tr","delay":0.12,"fade":0.3}}
  ```
  `pos`: `tr/tl/br/bl/center` 或 `[x,y]`；`br/bl` 已自动上移到字幕区之上；`over` 可传数组贴多张。
- 产品图全屏特写（成分表/含量）：`{"kind":"img","src":"产品图.png","fit":"contain"}`——`contain` 等比留边（配 `pad_color`），默认 `cover` 等比裁切不拉伸；AI 3:2 图配 `cover` 与原行为等价。
- 旧配置的 `img_prefilter` 仍然兼容（优先级最高）。
- **贴图必须用 `-loop 1 -t <镜长>` 让图片变成视频流**：单帧图片的 fade 时间轴永远停在 t=0，配置了 delay>0 的淡入会让贴图**恒透明**（整段不可见，2026-09-16 实测踩坑）。渲染器已内置此修复。
- **重点字位置避让**：贴图默认贴右上（tr），该镜的重点字要用 `pos:[x,y]` 挪到左侧，否则大字压在产品卡上。DEFAULT_POS 只有 6 组会循环复用，关键字多于 6 个时务必逐个检查落点。
- **aecho 延时级数必须等于衰减级数**：`delays` 和 `decays` 数量不同会直接 Invalid argument（exit 234）。5 级渐隐的完整写法：`delays:"250|500|750|1000|1250", decays:"0.45|0.30|0.20|0.13|0.07"`（历史基线只记了 decays，漏了 delays，2026-09-16 补全）。
- `build_ad_video.py` 的 pchars 标点表已与 align_vo.py 对齐（含全角冒号/分号/破折号），config 文案含「：；——」不会再导致字数不匹配。
- 若字数不等（±1），把差额记到最后的句子上；差得多说明用户录音没有照文案念，需先和用户确认文稿。

## 素材
- 动漫分镜图：用 ImageGen 按分镜脚本生成（图生图垫参考图保持角色一致），提示词要求"NO text, NO watermark, 底部留字幕区"，16:9。
- **ImageGen 留字幕区的正确写法**：必须用 "empty space at bottom fifth of frame for subtitles"（第五分之一，弱表述）。写 "keep the bottom third clean and empty for subtitles" 会生成整块纯色带（模型理解成 letterbox）。若已出色带：色带在底部时可直接 `crop=1536:756:0:0,pad=1536:1024:0:268:black` 裁掉补黑（字幕区反正是黑的）。
- **ImageGen 并行调用会撞文件名**（时间戳精确到秒）：多张图必须串行生成，或每张用不同 output_dir，否则后一张覆盖前一张。
- 新图入库：去水印后存到 `<交付目录>/动漫分镜图/镜X_名称.png`，下次同品类文案直接复用。
- AI 图右下角有"AI生成"水印：统一 `delogo=x=1360:y=895:w=170:h=119`（1536x1024 图）去掉后再进剪辑。
- 产品视频段：先抽 0.2fps 拼图浏览全片挑段；记录好用的秒数段复用。
- 图转动态视频（可选升级）：VideoGen 工具图生视频，720P 约 25–50 积分/条、1080P 约 50–100 积分/条，5 秒/条，剪到语句时长；生成前必须告知用户积分消耗。

## 质检
- 渲染后抽帧检查（重点字时刻 n=秒*30）：`select='eq(n\,51)+...',scale=640:-1,tile=3x2`。
- 检查点：水印是否漏、字幕错位、重点字位置是否压脸/出画、产品段画面内容对口播。

## 用户已知偏好（快手带货/肌肉博士五黑）
- 口播增益：**vo_gain 1.27（+15%，当前基线）**，早期默认 1.1。
- 底部字幕：黑白简洁（白字黑边、单行、无阴影）。
- 重点字：说到该词才弹出（带弹跳+音效）、颜色每次不同、位置互相错开、人物镜头压在人物身后上方。
- BGM 用 sidechain 闪避（threshold=0.05 ratio=7），结尾 1s 淡出。
- 交付目录习惯：`/Volumes/陈灿会展/广告视频/<品类>/<产品>/2602/`。

## ✅ 当前默认定式（2026-09-16 诺特烟酰胺 v5，用户确认的工作流基线）

用户（科普型带货达人）确认：**以后给「文案 + 口播配音」直接按这个定式出片**，不需要逐项再确认：

- 输入：文案 + 口播音频；BGM 有就配（sidechain 闪避，threshold=0.05 ratio=7）；产品图/特效用户自己在剪映加。
- 分镜：每句一镜，静态 AI 分镜图（绿肌蛛网纹无脸猛男主 IP，垫定妆图保持一致），六段式结构（钩子→科普铺垫→避坑→选品标准→产品种草→价格+CTA）。
- 动效：轻 Ken Burns——推/拉 1.0↔1.14、摇镜 1.10 + 纵向居中（大幅推拉被用户批"人物出画、很割裂"）；开场 punch 保留画面（1.20→1.14 settle）但 `opening_punch_sfx: false` 不出重音。
- **甩入 + 唿哨**：`swing_shots` = `transition_whoosh` = [2,5,8,11,14,…]（每隔两个镜）。甩入 = 图片先放大约 8% 留旋转余量，再 `rotate=a='0.05*(1-min(t/0.55,1))*sin(13*t)'`（~2.8°，0.55s 衰减归正）；唿哨在 whoosh(唰)/whoosh2(嗖,升调)/whoosh3(咻,降调) 中**随机抽**，音量 whoosh 1.2，无回声无 highpass。
- **默认不带**：重点字弹出（keywords）、全屏大字卡（title_cards）、产品贴图（over）、其他任何音效——能力都在渲染器里，用户点名才加。
- 字幕：sub_size 78 白字黑边单行，`sub_highlights` 字内变色保留。
- 音量：vo_gain 1.46 / bgm_volume 0.24 / loudnorm I=-11.5 TP=-0.7；全片 mean 约 -13.8dB 正常。
- 交付：成片归档到 `/Volumes/陈灿会展/广告视频/<品类>/<产品>/2602/`，present_files 交付。

## 调音史与诊断法（历史参考）

- 演进：vo_gain 1.1→1.27→1.46；BGM 0.55→0.47→0.376→0.301→0.24；sub_size 60→70→78。
- 2026-09-15 四轮（旧"甜点区"，全特效时期）：whoosh 3.6 / impact 4.2 / ding 8.6 + 50Hz highpass + 五级渐隐回声。关键经验：**内建音效原始峰值差异极大**（impact -13.1 / whoosh -13.9 / ding -24.1 dBFS），同一倍数放大必然"一个闷死一个听不见"——必须先 volumedetect 量原始峰值再分别配平到相差 5dB 内。
- 削波防护靠末端 `alimiter=limit=0.95`；音效推过高会顶限制器发闷（"闷"的复现路径）。
- 2026-09-18 **音色池扩充 + 逐词分配**（用户："音效太单一"→"都保存，按词意搭配合适的"）：重点字音效不再全片统一。新增 7 款纯音音色（`kw_glass` 玻璃清响 / `kw_triple` 三连快叮 / `kw_charge` 能量蓄满 / `kw_thump` 双段冲击 / `kw_bloom` 四音绽放 / `kw_spring` 弹性弹入 / `kw_shine` 叮—咚收束），与原有 5 款共 **12 款**（全表见 SKILL.md）。**每条片按词意逐词分配、相邻不撞色**，语义映射固定：开场点题→`kw_boom`、能量/爆发→`kw_charge`、数据/纯度→`kw_glass`、数字/利益点→`kw_bloom`、价格/福利→`kw_triple`、踩坑/警告→`kw_thump`、术语→`kw_riser`、收尾 CTA→`kw_shine`。音色系数按听感微调（glass 0.72 防高频刺、thump 0.84 让位开场咚、charge 0.80 稍放推感），源文件峰值统一 -9dBFS。
  铁律：**新音色落盘 `assets/sfx/` 只完成一半，必须登记进 `build_ad_video.py` 的 `sfx_files` 白名单 + `DEFAULT_SFX_VOL`**，否则渲染器静默不加载。
  验收"只换音效、画面不动"：`ffmpeg -i 新.mp4 -i 旧.mp4 -lavfi "[0:v][1:v]ssim" -f null -`，逐帧 SSIM 应为 1.000000 (inf)。
- 诊断法：volumedetect 量各音源原始峰值 → 量成品纯口播段与特效落点的 mean/max 差值 → 抬升 2~3.5dB 为最佳听感区间。
- aecho 延时级数必须等于衰减级数（delays 与 decays 数量不等直接报错）。

