# -*- coding: utf-8 -*-
"""
带货口播成片渲染器：按配置把「口播音频 + 分镜素材(动漫图/产品视频段) + BGM + 音效」
渲染成 16:9 1080P 成片，字幕和重点字随语音逐字对齐。

用法:
  python3 build_ad_video.py config.json

config.json 结构见 assets/config.example.json 或本文件底部注释。
依赖: ffmpeg 二进制路径写在 config.ffmpeg 或环境变量 FFMPEG_BIN。
"""
import json, subprocess, os, sys, random, re, shutil

# ---------- 默认调色板（重点字颜色轮换, ASS &HBBGGRR）----------
PALETTE = ["&H0000FFFF", "&H00FFFF00", "&H000040FF", "&H0000D7FF", "&H0000FF00", "&H00FF00FF"]
# 人物镜头重点字默认位置（压在人物身后上方, 1920x1080）
DEFAULT_POS = [(1330, 270), (500, 290), (960, 260), (460, 320), (1430, 280), (960, 235)]
DW_SIDE_POS = (330, 330)   # ★超级重点字检测失败时的侧位兜底（左侧，不压画面正中人物）
DEFAULT_SFX_VOL = {"whoosh": 0.70, "impact": 0.95, "ding": 0.75,
                   "pop_in": 0.70, "pop_in2": 0.70, "float_in": 0.70,
                   "kw_pop": 0.80, "kw_ding": 0.75, "kw_click": 0.58,
                   "kw_riser": 0.72, "kw_boom": 0.88,
                   # 2026-09-18 扩充（全部纯音合成、峰值统一 -9dBFS，音量按听感陡缓微调）
                   "kw_glass": 0.72,   # 玻璃清响：高频干脆带短尾韵，压低防刺耳
                   "kw_triple": 0.76,  # 三连快叮：三音总能量大，略降
                   "kw_charge": 0.80,  # 能量蓄满：渐强推感，可稍放
                   "kw_thump": 0.84,   # 双段冲击：低频闷击，略低于 kw_boom 让位开场
                   "kw_bloom": 0.78,   # 四音绽放：琶音明亮
                   "kw_spring": 0.76,  # 弹性弹入：短促上滑
                   "kw_shine": 0.78,   # 叮—咚收束：高频短+低频长尾
                   # 2026-09-27 扩充：短促/收尾利落款（scripts/synth_kw_sfx_v3.py）
                   "kw_tick": 0.62,    # 极短高频嘀：高频最刺，压最低
                   "kw_snap": 0.74,    # 木质短哒：瞬态强，略压
                   "kw_pluck": 0.76,   # 明亮弹拨
                   "kw_bellup": 0.78,  # 上滑五度：强调用，可放
                   "kw_marker": 0.74,  # 圆润马林巴：中音柔和
                   "kw_drop": 0.76,    # 下滑 puu
                   "kw_spark": 0.70,   # 三连颗粒：三音总能量大，降一档
                   "timi": 0.85}       # TiMi 游戏启动音：人声主响，给到中上音量（2026-09-29）
# ---------- 重点字字幕预设库（颜色语义 × 动效 × 匹配音效）----------
# 颜色遵循抖音带货通用语义：金=利益点/价格, 蓝=数据/成分, 白=基础信息,
# 绿=原料/健康, 红=稀缺/踩坑, 紫=专业术语/IP概念词。
# ASS 颜色格式 &HBBGGRR。
KW_STYLE = {
    "pop":    {"color": "&H00279FEF", "sfx": "kw_pop",   "cn": "金色弹跳放大"},    # 利益点/价格/福利
    "rise":   {"color": "&H00DD8A37", "sfx": "kw_ding",  "cn": "蓝色上滑定住"},    # 数据/成分/规格
    "type":   {"color": "&H00FFFFFF", "sfx": "kw_click", "cn": "白色逐字打字机"},  # 基础信息/概念定义
    "mark":   {"color": "&H00043417", "sfx": "whooshC",  "cn": "绿色荧光笔划线"},  # 原料/健康/天然
    "pulse":  {"color": "&H004A4BE2", "sfx": "impact",   "cn": "红色闪动强调"},    # 稀缺/限时/踩坑
    "stroke": {"color": "&H00DD777F", "sfx": "kw_riser", "cn": "紫色描边转实心"},  # 专业术语/IP概念词
    # ★2026-09-24 超级重点字专用：重击音效 + 大幅弹出（字号由 kw_super_size 给；
    #   颜色**不固定白**——按 kw_super_colors 白/黄交替，见下方 keywords 循环）
    "hero":   {"color": "&H00FFFFFF", "sfx": "kw_boom",   "cn": "超级弹跳·重击"},
}
KW_MARK_BAR = "&H0059C497"   # 荧光笔底色（浅绿）


# ---------- 重点字音效去重 / 合并策略（2026-09-27，可配置 sfx_policy）----------
# 用户诉求：不要逐字配音效。① 两个重点字挨太近（默认 <1.5s）只留一个，留语义更重
# 或在前的；② 邻近已有转场/入场/开场动效（默认 ±0.8s）的重点字不发声；③ 整体密度
# 克制（默认 8s 窗口内最多 3 个），避免堆叠掩盖与听觉疲劳。
# 只掐声音、保留视觉（大字照常弹出），与既有 kw_gap_policy="visual" 口径一致。
def kw_weight(ev, brand=""):
    """语义权重：越大越"重"。显式 weight > super/hero > 品牌名 > 数字/单位 > 词长。"""
    kw = str(ev.get("kw", ""))
    w = 0.0
    if ev.get("weight") is not None:          # 手动指定一律优先
        w += float(ev["weight"])
    if ev.get("super") or str(ev.get("tier", "")).lower() in ("super", "hero"):
        w += 100.0
    if brand and brand in kw:
        w += 60.0
    if ev.get("brand"):
        w += 60.0
    if re.search(r"\d|[%‰]|mg|毫升|ml|千克|kg|克|瓶|袋|元|块|折|倍", kw):
        w += 30.0                              # 数字/单位＝硬信息，比形容词重
    w += min(len(kw), 6) * 2.0                 # 同档时略偏好信息量大的词
    return w


def apply_kw_sfx_policy(kw_events, cfg, timeline, others=None):
    """按 sfx_policy 对重点字音效做去重/合并/邻避/密度控制，原地打 mute 标记并返回。"""
    pol = cfg.get("sfx_policy")
    if pol is False or (isinstance(pol, str)
                        and pol.strip().lower() in ("false", "off", "none", "no")):
        return kw_events
    pol = pol if isinstance(pol, dict) else {}   # 未配置＝启用默认参数
    if not bool(pol.get("enabled", True)):
        return kw_events
    print(f"  重点字音效策略: 合并阈值 {float(pol.get('kw_dedup_gap', 1.5)):.2f}s ｜ "
          f"邻近窗口 {float(pol.get('kw_near_window', 0.8)):.2f}s ｜ "
          f"密度 {int(pol.get('kw_density_max', 3))} 个 / {float(pol.get('kw_density_window', 8.0)):.0f}s ｜ "
          f"保留 {pol.get('kw_keep', 'heaviest')}")
    dedup = float(pol.get("kw_dedup_gap", 1.5))         # 重点字互相合并阈值
    near = float(pol.get("kw_near_window", 0.8))        # 与已有动效的邻避窗口
    dwin = float(pol.get("kw_density_window", 8.0))     # 密度窗口
    dmax = int(pol.get("kw_density_max", 3))            # 窗口内上限
    keep = str(pol.get("kw_keep", "heaviest")).lower()  # heaviest | first | latest
    brand = str(cfg.get("brand", "") or "")
    others = list(others or [])

    # ---- 步骤 0：按权重排序做决策（时间并列时权重高者胜）----
    for e in kw_events:
        e["_w"] = kw_weight(e, brand)

    # ---- 步骤 1：邻避——附近已有转场/入场/开场动效，则重点字不发声 ----
    survivors = []
    for e in sorted(kw_events, key=lambda x: x["t0"]):
        hit = min(((abs(e["t0"] - t), tag) for t, tag in others), default=None)
        if hit and hit[0] < near:
            e["sfx"] = None
            print(f"  重点字去重: 静音「{e['kw']}」@{e['t0']:.2f}s"
                  f" —— 邻近 {hit[1]}（距 {hit[0]:.2f}s < {near:.2f}s），让位已有动效")
        else:
            survivors.append(e)

    # ---- 步骤 2：合并——按时间贪心，与「上一个已保留者」比间隔 ----
    # （不做链式整组吞并：那会把一串间隔 1s 的字并成一组只留 1 个，过于激进；
    #   贪心能做到"任意两个发声的字间隔 ≥ 阈值"，密度交由步骤 3 控制）
    kept = []
    tie = {"heaviest": "语义更重者", "first": "时间靠前者", "latest": "时间靠后者"}.get(keep, "语义更重者")
    for e in sorted(survivors, key=lambda x: x["t0"]):
        if not kept:
            kept.append(e)
            continue
        last = kept[-1]
        if e["t0"] - last["t0"] >= dedup:
            kept.append(e)
            continue
        # 阈值内冲突：按 keep 决定谁留下
        if keep == "first":
            win = last
        elif keep == "latest":
            win = e
        else:                                   # heaviest：权重高者胜，并列取在前
            win = e if e["_w"] > last["_w"] else last
        loser = e if win is last else last
        loser["sfx"] = None
        print(f"  重点字去重: 静音「{loser['kw']}」@{loser['t0']:.2f}s"
              f" —— 与「{win['kw']}」@{win['t0']:.2f}s 仅隔 "
              f"{abs(e['t0'] - last['t0']):.2f}s (<{dedup:.2f}s)，保留{tie}")
        if win is e:                            # 更重者替换上位，成为新的比较基准
            kept[-1] = e

    # ---- 步骤 3：密度——滑动窗口内超限的（权重最低的）让位 ----
    kept.sort(key=lambda x: x["t0"])
    for i, e in enumerate(kept):
        if i < dmax:
            continue
        recent = kept[max(0, i - dmax):i]       # 已在窗口内保留的
        if len(recent) >= dmax and (e["t0"] - recent[0]["t0"]) < dwin:
            e["sfx"] = None
            print(f"  重点字去重: 静音「{e['kw']}」@{e['t0']:.2f}s"
                  f" —— {dwin:.0f}s 窗口内已达上限 {dmax} 个（密度控制），避免音效堆叠")
    return kw_events
SFX_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, "assets", "sfx")
FONT_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                         os.pardir, "assets", "fonts"))
# ---------- 开场冲击包（opening_burst）----------
# 用户口径（2026-09-18 定稿）：**不要独立片头段**（旧的 hook 已废弃），而是在正片
# 开头几秒的画面本身上叠程序化动效 + 配套音效，让开头就抓人。
# ✅ **默认开启**：config 不写 opening_burst 也会自动从下面 6 款 preset 里**随机挑一款**；
#    想指定就写 {"preset":"lightning"}，想本条片不用就写 "opening_burst": false
#    （用户原话：「这几个开场默认开启使用，下回使用时随机使用一个……哪天我不想用了，
#      我会说先不用。」临时批量关闭可用环境变量 AD_BURST_OFF=1）。
# 关键约束：**不新增时长、不动任何时间轴**——镜头/字幕/口播/重点字一行都不用改，
# 冲击只是叠在正片最前面那 1 秒左右的画面上。
# 元素素材由 make_burst_assets.py 程序化生成（1920x1080 RGBA，0 积分），内容画在
# 画面中心且占比小，留给 zoompan 放大 1→3~5 倍（zoompan 的 z 不能小于 1）。
BURST_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          os.pardir, "assets", "burst"))
BURST_EL = {"rays": "rays.png",    # 放射线爆发
            "bolt": "bolt.png",    # 闪电分叉
            "spark": "spark.png",  # 粒子点云（粉末炸开）
            "shock": "shock.png",  # 同心冲击环
            "lines": "lines.png"}  # 斜向速度线
# 每层字段：el=元素, t0=窗口内起始秒, dur=持续秒, z=(起,止)缩放, rot=每秒旋转度数,
#          a=整体不透明度, pw=缩放缓动指数(越小起手越猛), blink=[(起,长)]只在这些窗口显示,
#          fade=淡出秒数(默认取 dur 的一半，最长 0.3)。窗口整体时长由顶层 dur 兜底。
# 顶层字段：dur=冲击窗口, flashes=[(起,长)]闪白, shake=抖动倍率(1.0≈±12px), sfx=[(音色,时刻)]
BURST_PRESETS = {
    # 能量爆发：最通用，放射线炸开 + 粒子扩散 + 冲击环，起手最猛
    # ⚠️ 亮度/时长实测取值（2026-09-18）：放射线 a>0.8 或 dur>0.8s 会把主体和字幕
    #    一起糊掉（实测 0.95s/0.95 把整段压在白光里）。冲击要「一下掠过」而不是「持续照亮」。
    "energy": {
        "dur": 0.74,
        "layers": [
            {"el": "shock", "t0": 0.00, "dur": 0.42, "z": (1.00, 4.30), "a": 0.48},
            {"el": "rays",  "t0": 0.00, "dur": 0.62, "z": (1.00, 3.60), "rot": 26,
             "a": 0.62, "pw": 0.50},
            {"el": "spark", "t0": 0.04, "dur": 0.55, "z": (1.00, 3.70), "rot": -14, "a": 0.58},
        ],
        "flashes": [(0.00, 0.06), (0.12, 0.03)],
        "shake": 1.5,
        "sfx": [("impact", 0.02), ("kw_charge", 0.10)],
    },
    # 闪电劈下：两次闪现 + 曝光骤亮 + 强抖，适合反常识/反差开场
    # ⚠️ 白闪压在元素之上，会把闪电那一瞬盖掉——所以这里 ① 白闪峰值降到 0.82、
    #    ② 白闪窗口压到 0.035s、③ 闪电闪现窗口比白闪长（白闪 0.04-0.075 / 闪电 0.04-0.19）。
    "lightning": {
        "dur": 0.78,
        "layers": [
            {"el": "bolt",  "t0": 0.00, "dur": 0.46, "z": (1.00, 1.18), "a": 1.0,
             "blink": [(0.00, 0.15), (0.19, 0.14)], "fade": 0.05},
            {"el": "shock", "t0": 0.16, "dur": 0.46, "z": (1.00, 4.00), "a": 0.50},
            {"el": "spark", "t0": 0.16, "dur": 0.50, "z": (1.00, 3.20), "rot": 10, "a": 0.62},
        ],
        "flashes": [(0.00, 0.035), (0.19, 0.035)],
        "flash_a": 0.82,
        "shake": 2.2,
        "sfx": [("kw_thump", 0.01), ("impact", 0.19)],
    },
    # 粉末炸开：粒子为主，适合补剂/粉末/冲泡类产品特写开场
    # ⚠️ 实测（2026-09-18 预览复核）：粒子层 a=1.0 + dur=0.85 会把主角整段埋在云里，
    #    0.16~0.7s 完全看不到人。云状元素必须半透明、且 0.6s 内散掉——收到 a=0.55/dur=0.62。
    "powder": {
        "dur": 0.72,
        "layers": [
            {"el": "spark", "t0": 0.00, "dur": 0.62, "z": (1.00, 3.20), "rot": 22,
             "a": 0.55, "pw": 1.10},
            {"el": "rays",  "t0": 0.04, "dur": 0.45, "z": (1.00, 2.40), "rot": -20, "a": 0.42},
        ],
        "flashes": [(0.02, 0.06)],
        "shake": 1.2,
        "sfx": [("kw_thump", 0.02), ("whoosh", 0.08)],
    },
    # 铁片砸地：重下压 + 强抖 + 冲击环，适合强结论/警告句开场
    "impact": {
        "dur": 0.64,
        "layers": [
            {"el": "shock", "t0": 0.00, "dur": 0.55, "z": (1.00, 4.20), "a": 0.70},
            {"el": "spark", "t0": 0.02, "dur": 0.38, "z": (1.00, 2.60), "a": 0.50},
        ],
        "flashes": [(0.00, 0.05)],
        "shake": 2.6,
        "sfx": [("impact", 0.01)],
    },
    # 心跳泵感：三次脉冲冲击环，节奏型，适合痛点共鸣/反复踩坑开场
    "pulse": {
        "dur": 1.25,
        "layers": [
            {"el": "shock", "t0": 0.02, "dur": 1.18, "z": (1.00, 3.40), "a": 0.75,
             "blink": [(0.00, 0.24), (0.32, 0.24), (0.64, 0.24)], "fade": 0.06},
            {"el": "spark", "t0": 0.02, "dur": 0.70, "z": (1.00, 2.20), "a": 0.40},
        ],
        "flashes": [(0.00, 0.05), (0.32, 0.04), (0.64, 0.04)],
        "shake": 0.8,
        "sfx": [("kw_boom", 0.01), ("kw_thump", 0.33), ("kw_thump", 0.65)],
    },
    # 数字冲刺：速度线扫过后中心爆发，适合数字/数据开场（数字本身另需字母轨）
    "countdown": {
        "dur": 0.95,
        "layers": [
            {"el": "lines", "t0": 0.00, "dur": 0.62, "z": (1.00, 1.30), "rot": -8, "a": 0.85},
            {"el": "rays",  "t0": 0.42, "dur": 0.50, "z": (1.00, 2.80), "rot": 30, "a": 0.75},
        ],
        "flashes": [(0.42, 0.06)],
        "shake": 1.1,
        "sfx": [("whoosh", 0.02), ("impact", 0.42)],
    },
}
# ---------- BGM：技能内置默认曲（用户 2026-09-18 指定）----------
# config 不写 bgm 就自动用它；写 "none"/false 可单条关闭。
# 该曲入库时已用 prep_bgm.py 处理过：动态从 18.8dB 拉平到 ~5dB（原曲前奏 -17.8dB、
# 入歌后 -9.4dB，直接混音会盖住配音），峰值留到 -7.5dB，整体约 -20 LUFS。
# 所以渲染时只需一个很小的 bgm_volume；换未处理过的 BGM 时开 bgm_auto_gain 自动配平。
BGM_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        os.pardir, "assets", "bgm"))
# ★2026-09-23 用户拍板：BGM 有两首，没特殊要求就**随机选一首**（不要再固定同一首）。
#   assets/bgm/ 下所有 bgm_* 音频都进池；新曲先用 scripts/prep_bgm.py 拉平动态再丢进来。
#   当前池：bgm_我爱吃派大星（原默认曲，2026-09-18 入库）/ bgm_吴崽崽（2026-09-23 入库）。
# ★2026-10-04 团队共享改造：BGM 打包时压成 m4a（AAC 128k，31MB→2.9MB），故默认曲按
#   「有 m4a 用 m4a，否则回落 wav」解析，避免共享包里的曲名对不上导致 BGM 环节静默失效。
BGM_EXT = (".wav", ".mp3", ".m4a", ".flac", ".aac")


def _default_bgm():
    """默认 BGM：优先 m4a（团队共享包形态），否则 wav（单机原形态）。"""
    base = "bgm_我爱吃派大星"
    for ext in (".m4a", ".wav", ".mp3"):
        p = os.path.join(BGM_DIR, base + ext)
        if os.path.exists(p):
            return p
    return os.path.join(BGM_DIR, base + ".wav")


DEFAULT_BGM = _default_bgm()


def bgm_pool():
    """返回 BGM 候选池（bgm_* 优先；池空则回退 DEFAULT_BGM）。"""
    pool = []
    if os.path.isdir(BGM_DIR):
        for n in sorted(os.listdir(BGM_DIR)):
            low = n.lower()
            if low.startswith("bgm_") and low.endswith(BGM_EXT):
                pool.append(os.path.join(BGM_DIR, n))
    if not pool and os.path.exists(DEFAULT_BGM):
        pool = [DEFAULT_BGM]
    return pool


def pick_bgm(cfg_bgm=None, pick=None):
    """选 BGM：config 显式写路径 > 随机（避免与上次同曲）> 回退默认。"""
    if cfg_bgm:
        return cfg_bgm
    pool = bgm_pool()
    if len(pool) <= 1:
        return pool[0] if pool else None
    mode = str(pick or "random").lower()
    if mode in ("order", "rotate"):
        st_file = os.path.join(BGM_DIR, "_bgm_rotate.json")
        idx = 0
        try:
            idx = (int(json.load(open(st_file, encoding="utf-8")).get("idx", -1)) + 1) % len(pool)
        except Exception:
            idx = 0
        try:
            json.dump({"idx": idx}, open(st_file, "w", encoding="utf-8"))
        except Exception:
            pass
        return pool[idx]
    # random：若与上次同曲且池里有备选，就重掷一次（避免连着两条片同一首歌）
    last = None
    st_file = os.path.join(BGM_DIR, "_bgm_last.json")
    try:
        last = json.load(open(st_file, encoding="utf-8")).get("last")
    except Exception:
        pass
    choice = random.choice(pool)
    if last and os.path.basename(choice) == last and len(pool) > 1:
        choice = random.choice([p for p in pool if os.path.basename(p) != last])
    try:
        json.dump({"last": os.path.basename(choice)}, open(st_file, "w", encoding="utf-8"))
    except Exception:
        pass
    return choice


def ensure_bgm_len(path, need, ff=None):
    """BGM 短于成片时长时循环补足（新曲常比默认曲短），返回可用路径。"""
    ff = ff or os.environ.get("FFMPEG_BIN") or "/Users/mac/.local/bin/ffmpeg"
    try:
        r = subprocess.run([ff, "-hide_banner", "-i", path, "-f", "null", "-"],
                           capture_output=True, text=True, timeout=60)
        m = re.search(r"time=(\d+):(\d+):(\d+\.\d+)", r.stderr)
        if not m:
            return path
        dur = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    except Exception:
        return path
    if dur >= need - 0.2:
        return path
    reps = int(need / max(dur, 0.1)) + 2
    lst = os.path.join(BGM_DIR, "_loop_list.txt")
    with open(lst, "w", encoding="utf-8") as f:
        for _ in range(reps):
            f.write(f"file '{path}'\n")
    # ★2026-10-04：循环补足的输出容器跟随源格式。共享包里 BGM 是 m4a，
    #   若固定输出 .wav 而用 `-c copy`，容器不匹配会失败 → 静默回落到短曲（片尾静音）。
    ext = os.path.splitext(path)[1].lower() or ".wav"
    out = os.path.join(BGM_DIR, f"_bgm_looped{ext}")
    r = subprocess.run([ff, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-t", f"{need + 2:.2f}", "-c", "copy", out],
                       capture_output=True, text=True, timeout=120)
    if r.returncode == 0 and os.path.exists(out):
        print(f"  BGM 时长 {dur:.1f}s < 成片 {need:.1f}s → 已循环补足")
        return out
    return path
DEFAULT_BGM_VOL = 0.30       # 归一化过的 BGM 的默认音量系数
BGM_OFFSET_DB = 10.0         # 自动配平目标：BGM 比口播低 10dB（用户 2026-09-18 拍板：16dB 太小）
# 闪避参数：口播出声时 BGM 再让一档，句间自动回来。
# 别设太狠——ratio 8 / threshold 0.03 这种会让人声不断时 BGM 被压掉 10dB 以上，等于听不到音乐。
BGM_DUCK = {"threshold": 0.05, "ratio": 5, "attack": 25, "release": 380}

# ---------- 整体响度（2026-09-18 用户要求"配音和 BGM 一起提 10"） ----------
# 关键认知：单独调 vo_gain / bgm_volume 是**无效**的——链尾的 loudnorm 会把整体响度拉回目标，
# 你加多少它减多少（实测 vo_gain 1.46 与 14.6 过完 loudnorm 后成片电平完全一样）。
# "整体提 10dB" 也做不到：本条链路峰值已顶到 0dBTP，实测目标超过 MASTER_LUFS_MAX 后
# 只增加挤压、不再增加响度（-11.5→-10.2 / -8.5→-9.8 / -7.5→-9.6 / -5.5→-9.7 LUFS 收敛）。
# 所以这里做的是：把 loudnorm 的目标本身顶到实测天花板，这是物理上能给到的全部（约 +2dB）。
MASTER_LUFS_MAX = -8.5        # 全片响度实测天花板（再往上只有挤压，没有增益）
DEFAULT_LOUDNESS_BOOST_DB = 3.0   # 基础 -11.5 + 3.0 = -8.5，正好顶到天花板

# ---------- 末级真峰值保护（2026-09-30 新增） ----------
# 踩坑记录：loudnorm 的单遍（dynamic）模式**不严格保证** True Peak ≤ TP 目标。
# 实测同一条链路、同一套参数，V1 成片 -0.3 dBTP（刚好贴线），换了一首 BGM 的
# V2 直接冲到 +0.4 dBTP（>0dBFS ⇒ 削波失真）。
# 根因：链尾只有 loudnorm，它优先满足 I（响度），TP 只是"尽力"。
# 修法：loudnorm 之后**再补一级 lookahead 限幅器**兜底。
#   ★ level=0 是关键——alimiter 的 level 参数默认 true（auto level），
#     会把输出重新拉回满刻度，等于没限。注意必须写 `level=0`：
#     写成 `level=disabled` 不会被 ffmpeg 识别（不报错但也不生效，实测成片真峰值
#     反而冲到 +0.6 dBTP）。用 `level=0` 实测 limit=0.5 输出 max_volume=-6.0dB ✅
#   limit=0.90 线性 ≈ -0.92 dBFS，给真峰值插值留余量，实测成片落在 -0.5 左右。
# 可用 config 覆盖：{"peak_guard": 0.9} 或 {"peak_guard": false} 关闭。
DEFAULT_PEAK_GUARD = 0.90

# ---------- 封面帧（2026-09-18 用户拍板：以后每条片都用这一版贴在开头） ----------
# 账号固定封面图（精确 3:4 竖图，1152x1536）。config 不写 cover 也自动带上，
# 让抖音抓首帧做封面时与账号封面风格统一。想临时关掉写 "cover": false，
# 想换图写 "cover": {"src": "...", ...}。
COVER_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                          os.pardir, "assets", "cover"))
DEFAULT_COVER = os.path.join(COVER_DIR, "default_cover_3x4.jpg")
DEFAULT_COVER_DUR = 0.1      # 3 帧；1 帧平台二压后容易抓不到
# 重点字动效字体：默认「新青年体」（用户指定），家族名取自 fonts/新青年体.ttf 的 name 表。
# 用 fontsdir 随技能加载，无需把字体装进系统；系统字体(PingFang)仍由 fontconfig 默认目录解析。
KW_FONT_DEFAULT = "文悦新青年体 (非商用) W8"
# 空白区检测网格：1920x1080 缩放到 192x108（1 格 = 10px），纯标准库即可算
GRID_W, GRID_H = 192, 108

def sh(cmd, **kw):
    # ★2026-10-05 补：ffmpeg 失败时把 stderr 尾部打出来。
    # 原版只抛 CalledProcessError，而 cmd 里 -v error 让真实原因（滤镜语法/编码器/尺寸）
    # 全部被吞掉，日志里只剩一长串参数列表，排查时只能靠猜（本次实撞：查了 20+ 分钟）。
    if "-v" in cmd:
        i = cmd.index("-v")
        if i + 1 < len(cmd) and cmd[i + 1] == "error":
            cmd = list(cmd)
            cmd[i + 1] = "warning"
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        print("[ffmpeg 失败] 返回码", r.returncode, file=sys.stderr)
        for line in err[-25:]:
            print("   ", line, file=sys.stderr)
        r.check_returncode()
    return r

def audio_mean_db(FF, path, af="", limit=None):
    """volumedetect 测素材平均电平(dB)，用于 BGM 自动配平。失败返回 None。"""
    cmd = [FF, "-hide_banner"]
    if limit:
        cmd += ["-t", f"{float(limit):.2f}"]
    cmd += ["-i", path, "-vn"]
    cmd += ["-af", (f"{af},volumedetect" if af else "volumedetect")]
    cmd += ["-f", "null", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception:
        return None
    m = re.search(r"mean_volume:\s*(-?[\d.]+) dB", r.stderr)
    return float(m.group(1)) if m else None

def ts(sec):
    h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    return f"{h}:{m:02d}:{s:05.2f}"

def pchars(t):
    return [c for c in t if c not in "，。！？、：；—…·～「」『』《》\"\"''（）()" and not c.isspace()]

def _ffprobe_bin(FF):
    """定位 ffprobe：优先与 FF 同目录，其次 PATH。
    ★ 必须用 ffprobe 而不是 ffmpeg —— `-select_streams` / `-show_entries`
      是 ffprobe 的选项，ffmpeg 会报「Unrecognized option」退出 8，
      于是「有音轨的素材」被误判成「没音轨」，自带混音接不进去。"""
    probe = os.path.join(os.path.dirname(FF), "ffprobe")
    if os.path.exists(probe):
        return probe
    return shutil.which("ffprobe")

def _probe_has_audio(FF, path):
    """素材是否含**有效**音频流。prod 镜写 own_audio 前先探一次，
    避免「只有画面」的素材被当自带混音接入 → 该区间反而没了人声。
    ★2026-10-09 加静音检测：Remotion 渲的卷播带 -91dB 静音轨（aac 流存在），
    只看 codec_type 会误判「自带混音」→ 屏蔽全局 vo → 开头轮播没人声（sA 实测翻车）。
    峰值 < -60dB 视为无声，回落到全局 vo。"""
    probe = _ffprobe_bin(FF)
    if not probe:
        return True                      # 找不到 ffprobe 时不拦截，让 ffmpeg 自己报错
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-select_streams", "a", "-show_entries",
             "stream=codec_type", "-of", "csv=p=0", path],
            capture_output=True, timeout=60)
        if not (r.returncode == 0 and "audio" in r.stdout.decode("utf-8", "ignore")):
            return False
        # 静音检测：抽音轨量峰值
        v = subprocess.run(
            [FF, "-v", "info", "-i", path, "-map", "0:a", "-t", "10",
             "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, timeout=120)
        m = re.search(rb"max_volume:\s*(-?[\d.]+)\s*dB", v.stderr)
        if m:
            if float(m.group(1)) < -60.0:
                print(f"  [own_audio] {os.path.basename(path)} 音轨峰值 "
                      f"{m.group(1).decode()}dB → 判定静音轨，忽略")
                return False
        return True
    except Exception:
        return False

def _probe_dur(FF, path):
    """素材时长（秒）。取不到返回 None。
    own_audio 时用来判断「素材比该镜短」——短了就只能取满素材，
    多出的画面时长靠末帧静止撑住（画面本就该定住），音轨不能跟着拖长。"""
    probe = _ffprobe_bin(FF)
    if not probe:
        return None
    try:
        r = subprocess.run(
            [probe, "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path],
            capture_output=True, timeout=60)
        if r.returncode != 0:
            return None
        return float(r.stdout.decode("utf-8", "ignore").strip())
    except Exception:
        return None

def frame_gray(FF, path, is_video, ss, crop_y, W=GRID_W, H=GRID_H):
    """把素材按渲染时的裁切方式(cover+顶部对齐)缩成 WxH 灰度，返回 bytes。
    纯 ffmpeg + 标准库，不依赖 numpy/PIL。"""
    vf = (f"scale=2880:1620:force_original_aspect_ratio=increase,"
          f"crop=2880:1620:0:{int(crop_y)},scale={W}:{H}")
    cmd = [FF, "-v", "error"]
    if is_video:
        cmd += ["-ss", f"{max(0.0, float(ss)):.2f}", "-i", path, "-frames:v", "1"]
    else:
        cmd += ["-i", path]
    cmd += ["-vf", vf, "-pix_fmt", "gray", "-f", "rawvideo", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < W * H:
        return None
    return r.stdout


def energy_map(buf, W=GRID_W, H=GRID_H):
    """梯度能量图：|dx|+|dy|。值越低=越平=越"空"。"""
    e = [[0.0] * W for _ in range(H)]
    for y in range(1, H - 1):
        r0 = buf[(y - 1) * W:y * W]
        r1 = buf[y * W:(y + 1) * W]
        r2 = buf[(y + 1) * W:(y + 2) * W]
        ey = e[y]
        for x in range(1, W - 1):
            ey[x] = abs(r1[x + 1] - r1[x - 1]) + abs(r2[x] - r0[x])
    return e


def integral(e, W=GRID_W, H=GRID_H):
    I = [[0.0] * (W + 1) for _ in range(H + 1)]
    for y in range(H):
        row, nxt, er = I[y], I[y + 1], e[y]
        s = 0.0
        for x in range(W):
            s += er[x]
            nxt[x + 1] = row[x + 1] + s
    return I


def pick_blank_spot(e, bw, bh, occupied, W=GRID_W, H=GRID_H, margin=3, y_top=4, y_bot=66,
                    x_bands=None):
    """在能量图上滑窗找"最空"的 bw x bh 区块，返回 (x0,y0,平均能量) 或 None。
    y_top/y_bot 限制纵向范围(网格行)，默认避开顶部与底部字幕区。
    x_bands: 可选 [(0.05,0.30),(0.70,0.95)] —— 只允许**框中心**落在这些横向比例带内，
             超级重点字用它在**左右两侧**落位，永远不压画面正中的人物主体。"""
    I = integral(e, W, H)
    tot = I[H][W]
    avg = tot / float(W * H) or 1.0
    y_hi = min(y_bot, H - bh - 1)
    if x_bands:
        xs = []
        for (_a, _b) in x_bands:
            # 带语义 = 框**中心**允许区间 → 换算成框左边缘范围
            xa = max(margin, int(round(_a * W - bw / 2.0)))
            xb = min(W - bw - margin, int(round(_b * W - bw / 2.0)))
            if xb >= xa:
                xs += list(range(xa, xb + 1))
        if not xs:
            xs = list(range(margin, W - bw - margin))
    else:
        xs = list(range(margin, W - bw - margin))
    best = None
    for y0 in range(y_top, max(y_top + 1, y_hi)):
        for x0 in xs:
            hit = False
            for (ox, oy, ow, oh) in occupied:
                if x0 < ox + ow and ox < x0 + bw and y0 < oy + oh and oy < y0 + bh:
                    hit = True
                    break
            if hit:
                continue
            s = I[y0 + bh][x0 + bw] - I[y0][x0 + bw] - I[y0 + bh][x0] + I[y0][x0]
            # 轻微偏好靠近画面水平中心，避免词被甩到边角（能量仍是主导项）
            bias = abs((x0 + bw / 2.0) - W / 2.0) / (W / 2.0)
            score = s + bias * avg * bw * bh * 0.30
            if best is None or score < best[0]:
                best = (score, x0, y0, s / float(bw * bh))
    return None if best is None else (best[1], best[2], best[3])


def frame_rgb(FF, path, is_video, ss, crop_y, W=GRID_W, H=GRID_H):
    """同 frame_gray，但输出 rgb24 原始字节 —— 识别「绿色 IP 主体」用（灰度判不出颜色）。"""
    vf = (f"scale=2880:1620:force_original_aspect_ratio=increase,"
          f"crop=2880:1620:0:{int(crop_y)},scale={W}:{H}")
    cmd = [FF, "-v", "error"]
    if is_video:
        cmd += ["-ss", f"{max(0.0, float(ss)):.2f}", "-i", path, "-frames:v", "1"]
    else:
        cmd += ["-i", path]
    cmd += ["-vf", vf, "-pix_fmt", "rgb24", "-f", "rawvideo", "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=90)
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < W * H * 3:
        return None
    return r.stdout


def person_masks(buf, W=GRID_W, H=GRID_H):
    """从 RGB 网格找绿色 IP 主体 → (body, head) 两张布尔表。
    head = 画面上部 45% 的绿色（头套/脸），贴图落位时权重最大——绝不能让卡片压到脸。
    绿像素太少(<0.8%)判定该镜没有绿色主体，返回 (None, None)。"""
    body = [[False] * W for _ in range(H)]
    head = [[False] * W for _ in range(H)]
    n = 0
    for y in range(H):
        base = y * W * 3
        for x in range(W):
            i = base + x * 3
            r, g, b = buf[i], buf[i + 1], buf[i + 2]
            if g > r + 15 and g > b + 15 and g > 60:
                body[y][x] = True
                if y < H * 0.45:
                    head[y][x] = True
                n += 1
    if n < W * H * 0.008:
        return None, None
    return dilate(body, W, H), dilate(head, W, H)


def dilate(m, W=GRID_W, H=GRID_H, r=2):
    """布尔表膨胀 r 格（1 格=10px）——给主体留出安全边，避免贴图压到轮廓。"""
    out = [[False] * W for _ in range(H)]
    for y in range(H):
        row = m[y]
        for x in range(W):
            if not row[x]:
                continue
            for dy in range(-r, r + 1):
                yy = y + dy
                if yy < 0 or yy >= H:
                    continue
                orow = out[yy]
                for dx in range(-r, r + 1):
                    xx = x + dx
                    if 0 <= xx < W:
                        orow[xx] = True
    return out


def integral_mask(m, W=GRID_W, H=GRID_H):
    """布尔表的积分图（算区块内主体像素数，O(1) 查询）。"""
    I = [[0] * (W + 1) for _ in range(H + 1)]
    for y in range(H):
        row, nxt, mr = I[y], I[y + 1], m[y]
        s = 0
        for x in range(W):
            if mr[x]:
                s += 1
            nxt[x + 1] = row[x + 1] + s
    return I


def _sum4(I, x0, y0, w, h):
    return I[y0 + h][x0 + w] - I[y0][x0 + w] - I[y0 + h][x0] + I[y0][x0]


def place_card(e, body, head, bw, bh, occupied, W=GRID_W, H=GRID_H,
               y_top=2, y_bot=88, step=2):
    """给产品详情卡片找落点：**贴着主体旁边、且不压人物**优先，其次才是画面最空。
    评分 = 区块能量(越空越低) + 主体重叠惩罚 + 头部重叠重罚。
    y_bot 是卡片"底边"的网格行上限（用来避开底部字幕区）。返回 (score,x0,y0,能量,体重叠,头重叠)。"""
    if e is None:
        return None
    I = integral(e, W, H)
    Ib = integral_mask(body, W, H) if body else None
    Ih = integral_mask(head, W, H) if head else None
    avg = (I[H][W] / float(W * H)) or 1.0
    y_hi = min(y_bot - bh, H - bh - 1)
    best = None
    for y0 in range(y_top, max(y_top + 1, y_hi + 1), step):
        for x0 in range(2, max(3, W - bw - 1), step):
            hit = False
            for (ox, oy, ow, oh) in occupied:
                if x0 < ox + ow and ox < x0 + bw and y0 < oy + oh and oy < y0 + bh:
                    hit = True
                    break
            if hit:
                continue
            s = _sum4(I, x0, y0, bw, bh)
            ob = _sum4(Ib, x0, y0, bw, bh) if Ib else 0
            oh_ = _sum4(Ih, x0, y0, bw, bh) if Ih else 0
            score = s + avg * bw * bh * (0.10 * ob + 1.20 * oh_)
            if best is None or score < best[0]:
                best = (score, x0, y0, s / float(bw * bh), ob, oh_)
    return best


def kw_box_px(text, size):
    """估算重点字在 1920x1080 下的文字包围盒（中文按 1 字宽，英数按 0.56）"""
    n = 0.0
    for c in text:
        n += 0.56 if (c.isascii() and (c.isalnum() or c in "%.+-")) else 1.0
    w = n * size * 1.06 + 80
    h = size * 1.60
    return int(round(w)), int(round(h))


def _cjk_units(text, protect=None):
    """把文本切成可断单元：关键词（protect）保持整块，普通文本按标点切分。"""
    protect = sorted(protect or [], key=len, reverse=True)
    # 1) 先按关键词把文本切成 token（关键词整体归一块）
    tokens = []
    i = 0
    while i < len(text):
        hit = None
        for kw in protect:
            if text.startswith(kw, i):
                hit = kw
                break
        if hit:
            # 关键词必须作为独立 "k" 单元，才能被下游"不切断"逻辑保护。
            # （曾经误写成 "t"，导致 protect 失效、关键词被硬切，如「99.99%」被拆成「99.」+「99%」）
            tokens.append(("k", hit))
            i += len(hit)
        else:
            ch = text[i]
            if tokens and tokens[-1][0] == "t":
                tokens[-1] = ("t", tokens[-1][1] + ch)
            else:
                tokens.append(("t", ch))
            i += 1
    # 2) 把普通文本块按标点切成可断单元（关键词保持整块）
    PUNCT = "，。、！？；：,.!?;: \n\t"
    units = []
    for typ, val in tokens:
        if typ == "k":
            units.append(val)
        else:
            buf = ""
            for idx, ch in enumerate(val):
                buf += ch
                # ★2026-09-24 破折号「——」当作一个整体断点：只在整串的最后一个「—」后断，
                #   否则会把「颜值——今天」切成「颜值—」+「—今天」，在破折号中间裂开。
                if ch == "—":
                    if idx + 1 < len(val) and val[idx + 1] == "—":
                        continue
                    units.append(buf)
                    buf = ""
                elif ch in PUNCT:
                    units.append(buf)
                    buf = ""
            if buf:
                units.append(buf)
    return units


def cjk_wrap_lines(text, per_line, protect=None):
    """按可见字符数把长句切成多行（返回行列表，不拼接）。
    - protect: 关键词列表（如「肌酸」「99.99%」），作为不可分割整体，绝不被切断；
    - 优先在标点（，。、！？；：等）后断行，断句更自然；
    - 单块超过 per_line 时按字符硬切兜底。
    """
    if per_line <= 0:
        return [text] if text else []
    units = _cjk_units(text, protect)
    PUNCT_END = tuple("，。、！？；：,.!?;: \n\t—")
    # 3) 贪心凑行；断行时若行尾不是标点，回退到本行内最近一个标点边界，
    #    避免把「……基础款｜入手？」这类完整短语从中间切开、还甩出 2~3 字的孤行。
    lines = []
    cur = ""
    buf = []                       # 当前行已收的单元
    for u in units:
        while len(u) > per_line:   # 单元本身超长：先落掉当前行再硬切
            if cur:
                lines.append(cur)
                cur, buf = "", []
            lines.append(u[:per_line])
            u = u[per_line:]
        if not cur:
            cur, buf = u, [u]
        elif len(cur) + len(u) <= per_line:
            cur += u
            buf.append(u)
        else:
            cut = None
            if cur[-1] not in PUNCT_END:          # 行尾不在标点 → 尝试回退
                for j in range(len(buf) - 1, -1, -1):
                    if buf[j][-1] in PUNCT_END:
                        head = "".join(buf[:j + 1])
                        tail = "".join(buf[j + 1:]) + u
                        if head and len(tail) <= per_line:
                            cut = (head, tail)
                        break
            if cut:
                lines.append(cut[0])
                cur, buf = cut[1], [cut[1]]
            else:
                lines.append(cur)
                cur, buf = u, [u]
    if cur:
        lines.append(cur)
    # ★2026-09-23 孤行修复（用户反馈：20 字句被硬切成 19+1，末行只剩一个「腿」很难看）：
    #   折成 2 行、且末行不足 3 字时，把两行按字符重新均衡切分（各占一半）。
    #   注意 _cjk_units 会把「无标点整句」合成单一单元，所以这里只在字符层面切；
    #   断点用 protect 关键词做保护——绝不允许落在关键词内部，否则原样返回。
    #   仅当 2 行且末行＜3 字时触发，其余句子完全不受影响。
    if len(lines) == 2 and len(lines[1]) < 3:
        joined = lines[0] + lines[1]
        total, target = len(joined), max(1, len(joined) // 2)
        _spans = []
        for _kw in sorted(set(protect or []), key=len, reverse=True):
            _st = joined.find(_kw)
            while _st != -1:
                _spans.append((_st, _st + len(_kw)))
                _st = joined.find(_kw, _st + 1)
        _bad = lambda k: any(a < k < b for a, b in _spans)   # 断点落在关键词内部
        _cand = None
        for _d in range(len(joined)):
            for _k in (target - _d, target + _d):
                if (1 <= _k <= total - 1 and _k <= per_line and total - _k <= per_line
                        and not _bad(_k)):
                    _cand = _k
                    break
            if _cand is not None:
                break
        if _cand is not None and abs(_cand - target) <= 3:   # 只在「接近对半」时才动
            lines = [joined[:_cand], joined[_cand:]]
    return lines


def cjk_wrap(text, per_line, protect=None):
    """cjk_wrap_lines 的拼接版：多行用 ASS 换行符 \\N 连接。"""
    return "\\N".join(cjk_wrap_lines(text, per_line, protect))


def main():
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    FF = cfg.get("ffmpeg") or os.environ.get("FFMPEG_BIN") or "/Users/mac/.local/bin/ffmpeg"
    FPS = cfg.get("fps", 30)
    VO_GAIN = cfg.get("vo_gain", 1.0)          # 口播增益（进混音前）。
    # 注意：它**不改变成片响度**——链尾 loudnorm 会把整体拉回目标，加多少减多少。
    # 想让成片整体更响，用 loudness_boost_db（见文件头常量说明）。
    IMG_KW_POS = cfg.get("keyword_positions")  # 可选: 自定义重点字坐标列表
    # ---------- 运动风格（零积分观感升级，全部可关）----------
    # ease    : linear | out(三次缓出) | inout(smoothstep)
    # breath  : 呼吸幅度，缩放单位，0.004~0.010 为宜，0=关
    # shake   : 手持微晃强度倍数，1.0 为默认(约 6px@1080p)，0=关
    # diagonal: 斜向推拉系数 0~1，0=纯中心推拉
    # arc     : 弧线摇镜起伏，占可移动范围比例，0=直摇
    # grain   : 胶片颗粒强度 0=关，6 为轻，10 偏重
    # vignette: 暗角 true/false
    # z_min   : 推拉最小缩放，必须 >1 才留得出抖动余量（1.0 时无法位移）
    # z_range : 推拉幅度
    ms = cfg.get("motion_style", {})
    EASE   = ms.get("ease", "out")
    BREATH = float(ms.get("breath", 0.006))
    SHAKE  = float(ms.get("shake", 1.0))
    DIAG   = float(ms.get("diagonal", 0.7))
    ARC    = float(ms.get("arc", 0.06))
    GRAIN  = int(ms.get("grain", 6))
    VIGN   = ms.get("vignette", True)
    ZMIN   = float(ms.get("z_min", 1.04))
    ZRANGE = float(ms.get("z_range", 0.12))
    ZPAN   = float(ms.get("z_pan", 1.10))
    DIRS   = [(1, 1), (-1, 1), (1, -1), (-1, -1)]   # 斜向/弧线方向，按镜号轮换
    ITENS  = float(ms.get("intensity", 1.0))        # 全局幅度系数：嫌大调 0.6~0.8，想更活调 1.2
    DIAGC  = float(ms.get("diagonal", 1.0))         # 斜向/弧线总系数（0=关，1=按景别档位，>1 加强）
    ARCC   = float(ms.get("arc", 1.0))

    # 按景别分档：全套动效都保留（缓动/呼吸/微晃/斜向/弧线/颗粒/暗角），
    # 只把「会让主体溢出画面」的推拉移幅度按人物占比收窄，而非整档关掉。
    # pan   : 摇镜可平移范围系数（人物占比越大，容许平移越少）
    # no_zoom_in: 该档禁止「推近」（推近会放大主体导致切边），自动改为拉远
    LEVELS = {
        "wide":  dict(zmin=1.03, zrange=0.090, diag=0.60, arc=0.050, shake=1.00, tilt=0.0000,
                      zpan=1.10, pan=1.00, breath=1.3, no_zoom_in=False),
        "mid":   dict(zmin=1.02, zrange=0.050, diag=0.36, arc=0.028, shake=1.15, tilt=0.0038,
                      zpan=1.06, pan=0.55, breath=2.0, no_zoom_in=False),
        # close 档：推拉位移压到最小(防溢出)，改用「不切主体」的动效做主要动感——
        # 呼吸缩放(2%)+手持微晃(≈10px)+倾斜摇摆(0.43°)都加大，让近景明显"活着"
        "close": dict(zmin=1.04, zrange=0.030, diag=0.24, arc=0.016, shake=1.60, tilt=0.0075,
                      zpan=1.03, pan=0.30, breath=3.4, no_zoom_in=True),
    }
    for _k, _v in (ms.get("levels") or {}).items():
        if _k in LEVELS:
            LEVELS[_k].update(_v)
    # 旧字段 z_min/z_range 视为 wide 档的覆盖，保持向后兼容
    if "z_min" in ms:   LEVELS["wide"]["zmin"] = float(ms["z_min"])
    if "z_range" in ms: LEVELS["wide"]["zrange"] = float(ms["z_range"])

    def shot_of(pp):
        """判断景别：wide=全身/全景，mid=中景，close=近景/特写（人物占比大）"""
        if pp.get("shot") in LEVELS:
            return pp["shot"]
        name = os.path.basename(pp.get("src") or "")
        if re.search(r"特写|近景|大头|脸部|半身", name): return "close"
        if re.search(r"中景|腰上", name):                return "mid"
        if re.search(r"全身|全景|远景|大景", name):      return "wide"
        return "mid"                                    # 认不出景别按中景保守处理

    def ease(nn):
        """归一化进度的缓动表达式：线性 -> 缓出 / 缓入缓出"""
        p = f"min(on/{nn},1)"
        if EASE == "inout":
            return f"({p})*({p})*(3-2*({p}))"
        if EASE == "out":
            return f"(1-pow(1-{p},3))"
        return p

    # ★越界防护：镜数因合并/删镜变少后，config 里残留的旧 0-based 索引会越界
    #   （历史事故：D 条 17→16 镜后 transition_whoosh 残留 16 ⇒ IndexError 渲染中断）。
    #   渲染期一律把超出镜数的索引丢弃，不报错。
    swing = set(cfg.get("swing_shots", []))    # 这些镜(0起)入场时轻微甩动+唰声
    workdir = cfg.get("workdir") or os.path.dirname(os.path.abspath(cfg["output"]))
    os.makedirs(workdir, exist_ok=True)
    # ★2026-10-05 补：output 常配成 <workdir>/render/out_x.mp4，而这里只建了 workdir，
    # render/ 子目录不存在时 ffmpeg 报 "Error opening output … No such file or directory"
    # 退出码 254，**且 -v error 把真实原因吞掉**，日志里只剩一长串参数（本次排查 20+ 分钟）。
    _outdir = os.path.dirname(os.path.abspath(cfg["output"]))
    if _outdir:
        os.makedirs(_outdir, exist_ok=True)
    # 产品详情卡片：scripts/make_cards.py 预生成的「圆角+白边+投影」PNG + 尺寸元数据
    card_meta = {}
    _cmp = os.path.join(workdir, "cards", "cards.json")
    if os.path.exists(_cmp):
        try:
            card_meta = json.load(open(_cmp, encoding="utf-8"))
        except Exception as _e:
            print("  WARN cards.json 解析失败:", _e)
    # 音效表提前定义：重点字音效轮换与冲突消解都要用到可用音效集合
    sfx_files = {"whoosh": "whoosh.wav", "whoosh2": "whoosh2.wav", "whooshC": "whooshC.wav",
                 "pop_in": "pop_in.wav", "pop_in2": "pop_in2.wav",
                 "float_in": "float_in.wav",       # 噪声底候选 float_in2 已剔除(2026-09-17)
                 "impact": "impact.wav", "ding": "ding.wav",
                 "kw_pop": "kw_pop.wav", "kw_ding": "kw_ding.wav",
                 "kw_click": "kw_click.wav", "kw_riser": "kw_riser.wav",
                 "kw_boom": "kw_boom.wav",
                 # 2026-09-18 扩充音色池（scripts/synth_kw_sfx_v2.py 合成，按词意挑）
                 "kw_glass": "kw_glass.wav", "kw_triple": "kw_triple.wav",
                 "kw_charge": "kw_charge.wav", "kw_thump": "kw_thump.wav",
                 "kw_bloom": "kw_bloom.wav", "kw_spring": "kw_spring.wav",
                 "kw_shine": "kw_shine.wav",
                 # 2026-09-27 扩充：短促/收尾利落款（scripts/synth_kw_sfx_v3.py 合成）
                 # 用户反馈「重点字全绑同一个音效，听感单调」→ 按动效语气分音色，见 SKILL.md §音效库
                 "kw_tick": "kw_tick.wav",       # 0.10s 极短高频嘀 → 快速闪现/逐字
                 "kw_snap": "kw_snap.wav",       # 0.16s 木质短哒   → 利落落点/否定
                 "kw_pluck": "kw_pluck.wav",     # 0.30s 明亮弹拨   → 弹出
                 "kw_bellup": "kw_bellup.wav",   # 0.34s 上滑五度   → 强调/数据上升
                 "kw_marker": "kw_marker.wav",   # 0.34s 圆润马林巴 → 圈选/标注
                 "kw_drop": "kw_drop.wav",       # 0.30s 下滑 puu   → 落定/转折收束
                 "kw_spark": "kw_spark.wav",     # 0.30s 三连颗粒   → 清单/数量
                 "timi": "timi.wav"}             # 1.95s TiMi 游戏启动音（2026-09-29 用户拍板：文案说道「玩手机」自动配）
    sfx_files.update(cfg.get("sfx_files", {}))
    sfx_avail = {n for n, f in sfx_files.items() if os.path.exists(os.path.join(SFX_DIR, f))}

    # ---------- 1. 字符级时间轴 (whisper --max-len 4 的 -oj JSON) ----------
    w = json.load(open(cfg["tokens"], encoding="utf-8"))
    chars = []
    for seg in w["transcription"]:
        s, e = seg["offsets"]["from"] / 1000, seg["offsets"]["to"] / 1000
        txt = seg["text"].strip()
        n = len(txt)
        for i, c in enumerate(txt):
            chars.append((s + (e - s) * i / n, s + (e - s) * (i + 1) / n, c))

    phrases = cfg["phrases"]
    need_total = sum(len(pchars(p["text"])) for p in phrases)
    if need_total != len(chars):
        print(f"[警告] 口播字数 {len(chars)} != 文案字数 {need_total}，对齐可能漂移，请核对文案与音频一致")

    # ---------- 2. 按字数贪心对齐（必须保留 B1B2B6/3 等字母数字字符）----------
    timeline, ci = [], 0
    for k, p in enumerate(phrases):
        need = len(pchars(p["text"]))
        idxs = range(ci, ci + need)
        timeline.append({
            "text": p["text"],
            "start": chars[ci][0], "end": chars[ci + need - 1][1],
            "ctimes": [chars[x][0] for x in idxs],
        })
        ci += need
    TOTAL = timeline[-1]["end"] + cfg.get("tail", 1.1)

    # ---------- 2.5 【已废弃】独立片头钩子段 ----------
    # 2026-09-18 用户明确否决：「不是单独在前面再加3秒，而是一开始就是正文，但是要有
    # 勾人的效果」→ 改为 opening_burst（叠在正片开头几秒的画面上，不改时长）。
    # 下面这段解析与后面的钩子视频链全部**已不可达**（`hook` 会在解析处被强制置 None），
    # 保留仅为兼容旧 config 不报错，**新片一律不要写 hook**。
    # config: "hook": {"shots":["图1.png","图2.png","图3.png"], "secs":[0.9,0.95,1.15],
    #                  "sfx":[[0.02,"kw_boom"],[0.9,"whoosh"],[1.85,"kw_charge"],[2.75,"thump"]],
    #                  "glitch":true, "flash":true, "grain":7, "shake":1.0, "sfx_volume":{...}}
    # 旧实现：钩子垫在正片之前，时长 HO：正片镜头/字幕/卡片靠 concat 自然后移（不动时间轴），
    # 口播靠 adelay 后移，音效事件时间统一 +HO（钩子自己的音效用绝对时间）。
    # 钩子存在时自动关闭「撕裂开场/开场重击」——钩子本身已承担开场，叠加会打架。
    hk = cfg.get("hook")
    HK_FRAMES, HO, HK_SFX = [], 0.0, []
    if hk:
        # ⚠️ 已废弃（2026-09-18 用户明确口径）：不要「独立片头段」。
        #    正片开头要勾人，一律用 opening_burst（叠在正片前几秒的画面上，不改时长），
        #    不再另起 3 秒前置钩子。这里直接忽略，防止旧 config 又把片头撑长。
        print("  ⚠️  hook 已废弃：用户不要独立片头段 —— 本次忽略，请改用 opening_burst")
        hk = None
    # 开场冲击包：只叠画面与音效，不改任何时间轴
    # ✅ 默认开启（2026-09-18 用户拍板）——原话：「这几个开场默认开启使用，下回使用时随机
    #    使用一个。哪天我不想用了，我会说先不用。」所以**不写 opening_burst 就自动随机挑一款**。
    # 关闭（用户说「先不用」时）：config 写 "opening_burst": false（或 "off"/"none"/"no"）；
    #    也可临时用环境变量 AD_BURST_OFF=1（不动 config，批量比对旧片时用）。
    _RAW_BST = cfg.get("opening_burst")
    _BURST_OFF_STR = ("off", "none", "no", "false", "0", "关闭", "不用", "先不用", "别开")
    _BST_ON, _BST_SPEC = True, {}
    if _RAW_BST is None or _RAW_BST is True:
        _BST_SPEC = {}                                  # 没写 / true → 默认走随机
    elif _RAW_BST is False or _RAW_BST == 0:
        _BST_ON = False
    elif isinstance(_RAW_BST, str):
        _s = _RAW_BST.strip().lower()
        if _s in _BURST_OFF_STR:
            _BST_ON = False
        else:
            _BST_SPEC = {"preset": _s}                  # "opening_burst": "lightning" 也认
    elif isinstance(_RAW_BST, dict):
        _BST_SPEC = dict(_RAW_BST)
    else:
        print("  开场冲击: opening_burst 格式不对（应为对象 / preset 字符串 / false），已按默认随机处理")
    if os.environ.get("AD_BURST_OFF"):
        _BST_ON = False
        print("  开场冲击: AD_BURST_OFF 已设置 → 本次跳过开场动效")

    BS_PRESET, BS_DUR, BS_NAME = None, 0.0, ""
    bst = None
    if _BST_ON:
        _bp = str(_BST_SPEC.get("preset") or "random").strip().lower()
        _env_p = (os.environ.get("AD_BURST_PRESET") or "").strip().lower()
        if _env_p:
            _bp = _env_p                                 # 调试/比对时可临时指定
            print(f"  开场冲击: AD_BURST_PRESET 临时指定「{_bp}」")
        if _bp in ("", "random", "auto", "随机"):
            # 随机挑一款（用户口径就是「随机用一个」，不要擅自固定成 energy）。
            # 想复现同一条片：AD_BURST_SEED=<任意数>；不设则每次不同。
            _seed = os.environ.get("AD_BURST_SEED") or _BST_SPEC.get("seed")
            _rng = random.Random(_seed) if _seed not in (None, "") else random.Random()
            _bp = _rng.choice(sorted(BURST_PRESETS))
            print(f"  开场冲击: 随机选中「{_bp}」 —— 想固定就写 "
                  f"\"opening_burst\": {{\"preset\": \"{_bp}\"}}；本次不用写 \"opening_burst\": false")
        else:
            # ★2026-09-19 补：config 写死 preset 时原本**一行都不打印**，
            #   qc_burst.py 的闸门1 找不到"选中记录"就误判成"冲击未生效"。
            #   选中记录是验收锚点，三种来源都必须留痕。
            print(f"  开场冲击: config 指定「{_bp}」")
        BS_NAME = _bp
        # ⚠️ 这里必须保证 bst 是**非空 dict**。踩过的真坑（2026-09-19 用户发现）：
        #    config 不写 opening_burst（＝默认随机路径）时 `_BST_SPEC` 是 `{}`，
        #    而空 dict 是 falsy → 下面 `if bst and BS_PRESET:` 整块被跳过 →
        #    **日志照样打印「随机选中 X」，成片里画面音效却一层都没叠**。
        #    表现极隐蔽：只看日志会误判"已生效"。所以这里显式补上 preset 键。
        bst = {"preset": _bp}
        bst.update(_BST_SPEC or {})
        _pre = BURST_PRESETS.get(_bp)
        if _pre is None:
            print(f"  开场冲击: 未知 preset「{_bp}」，已跳过（可选: {' / '.join(sorted(BURST_PRESETS))}）")
            bst = None
        else:
            # 起手整体后移 lead：**给封面帧留出干净的第 0 帧**——封面是平台抓首帧用的，
            # 白闪从 0.00s 起会把它整帧盖白（实测）。1 帧多（默认 0.04s）观感无感。
            # ★2026-09-19 修正：lead 必须**不小于封面时长**，否则封面那 0.1s 会把冲击
            #   起手（第一下闪白 + 冲击环）整段盖掉——用户看到的就是"开场动效好像没出来"。
            #   封面默认 0.1s（DEFAULT_COVER_DUR），所以这里自动抬到 0.1s；封面关掉时
            #   仍走 0.04s（留一帧干净起手），冲击不被裁切、总时长依旧不变。
            _lead = float(bst.get("lead", 0.04) or 0)
            _cov_cfg = cfg.get("cover")
            _cov_disabled = (_cov_cfg is False or
                             (isinstance(_cov_cfg, dict) and _cov_cfg.get("enabled") is False))
            _cov_dur = 0.0
            if not _cov_disabled:
                _cov_dur = float(_cov_cfg.get("dur", DEFAULT_COVER_DUR)) \
                    if isinstance(_cov_cfg, dict) else DEFAULT_COVER_DUR
            if _cov_dur > _lead:
                print(f"  开场冲击: lead {_lead:.3f}s → {_cov_dur:.3f}s（避开封面帧，"
                      f"否则起手第一下会被封面盖住）")
                _lead = _cov_dur
            BS_PRESET = {"dur": _pre.get("dur"), "shake": _pre.get("shake"),
                         "flash_a": _pre.get("flash_a"),
                         "layers": [dict(l) for l in _pre["layers"]],
                         "flashes": list(_pre.get("flashes") or []),
                         "sfx": list(_pre.get("sfx") or [])}
            if _lead:
                for _l in BS_PRESET["layers"]:
                    _l["t0"] = float(_l.get("t0", 0.0)) + _lead
                BS_PRESET["flashes"] = [(float(a) + _lead, float(b))
                                        for a, b in BS_PRESET["flashes"]]
                BS_PRESET["sfx"] = [(n, float(t) + _lead) for n, t in BS_PRESET["sfx"]]
            BS_DUR = float(bst.get("dur") or _pre.get("dur") or 1.2)
            _need = max([float(l.get("t0", 0)) + float(l.get("dur", 0))
                         for l in BS_PRESET["layers"]] + [0.0])
            BS_DUR = max(BS_DUR, _need)
    # 守护网（2026-09-19）：preset 解析成功、开关却为假 → 冲击不会落画面。
    # 这正是「日志说要生效、成片里啥都没有」的那个 bug，宁可在日志里吼一声。
    if BS_PRESET and not bst:
        print("  ⚠️ 开场冲击: preset 已解析但开关为假 → 冲击不会生效，请检查 bst 赋值")
    if isinstance(hk, dict) and hk.get("shots"):
        hk = dict(hk)
        _fpsH = float(cfg.get("fps", 30))
        _secs_in = hk.get("secs") or []
        _w = float(hk.get("dur", 3.0)) / max(1, len(hk["shots"]))
        _keep = []
        for _i, _s in enumerate(hk["shots"]):
            _sc = _secs_in[_i] if _i < len(_secs_in) else _w
            if os.path.exists(_s):
                _keep.append((_s, float(_sc)))
            else:
                print(f"  片头钩子: 跳过缺失素材 {_s}")
        if _keep:
            hk["_pairs"] = _keep
            HK_FRAMES = [max(2, int(round(_sc * _fpsH))) for _s, _sc in _keep]
            HO = sum(HK_FRAMES) / _fpsH
            HK_SFX = [(float(a), b) for a, b in (hk.get("sfx") or [])]
            print(f"  片头钩子: {len(_keep)} 镜 / 共 {HO:.2f}s（正片与口播整体后移 {HO:.2f}s）")
        else:
            hk = None
    else:
        hk = None
    TOTAL_ALL = TOTAL + HO

    # ---------- 2.6 镜尾延长 `extend`（2026-10-06 灿爹「肌酸后留反应时间」加）----------
    # 用途：句尾需要多停一会儿（如片头卷播大图定格后），但**不新增配音**。
    # 该镜 cut_end 往后推；后续镜头**只在超出原有空档的部分**才后移。
    #
    # ★2026-10-06 修正（双重延长 bug）：
    #   原实现 `timeline[i].cut_end = next.start + extend` 且把后续 start 全部 +=extend。
    #   这假设「下一镜 start 紧贴本镜 cut_end」。但用带停顿配音
    #   （vo_with_pause：镜1 末字 3.64 → 镜2 首字 4.79，中间本来就有 1.15s 空档）时，
    #   目标只是「让画面填满这段已有空档」，而原实现又额外推了 1.15s
    #   ⇒ 镜2 画面起点变成 5.94，比它的语音(4.79)晚 1.15s，全片音画错位。
    #   现在改成：**先把 cut_end 推到 min(下一镜start, 末字+extend)**，
    #   只有真正超出下一镜 start 的部分才顺延后续镜头 —— 空档不被重复计算。
    _EXT = [float(pp.get("extend", 0.0) or 0.0) for pp in phrases]
    # ---------- 2.6b`cover_until`：卷播镜显式声明画面盖到哪一秒 ----------
    # 场景（2026-10-06 灿爹「整句钩子全部走卷播」）：片头卷播要把整句钩子
    # **以及紧跟的科普段**一起吃掉（如 B 条镜1 吃掉原镜1+镜2+镜3，共 0→6.84s）。
    # 此时 `extend` 会被「下一镜首字」卡住 —— 镜2 首字就在 3.92s，
    # 于是卷播只拿到 3.87s 画面，后面本该属于卷播的语音区间全被切走。
    #
    # `cover_until` 绕过这个限制：该镜 cut_end 直接 = cover_until，
    # 真正被顶开的后续镜头按差额整体顺延（顺延后画面仍在各自语音之前，不会错位）。
    _COVER = [float(pp.get("cover_until", 0.0) or 0.0) for pp in phrases]
    if any(_COVER):
        for _i in range(len(timeline) - 1, -1, -1):
            if _COVER[_i] <= 0:
                continue
            _old_next = timeline[_i + 1]["start"] if _i + 1 < len(timeline) else TOTAL
            _new_cut = max(_COVER[_i], timeline[_i]["end"])
            _grow = max(0.0, _new_cut - _old_next)
            timeline[_i]["cut_end"] = _new_cut
            if _grow > 0 and _i + 1 < len(timeline):
                for _j in range(_i + 1, len(timeline)):
                    timeline[_j]["start"] += _grow
                    timeline[_j]["end"] += _grow
            TOTAL += _grow
            print(f"  [cover_until] 镜{_i+1}: 画面盖到 {timeline[_i]['cut_end']:.2f}s"
                  + (f"（顶开下一镜 {_old_next:.2f}s，顺延后续 {_grow:.2f}s）"
                     if _grow > 0 else "（未顶开下一镜）"))
        print(f"[cover_until] 总时长 → {TOTAL:.2f}s")
    if any(_EXT):
        for _i in range(len(timeline) - 1, -1, -1):
            if _EXT[_i] <= 0:
                continue
            _old_next = timeline[_i + 1]["start"] if _i + 1 < len(timeline) else TOTAL
            # 语音末字（本镜 text 结束时刻）
            _vo_end = timeline[_i]["end"]
            # 目标：画面至少停到 末字+extend；但不越过下一镜的语音起点
            _want = _vo_end + _EXT[_i]
            _new_cut = min(_want, _old_next) if _i + 1 < len(timeline) else _want
            _grow = max(0.0, _new_cut - _old_next)      # 真正需要顺延后续的量
            timeline[_i]["cut_end"] = max(_new_cut, _old_next)
            if _grow > 0 and _i + 1 < len(timeline):
                for _j in range(_i + 1, len(timeline)):
                    timeline[_j]["start"] += _grow
                    timeline[_j]["end"] += _grow
            TOTAL += _grow
            if _EXT[_i] > 0:
                _gap = _old_next - _vo_end
                print(f"  [extend] 镜{_i+1}: 末字 {_vo_end:.2f}s，空档至下一镜 "
                      f"{_old_next:.2f}s（{_gap:.2f}s）→ 画面停到 "
                      f"{timeline[_i]['cut_end']:.2f}s"
                      + (f"（用满空档，顺延后续 {_grow:.2f}s）" if _grow > 0
                         else "（空档已够用，不顺延后续）"))
        print(f"[extend] 总时长 → {TOTAL:.2f}s")

    for i, t in enumerate(timeline):
        if i + 1 < len(timeline):
            # 有 extend / cover_until 的镜：cut_end 已含延长，不再被下一镜起点覆盖
            if _EXT[i] > 0 or _COVER[i] > 0:
                pass
            else:
                t["cut_end"] = timeline[i + 1]["start"]
        else:
            t["cut_end"] = TOTAL
        print(f"[{i+1:2d}] {t['start']:6.2f}-{t['cut_end']:6.2f}  {t['text'][:18]}"
              + (f"  (+extend {_EXT[i]:.2f}s)" if _EXT[i] > 0 else ""))

    # ---------- 3. 视频链 ----------
    inputs, fc, in_i = [], [], 0
    # ★2026-10-21 灿爹点名：「除了开头有动效（卷播/钩子）的不加，其他硬切的分镜加转场」。
    #   实现用 ffmpeg xfade（重叠式转场）——它会把总长缩短 T，所以**除最后一镜外每镜多渲
    #   T(+2 帧缓冲)**，让 xfade 把多出来的这段吃掉；成片总长仍等于 Σ原镜长（结尾再 trim
    #   到 TOTAL 兜住取整误差）⇒ 字幕 / 口播 / 动效层时间轴一行都不用改。
    #   cfg: "transition": {"dur":0.3, "default":"smoothleft", "flash":[2,4,6],
    #                       "flash_type":"fadewhite"}
    #   flash = 「第 k 镜之后」用白闪（1 基），其余用 default 推镜 ⇒ 推+闪混着来。
    _xf = cfg.get("transition") or {}
    XF_T = float(_xf.get("dur", 0) or 0)
    XF_TYPE = str(_xf.get("default") or "smoothleft")
    XF_FLASH = set(int(x) for x in (_xf.get("flash") or []))
    XF_FLASH_TYPE = str(_xf.get("flash_type") or "fadewhite")
    # ★mode：
    #   mix   —— ★默认（2026-10-21 灿爹二次点名）：**每个切点都真转场**，按 cuts 列表
    #            逐个指定 push_left / push_up / push_right / push_down / flash。
    #            推 = overlay 位移，零时长变化、不截断流（见下方实现注释）。
    #   flash —— 只打亮度脉冲（早期版本，弱闪几乎看不出来，已降级为兜底）。
    #   xfade —— 重叠式推镜，**会把视频流静默截断**（见坑表 ⑨），仅留作对照。
    XF_MODE = str(_xf.get("mode") or "mix")
    # 每个切点的手法（1 基切点顺序：cuts[0] = 镜1→镜2）。缺省按 flash 处理。
    XF_CUTS = [str(c) for c in (_xf.get("cuts") or [])]
    if XF_T > 0:
        print(f"  镜间转场: {XF_TYPE} {XF_T:.2f}s"
              + (f" + 白闪@{sorted(XF_FLASH)}" if XF_FLASH else ""))
    motions = ["zoom_in", "pan_r", "zoom_out", "pan_l"]
    img_idx = prod_idx = 0
    # prod/imgvid 镜声明 own_audio 时登记的「自带音轨」清单，音频段据此替换全局 vo
    OWN_AUDIO = []
    # 贴图位置预设（主画面 1920x1080；br/bl 上移到字幕区之上）
    OVER_POS = {
        "tr": ("W-w-56", "56"), "tl": ("56", "56"),
        "br": ("W-w-56", "H-h-260"), "bl": ("56", "H-h-260"),
        "center": ("(W-w)/2", "(H-h)/2"),
    }
    for i, t in enumerate(phrases):
        dur = timeline[i]["cut_end"] - timeline[i]["start"]
        n = max(2, round(dur * FPS))
        # ★2026-10-21 转场长度**不靠加帧**（实测 zoompan 的实际输出长度与 n 不同步，
        #   加 n 后 A 仍然在原时长处结束 ⇒ xfade 落在 A 之外、退化成硬切，且总长会变）。
        #   改为在每镜末尾 `tpad=stop_mode=clone` 冻结 T 秒专门喂给转场重叠，
        #   n 保持原样 ⇒ 单镜运动节奏不变、总长严格 = Σ原镜长。
        # 图片填充方式: cover=等比放大裁切(默认, 不拉伸) / contain=等比缩放留边 /
        #              safe=等比缩放 + 放大模糊同图补边(人物顶天立地的素材用它, 保证主体不被切) / stretch=强制拉伸
        # 注意: 中间画布必须是 16:9(2880x1620)。早期写成 2880x1920(3:2) 再让 zoompan 输出
        # 1920x1080，等于把 3:2 硬拉成 16:9 —— 全片横向拉伸 18.5%(实测蓝方格 1.000→1.169)。
        fit = t.get("fit") or cfg.get("img_fit", "cover")
        # 3:2 素材裁成 16:9 时要垂直裁掉 300/1920。默认 **顶部对齐**(crop_y=0)：
        # 实测中心裁会把全身像的头顶切掉(图库里 24/25 张是 3:2 全身/中景像，人物头顶普遍贴顶边)，
        # 而人物总高一般 < 1620，顶部对齐即可完整容纳，多裁掉的只是脚部/地面阴影。
        # 个别图主体偏下 → 该句写 "crop_y": 150(或 300)；全局默认可用 cfg.crop_y 改。
        def crop_y_px(scale_h=1920):
            cy = t.get("crop_y", cfg.get("crop_y", 0))
            return max(0, min(int(round(float(cy) * scale_h / 1920.0)), scale_h - int(scale_h * 1620 // 1920)))
        def prep(src_label, tag):
            pf = t.get("prefilter") or cfg.get("img_prefilter")
            if pf:
                base = pf
            elif fit == "stretch":
                base = "scale=2880:1620,setsar=1"
            elif fit == "contain":
                pad = t.get("pad_color") or cfg.get("img_pad_color", "white")
                base = (f"scale=2880:1620:force_original_aspect_ratio=decrease,"
                        f"pad=2880:1620:(ow-iw)/2:(oh-ih)/2:color={pad},setsar=1")
            else:
                base = (f"scale=2880:1620:force_original_aspect_ratio=increase,"
                        f"crop=2880:1620:0:{crop_y_px(1920)},setsar=1")
            if t.get("flip"):
                base += ",hflip"  # 图库复用防重复: 镜像翻转
            return base
        if t.get("fg") and t.get("bg"):
            # ---- 抠图分层：背景层走运镜，人物层独立做弹入/浮动（背景干净的图才用）----
            inputs += ["-i", t["bg"]]; kb = in_i; in_i += 1
            inputs += ["-i", t["fg"]]; kf = in_i; in_i += 1
            fgm = t.get("fg_motion", "popin")          # popin=弹入 / float=浮动
            fin = min(float(t.get("fg_in", 0.6)), dur * 0.5)
            INN = max(1, round(fin * FPS))
            P = f"min(on/{INN},1)"
            PT = f"min(t/{fin:.3f},1)"
            if fgm == "popin":
                # 从 0.86 倍带回弹落位 + 轻微下落，落位后转呼吸浮动
                zf = (f"if(lt(on,{INN}),0.86+0.14*(1-pow(1-{P},3))+0.04*sin(PI*{P}),"
                      f"1+0.012*sin(1.5*(on/{FPS}-{fin:.3f})))")
                ox = "0"
                oy = f"55*pow(1-{PT},3)+10*sin(1.1*t)"
            else:                                       # float：呼吸缩放 + 上下左右轻漂
                zf = f"1+0.014*sin(1.5*on/{FPS})"
                ox = "8*sin(0.8*t+1)"
                oy = "14*sin(1.1*t)"
            E = ease(n)
            bgm = t.get("bg_motion", "zoom_in")
            bz = float(t.get("bg_zoom", 0.05))
            # ★2026-09-24 修「分层镜背景帧间差≈0.6、被 qc_motion 判『夹死/静止』」：
            #   旧版 bg 只有一次**单调**推拉（推完就彻底静止），而 bg 素材是 `_bg_soft.png`
            #   （抠图时同步产出的**已软化模糊**背景）——两者叠加 ⇒ 全镜后半段帧间差近乎为 0。
            #   实测：镜10 四角 MAE=0.6（≈纯压缩噪声），而单独跑 bg filter 能到 5.0。
            #   现在给 bg 叠上与其他镜同款的呼吸缩放，静止段持续有微动。
            #   （只加缩放、**不加平移晃动**：fg 是独立叠加的，bg 位移会让"跳出来的人物"看起来在飘。）
            #   想关掉/调幅：config 写 "bg_zoom": 0（推拉幅度）或全局 motion_style.breath=0。
            _bph = 1.0 + 0.37 * (i % 5)
            bb = f"+{BREATH:.4f}*sin(1.6*on/{FPS}+{_bph:.2f})" if BREATH else ""
            bzz = (f"{bz:.3f}*{E}" if bgm == "zoom_in" else f"{bz:.3f}*(1-{E})")
            zb = f"1.03+{bzz}{bb}"
            flipf = ",hflip" if t.get("flip") else ""
            # bg：背景已无人，可放开推近（仍用 min(max()) 防出界）；画布必须 16:9 否则横向拉伸
            fc.append(f"[{kb}:v]scale=2880:1620:force_original_aspect_ratio=increase,"
                      f"crop=2880:1620:0:{crop_y_px(1920)},setsar=1{flipf},"
                      f"zoompan=z='{zb}':x='min(max(iw/2-(iw/zoom/2),0),iw-iw/zoom)'"
                      f":y='min(max(ih/2-(ih/zoom/2),0),ih-ih/zoom)'"
                      f":d={n}:s=1920x1080:fps={FPS}[cbg{i}]")
            # fg：pad 到透明大画布后 zoompan（基准 1.5 = 取回原构图），透明层无需 clamp
            fc.append(f"[{kf}:v]format=rgba,"
                      f"scale=1920:1080:force_original_aspect_ratio=increase,"
                      f"crop=1920:1080:0:{crop_y_px(1280)},setsar=1{flipf},"
                      f"pad=2880:1620:(ow-iw)/2:(oh-ih)/2:color=black@0.0,"
                      f"zoompan=z='1.5*({zf})':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                      f":d={n}:s=1920x1080:fps={FPS}[cfg{i}]")
            fc.append(f"[cbg{i}][cfg{i}]overlay=x='{ox}':y='{oy}':format=auto,format=yuv420p[vb{i}]")
            img_idx += 1
            print(f"    镜{i+1:2d} 抠图分层 fg={fgm} bg={bgm} 入场={fin:.2f}s")
        elif t["kind"] == "img":
            inputs += ["-i", t["src"]]; k = in_i; in_i += 1
            if t.get("motion"):  # 图库复用防重复: 指定推/拉/摇, 与上次不同
                m = t["motion"]
            else:
                m = "punch" if i == 0 and cfg.get("opening_punch", True) else motions[img_idx % 4]
            img_idx += 1
            # ---- 按景别分档：人物占比越大，运镜越保守 ----
            lv = shot_of(t)
            L = LEVELS[lv]
            zmin, zr = L["zmin"], L["zrange"] * ITENS
            dg, ac = L["diag"] * DIAGC * ITENS, L["arc"] * ARCC * ITENS
            sk, tl = SHAKE * L["shake"] * ITENS, L["tilt"] * ITENS
            bamp = BREATH * L["breath"]
            if m == "static":
                pass
            elif L.get("no_zoom_in") and m == "zoom_in":
                m = "zoom_out"          # 人物占比大的镜不推近(会切边)，改为拉远
            # ---- 动态风格：缓动进度 + 呼吸 + 手持微晃 + 斜向/弧线（每档都开，只是幅度不同）----
            T = f"on/{FPS}"                       # zoompan 无 t 变量，用帧号换算秒
            ph = 1.0 + 0.37 * (i % 5)             # 相位错开，避免每个镜头抖得一模一样
            dx, dy = DIRS[i % 4]
            A = 9.0 * sk
            if sk > 0 and i not in swing:         # swing 镜已有甩动，不叠加抖动
                shx = f"+{A:.2f}*sin(2.1*{T}+{ph:.2f})+{A*0.55:.2f}*sin(3.7*{T}+{ph*2:.2f})"
                shy = f"+{A*0.8:.2f}*sin(1.9*{T}+{ph*1.3:.2f})+{A*0.45:.2f}*sin(3.1*{T}+{ph*0.7:.2f})"
            else:
                shx = shy = ""
            br = f"{bamp:+.4f}*sin(1.6*{T}+{ph:.2f})" if bamp else ""
            # 倾斜摇摆：甩入镜用衰减摆动，其余按景别给低频正弦（特写主要动感来源）
            tilt_expr = ("0.05*(1-min(t/0.55,1))*sin(13*t)" if i in swing else
                         f"{tl:.4f}*sin(0.9*t+{ph:.2f})" if tl > 0.00001 else "")
            # rotate 前预放大到 3104(=2880x1.0778)，zoompan 的 zoom 必须同比例**放大**才能对上原构图。
            # 注意：早期版本写成除以 padf，会让 zoom 落到 1 以下 → iw-iw/zoom 变负 →
            # min(max(x,0),负数) 把取景框夹死，抖动/呼吸/推拉全部失效（只有带 tilt 的镜头中招）。
            padf = 1.0778 if tilt_expr else 1.0
            if m == "punch":
                amp = max(zr * 1.5, 0.06)         # 开场收缩幅度，近景也有最小冲击力
                zx = (f"if(lt(on,10),{zmin + amp:.4f}-{amp / 10:.4f}*on,"
                      f"min({zmin + amp * 0.45:.4f}+{amp * 0.004:.5f}*(on-10),"
                      f"{zmin + amp * 0.75:.4f})){br}")
                cx, cy = f"iw/2-(iw/zoom/2){shx}", f"ih/2-(ih/zoom/2){shy}"
            elif m == "static":
                zx = f"{zmin:.4f}{br}"
                cx, cy = f"iw/2-(iw/zoom/2){shx}", f"ih/2-(ih/zoom/2){shy}"
            elif m in ("zoom_in", "zoom_out"):
                E = ease(n)
                zx = (f"{zmin:.4f}+{zr:.4f}*{E}" if m == "zoom_in"
                      else f"{zmin + zr:.4f}-{zr:.4f}*{E}") + br
                # 斜向：取景框沿对角线平移，起点终点分居中心两侧
                ox = f"{dx*dg*40:+.2f}*(1-2*{E})" if dg else ""
                oy = f"{dy*dg*26:+.2f}*(1-2*{E})" if dg else ""
                cx, cy = f"iw/2-(iw/zoom/2){ox}{shx}", f"ih/2-(ih/zoom/2){oy}{shy}"
            else:
                E = ease(n)
                # 摇镜位移按景别收窄：人物占比越大，可平移范围越小（防主体被推出画）
                pn = min(L["pan"] * ITENS, 1.0)
                px = (f"{0.5-0.45*pn:.4f}+{0.90*pn:.4f}*{E}" if m == "pan_r"
                      else f"{0.5+0.45*pn:.4f}-{0.90*pn:.4f}*{E}")
                py = f"0.5+{ac*dy:.4f}*sin(PI*{E})" if ac else "0.5"
                zx = f"{L['zpan']:.4f}{br}"
                cx = f"(iw-iw/zoom)*({px}){shx}"
                cy = f"(ih-ih/zoom)*({py}){shy}"
            zp = (f"zoompan=z='({zx})*{padf:.4f}'"
                  f":x='min(max({cx},0),iw-iw/zoom)'"
                  f":y='min(max({cy},0),ih-ih/zoom)'"
                  f":d={n}:s=1920x1080:fps={FPS}")
            if fit == "safe":
                # 人物顶天立地的素材(3:2 全身像)：不裁切，改为等比缩小后用"放大模糊的同图"补边，
                # 保证头脚都不被切。overlay 居中 → 接缝在两侧，原图背景多为灰白渐变，观感接近海报留白。
                fl = ",hflip" if t.get("flip") else ""
                fc.append(
                    f"[{k}:v]split=2[sfa{i}][sfb{i}];"
                    f"[sfa{i}]scale=2880:1620:force_original_aspect_ratio=increase,crop=2880:1620,"
                    f"setsar=1,boxblur=40:2[sfbg{i}];"
                    f"[sfb{i}]scale=2880:1620:force_original_aspect_ratio=decrease{fl},setsar=1[sfgf{i}];"
                    f"[sfbg{i}][sfgf{i}]overlay=(W-w)/2:(H-h)/2,{zp}[vb{i}]")
            elif tilt_expr:
                # 入场甩动 / 轻微旋转摇摆（需先放大留旋转余量）
                fc.append(f"[{k}:v]{prep(k, i)},"
                          f"scale=3104:1746:force_original_aspect_ratio=increase,crop=3104:1746,setsar=1,"
                          f"rotate=a='{tilt_expr}':c=black,{zp}[vb{i}]")
            else:
                fc.append(f"[{k}:v]{prep(k, i)},{zp}[vb{i}]")
            print(f"    镜{i+1:2d} {lv:5s} {m:8s} z={zmin:.2f}+{zr:.3f} 斜={dg:.2f} 晃={sk:.2f} 倾={tl:.4f}")
        elif t["kind"] == "imgvid":
            # AI 图生视频片段: 裁到语句时长, **缓慢推近**增加电影感
            # ★2026-09-24 修：原 z 是常数(1.10/1.15) ⇒ zoompan 只做恒定裁切、**画面零运动**，
            #   静素材(沙漠剪影远景 MAE≈4.5)整镜被判"夹死/静止"(像素差 0.4%)。
            #   改为随输出帧号缓动推近 1.06 → 1.14（复用 ease，与 img/prod 镜同款曲线）。
            inputs += ["-i", t["src"]]; k = in_i; in_i += 1
            img_idx += 1
            _n = max(int(dur * FPS), 1)
            # ★2026-10-26 修（重大）：**短片比镜短时的死锁**。
            #   trim=duration 只能截短、不能补长 ⇒ 素材 3.60s 而本镜 4.22s 时 [vb] 只有 3.60s。
            #   下一处转场是 overlay 覆盖式推镜，主输入提前 EOF 后输出就冻结在主路末帧，
            #   副路再滑进来也盖不上 ⇒ 整段下一镜被上一镜末帧顶掉（实测镜 2 全段显示镜 1 画面）。
            #   这里按实测时长差补 clone 尾帧，保证 [vb] 长度 == dur（多给 0.05 防取整差 1 帧）。
            _vd = _probe_dur(FF, t["src"]) or 0.0
            _vgap = dur - _vd
            _vpad = (f",tpad=stop_mode=clone:stop_duration={_vgap + 0.05:.3f}"
                     if _vgap > 0.02 else "")
            if _vgap > 0.02:
                print(f"  ⚠️ 镜{i+1} 短片 {_vd:.2f}s < 镜长 {dur:.2f}s → 补冻结尾帧 {_vgap + 0.05:.2f}s"
                      f"（不补会让下一镜被本镜末帧盖住）")
            if t.get("no_zoom"):
                # ★2026-10-21：**已经自带运镜的短片**（镜 1 扫描重建入场）不要二次推近。
                #   多加一次 zoompan 会再裁掉 6~14% 画面 + 多一道重采样，扫描噪点会被糊掉；
                #   且动效末帧本该是原图全幅，再放大就与下一镜对不上。
                fc.append(f"[{k}:v]fps={FPS},scale=1920:1080,setsar=1,"
                          f"trim=duration={dur:.3f},setpts=PTS-STARTPTS{_vpad}[vb{i}]")
            else:
                z = f"1.06+0.08*{ease(_n)}"
                fc.append(
                    f"[{k}:v]fps={FPS},scale=2304:1296,setsar=1,"
                    f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS},"
                    f"trim=duration={dur:.3f},setpts=PTS-STARTPTS{_vpad}[vb{i}]")
        else:  # prod: 产品视频段 [起始秒, 结束秒]
            ss, se = t["src"]
            inputs += ["-ss", str(ss), "-t", str(se - ss + 0.3), "-i", t["video"]]; k = in_i; in_i += 1
            z = ["1.10", "1.18", "1.08"][prod_idx % 3]; prod_idx += 1
            fc.append(
                f"[{k}:v]fps={FPS},scale=2304:1296,setsar=1,"
                f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps={FPS},"
                f"trim=duration={dur:.3f},setpts=PTS-STARTPTS[vb{i}]")
            # ---------- 3.1b prod 镜自带音轨（2026-10-06 灿爹「卷播音效没进成片」）----------
            # 卷播开头这类片段自带完整混音（人声+卷播音效+定格咚），而渲染器原来
            # 只取 [k:v] 把音轨丢了 ⇒ 成片开头 0~4.4s 只有全局 vo 的人声，
            # 用户在样片里确认过的音效一个都没进成片。
            # 这里把该镜的音轨登记下来，交给音频段（5.x）替换全局 vo 的对应区间。
            if t.get("own_audio"):
                if _probe_has_audio(FF, t["video"]):
                    _want = max(0.05, timeline[i]["cut_end"] - timeline[i]["start"])
                    # 素材比该镜短时（extend 让画面停在定格帧上），音轨不能跟着拖长 ——
                    # atrim 超长只会取到素材末尾的静音，反而在定格画面上留一段死寂。
                    # 这里裁到素材实际时长，多出的画面时长靠末帧静止撑住（画面本就该定住）。
                    _alen = _probe_dur(FF, t["video"])
                    _adur = min(_want, _alen) if _alen and _alen > 0 else _want
                    OWN_AUDIO.append({
                        "in_i": k, "pi": i,
                        # 素材自带混音从素材 0s 起就是完整的一条（首字约在 0.2s，
                        # 但起手音效在前 0.2s 就已经响了）⇒ 接入点必须落在 0s，
                        # 若跟着 timeline.start(0.20) 走会整体右移 0.2s，与字幕错位。
                        "t0": 0.0,
                        "t1": _adur,
                        "sil_until": _want,      # 画面仍持续到 _want，_adur~_want 之间靠静音
                        "gain": float(t.get("own_audio_gain", 1.0)),
                    })
                    _note = (f"（素材 {_alen:.2f}s < 画面 {_want:.2f}s，取满素材、"
                             f"余下 {_want - _adur:.2f}s 静音定格）"
                             if _alen and 0 < _alen < _want else "")
                    print(f"  镜{i+1} 自带音轨: 采用（0.00-{_adur:.2f}s，"
                          f"屏蔽全局 vo 同区间）{_note}")
                else:
                    print(f"  ⚠️ 镜{i+1} 写了 own_audio 但 "
                          f"{os.path.basename(t['video'])} 无音轨 → 忽略")

        # ---------- 3.2 产品图贴图 (over): 说到产品内容时贴上去 ----------
        ovs = t.get("over") or []
        if isinstance(ovs, dict):
            ovs = [ovs]
        cur = f"vb{i}"
        for j, ov in enumerate(ovs):
            # -loop 让单帧图片变成视频流，否则 fade 的时间轴永远停在 t=0（延迟>0 时贴图恒透明）
            inputs += ["-loop", "1", "-t", f"{dur + 0.5:.2f}", "-i", ov["src"]]; ok = in_i; in_i += 1
            w = int(ov.get("w", 620))
            delay = float(ov.get("delay", 0.12))
            fo = float(ov.get("fade", 0.3))
            p = ov.get("pos", "tr")
            if isinstance(p, (list, tuple)):
                px, py = str(p[0]), str(p[1])
            else:
                px, py = OVER_POS.get(p, OVER_POS["tr"])
            fc.append(f"[{ok}:v]scale={w}:-2,format=rgba,"
                      f"fade=t=in:st={delay:.2f}:d={fo:.2f}:alpha=1[ov{j}_{i}]")
            fc.append(f"[{cur}][ov{j}_{i}]overlay=x='{px}':y='{py}':format=auto[vo{j}_{i}]")
            cur = f"vo{j}_{i}"
            print(f"  贴图 镜{i+1}: {os.path.basename(ov['src'])} w={w} pos={p}")
        # ★转场喂料：非最后一镜尾部冻结 T 秒（clone 末帧），专供 xfade 的重叠段使用。
        #   ⚠️ 必须给 T + 0.12 的余量（2026-10-21 实测）：xfade 的输出长度 = offset + len_B - T，
        #   每处都恰好少 ~1 帧；链式下去第二处就出现 offset > len(A) ⇒ 输出空、后续全断，
        #   成片画面在第 7 秒就没了（音频 21.6s 还在）。多冻结 0.12s 让每处留 3~4 帧余量，
        #   多余的尾巴会在下一次 xfade 处被自动丢弃，总长仍是 Σ原镜长。
        _xpad = (f"tpad=stop_mode=clone:stop_duration={XF_T + 0.12:.3f}"
                 if (XF_MODE == "xfade" and XF_T > 0 and i < len(phrases) - 1) else "null")
        # ★mix 模式：**除首镜外每镜头部冻结 c 秒**（c = 本镜起点）。冻结出来的是本镜首帧，
        #   推镜时它从画面外滑进来、就位后才开始走运动 ⇒ 本镜的运镜节奏一帧不挪。
        #   （实测 tpad start 的克隆帧几乎不耗 CPU，21s 片总共多跑约 1500 帧。）
        if XF_MODE == "mix" and XF_T > 0 and i > 0:
            _xpad = f"tpad=start_mode=clone:start_duration={timeline[i - 1]['cut_end']:.3f}"
        if ovs:
            fc.append(f"[{cur}]{_xpad}[v{i}]")
        else:
            fc.append(f"[vb{i}]{_xpad}[v{i}]")

    nseg = len(phrases)
    # ---------- 3.5 划开撕裂开场 (可选): 黑场白线划过 -> 上下两半撕开露镜1 ----------
    tear_T = float(cfg.get("opening_tear") or 0)
    if HO > 0 and tear_T > 0:
        print(f"  片头钩子: 自动关闭撕裂开场（原 {tear_T}s）—— 钩子已承担开场，两者相叠会打架")
        tear_T = 0.0
    if BS_PRESET and tear_T > 0:
        # 开场冲击包现在**默认开启**，撕裂开场同样抢前 1s —— 不靠"记得别写"来保证，
        # 代码直接让位（冲击包信息量更大，且带音效）。
        print(f"  撕裂开场: 自动关闭（原 {tear_T}s）—— 开场冲击包「{BS_NAME}」已占开场前 1s，相叠会打架")
        tear_T = 0.0
    v0_label = "v0"
    if tear_T > 0:
        H = 544
        inputs += ["-f", "lavfi", "-i", f"color=black:s=1920x{H}:r={FPS}:d={tear_T}"]; p_top = in_i; in_i += 1
        inputs += ["-f", "lavfi", "-i", f"color=black:s=1920x{H}:r={FPS}:d={tear_T}"]; p_bot = in_i; in_i += 1
        inputs += ["-f", "lavfi", "-i", f"color=white:s=1920x10:r={FPS}:d={tear_T}"]; p_ln = in_i; in_i += 1
        fc.append(f"[{p_ln}]format=yuva420p,fade=t=in:st=0:d=0.12:alpha=1,fade=t=out:st={tear_T-0.35:.2f}:d=0.2:alpha=1[pline]")
        prog = f"pow(min(max((t-0.25)/{tear_T-0.35:.2f},0),1),2)*(3-2*min(max((t-0.25)/{tear_T-0.35:.2f},0),1))"
        fc.append(f"[v0][{p_top}]overlay=x='-38*{prog}':y='-{H}*{prog}':enable='lt(t,{tear_T})'[vt1]")
        fc.append(f"[vt1][{p_bot}]overlay=x='38*{prog}':y='{1080-H}+{H}*{prog}':enable='lt(t,{tear_T})'[vt2]")
        fc.append(f"[vt2][pline]overlay=x=0:y={1080-H-9}:enable='lt(t,{tear_T})'[v0f]")
        v0_label = "v0f"
        print(f"撕裂开场: {tear_T}s")
    _labs = [v0_label] + [f"v{i}" for i in range(1, nseg)]
    # ★★mix：每个切点一次真转场（2026-10-21 灿爹「怎么只有一个呀」→ 全部切点都要有）
    #   为什么不用 xfade：xfade 是**重叠**，输出长度 = offset + len(B) - T，每处少 T，
    #   链式累积会把视频流静默截断（实测音频 21.6s / 画面只剩 7.47s）。
    #   这里用 **overlay 覆盖式推镜**：B 从画面外滑进来盖住 A，两路不重叠 ⇒ 时长严格不变。
    #   总长为什么天然正确：B 头部 tpad 冻结 c 秒 ⇒ len(B) = c + T_shot = 累计到本镜末，
    #   而 overlay 默认取最长流输出 ⇒ 每步 acc 自动延到新的累计点，末镜自然落到 TOTAL。
    CUT_KIND = []
    if XF_MODE == "mix" and XF_T > 0 and nseg > 1:
        _acc, _cum = _labs[0], 0.0
        _plan = XF_CUTS + ["flash"] * (nseg - 1)
        for k in range(1, nseg):
            _cum += timeline[k - 1]["cut_end"] - timeline[k - 1]["start"]
            _kind = str(_plan[k - 1])
            if _kind in ("cut", "none", "hard"):
                # ★2026-10-28 纯净硬切（拆镜专用）：B 直接顶上来，不做位移、**不打亮度脉冲**。
                #   背景：push 转场会给切点加一记 0.40 提亮 + 0.22s 位移，而字幕是烧进画面的，
                #   转场期间旧字幕被推出、新字幕还没推入 ⇒ 字幕带空一截再冒出来。切点一密
                #   （拆镜后每 1~1.5s 一个）就成了灿爹说的「字幕一闪一闪」。
                #   同一句内部拆出来的子镜之间就该用硬切：换图不换句，字幕瞬间替换、零空窗。
                #   enable 必须比 tpad 起点早 0.10s（见下方 flash 分支的实测边界）。
                fc.append(f"[{_acc}][{_labs[k]}]overlay=x=0:y=0:eval=frame:"
                          f"enable='gte(t,{_cum - 0.10:.3f})'[mx{k}]")
                # 刻意不 append CUT_KIND ⇒ 这个切点没有提亮脉冲
            elif _kind.startswith("push"):
                _axis = _kind.split("_", 1)[1]
                # p: 1 → 0 在 [c-T, c] 上线性推进（c 之后夹到 0 = 完全就位）
                _p = f"max(0,min(1,({_cum:.3f}-t)/{XF_T:.3f}))"
                _xy = {"left": (f"W*{_p}", "0"),
                       "right": (f"-W*{_p}", "0"),
                       "up": ("0", f"H*{_p}"),
                       "down": ("0", f"-H*{_p}")}[_axis]
                fc.append(f"[{_acc}][{_labs[k]}]overlay=x='{_xy[0]}':y='{_xy[1]}':"
                          f"eval=frame:enable='gte(t,{_cum - XF_T:.3f})'[mx{k}]")
                CUT_KIND.append((_cum, 0.40, XF_T * 0.30, _kind))
            else:
                # ⚠️ 白闪切：就位时刻**必须比 tpad 起点早 0.1s**。实测 enable 正好等于
                #    tpad 起点（如 tpad=2.000 / enable=gte(t,2.000)）时这一路**静默失效**，
                #    B 再也不出现（画面一路停在上一镜）；1.95 有效、1.98 失效，边界很刁。
                fc.append(f"[{_acc}][{_labs[k]}]overlay=x=0:y=0:eval=frame:"
                          f"enable='gte(t,{_cum - 0.10:.3f})'[mx{k}]")
                CUT_KIND.append((_cum, 1.0, XF_T * 0.35, "flash"))
            _acc = f"mx{k}"
            print(f"  转场 镜{k}→{k + 1}: {_kind} @{_cum:.2f}s")
        vcat_label = _acc
    elif XF_MODE == "xfade" and XF_T > 0 and nseg > 1:
        _acc, _cum = _labs[0], 0.0
        for k in range(1, nseg):
            _cum += timeline[k - 1]["cut_end"] - timeline[k - 1]["start"]
            _ty = XF_FLASH_TYPE if k in XF_FLASH else XF_TYPE
            fc.append(f"[{_acc}][{_labs[k]}]xfade=transition={_ty}:duration={XF_T:.3f}:"
                      f"offset={_cum:.3f}[xf{k}]")
            _acc = f"xf{k}"
            print(f"  转场 镜{k}→{k + 1}: {_ty} {XF_T:.2f}s @{_cum:.2f}")
        vcat_label = _acc
    else:
        fc.append("".join(f"[{l}]" for l in _labs) + f"concat=n={nseg}:v=1:a=0[vcat]")
        vcat_label = "vcat"
    post = []
    if GRAIN:
        post.append(f"noise=alls={GRAIN}:allf=t+u")   # 动态颗粒，消除 AI 图塑料感
    if VIGN:
        post.append("vignette=angle=PI/5")            # 暗角，收拢视线
    pfx = ("," + ",".join(post)) if post else ""
    # 有片头钩子时去掉正片的「淡入」——钩子末镜已经是一次冲击收尾，
    # 再来 0.2s 黑起会白白吃掉一次节奏（钩子结束处硬切更利落）。淡出保留。
    # 有开场冲击包时把淡入压到 0.08s：开头要「啪」一下出来，0.2s 的黑起会把第一下吃掉。
    if HO > 0:
        _fin = ""
    elif bst:
        _fin = "fade=t=in:st=0:d=0.08,"
    else:
        _fin = "fade=t=in:st=0:d=0.2,"
    if XF_MODE in ("xfade", "mix") and XF_T > 0:
        # 兜底裁到 TOTAL：mix 的总长本来就等于 Σ镜长，这里只是把取整尾差吃掉
        fc.append(f"[{vcat_label}]trim=end={TOTAL:.4f},setpts=PTS-STARTPTS[vtrim]")
        vcat_label = "vtrim"
    # ★闪光切（默认转场，2026-10-21）：切点处打一记亮度脉冲（段尾强闪、段中弱闪），
    #   **不重叠两路画面 ⇒ 时长零变化、不会像 xfade 那样把视频流截断**。
    #   表达式按时间求值，多个切点线性相加，min(1,...) 封顶；同时拉低饱和，
    #   闪到顶时接近纯白，切换点被完全吃掉。
    _fl = ""
    if XF_MODE in ("flash", "mix") and XF_T > 0 and nseg > 1:
        if CUT_KIND:
            # mix：推镜切点只给一记轻提亮（0.40）盖住接缝，闪切点给足 1.0
            _cuts = [(c, st, w) for c, st, w, _k in CUT_KIND]
        else:
            _cuts, _cum = [], 0.0
            for k in range(1, nseg):
                _cum += timeline[k - 1]["cut_end"] - timeline[k - 1]["start"]
                _hard = k in XF_FLASH
                _cuts.append((_cum, 1.0 if _hard else 0.58, XF_T * (0.35 if _hard else 0.23)))
        _expr = "+".join(f"{st}*max(0,1-abs(t-{c:.3f})/{w:.3f})" for c, st, w in _cuts)
        _fl = (f"eq=eval=frame:brightness='min(1,{_expr})':"
               f"saturation='1-0.85*min(1,{_expr})',")
        print("  闪光切: " + " / ".join(
            f"{'强' if st >= 0.9 else '弱'}@{c:.2f}s"
            for c, st, _w in _cuts))
    fc.append(f"[{vcat_label}]{_fin}{_fl}fade=t=out:st={TOTAL-0.45:.2f}:d=0.45{pfx},format=yuv420p[vout]")

    # ---------- 3.8 开场冲击包：正片最前 BS_DUR 秒叠程序化动效 ----------
    # 纯画面层，**不新增时长**：正片镜头/字幕/口播/重点字时间轴一行都不动，只是把开头
    # 那 1 秒的画面"点燃"。链路：vout → split(冲击支/干净支) → 各元素(zoompan放大+旋转+
    # 淡出) overlay → 闪白 overlay → 衰减式抖动+推近(zoompan) → **只在冲击窗口内**用
    # 冲击支替换干净支 → [bsmix]，随后照常叠卡片与字幕。
    # ⚠️ 为什么要 split（2026-09-19 实测）：冲击支做过 zoompan（起手 1.0417 倍）＝一次
    #    重采样。若整条正片都走它，即便缩放已按 exp 衰减回 1.000，**全片仍被重采样一遍**
    #    （实测除开场外 SSIM 只有 0.976，前 10s 更低到 0.93）。分成两支后，约 1s 之后
    #    完全走干净原画，画质与没开冲击时一致。切换点不可见：那时抖动 ≈0.2px、缩放 ≈1.0008。
    vb_out = "vout"
    if bst and BS_PRESET:
        fc.append("[vout]split=2[vbsrc][vcsrc]")
        cur = "vbsrc"
        _bl = 0
        for ly in BS_PRESET["layers"]:
            _png = os.path.join(BURST_DIR, BURST_EL.get(ly["el"], ""))
            if not os.path.exists(_png):
                print(f"  开场冲击: 元素缺失 {_png}，跳过该层"
                      f"（跑 scripts/make_burst_assets.py 生成）")
                continue
            lt0 = float(ly.get("t0", 0.0))
            ldur = float(ly.get("dur", BS_DUR))
            lz0, lz1 = ly.get("z", (1.0, 3.0))
            _a = float(ly.get("a", 0.90))
            _rot = float(ly.get("rot", 0.0))
            _pw = float(ly.get("pw", 0.75))
            _fd = float(ly.get("fade", min(0.30, ldur * 0.5)))
            _N = max(2, int(round(ldur * FPS)))
            inputs += ["-loop", "1", "-t", f"{ldur:.3f}", "-i", _png]
            _bk = in_i; in_i += 1
            ch = [f"fps={FPS}", "format=rgba",
                  f"zoompan=z='{lz0:.3f}+{lz1 - lz0:.3f}*pow(min(on/{_N},1),{_pw:.3f})'"
                  f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
                  f":d=1:s=1920x1080:fps={FPS}", "format=rgba"]
            if abs(_rot) > 0.001:
                ch.append(f"rotate=angle='{_rot:.3f}*PI/180*t':ow=1920:oh=1080:c=none")
            if _fd > 0.001:
                ch.append(f"fade=t=out:st={max(0.0, ldur - _fd):.3f}:d={_fd:.3f}:alpha=1")
            if _a < 0.999:
                ch.append(f"colorchannelmixer=aa={_a:.3f}")
            fc.append(f"[{_bk}:v]" + ",".join(ch) + f"[bsl{_bl}]")
            if ly.get("blink"):
                en = "+".join(f"between(t,{lt0 + bt:.3f},{lt0 + bt + bw:.3f})"
                              for bt, bw in ly["blink"])
            else:
                en = f"between(t,{lt0:.3f},{lt0 + ldur:.3f})"
            fc.append(f"[{cur}][bsl{_bl}]overlay=0:0:format=auto:enable='{en}'[bso{_bl}]")
            cur = f"bso{_bl}"
            print(f"  开场冲击层: {ly['el']:<5s} {lt0:.2f}-{lt0 + ldur:.2f}s "
                  f"缩放{lz0:.2f}→{lz1:.2f}"
                  + (f" 旋转{_rot:g}°/s" if _rot else "")
                  + (f" 闪现{len(ly['blink'])}次" if ly.get("blink") else "")
                  + f" 不透明度{_a:.2f}")
            _bl += 1
        if bst.get("flash", True):
            for _fi, (_ft, _fw) in enumerate(BS_PRESET.get("flashes") or []):
                _ft, _fw = float(_ft), float(_fw)
                # 白闪靠 alpha 淡出做「曝光骤亮」；输入必须覆盖到 _ft 时刻才取得到帧
                inputs += ["-f", "lavfi", "-i",
                           f"color=c=white:s=1920x1080:r={FPS}:d={_ft + _fw:.3f}"]
                _fk = in_i; in_i += 1
                _fa = float(bst.get("flash_alpha", BS_PRESET.get("flash_a", 1.0)) or 1.0)
                _mix = f",colorchannelmixer=aa={_fa:.3f}" if _fa < 0.999 else ""
                fc.append(f"[{_fk}:v]format=rgba{_mix},"
                          f"fade=t=out:st={_ft:.3f}:d={_fw:.3f}:alpha=1[bsf{_fi}]")
                fc.append(f"[{cur}][bsf{_fi}]overlay=0:0:format=auto:"
                          f"enable='between(t,{_ft:.3f},{_ft + _fw:.3f})'[bsfo{_fi}]")
                cur = f"bsfo{_fi}"
            print(f"  开场冲击: 闪白 {len(BS_PRESET.get('flashes') or [])} 次")
        _sk = float(bst.get("shake", 1.0) or 0) * float(BS_PRESET.get("shake", 1.0))
        if _sk > 0:
            # 抖动 + 推近，一个 zoompan 同时做完，两者**共用同一条指数衰减曲线**。
            # ⚠️ 2026-09-19 修的真坑：早先写法是 `scale=2000:1126,crop=1920:1080:x=...`。
            #    抖动幅度确实随 exp 衰减归零、不会污染后面，**但那个 1.0417 倍的放大是恒定的**，
            #    会跟着**整条片**走 —— 实测同一帧对比，全片被永久推近 4.17%（人物/产品切边、
            #    且白丢清晰度），除开场外 SSIM 只有 0.65。现在 z 也按 exp(-4t) 衰减：
            #    起手 1.0417 倍（刚好留出抖动余量），约 1s 后回到 1.000，两全。
            #    余量自检：shake=A·e^(-4t)、可用余量≈W/2·(1-1/z)，A=13.2 时 t=0 余量 38px，
            #    t=0.3s 余量 12px（shake 已衰到 4px），全程不夹死。
            _A = 12.0 * _sk
            _zf = 1.0 + 1.0 / 24.0                       # 起手 1.04167（等价旧的 2000/1920）
            _zx = f"1+{_zf - 1.0:.5f}*exp(-4.0*on/{FPS})"
            _dx = f"{_A:.2f}*exp(-4.0*on/{FPS})*sin(47*on/{FPS})"
            _dy = f"{_A * 0.7:.2f}*exp(-4.3*on/{FPS})*sin(53*on/{FPS}+1.1)"
            _sx = f"min(max(iw/2-(iw/zoom/2)+{_dx},0),iw-iw/zoom)"
            _sy = f"min(max(ih/2-(ih/zoom/2)+{_dy},0),ih-ih/zoom)"
            fc.append(f"[{cur}]zoompan=z='{_zx}':x='{_sx}':y='{_sy}'"
                      f":d=1:s=1920x1080:fps={FPS},format=yuv420p[bshake]")
            cur = "bshake"
            print(f"  开场冲击: 抖动 强度{_sk:.2f} + 推近 1.0417→1.000（同曲线衰减，"
                  f"约 1s 归零，正片画面不受影响）")
        # 只在冲击窗口内用「叠好元素(+抖动)」的冲击支替换干净支；窗口外走干净原画。
        # 窗口取 max(BS_DUR, 1.0)：既覆盖最后一个元素/闪白，也覆盖抖动与推近的衰减尾巴。
        _sw = max(BS_DUR, 1.0)
        fc.append(f"[vcsrc][{cur}]overlay=0:0:eof_action=pass:"
                  f"enable='lt(t,{_sw:.3f})'[bsmix]")
        cur = "bsmix"
        vb_out = cur
        print(f"  开场冲击包: preset={BS_NAME} 窗口 {BS_DUR:.2f}s（总时长不变；"
              f"仅前 {_sw:.2f}s 替换正片，其余帧走干净原画）")

    # ---------- 4. 重点字: 说到才弹出, 颜色轮换, 位置自动挑画面空白处 ----------
    kw_events = []
    kw_size_default = int(cfg.get("kw_size", 120))
    # ★2026-09-24 超级重点字（用户拍板）：比普通重点字大两号、纯白、强制靠边、必带音效动效
    kw_super_size = int(cfg.get("kw_super_size", 190))          # 普通重点字 132 → 超重字 190（★2026-09-24 用户「再大一个号」：170→190）
    kw_super_color = cfg.get("kw_super_color", "&H00FFFFFF")    # ★超级重点字**起手色**（默认白）；实际按 kw_super_colors 白/黄交替
    kw_color_default = cfg.get("kw_color")      # 全片重点字统一色（覆盖 style 预设色）
    # ⚠️ 已废弃（2026-09-18 用户反馈「音效太单一」）：写它是把全片重点字统一成一个音，别再用。
    #    仅为兼容旧 config 保留；新片一律在 keywords 里逐词写 "sfx"，按词意分配不同音色。
    kw_sfx_default = cfg.get("kw_sfx_default")
    kw_min_gap = float(cfg.get("kw_min_gap", 0) or 0)   # 重点字之间最小间隔(秒)，0=不限制
    # ★2026-09-27：sfx_policy（去重/合并/邻避/密度）启用时，旧的「先到先得」节流整体让位，
    #   由新策略统一裁决（它按语义权重保留更重的那个，而不是简单地留前一个）。
    #   想退回旧行为：config 写 "sfx_policy": false。
    _pol = cfg.get("sfx_policy")
    KW_POL_ON = not (_pol is False or (isinstance(_pol, str)
                     and _pol.strip().lower() in ("false", "off", "none", "no")))
    if KW_POL_ON and float(cfg.get("kw_min_gap", 0) or 0) > 0:
        print(f"  注：kw_min_gap={cfg.get('kw_min_gap')} 已被 sfx_policy 接管（旧的先到先得节流停用）")
        kw_min_gap = 0.0
    blank_cache, occ_map = {}, {}
    # 兜底音色池（仅当某词既没写 kw.sfx、其 style 也没配 sfx 时才用）：多款轮换避免撞同一个音
    kw_pool = [n for n in ("kw_pop", "kw_ding", "kw_spring", "kw_glass", "kw_bloom")
               if n in sfx_avail]
    kw_state = {"seq": [], "prev": None}

    def next_kw_sfx():
        """重点字音效轮换（不用纯随机，避免一条片全同一个音）"""
        if not kw_pool:
            return "ding"
        if not kw_state["seq"]:
            seq = kw_pool[:]
            random.shuffle(seq)
            if len(seq) > 1 and seq[-1] == kw_state["prev"]:
                seq[0], seq[-1] = seq[-1], seq[0]
            kw_state["seq"] = seq
        pick = kw_state["seq"].pop()
        kw_state["prev"] = pick
        return pick

    def shot_source(pi):
        """该镜用于画面分析的素材 → (src, is_video, 抽帧秒, crop_y)
        ★pi 为 **1 起始镜号**（2026-09-20 修错位：旧代码当 0 起始下标，
        重点字/卡片全体后移一镜、重点字拼音在错误句里找不到又退化到「下一镜句尾」，
        表现为「话说完好几秒重点字才弹出来、还挂在下一个镜头上」）。"""
        pp = phrases[pi - 1]
        dur = max(0.2, timeline[pi - 1]["cut_end"] - timeline[pi - 1]["start"])
        kind = pp.get("kind")
        src, is_vid, ss = None, False, 0.0
        if kind == "img":
            src = pp.get("src")
        elif kind == "prod":
            src, is_vid = pp.get("video"), True
            ss = float(pp["src"][0]) + dur * 0.45
        elif kind == "imgvid":
            src, is_vid = pp.get("src"), True
            ss = dur * 0.45
        elif pp.get("bg"):                      # 抠图分层镜：用背景层估空白
            src = pp["bg"]
        cy = float(pp.get("crop_y", cfg.get("crop_y", 0)))
        return src, is_vid, ss, max(0.0, min(cy, 300.0))

    def blank_of(pi):
        """取该镜素材的能量图（按渲染裁切方式），失败返回 None"""
        if pi in blank_cache:
            return blank_cache[pi]
        src, is_vid, ss, cy = shot_source(pi)
        e = None
        if src and os.path.exists(src):
            buf = frame_gray(FF, src, is_vid, ss, cy)
            if buf:
                e = energy_map(buf)
        blank_cache[pi] = e
        return e

    # ---------- 3.9 产品详情卡片：圆角图 + 出入动画 + 自动贴主体旁边 ----------
    # 用户 2026-09-18 需求：口播说到产品特点时，把产品详情图贴进来——圆角、有进入/退出动画、
    # 贴在主体人物旁边且不压人物/物品。落点由「能量图(哪里空) + 绿色主体掩膜(哪是人)」自动求解，
    # 卡片占位写进 occ_map，后面的重点字会自动避让，两者不叠在一起。
    # 卡片 PNG 由 scripts/make_cards.py 预生成（圆角+白边+投影），产物在 <workdir>/cards/。
    # ══════════ 卡片合法性校验（2026-10-04 灿爹拍板，最高优先级）══════════
    # 【规则1 卡片来源】只有「图库里的产品详情」才可作卡片；图库里的普通分镜图
    #   （IP 场景图、餐食图、情绪图、场景空镜…）必须保持为图片元素，禁止被包装成卡片。
    #   判据＝路径必须落在产品详情来源：<品牌>/产品图库/ 或 <品牌>/<品牌>_图片/ 下的
    #   官方主图/成分表/规格图；显式白名单前缀可用 cfg["card_src_ok_prefixes"] 追加。
    # 【规则2 卡片时序】卡片只允许在「对应产品内容出现之后」跟随展示；科普段（产品点名镜
    #   之前）不得出现任何卡片；卡片不得提前渲染、不得独立悬浮。
    #   产品点名镜＝首个文案出现品牌名或产品型号的镜。卡片 phrase 必须 ≥ 该镜号。
    # 两条规则冲突时以此为准，高于本文件其余一切落点/延长/自动定位逻辑。
    # ══════════════════════════════════════════════════════════════════
    def _is_product_detail_path(path):
        """规则1（★白名单制）：判断 src 是否为「真产品详情图」。
        ⚠️ 2026-10-04 二次收紧：原判据「文件名含 产品_/营养_/成分… 或位于品牌目录」
           会把**小绿人分镜图**（如 `产品_葡萄籽虾青素_中景.png`，IP 站在厨房里）
           误判为产品详情 ⇒ 画面上出现"分镜图做成卡片"。实测踩坑。
        ★ 真正的产品详情只有两类来源：
             ① **素材盘官方产品图库**：路径含 `<品牌>/产品图库/`（灿爹实拍/官方主图/
                成分表/规格图），这是唯一权威来源；
             ② **显式白名单**：config 写 `card_src_ok`（绝对路径列表）或
                `card_src_ok_prefixes`（目录前缀），用于登记「营养成分表卡」这类
                由官方图裁切而来的衍生卡。
           品牌分镜图目录（<品类>/<品牌>/图片/）里的 `产品_*.png` **一律不作卡片**，
           它们是分镜素材，卡片化会让同一画面既当图片镜又当卡片（画面重复且语义不符）。
        """
        if not path:
            return False
        ap = os.path.abspath(path)
        # ① 素材盘官方产品图库（权威）
        if os.sep + "产品图库" + os.sep in ap:
            return True
        # ② 显式白名单：精确路径
        for ok in cfg.get("card_src_ok", []) or []:
            if ap == os.path.abspath(ok):
                return True
        # ③ 显式白名单：目录前缀
        for pref in cfg.get("card_src_ok_prefixes", []) or []:
            if ap.startswith(os.path.abspath(pref)):
                return True
        return False

    def _product_named_shot():
        """规则2：返回首个「点名产品」的镜号（1-based），无则 None。
        点名＝出现品牌名（cfg.brand，逗号分隔）；无 brand 时退化为
        「产品型号词 + 品类/规格词」同句共现（如「诺特兰德烟酰胺片」）。
        ★不可用单一品类词（"维生素/元素/片"）判定——「微量元素」这类
        科普用词会把它误判成产品点名，导致科普段被当成产品段（实测 A 条镜5 误判）。"""
        brands = [b.strip() for b in (cfg.get("brand") or "").split(",") if b.strip()]
        # 品类/剂型词（需与型号/品牌词共现才算点名）
        cat = r"(?:维生素|烟酰胺|肌酸|钙|片|胶囊|软糖|冲剂|蛋白粉|奶昔|粉)"
        for i, tp in enumerate(timeline, 1):
            t = tp.get("text", "") if isinstance(tp, dict) else ""
            if not t:
                continue
            if brands:
                if any(b in t for b in brands):
                    return i
                continue
            # 无 brand：必须「中文词 + 品类词」相邻共现
            if re.search(r"[\u4e00-\u9fa5]{2,8}" + cat + r"[\u4e00-\u9fa5]{0,4}" + cat, t):
                return i
        return None

    _prod_shot = _product_named_shot()
    _raw_cards = list(cfg.get("product_cards", []))
    _legal, _rejected = [], []
    for _cd in _raw_cards:
        _src = _cd.get("src")
        if not _is_product_detail_path(_src):
            _rejected.append((_cd, "规则1：非图库产品详情来源，禁止作卡片"))
            continue
        _pi = int(_cd.get("phrase", 0))
        if _prod_shot is not None and _pi < _prod_shot:
            _rejected.append((_cd, f"规则2：卡片镜{_pi} 早于产品点名镜{_prod_shot}（科普段不得有卡片）"))
            continue
        _legal.append(_cd)
    for _cd, _why in _rejected:
        print(f"  ❌ 卡片被拦截（{_why}）：{os.path.basename(str(_cd.get('src')))}"
              f" @镜{_cd.get('phrase')} —— 该图应作为普通图片镜使用")
    if _prod_shot:
        print(f"  卡片门禁：产品点名镜=镜{_prod_shot}｜合法 {len(_legal)} / 原始 {len(_raw_cards)}")
    cfg = dict(cfg)
    cfg["product_cards"] = _legal

    cards = []
    CARD_POS = {
        "tl": lambda w, h: (60, 60),
        "tr": lambda w, h: (1920 - w - 60, 60),
        "bl": lambda w, h: (60, 1080 - h - 250),        "br": lambda w, h: (1920 - w - 60, 1080 - h - 250),
    }
    for _ci, cd in enumerate(cfg.get("product_cards", [])):
        csrc = cd.get("src")
        if not csrc:
            continue
        cmeta = card_meta.get(os.path.abspath(csrc)) or {}
        prepped = bool(cmeta.get("png"))
        cpng = os.path.join(workdir, "cards", cmeta["png"]) if prepped else csrc
        # ★2026-09-19 修：「详情图只出来一部分」的根因就在这里。
        #   旧写法是 cmeta 为空就直接 csrc，而 csrc 是**原始像素尺寸**的详情图（常 1170x930），
        #   overlay 又完全不做缩放 → 卡片以原生大小盖在 (1300,380)，右/下被画面切掉。
        #   现在只要没有预处理元数据，就按目标宽自动等比缩放：最多损失圆角/投影，
        #   **绝不会再超出画面**。（根因是漏跑 scripts/make_cards.py，日志会提示去跑。）
        need_scale = not prepped
        if prepped and not os.path.exists(cpng):
            print(f"  WARN 卡片 PNG 丢失({cmeta.get('png')}) → 退回原图并按宽自动缩放")
            cpng, need_scale = csrc, True
        if need_scale:
            _w = int(cd.get("w") or 450)
            print(f"  WARN 卡片未预处理({os.path.basename(csrc)}) → 自动缩放到 {_w}px 宽"
                  f"（无圆角/无投影）；要圆角卡片请先跑 scripts/make_cards.py")
        # ★2026-09-20 修：config 的 phrase 是 **1 起始镜号**，旧代码 timeline[pi] 按 0 起始取，
        #   卡片整体后移一镜（本该贴「认准含量」镜的卡贴到了下一镜）。现在统一按 1 起始换算。
        pi = int(cd["phrase"])
        first = max(0, pi - 1)
        clast = max(first, min(pi + int(cd.get("span", 1)) - 2, len(timeline) - 1))
        ct0 = cd.get("t0")
        ct0 = float(ct0) if ct0 is not None else timeline[first]["start"] + float(cd.get("in_off", 0.45))
        ct1 = cd.get("t1")
        ct1 = float(ct1) if ct1 is not None else timeline[clast]["cut_end"] - float(cd.get("out_off", 0.15))
        # ★2026-09-30 新增（灿爹拍板「详情卡不能一闪而过，以后都不能闪太快」）：
        #   详情卡承担「看清含量/纯度/规格」的职能，展示太短等于白贴。
        #   默认最短 CARD_MIN=2.0s（config 可用 card_min_sec 覆盖）。
        #   不足时自动向后延长、覆盖后续镜，直到达标或到片尾（显式写 t1 的卡不干预）。
        _card_min = float(cfg.get("card_min_sec", 2.0))
        if cd.get("t1") is None:
            _ct1_0 = ct1
            _ex = clast
            while (ct1 - ct0) < _card_min and _ex < len(timeline) - 1:
                _ex += 1
                ct1 = timeline[_ex]["cut_end"] - float(cd.get("out_off", 0.15))
            if _ex != clast:
                print(f"  卡片 {os.path.basename(csrc)} 展示仅 {_ct1_0 - ct0:.2f}s < {_card_min:.1f}s "
                      f"→ 自动延长覆盖到镜 {_ex + 1}（{ct1 - ct0:.2f}s）")
                clast = _ex
        vis_w = int(cmeta.get("vis_w") or cd.get("w") or 450)
        vis_h = int(cmeta.get("vis_h") or round(vis_w * 0.82))
        cpad = int(cmeta.get("pad", 0))
        bw = max(4, int(round(vis_w / 10.0)))
        bh = max(3, int(round(vis_h / 10.0)))
        cpos = cd.get("pos", "auto")
        cx = cyy = None
        if isinstance(cpos, (list, tuple)):
            cx, cyy = int(cpos[0]), int(cpos[1])
        elif isinstance(cpos, str) and cpos.lower() in CARD_POS:
            cx, cyy = CARD_POS[cpos.lower()](vis_w, vis_h)
        if cx is not None:
            occ_map.setdefault(pi, []).append((max(0, cx // 10 - 2), max(0, cyy // 10 - 2), bw, bh))
            print(f"  卡片 {os.path.basename(csrc)} 指定位置 ({cx},{cyy}) {vis_w}x{vis_h}")
        else:
            sr, is_vid, ss, scy = shot_source(pi)
            body = head = None
            if sr and os.path.exists(sr):
                rb = frame_rgb(FF, sr, is_vid, ss, scy)
                if rb:
                    body, head = person_masks(rb)
            best = place_card(blank_of(pi), body, head, bw, bh, occ_map.setdefault(pi, []),
                              y_top=int(cfg.get("card_y_top", 2)),
                              y_bot=int(cfg.get("card_y_bot", 88)))
            if best:
                _, sx, sy, en, ob, oh_ = best
                cx, cyy = sx * 10, sy * 10
                occ_map[pi].append((sx, sy, bw, bh))
                print(f"  卡片 {os.path.basename(csrc)} 自动落位 空白度={en:.1f}(越低越空) "
                      f"主体重叠={ob}格 头部重叠={oh_}格 → ({cx},{cyy}) {vis_w}x{vis_h}")
            else:
                cx, cyy = 1920 - vis_w - 60, 80
                occ_map[pi].append((max(0, cx // 10 - 2), max(0, cyy // 10 - 2), bw, bh))
                print(f"  卡片 {os.path.basename(csrc)} 素材分析失败 → 回退右上 ({cx},{cyy})")
        # 几何自检：卡片必须**完整落在画面内**（详情图尺寸千奇百怪，不能靠假设）
        if cx + vis_w > 1920 or cyy + vis_h > 1080 or cx < 0 or cyy < 0:
            nx = min(max(0, cx), 1920 - vis_w)
            ny = min(max(0, cyy), 1080 - vis_h)
            print(f"  WARN 卡片超出画面 ({cx},{cyy}) {vis_w}x{vis_h} → 夹回 ({nx},{ny})")
            cx, cyy = nx, ny
        cards.append({"png": cpng, "vis_x": cx, "vis_y": cyy,
                      "png_x": cx - cpad, "png_y": cyy - cpad,
                      "png_w": int(cmeta.get("w") or vis_w), "t0": ct0, "t1": ct1,
                      # 动画默认跟随预处理样式：sticker 贴纸图 → 贴纸动效；圆角卡片 → 滑入
                      # （config 里显式写 anim 永远优先）
                      "anim": (cd.get("anim") or cmeta.get("style") or "slide").lower(),
                      "float": cd.get("float"), "float_period": cd.get("float_period"),
                      "sfx": cd.get("sfx"),
                      "scale_w": vis_w if need_scale else 0,
                      "da": float(cd.get("dur_anim", 0.45)), "src": csrc})

    # ★★2026-10-02 修（灿爹：「视频里有详情卡重叠的情况，不要重叠」——抹掉条仍然复现）：
    #   根因＝上面那条「详情卡不足最短时长 → 自动向后延长覆盖后续镜」（#112）在**后续镜自己也有卡片**
    #   时会造出两张卡同屏。实测 21金维他维生素B s3（抹掉）：镜11「三瓶」卡 18.25–22.20s 与
    #   镜12「成分表」卡 20.29–24.42s **同为右侧、重叠 1.91s**（落位仅差 80/34px ⇒ 肉眼就是糊成一坨）。
    #   ⇒ 全局去重叠：按 t0 排序，前一张的 t1 一律不得晚于后一张 t0 − card_gap_sec；
    #     显式写了 t1 的卡同样受约束（两张叠在一起比短一点糟得多）。
    _card_gap = float(cfg.get("card_gap_sec", 0.25))
    if len(cards) > 1:
        _cs = sorted(cards, key=lambda c: c["t0"])
        for _a, _b in zip(_cs, _cs[1:]):
            _lim = _b["t0"] - _card_gap
            if _a["t1"] > _lim:
                print(f"  卡片去重叠: {os.path.basename(_a['src'])} {_a['t1']:.2f}s → {_lim:.2f}s"
                      f"（让位后一张 {os.path.basename(_b['src'])} {_b['t0']:.2f}s）")
                _a["t1"] = _lim
                if _lim - _a["t0"] < 0.60:
                    print(f"    ⚠️ 该卡仅剩 {_lim - _a['t0']:.2f}s（两卡起始太近，建议错开镜号）")

    # ★2026-09-24 超级重点字配色：**白 / 黄交替**（用户拍板「白色和我之前的黄色交叉来显示」）。
    #   按 keywords 里 super 的出现次序轮转取色，第 1 个白、第 2 个黄、第 3 个白、第 4 个黄…
    #   字号/落位/动效/音效一律不变（仍走 super 那套），**只换颜色**。
    #   想改序或加成三色：config 写 "kw_super_colors": ["&H00FFFFFF", "&H0000D4FF", ...] 即可。
    _kw_super_colors = (cfg.get("kw_super_colors")
                        or [kw_super_color, kw_color_default or "&H0000D4FF"])
    _kw_super_seen = 0

    for j, kw in enumerate(cfg.get("keywords", [])):
        # ★2026-09-24 超级重点字：kw 里写 "super": true（或 "tier":"super"）即启用——
        #   全片级超级重点（例：点题句「今天我们讲，肌酸」里的产品名）。
        #   字号大两号 / 默认纯白 / 落位强制左右两侧不压人物 / 音效必加且不受 5s 节流 / hero 弹跳动效。
        is_super = bool(kw.get("super")) or str(kw.get("tier", "")).lower() in ("super", "hero")
        # ★2026-09-20 修错位：config 的 phrase 是 **1 起始镜号**，旧代码 timeline[pi] 按 0 起始取，
        #   重点字全体落到下一镜；拼音又在错误句里找不到 → 退化成「下一镜句尾」，
        #   表现为「话说完好几秒重点字才弹出来，还挂在下一个镜头上」。
        pi = int(kw["phrase"])
        t = timeline[pi - 1]
        pc, kc = pchars(t["text"]), pchars(kw["kw"])
        hit = next((i for i in range(len(pc) - len(kc) + 1) if pc[i:i+len(kc)] == kc), None)
        off = hit if hit is not None else 0   # 找不到拼音回退**镜首**（旧写法回退句尾，重点字迟到）
        t0 = t["ctimes"][off]
        if kw_min_gap > 0 and not is_super:      # ★超级重点字豁免节流：用户要求「一定要加音效」
            near = next((e for e in kw_events if abs(e["t0"] - t0) < kw_min_gap), None)
            if near:
                # ★2026-09-23 改策略：旧写法「整条丢弃」，导致配音一变短，相隔 5s 内的重点字
                #   连字都不见了（本次「高含量」就被丢掉）。动效音效铁律只约束**声音**，
                #   所以默认改为「保留视觉、只掐音效」（kw_gap_policy="visual"）；
                #   确实想要旧行为就写 "kw_gap_policy": "drop"。
                if str(cfg.get("kw_gap_policy", "visual")).lower() == "drop":
                    print(f"  KW '{kw['kw']}' @ {t0:.2f}s 丢弃 —— 距重点字「{near['kw']}」"
                          f"{abs(near['t0'] - t0):.2f}s (<{kw_min_gap:.1f}s)，避免动效音效在 5 秒内响两次")
                    continue
                print(f"  KW '{kw['kw']}' @ {t0:.2f}s 距「{near['kw']}」仅 "
                      f"{abs(near['t0'] - t0):.2f}s (<{kw_min_gap:.1f}s) → 保留视觉、去掉音效")
                _kw_no_sfx = True
            else:
                _kw_no_sfx = False
        else:
            _kw_no_sfx = False
        st = (kw.get("style") or ("hero" if is_super else "")).lower()
        st_def = KW_STYLE.get(st, {})
        # 超级重点字：按出现次序在白/黄之间轮转（kw 显式写 color 永远优先）
        _kw_super_col = None
        if is_super:
            _kw_super_col = _kw_super_colors[_kw_super_seen % len(_kw_super_colors)]
            _kw_super_seen += 1
        color = (kw.get("color") or _kw_super_col
                 or kw_color_default or st_def.get("color")
                 or PALETTE[j % len(PALETTE)])
        size = int(kw.get("size") or (kw_super_size if is_super else kw_size_default))
        pos = kw.get("pos")
        if not pos and IMG_KW_POS:
            pos = IMG_KW_POS[j]
        if not pos:
            wpx, hpx = kw_box_px(kw["kw"], size)
            bw = min(max(4, int(round(wpx / 10.0))), GRID_W - 8)
            bh = min(max(3, int(round(hpx / 10.0))), GRID_H - 10)
            e = blank_of(pi)
            # ★超级重点字：只允许落在左右两侧带，正中留给人物主体（绝不挡人）
            _bands = (cfg.get("kw_super_x_bands", [[0.05, 0.30], [0.70, 0.95]])
                      if is_super else cfg.get("kw_x_bands"))
            spot = pick_blank_spot(e, bw, bh, occ_map.setdefault(pi, []),
                                   y_top=int(cfg.get("kw_y_top", 4)),
                                   y_bot=int(cfg.get("kw_y_bot", 66)),
                                   x_bands=_bands) if e else None
            if spot:
                sx, sy, en = spot
                occ_map[pi].append((sx, sy, bw, bh))
                pos = (int((sx + bw / 2.0) * 10), int((sy + bh / 2.0) * 10))
                print(f"  KW '{kw['kw']}' 自动落位 空白度={en:.1f}(越低越空) 框={wpx}x{hpx}px")
            else:
                pos = (DW_SIDE_POS if is_super else DEFAULT_POS[j % len(DEFAULT_POS)])
                print(f"  KW '{kw['kw']}' 空白检测失败(素材/取帧问题)，回退默认位 {pos}")
        want = None if _kw_no_sfx else (kw.get("sfx") or kw_sfx_default or st_def.get("sfx"))
        sfx_name = (want if (want and want in sfx_avail)
                    else (None if _kw_no_sfx else next_kw_sfx()))
        kw_events.append({"kw": kw["kw"], "t0": t0, "color": color, "x": pos[0], "y": pos[1],
                          "sfx": sfx_name, "tilt": kw.get("tilt"), "super": is_super,
                          "size": (kw.get("size") or (kw_super_size if is_super else None)),
                          "style": st, "bord": kw.get("bord"),
                          "type_step": kw.get("type_step"), "bar_color": kw.get("bar_color"),
                          "end": min(max(t["cut_end"] + 0.3, t0 + 1.2), t0 + 2.6)})
        print(f"KW '{kw['kw']}' @ {t0:.2f}s {color} pos={pos} sfx={sfx_name} "
              f"style={st or 'pop(默认)'}{('·' + st_def['cn']) if st_def else ''}"
              f"{'  ★超级重点字' if is_super else ''} 字号={size}")

    # ---------- 3.95 纯音效触发词（无视觉，2026-09-29）----------
    # config 写 "trigger_sfx": [{"kw":"玩手机","sfx":"timi"}] —— 只响音效、不弹大字不动画面。
    # phrase 可省略：省略时全片逐镜扫描，每次出现都触发。
    # 默认优先级 0（与重点字同级、参加 5s 响亮互斥）；写 "priority": 3 可降为轻音。
    # ★anchor（2026-09-29 二次拍板）："kw"=说到该词那一刻（默认，词在句尾时听着像台词说完才响）
    #   "shot"=含触发词的镜**开口第一字**就响（与台词同步）；"lead": N 可再提前 N 秒（夹到 ≥0）。
    trig_events = []
    for tg in cfg.get("trigger_sfx", []):
        tk, tn = "".join(pchars(str(tg.get("kw", "")))), str(tg.get("sfx", ""))
        if not tk:
            print(f"  trigger_sfx 跳过: kw 为空")
            continue
        if tn not in sfx_avail:
            print(f"  trigger_sfx 跳过: 音色 {tn!r} 不在音效池（查 assets/sfx/ 与 sfx_files）")
            continue
        _anchor = str(tg.get("anchor", "kw")).lower()
        _lead = max(0.0, float(tg.get("lead", 0) or 0))
        _pr = int(tg.get("priority", 0))
        _scope = [int(tg["phrase"])] if tg.get("phrase") else range(1, len(timeline) + 1)
        _n = 0
        for pi in _scope:
            if not (1 <= pi <= len(timeline)):
                continue
            t = timeline[pi - 1]
            pc = "".join(pchars(t["text"]))    # pchars 返回 list → 拼回 str 好用 find
            if tk not in pc:
                continue
            if _anchor == "shot":              # 镜开口第一字，每镜只响一次
                trig_events.append((max(0.0, t["ctimes"][0] - _lead), tn, _pr,
                                    f"触发词「{tg['kw']}」镜{pi}·镜首"))
                _n += 1
                continue
            start = 0
            while True:
                hit = pc.find(tk, start)
                if hit < 0:
                    break
                trig_events.append((max(0.0, t["ctimes"][hit] - _lead), tn, _pr,
                                    f"触发词「{tg['kw']}」镜{pi}"))
                _n += 1
                start = hit + len(tk)
        print(f"  trigger_sfx: 「{tg['kw']}」→ {tn}  命中 {_n} 处  (anchor={_anchor}, lead={_lead}s)")

    # ---------- 4.5 全屏大字标题卡 (参考片 OMEGA-3 同款) ----------
    title_cards = []
    for tc in cfg.get("title_cards", []):
        t0 = tc.get("t0")
        if t0 is None:
            t0 = timeline[int(tc["phrase"]) - 1]["start"] + tc.get("offset", 0.15)  # ★phrase 为 1 起始镜号
        dur = tc.get("dur", 1.6)
        title_cards.append({"text": tc["text"], "t0": t0, "end": t0 + dur,
                            "color": tc.get("color", "&H0000FFFF"),
                            "size": tc.get("size", 185), "y": tc.get("y", 440),
                            "tilt": tc.get("tilt"), "sfx": tc.get("sfx", "impact")})
        print(f"TITLE '{tc['text']}' @ {t0:.2f}-{t0+dur:.2f}s")

    # ---------- 5. ASS 字幕 ----------
    kw_font = cfg.get("kw_font") or KW_FONT_DEFAULT
    # 新青年体本身是 W8 超粗字重 → 不再叠 Bold/Italic 合成粗斜，避免边缘发虚
    kw_bold, kw_ital = int(cfg.get("kw_bold", 0)), int(cfg.get("kw_italic", 0))
    # ── 逐字卡拉OK字幕（2026-10-13 灿爹「字幕用我最新的，逐字卡拉OK」）──
    # 样式来源：assets/caption_fx Demo B（米纸底 #F5EFE2 / 灰米未填充 #CFC4AC /
    # 墨黑已填充 #1A1A1A / 重点字红 #D8331F + 弹跳 1.35 倍）；逐字 steps 硬切
    # = ASS \k（libass 原生渲染）。config: sub_karaoke=true 开启，老片零影响。
    # ══ 字幕效果体系（2026-10-13 灿爹定稿：7 款入库，未指定时每片随机一款出片）══
    #   config.sub_style: karaoke|sweep|bounce|cinema|poster|minimal|neon|"random"(默认)|"off"
    #   随机 = 渲染时 random.choice，选中项打进日志可追溯；要复现某片就在 config/.kw.json 写死 style id。
    #   sub_style:"off" 或 sub_karaoke:false → 关特效回退普通 Main 字幕。
    _SUB_STYLES = {
        "karaoke": "逐字卡拉OK（米纸底·墨黑填充）",
        "sweep":   "卡拉OK＋高光扫过",
        "bounce":  "弹跳逐字",
        "cinema":  "黑条影院＋卡拉OK",
        "poster":  "大字报抖动",
        "minimal": "极简小字＋大金句（双行）",
        "neon":    "霓虹微光",
    }
    _ss_req = str(cfg.get("sub_style", "random")).strip().lower()
    if _ss_req in ("off", "none", "plain", "false", "0"):
        SUB_STYLE = None
    elif _ss_req in _SUB_STYLES:
        SUB_STYLE = _ss_req
    else:
        SUB_STYLE = random.choice(sorted(_SUB_STYLES))
    KARAOKE = bool(cfg.get("sub_karaoke", SUB_STYLE is not None))
    if not KARAOKE:
        SUB_STYLE = None
    K_BG = cfg.get("karaoke_bg", "&H00E2EFF5")       # 米纸底（不透明盒）
    K_UNF = cfg.get("karaoke_unfilled", "&H00ACC4CF")  # 灰米·未填充
    K_FILL = cfg.get("karaoke_filled", "&H001A1A1A")   # 墨黑·已填充
    # ★2026-10-13 灿爹指令：重点字红改黄（对齐宣言金 #F2C14E）。
    #   ⚠️键名不能用 karaoke_kw——那已被重点词表(列表)占用，曾把列表写进 \2c 变成废标签。
    K_KW = cfg.get("karaoke_kw_color", "&H004EC1F2")   # 重点字黄 #F2C14E
    K_PAD = cfg.get("karaoke_pad", 9)                  # 米纸盒内边距（Outline）
    K_POP = cfg.get("karaoke_pop", True)               # 重点字弹跳
    _NEON = "&H005AD5FF"                               # 霓虹琥珀光 #FFD55A
    # 每款调色/盒型（槽位沿用既有实测结论：Primary=未唱色，Secondary=已唱色）
    _PAL = {
        "karaoke": dict(bg=K_BG, unf=K_UNF, fill=K_FILL, out="&H00000000", box=True, pad=K_PAD),
        "sweep":   dict(bg=K_BG, unf=K_UNF, fill=K_FILL, out="&H00000000", box=True, pad=K_PAD),
        "bounce":  dict(bg=K_BG, unf=K_UNF, fill=K_FILL, out="&H00000000", box=True, pad=K_PAD),
        "poster":  dict(bg=K_BG, unf=K_UNF, fill=K_FILL, out="&H00000000", box=True, pad=K_PAD),
        "cinema":  dict(bg="&H00121212", unf="&H00989898", fill="&H00FFFFFF", out="&H00000000", box=True, pad=10),
        "minimal": dict(bg="&H00000000", unf="&H00C9D3D9", fill="&H00FFFFFF", out="&H00201E1C", box=False, outline=2),
        "neon":    dict(bg="&H00000000", unf="&H00C9D3D9", fill="&H00FFFFFF", out=_NEON, box=False, outline=2),
    }
    print(f"  字幕效果: {SUB_STYLE}（{_SUB_STYLES[SUB_STYLE]}）" if SUB_STYLE else "  字幕效果: 关（普通字幕）", flush=True)

    def _karaoke_style():
        if not KARAOKE or SUB_STYLE is None:
            return ""
        P = _PAL[SUB_STYLE]
        # ★2026-10-25 修（液体钙 261025 实测）：BorderStyle=3（不透明盒）时 libass 用的是
        #   **OutlineColour** 画盒底，不是 BackColour。旧写法把「米纸底」塞在 BackColour、
        #   OutlineColour 固定写黑 ⇒ karaoke/sweep/bounce/poster 四款的盒子全变成**纯黑条**
        #   （与 #197 文档「米纸底·墨黑填充」不符，成片里字幕像贴了黑胶布）。
        #   修法：盒型把 bg 写进 OutlineColour 槽；非盒型维持原 out（细描边色）。
        _boxcol = P['bg'] if P['box'] else P['out']
        s = (f"Style: Karaoke,{cfg.get('sub_font','Heiti SC')},{cfg.get('sub_size',60)},"
             f"{P['unf']},{P['fill']},{_boxcol},{P['bg']},-1,0,0,0,100,100,0,0,"
             f"{3 if P['box'] else 1},{P.get('pad', K_PAD) if P['box'] else P.get('outline', 2)},0,2,60,60,58,1")
        if SUB_STYLE == "minimal":   # 底部大金句行（动画 clip 左→右扫出，层级 1）
            s += (f"\nStyle: KWBig,{cfg.get('sub_font','Heiti SC')},{int(cfg.get('sub_size', 60)) * 2},"
                  f"&H004EC1F2,&H004EC1F2,&H001A1A1A,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,2,60,60,12,1")
        return s

    _KW_PUNCT = re.compile(r'''[，。！？、：；—…·～「」『』《》""''（）()\s]''')

    def _karaoke_text(text, ctimes, t0, kw_idx, kw_runs_out=None):
        r"""逐字 \k 串：ctimes=该句每个发音字符的起始秒；kw_idx=重点字发音索引集。
        标点不占语音时间（\k1 瞬切）；按 SUB_STYLE 分发七款逐字效果（相对行起点，绝对 ms \t）。
        kw_runs_out：minimal 款时收集重点词连续段 [(文本, 起秒, 止秒)]，供大金句双行发射。"""
        spans, idx = [], 0
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == "\\" and i + 1 < len(text) and text[i + 1] == "N":
                spans.append(("\\N", 0, False, None))   # ASS 折行标记：原样透传，不占 \k
                i += 2
                continue
            if ch in "\n\r":                            # 真换行符：同样直出
                spans.append((ch, 0, False, None))
                i += 1
                continue
            if _KW_PUNCT.match(ch):
                spans.append((ch, 1, False, None))
                i += 1
                continue
            s = ctimes[idx] if idx < len(ctimes) else (ctimes[-1] if ctimes else t0)
            e = ctimes[idx + 1] if idx + 1 < len(ctimes) else None
            cs = max(1, int(round(((e if e is not None else s + 0.18) - s) * 100)))
            spans.append((ch, cs, idx in kw_idx, s))
            idx += 1
            i += 1
        out, cur = [], None                              # cur=[文本,起秒,止秒] 收集连续重点段
        for ch, cs, is_kw, s in spans:
            if cs == 0:                          # 折行符直出
                out.append(ch)
                continue
            a = max(0, int(round((s - t0) * 1000))) if s is not None else 0
            ae = a + cs * 10                     # 本字唱完时刻（\k 百分秒→ms）
            if is_kw:
                if cur is None:
                    cur = [ch, s if s is not None else t0, (s if s is not None else t0) + cs / 100.0]
                else:
                    cur[0] += ch
                    cur[2] = (s if s is not None else cur[2]) + cs / 100.0
            elif cur is not None:
                kw_runs_out is not None and kw_runs_out.append(tuple(cur))
                cur = None
            if SUB_STYLE == "sweep":             # 唱完瞬间白光一闪再沉回已唱色（⚠️打\2c：本机 libass 实测 Secondary=已唱槽位）
                back = K_KW if is_kw else _PAL['sweep']['fill']
                out.append(f"{{{'\\2c' + K_KW + '&' if is_kw else ''}\\k{cs}"
                           f"\\t({ae},{ae + 90},\\2c&HFFFFFF&)\\t({ae + 90},{ae + 320},\\2c{back}&)}}{ch}")
            elif SUB_STYLE == "bounce":          # 每字出场带过冲弹跳
                col = f"\\2c{K_KW}&" if is_kw else ""
                out.append(f"{{{col}\\k{cs}\\t({a},{a + 140},\\fscx126\\fscy126)"
                           f"\\t({a + 140},{a + 320},\\fscx100\\fscy100)}}{ch}")
            elif SUB_STYLE == "poster":          # 重点字砸下抖三抖，普通字走原样
                if is_kw:
                    out.append(f"{{\\2c{K_KW}&\\k{cs}"
                               f"\\t({a},{a + 160},\\fscx158\\fscy158)\\t({a + 160},{a + 250},\\fscx90\\fscy90)"
                               f"\\t({a + 250},{a + 340},\\fscx114\\fscy114)\\t({a + 340},{a + 500},\\fscx100\\fscy100)}}{ch}")
                else:
                    out.append(f"{{\\k{cs}}}{ch}")
            elif SUB_STYLE == "neon":            # 入字时琥珀光晕呼吸，随后收干净
                col = f"\\2c{K_KW}&" if is_kw else ""
                out.append(f"{{{col}\\k{cs}\\bord5\\blur10\\3c{_NEON}&"
                           f"\\t({a},{a + 260},\\bord{max(2, _PAL['neon']['outline'])}\\blur0\\3c{_NEON}&)}}{ch}")
            elif SUB_STYLE == "minimal":         # 上行小字：已唱白/未唱灰，重点字金
                out.append((f"{{\\2c{K_KW}&\\k{cs}}}{ch}") if is_kw else f"{{\\k{cs}}}{ch}")
            elif is_kw and K_POP and s is not None:   # karaoke / cinema：重点字弹跳
                out.append(f"{{\\2c{K_KW}&\\k{cs}"
                           f"\\t({a},{a + 170},\\fscx135\\fscy135)"
                           f"\\t({a + 170},{a + 420},\\fscx100\\fscy100)}}{ch}")
            elif is_kw:
                out.append(f"{{\\2c{K_KW}&\\k{cs}}}{ch}")
            else:
                out.append(f"{{\\k{cs}}}{ch}")
        if cur is not None and kw_runs_out is not None:
            kw_runs_out.append(tuple(cur))
        return "".join(out)

    def _kw_char_idx(text, kws):
        """重点词 → 发音字符索引集（跳过标点计数，与 ctimes 对齐）"""
        hit = set()
        for kw in kws:
            start = 0
            while True:
                p = text.find(kw, start)
                if p < 0:
                    break
                for i in range(p, p + len(kw)):
                    if not _KW_PUNCT.match(text[i]):
                        hit.add(sum(1 for x in text[:i] if not _KW_PUNCT.match(x)))
                start = p + len(kw)
        return hit

    ass = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,{cfg.get('sub_font','Heiti SC')},{cfg.get('sub_size',60)},{cfg.get('sub_primary','&H00FFFFFF')},{cfg.get('sub_secondary','&H000000FF')},{cfg.get('sub_outline_colour','&H000000FF')},&H00000000,-1,0,0,0,100,100,0,0,1,{cfg.get('sub_outline',3)},{cfg.get('sub_shadow',0)},2,60,60,58,1
Style: Big,{kw_font},{cfg.get('kw_size',120)},&H00FFFFFF,&H000000FF,&H00101010,&H70000000,{kw_bold},{kw_ital},0,0,100,100,2,0,1,8,4,5,60,60,0,1
{_karaoke_style()}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    def hl(text):
        for h in cfg.get("sub_highlights", []):
            k = h["kw"]
            if k in text:
                text = text.replace(k, "{\\c" + h.get("color", "&H0000FFFF") + "&}" + k + "{\\c&HFFFFFF&}")
        return text

    # 字幕自动换行：按版面可用宽度算出每行最大字符数，长句主动插入 \\N，避免横向溢出。
    # （即便 WrapStyle 已设 0 智能换行，这里显式断句能让断点落在标点/关键词边界，更自然）
    sub_size = cfg.get("sub_size", 60)
    _ml, _mr = 60, 60  # 与 Style 的 MarginL/MarginR 保持一致
    per_line = max(8, int((1920 - _ml - _mr) / sub_size * 0.85))
    _protect = [h["kw"] for h in cfg.get("sub_highlights", [])]
    _protect += [k["kw"] for k in cfg.get("keywords", []) if "kw" in k]
    _protect = list(dict.fromkeys(_protect))  # 去重保序

    # 逐句卡拉OK式显隐：每句独立时间块，说完(下句起点)即消失；加短淡出强化"说完消失"手感。
    fade = cfg.get("sub_fade", "120,180")  # 入场120ms / 出场180ms 淡入淡出
    # ★2026-09-19 字幕拆分：超长句在标点/关键词边界拆成连续多条字幕，
    # 时间在原句窗口 [start, cut_end] 内按可见字数比例切分——不动音频、不动镜头时间轴。
    # ★2026-09-24 用户拍板「一句一屏」：默认 max_lines=1——屏幕上永远只有一排字幕，
    # 长句在标点处切成连续字幕块（说完一块换下一块），逐字卡点/重点字/字号不变。
    # config 里 sub_max_lines: 2 可回退旧两行堆叠样式。
    max_lines = max(1, int(cfg.get("sub_max_lines", 1)))

    def _chunk_text(text):
        """把超长句按标点单元切成连续字幕块（逐块模拟折行，保证每块 ≤ max_lines 行）；
        块尾若停在无标点的半句，把尾单元顺延到下一块，让分块边界尽量落在标点处。"""
        PUNCT = "，。、！？；：,.!?;: \n\t—"

        def ok(us):
            return len(cjk_wrap_lines("".join(us), per_line, _protect)) <= max_lines

        # 1) 贪心：逐单元累积，一旦折行超限就把当前块收口
        groups, cur = [], []
        for x in _cjk_units(text, _protect):
            if cur and not ok(cur + [x]):
                groups.append(cur)
                cur = [x]
            else:
                cur.append(x)
        if cur:
            groups.append(cur)
        # 2) 边界美化：块尾无标点（半句被切断）则把尾单元顺延到下一块
        for i in range(len(groups) - 1):
            while groups[i] and not (groups[i][-1][-1] in PUNCT):
                cand_i, cand_next = groups[i][:-1], [groups[i][-1]] + groups[i + 1]
                if cand_i and ok(cand_i) and ok(cand_next):
                    groups[i], groups[i + 1] = cand_i, cand_next
                else:
                    break
        # 3) 相邻两块能合则合，避免过度碎片化
        merged = []
        for g in groups:
            if merged and ok(merged[-1] + g):
                merged[-1] = merged[-1] + g
            else:
                merged.append(list(g))
        return ["".join(g) for g in merged if g]

    def _strip_tail(s):
        """★2026-09-24 用户要求：字幕行尾不拖标点（句中照旧保留）。
        只改「显示文本」——分块、时间窗、权重仍按带标点的原文算，断点与节奏完全不变。"""
        return s.rstrip("，。、！？；：,.!?;:—… \n\t")

    def _sub_prefix():
        return f"{{\\fad({fade})}}"
    lines = []
    # ★2026-09-30 新增（灿爹拍板）：sub_skip_shots=[1] —— 跳过指定镜的**底部字幕**。
    #   场景：镜1 用 title_cards 打「贴片大字」且不配音（如「180以上可以划走了」），
    #   底部再叠同一句字幕会与顶部大字重复，且让人误以为该句有配音。
    #   只抑制字幕，不影响画面/贴片大字/时间轴。
    _sub_skip = set(int(x) for x in (cfg.get("sub_skip_shots") or []))
    # 卡拉OK模式：重点字红填充+弹跳。来源优先级：karaoke_kw（fx 层同源词表）>
    # keywords > sub_highlights。注意 keywords 同时驱动 ASS 中央大字层，故默认走 karaoke_kw。
    _kw_words = list(cfg.get("karaoke_kw") or [])
    if not _kw_words:
        _kw_words = [k["kw"] for k in cfg.get("keywords", []) if "kw" in k]
        _kw_words += [h["kw"] for h in cfg.get("sub_highlights", []) if "kw" in h]
    _kw_words = list(dict.fromkeys(_kw_words))
    for _si, t in enumerate(timeline):
        if (_si + 1) in _sub_skip:
            continue
        t0, t1 = float(t["start"]), float(t["cut_end"])
        ln = cjk_wrap_lines(t["text"], per_line, _protect)
        chunks = [t["text"]] if len(ln) <= max_lines else _chunk_text(t["text"])
        if KARAOKE:
            _style, _kwidx = "Karaoke", _kw_char_idx(t["text"], _kw_words)
            _ct = t.get("ctimes") or []
            def _kara(chunk_text, cs_, ce_):
                r"""卡拉OK行：\k 逐字 + 标点瞬切 + 长行折行；长块按可见字数比例切分时间窗（不动音频）。
                minimal 款额外发射 KWBig 大金句行（层级1，动画 clip 左→右扫出，随重点词唱出）"""
                _wrapped = cjk_wrap(chunk_text, per_line, _protect)
                _kw_runs = []
                _body = _karaoke_text(_wrapped, _ct, cs_, _kwidx, _kw_runs)
                _line = f"Dialogue: 0,{ts(cs_)},{ts(ce_)},{_style},,0,0,0,," \
                        f"{_sub_prefix()}{_body}"
                for _rt, _rs, _re in (_kw_runs if SUB_STYLE == "minimal" else []):
                    _a = max(0, int(round((_rs - cs_) * 1000)))
                    _b = max(_a + 80, int(round((_re - cs_) * 1000)))
                    _line += (f"\nDialogue: 1,{ts(cs_)},{ts(ce_)},KWBig,,0,0,0,,"
                              f"{_sub_prefix()}{{\\clip(0,880,0,1080)\\t({_a},{_b},\\clip(0,880,1920,1080))}}{_rt}")
                return _line
        if len(chunks) == 1:
            _body = (_kara(_strip_tail(chunks[0]) or chunks[0], t0, t1) if KARAOKE else
                     f"Dialogue: 0,{ts(t0)},{ts(t1)},Main,,0,0,0,,"
                     f"{_sub_prefix()}{hl(cjk_wrap(_strip_tail(chunks[0]) or chunks[0], per_line, _protect))}")
            lines.append(_body)
        else:
            weights = [max(1.0, len(c)) for c in chunks]
            tot = sum(weights)
            acc = t0
            for j, (c, w) in enumerate(zip(chunks, weights)):
                e = t1 if j == len(chunks) - 1 else acc + (t1 - t0) * w / tot
                if KARAOKE:
                    lines.append(_kara(_strip_tail(c) or c, acc, e))
                else:
                    lines.append(f"Dialogue: 0,{ts(acc)},{ts(e)},Main,,0,0,0,,"
                             f"{_sub_prefix()}{hl(cjk_wrap(_strip_tail(c) or c, per_line, _protect))}")
                acc = e
    def kw_ass(e):
        """按 style 预设生成重点字 ASS（返回 [(图层, 文本)]；图层 1=荧光笔色块，2=文字）"""
        st = e.get("style") or "pop"
        x, y, col, txt = e["x"], e["y"], e["color"], e["kw"]
        size = int(e["size"] or kw_size_default)
        tilt = e.get("tilt", 0)
        xtra = (f"\\fs{size}" if e.get("size") else "") + (f"\\frz{tilt}" if tilt else "")
        bord = f"\\bord{e['bord']}" if e.get("bord") else ""
        if st == "rise":                                    # 蓝色上滑定住
            y0 = min(y + 80, 1000)
            return [(2, f"{{\\an5\\move({x},{y0},{x},{y},0,220)\\c{col}{xtra}{bord}"
                        f"\\fscx96\\fscy96\\t(0,220,\\fscx100\\fscy100)\\fad(70,170)}}{txt}")]
        if st == "type":                                    # 白色逐字打字机
            step = int(e.get("type_step") or 110)
            inner = "".join(f"{{\\alpha&HFF&\\t({i*step},{i*step},\\alpha&H00&)}}{c}"
                            for i, c in enumerate(txt))
            return [(2, f"{{\\an5\\pos({x},{y})\\c{col}{xtra}{bord}\\fad(40,170)}}{inner}")]
        if st == "mark":                                    # 绿色荧光笔划线（色块从左向右刷出）
            wpx, _ = kw_box_px(txt, size)
            bh = int(max(26, size * 0.66))
            lx, ty = x - wpx // 2, y - bh // 2
            bar = (f"{{\\an7\\pos({lx},{ty})\\p1\\c{e.get('bar_color') or KW_MARK_BAR}"
                   f"\\fscx0\\t(60,330,\\fscx100)\\fad(60,200)}}"
                   f"m 0 0 l {wpx} 0 l {wpx} {bh} l 0 {bh}")
            body = (f"{{\\an5\\pos({x},{y})\\c{col}{xtra}"
                    f"\\bord{max(2, int(size * 0.06))}\\fad(140,170)}}{txt}")
            return [(1, bar), (2, body)]
        if st == "pulse":                                   # 红色闪动强调
            return [(2, f"{{\\an5\\pos({x},{y})\\c{col}{xtra}{bord}\\alpha&HFF&"
                        "\\t(0,110,\\alpha&H00&)\\t(520,580,\\alpha&H70&)\\t(640,700,\\alpha&H00&)"
                        "\\t(980,1040,\\alpha&H70&)\\t(1100,1160,\\alpha&H00&)\\fad(0,170)}"
                        f"{txt}")]
        if st == "stroke":                                  # 空心描边转实心（空心→填色）
            # 坑（2026-09-17 实测）：绝不能写 \3c{col} 把描边也染成同色——超粗字重
            # （新青年体 W8）笔画间隙很窄，同色描边会把间隙填死，整词糊成一块色斑。
            # 保留 Style 的黑色描边，只让填充从透明渐显。
            bb = e.get("bord") or max(3, int(size * 0.035))
            return [(2, f"{{\\an5\\pos({x},{y})\\c{col}\\bord{bb}{xtra}"
                        "\\1a&HFF&\\t(0,340,\\1a&H00&)\\fad(0,170)}"
                        f"{txt}")]
        if st == "hero":                                    # ★超级重点字：25%→118% 强弹出 + 回弹 + 中段脉冲
            return [(2, f"{{\\an5\\pos({x},{y})\\c{col}{xtra}{bord}"
                        "\\fscx25\\fscy25"
                        "\\t(0,150,\\fscx118\\fscy118)\\t(150,290,\\fscx100\\fscy100)"
                        "\\t(560,620,\\fscx108\\fscy108)\\t(620,700,\\fscx100\\fscy100)"
                        "\\fad(0,210)}"
                        f"{txt}")]
        return [(2, f"{{\\an5\\pos({x},{y})\\c{col}\\fscx30\\fscy30{xtra}{bord}"  # pop 金色弹跳(默认)
                    f"\\t(0,90,\\fscx126\\fscy126)\\t(90,210,\\fscx100\\fscy100)\\fad(0,160)}}{txt}")]

    for e in kw_events:
        # 重点字防溢出：若整词估算宽度超过安全宽度（留 80px 左右边距），按比例自动缩小字号
        _ksize = int(e.get("size") or kw_size_default)
        _kw, _ = kw_box_px(e["kw"], _ksize)
        _safe_w = 1920 - 2 * 60 - 80  # 左右 margin 各 60，再留 80 保险
        if _kw > _safe_w:
            e["size"] = int(_ksize * _safe_w / _kw)
            print(f"  重点字「{e['kw']}」宽 {_kw}px 超界 → 字号 {_ksize} 缩至 {e['size']}")
        for layer, body in kw_ass(e):
            lines.append(f"Dialogue: {layer},{ts(e['t0'])},{ts(e['end'])},"
                         f"{'Draw' if layer == 1 else 'Big'},,0,0,0,,{body}")
    for tc in title_cards:
        tilt = tc.get("tilt", 0)
        frz = f"\\frz{tilt}" if tilt else ""
        pop = (f"{{\\an5\\pos(960,{tc['y']})\\c{tc['color']}\\fs{tc['size']}\\fscx20\\fscy20{frz}"
               f"\\t(0,110,\\fscx120\\fscy120)\\t(110,260,\\fscx100\\fscy100)\\fad(120,220)}}")
        lines.append(f"Dialogue: 3,{ts(tc['t0'])},{ts(tc['end'])},Big,,0,0,0,,{pop}{tc['text']}")
    ass_path = os.path.join(workdir, "subs.ass")
    open(ass_path, "w", encoding="utf-8").write(ass + "\n".join(lines) + "\n")
    # ---------- 5.5 产品详情卡片叠加：滑入+淡入 → 停留 → 滑出+淡出 ----------
    # 卡片是静态 PNG，用 setpts 把它的时间轴平移到 [t0, ...]，再用 overlay 的 x 表达式做滑入滑出；
    # enable 保证窗口之外绝不显示（否则 framesync 会拿第一帧顶上去）。
    vpre = vb_out
    for ci, c in enumerate(cards):
        cda = max(0.12, c["da"])
        cdur = max(0.6, (c["t1"] - c["t0"]) + cda + 0.4)
        inputs += ["-loop", "1", "-t", f"{cdur:.2f}", "-i", c["png"]]
        ck = in_i; in_i += 1
        ct0, ct1 = c["t0"], c["t1"]
        t_out = max(ct0, ct1 - cda)
        # 缺 cards.json 时的兜底：把原图按目标宽等比缩放（否则原始像素尺寸会溢出画面）
        csc = f"scale={c['scale_w']}:-1:flags=lanczos," if c.get("scale_w") else ""
        fc.append(f"[{ck}:v]fps={FPS},{csc}format=rgba,setpts=PTS-STARTPTS+{ct0:.3f}/TB,"
                  f"fade=t=in:st={ct0:.3f}:d={cda:.3f}:alpha=1,"
                  f"fade=t=out:st={t_out:.3f}:d={cda:.3f}:alpha=1[card{ci}]")
        if c["anim"] == "slide":
            off = -c["png_w"] if c["vis_x"] < 960 else 1920      # 从离它最近的那侧滑入
            xe = (f"if(lt(t,{ct0 + cda:.3f}),{off}+({c['png_x']}-({off}))"
                  f"*min(1,(t-{ct0:.3f})/{cda:.3f}),"
                  f"if(gt(t,{t_out:.3f}),{c['png_x']}+(({off})-({c['png_x']}))"
                  f"*min(1,(t-{t_out:.3f})/{cda:.3f}),{c['png_x']}))")
            ye = str(c["png_y"])
        elif c["anim"] in ("sticker", "pop"):
            # ★2026-09-19 用户要求：产品以「贴纸切入」显示，替代实拍视频/截图（避免与动漫世界割裂）。
            # 入场：从贴纸所在那侧偏 DX 处带 easeOutBack（超调约 10%）冲进来 → 有「啪」地贴上去的手感；
            #       同时从下方 DY 处升起（pow(1-e,3) 快出慢收）。
            # 停留：y 方向轻微正弦悬浮 ±AMP，让贴纸像浮在画面上。
            # 出场：淡出的同时向上揭起 OUT_DY —— 像被「揭下来」而不是简单消失。
            dx = 96 if c["png_x"] + c["png_w"] / 2 >= 960 else -96
            dy = 58
            amp = float(c.get("float") or 5.0)
            per = float(c.get("float_period") or 2.2)
            ody = 26
            xe = (f"st(0,min(1,max(0,(t-{ct0:.3f})/{cda:.3f})));"
                  f"{c['png_x']}+{dx}*(1-(1+2.70158*pow(ld(0)-1,3)+1.70158*pow(ld(0)-1,2)))")
            ye = (f"st(0,min(1,max(0,(t-{ct0:.3f})/{cda:.3f})));"
                  f"st(1,min(1,max(0,(t-{t_out:.3f})/{cda:.3f})));"
                  f"{c['png_y']}+{dy}*pow(1-ld(0),3)+{amp}*sin(2*PI*(t-{ct0:.3f})/{per})-{ody}*ld(1)")
        else:
            xe = str(c["png_x"])
            ye = str(c["png_y"])
        fc.append(f"[{vpre}][card{ci}]overlay=x='{xe}':y='{ye}':format=auto:eval=frame:"
                  f"enable='between(t,{ct0:.3f},{ct1:.3f})'[vcard{ci}]")
        vpre = f"vcard{ci}"
        print(f"  卡片叠加: {os.path.basename(c['src'])} {ct0:.2f}-{ct1:.2f}s "
              f"可见位=({c['vis_x']},{c['vis_y']}) 动画={c['anim']}")
    # 自定义字体目录（重点字字体随技能分发）；系统字体仍由 fontconfig 默认目录解析
    fd_arg = f":fontsdir={FONT_DIR}" if os.path.isdir(FONT_DIR) else ""
    # ---------- 5.5 片头钩子段视频链 ----------
    # 每镜：16:9 画布(2880x1620) → zoompan 强推拉 + 手持抖动 → 提对比/饱和；
    # 拼接后按切点打「RGB 通道分离」故障帧与「白闪」过渡，最后统一暗角+颗粒。
    # 钩子独立成流，最后与已烧好字幕的正片 concat —— 正片时间轴一行都不用改。
    if hk:
        hk_lbls = []
        _mo = hk.get("motions") or ["punch_in", "pull_out", "punch_in"]
        for _i, (_src, _sc) in enumerate(hk["_pairs"]):
            _n = HK_FRAMES[_i]
            inputs += ["-i", _src]; _k = in_i; in_i += 1
            _T = f"on/{FPS}"                        # zoompan 内没有 t，用输出帧号换算秒
            _ph = 1.0 + 0.37 * _i
            _ax = 7.0 * float(hk.get("shake", 1.0))
            _sx = f"+{_ax:.2f}*sin(2.4*{_T}+{_ph:.2f})"
            _sy = f"+{_ax * 0.8:.2f}*sin(2.9*{_T}+{_ph * 1.3:.2f})"
            _mode = _mo[_i % len(_mo)]
            if _mode in ("out", "pull_out"):
                _z = f"1.40-0.36*pow(min(on/{_n},1),0.65)"      # 从最近处猛拉远
            elif _mode == "in":
                _z = f"1.04+0.20*pow(min(on/{_n},1),0.85)"      # 缓推
            else:
                _z = f"1.03+0.34*pow(min(on/{_n},1),0.55)"      # punch_in：起手最快
            fc.append(
                f"[{_k}:v]scale=2880:1620:force_original_aspect_ratio=increase,"
                f"crop=2880:1620:0:0,setsar=1,"
                f"zoompan=z='{_z}':"
                f"x='min(max(iw/2-(iw/zoom/2){_sx},0),iw-iw/zoom)':"
                f"y='min(max(ih/2-(ih/zoom/2){_sy},0),ih-ih/zoom)':"
                f"d={_n}:s=1920x1080:fps={FPS},"
                f"setpts=PTS-STARTPTS,eq=contrast=1.06:saturation=1.12,"
                f"setsar=1,format=yuv420p[hook{_i}]")
            hk_lbls.append(f"[hook{_i}]")
        fc.append("".join(hk_lbls) + f"concat=n={len(hk_lbls)}:v=1:a=0[hkraw]")
        _v, _cu, _bounds = "[hkraw]", 0.0, []
        for _n in HK_FRAMES[:-1]:
            _cu += _n / FPS
            _bounds.append(_cu)
        # 暗角+颗粒先作用于画面本身；白闪放最后（保证是纯白爆闪，不被暗角压灰）
        _g = int(hk.get("grain", 7))
        fc.append(f"{_v}vignette=angle=PI/4.6"
                  + (f",noise=alls={_g}:allf=t+u" if _g > 0 else "")
                  + "[hkbase]")
        _v = "[hkbase]"
        if hk.get("flash", True) and _bounds:
            inputs += ["-f", "lavfi", "-i",
                       f"color=c=white:s=1920x1080:r={FPS}:d={HO:.3f}"]
            _wi = in_i; in_i += 1
            _en2 = "+".join(f"between(t,{b - 0.02:.3f},{b + 0.04:.3f})" for b in _bounds)
            fc.append(f"{_v}[{_wi}:v]overlay=0:0:enable='{_en2}'[hkfl]")
            _v = "[hkfl]"
        # 故障帧必须排在白闪之后：白闪若在后，会把整段 RGB 分离盖住，等于没做
        if hk.get("glitch", True) and _bounds:
            _en = "+".join(f"between(t,{b + 0.04:.3f},{b + 0.12:.3f})" for b in _bounds)
            fc.append(f"{_v}rgbashift=rh=11:bh=-11:enable='{_en}'[hkgl]")
            _v = "[hkgl]"
        fc.append(f"{_v}format=yuv420p[vhook]")
        fc.append(f"[{vpre}]subtitles={ass_path}{fd_arg}[vsubraw]")
        fc.append(f"[vhook][vsubraw]concat=n=2:v=1:a=0[vsub]")
        print(f"  片头钩子: {len(hk_lbls)} 镜 concat 到片头 | "
              f"推拉={','.join(_mo[_i % len(_mo)] for _i in range(len(hk_lbls)))} | "
              f"glitch={bool(hk.get('glitch', True))} flash={bool(hk.get('flash', True))} grain={_g}")
    else:
        fc.append(f"[{vpre}]subtitles={ass_path}{fd_arg}[vsub]")

    # ---------- 6. 音频: 口播 + 重点字音效 + BGM 闪避 ----------
    # BGM 解析：config 没写 bgm → 自动用技能内置默认曲（"下次不给我 BGM 就用这首"）；
    # 想单条关掉写 "bgm": false / "none"。
    _bgm_raw = cfg.get("bgm", None)
    if _bgm_raw is False or (isinstance(_bgm_raw, str)
                             and _bgm_raw.strip().lower() in ("", "none", "off", "false")):
        bgm_path = None
        print("  BGM: 已关闭")
    else:
        _raw_path = (_bgm_raw.strip() if isinstance(_bgm_raw, str) and _bgm_raw.strip()
                     else None)
        bgm_path = pick_bgm(_raw_path, cfg.get("bgm_pick"))
        if not bgm_path or not os.path.exists(bgm_path):
            print(f"  WARN BGM 不存在({bgm_path}) → 本条不加 BGM")
            bgm_path = None
        else:
            if not _raw_path:
                print(f"  BGM: 曲池随机 → {os.path.basename(bgm_path)}"
                      f"（池内 {len(bgm_pool())} 首，写 bgm 路径可指定）")
            bgm_path = ensure_bgm_len(bgm_path, TOTAL_ALL, FF)
    inputs += ["-i", cfg["vo"]]
    VO_I = in_i; in_i += 1
    SFX_I = {}
    for name, f in sfx_files.items():
        p = os.path.join(SFX_DIR, f)
        if os.path.exists(p):
            inputs += ["-i", p]; SFX_I[name] = in_i; in_i += 1
    # 有片头钩子时，口播整体后移 HO 秒（与视频 concat 对齐）；钩子段只有音效+BGM
    _hkdel = f"adelay={int(round(HO * 1000))}|{int(round(HO * 1000))}," if HO > 0 else ""
    # ---------- 5.0 prod 镜自带音轨（2026-10-06 灿爹「卷播音效没进成片」）----------
    # 卷播片段自带完整混音（人声 + 卷播音效 + 定格咚）。若仍与全局 vo 并存会**双重人声**，
    # 所以做法是：把全局 vo 在该区间静音，再把片段音轨按同一时间轴贴进去。
    # 静音用 volume 的 timeline enable（区间内 volume=0），比 volume 表达式稳。
    # ★ 静音上界取 sil_until（画面实际结束时刻）而不是 t1（音轨长度）：
    #   素材比画面短时，音轨只到 t1，画面还要靠末帧定住到 sil_until ——
    #   这段必须继续静音全局 vo，否则卷播结束后会冒出全局 vo 的人声。
    _mute_en = "+".join(
        f"between(t,{o['t0'] + HO:.3f},{(o.get('sil_until') or o['t1']) + HO:.3f})"
        for o in OWN_AUDIO)
    # ★ 逗号纪律：每段可选滤镜都要自带前导逗号，最后再统一补 ",apad"，
    #   否则 HO=0 且无自带音轨时会拼出 "volume=1.46,,apad" / "volume=1.46apad"
    #   这种缺分隔的滤镜链 —— ffmpeg 报 "No such filter" 或直接 misinterpreted，整个音频段废掉。
    _opt = ("," + _hkdel.rstrip(",")) if _hkdel else ""
    if _mute_en:
        _opt += f",volume=volume=0:enable='{_mute_en}'"
    fc.append(f"[{VO_I}:a]aresample=44100,aformat=channel_layouts=stereo,volume={VO_GAIN}"
              f"{_opt},apad[vo0]")
    # 自带音轨：按该镜时长裁切 → 贴到 timeline 起点（+HO 与画面 concat 对齐）
    own_lbls = []
    for _n, _o in enumerate(OWN_AUDIO):
        _lbl = f"own{_n}"
        _d = max(0.05, _o["t1"] - _o["t0"])
        _ms = int(round((_o["t0"] + HO) * 1000))
        fc.append(f"[{_o['in_i']}:a]aformat=channel_layouts=stereo,aresample=44100,"
                  f"atrim=0:{_d:.3f},asetpts=PTS-STARTPTS,volume={_o['gain']:.4f},"
                  f"adelay={_ms}|{_ms}[{_lbl}]")
        own_lbls.append(_lbl)
    # 注意: 无 BGM 时不能 asplit 出 [vok]（无消费者会导致滤镜图非法）
    if bgm_path:
        fc.append("[vo0]asplit=2[vo][vok]")
    else:
        fc.append("[vo0]anull[vo]")

    # ---------- 5.9 重点字音效去重 / 合并（可配置 sfx_policy，2026-09-27）----------
    # 在候选收集**之前**先定「谁发声」：先让位已有动效，再合并挨太近的重点字，
    # 最后按滑动窗口控密度。只掐声音、保留视觉（大字照常弹出）。
    _others = []
    if cfg.get("sfx", True):
        _w_at = cfg.get("transition_whoosh")
        if _w_at is None:
            _w_at = [i for i in (5, 13) if i < len(timeline)]
        _w_at = [i for i in _w_at if 0 <= i < len(timeline)]   # 越界丢弃（见 827 注释）
        for pi in _w_at:                       # 转场唿哨（时间=该镜起点）
            if 0 <= pi < len(timeline):
                _others.append((timeline[pi]["start"], f"镜{pi + 1}转场唿哨"))
        for c in cards or []:                  # 产品贴纸/卡片弹入
            if c.get("anim") in ("sticker", "pop"):
                _others.append((c["t0"], "卡片/贴纸入场"))
        for tc in title_cards or []:           # 全屏标题卡
            _others.append((tc["t0"], f"标题卡「{str(tc.get('text', ''))[:8]}」"))
        for i, t in enumerate(phrases):        # 抠图分层入场
            if t.get("fg") and t.get("bg"):
                _others.append((max(0.0, timeline[i]["start"]
                                    - float(cfg.get("layer_sfx_lead", 0.08))),
                                f"镜{i + 1}分层入场"))
        # 开场冲击默认不参与邻避：它与片头大字是"组合设计"（同处开场 0–1.3s），
        # 若纳入会把片头钩大字的音效掐掉。确需纳入写 kw_near_include_burst: true
        if (cfg.get("sfx_policy") or {}).get("kw_near_include_burst") and bst and BS_PRESET:
            for _nn, _tt in BS_PRESET.get("sfx") or []:
                _others.append((float(_tt), "开场冲击"))
    kw_events = apply_kw_sfx_policy(kw_events, cfg, timeline, _others)

    # ---- 候选音效统一收集（带优先级），最后统一做时间冲突消解 ----
    # 优先级: 0=重点字(绑定口播内容, 必须响) 1=标题卡 2=分层入场 3=开场重击 4=转场唿哨
    # 冲突时保高优先级、丢低优先级；凡是与 whoosh 相邻的避让距离更大（它更长更响）。
    cand = []
    if cfg.get("sfx", True):
        # 有片头钩子时不再叠正片开场音效（钩子自带开场重音，叠上会连响）
        if tear_T > 0 and HO <= 0:
            for _tt, _nn in ((0.28, "rip"), (0.34, "thump")):
                if _nn in SFX_I:
                    cand.append((_tt, _nn, 5, "撕裂开场"))
        elif (not bst) and HO <= 0 and cfg.get("opening_punch", True) and cfg.get("opening_punch_sfx", True):
            cand.append((0.12, "impact", 3, "开场重击"))
        for e in kw_events:
            if e["sfx"] in SFX_I:
                cand.append((e["t0"], e["sfx"], 0, f"重点字「{e['kw']}」"))
        for t_, tn_, pr_, tg_ in trig_events:
            if tn_ in SFX_I:
                cand.append((t_, tn_, pr_, tg_))
        for tc in title_cards:
            if tc["sfx"] in SFX_I:
                cand.append((tc["t0"], tc["sfx"], 1, f"标题卡「{tc['text']}」"))
        # 产品贴纸入场：「啪」地贴上画面 → 配 pop_in（2026-09-19 贴纸样式配套）
        # 优先级给 3（不占「5s 内不响两次」的响亮事件额度）：它是短促轻音，
        # 贴着重点字/标题卡出现也很自然；若与唿哨挨太近仍会被 0.6s 规则丢掉。
        for c in cards:
            if c.get("anim") not in ("sticker", "pop"):
                continue
            nm = c.get("sfx") or "pop_in"
            if nm in SFX_I:
                cand.append((c["t0"], nm, 3, f"产品贴纸·{os.path.basename(c['src'])[:14]}"))
        # 抠图分层镜自带入场音效(弹入 pop_in / 浮动 float_in)，略早于切点起播
        lead = float(cfg.get("layer_sfx_lead", 0.08))
        for i, t in enumerate(phrases):
            if not (t.get("fg") and t.get("bg")):
                continue
            nm = t.get("fg_sfx") or ("float_in" if t.get("fg_motion", "popin") == "float" else "pop_in")
            if nm in SFX_I:
                cand.append((max(0.0, timeline[i]["start"] - lead), nm, 2, f"镜{i+1}分层入场"))
    # 转场唿哨: 轮换取曲（洗牌顺序，避免一条片全同一个音）
    whoosh_at = cfg.get("transition_whoosh")
    if whoosh_at is None:
        whoosh_at = [i for i in (5, 13) if i < len(timeline)]
    whoosh_at = [i for i in whoosh_at if 0 <= i < len(timeline)]   # 越界丢弃（见 827 注释）
    whoosh_pool = [n for n in ("whoosh", "whoosh2", "whooshC") if n in SFX_I]
    seq, prev = [], None
    for pi in whoosh_at:
        if not whoosh_pool or not cfg.get("sfx", True):
            break
        if not seq:                                   # 一轮取完 → 重新洗牌
            seq = whoosh_pool[:]
            random.shuffle(seq)
            if len(seq) > 1 and seq[-1] == prev:      # 避免与上一轮末尾连号
                seq[0], seq[-1] = seq[-1], seq[0]
        pick = seq.pop()
        prev = pick
        cand.append((timeline[pi]["start"], pick, 4, f"镜{pi+1}转场唿哨"))
    gap_n = float(cfg.get("sfx_min_gap", 0.30))          # 普通音效之间的最小间隔
    gap_w = float(cfg.get("sfx_gap_whoosh", 0.60))       # 与唿哨之间（唿哨更长更响）
    # 响亮事件（重点字/标题卡/转场唿哨）之间的最小间隔：用户要求"5 秒内不能出现两次"，
    # 否则动效音效和转场音效挤在一起连着响，噪点太多。0=关闭该规则。
    gap_p = float(cfg.get("sfx_gap_prominent", 0.0))

    def _prom(pr_, nm_):
        # "有存在感的音效"：重点字(0)/标题卡(1)/分层入场(2)/转场唿哨 —— 它们互相之间只留一个
        return pr_ <= 2 or "whoosh" in nm_

    # 片头钩子：正片内所有音效事件整体后移 HO
    if HO > 0:
        cand = [(t_ + HO, nm, pr, tag) for (t_, nm, pr, tag) in cand]
    cand.sort(key=lambda c: (c[2], c[0]))
    events, acc = [], []
    for t_, nm, pr, tag in cand:
        clash = None
        for at, anm, apr, atag in acc:
            if gap_p > 0 and _prom(pr, nm) and _prom(apr, anm):
                need = gap_p                    # 响亮事件互斥：窗口内只留优先级最高的那个
                if abs(t_ - at) < need:
                    clash = (at, anm, atag, abs(t_ - at), need)
                    break
                continue                        # 已满足 5s，无需再看宽松规则
            need = gap_w if ("whoosh" in nm or "whoosh" in anm) else gap_n
            if abs(t_ - at) < need:
                clash = (at, anm, atag, abs(t_ - at), need)
                break
        if clash:
            print(f"  音效避让: 丢弃 [{tag}] {nm}@{t_:.2f}s —— 与 [{clash[2]}] "
                  f"{clash[1]}@{clash[0]:.2f}s 仅隔 {clash[3]:.2f}s (<{clash[4]:.2f}s)")
        else:
            acc.append((t_, nm, pr, tag)); events.append((t_, nm))
            print(f"  音效: {nm} @ {t_:.2f}s  [{tag}]")

    # 片头钩子音效：独立段落，**不参与正片的「5 秒响亮互斥」**——钩子与正片第一击之间
    # 是段落边界，按 5s 铁律算会把钩子音效全挤掉。内部只做最小间隔检查。
    if HO > 0 and HK_SFX:
        _hk_acc = []
        for _tt, _nn in HK_SFX:
            if _nn not in SFX_I:
                print(f"  片头钩子: 音效 {_nn} 不在音色池，已跳过")
                continue
            _tt = max(0.0, min(float(_tt), max(0.0, HO - 0.05)))
            if any(abs(_tt - _a) < gap_n for _a in _hk_acc):
                print(f"  片头钩子: 音效 {_nn}@{_tt:.2f}s 距前一音效不足 {gap_n}s，已跳过")
                continue
            _hk_acc.append(_tt)
            events.append((_tt, _nn))
            print(f"  音效: {_nn} @ {_tt:.2f}s  [片头钩子]")
    # 开场冲击音效：独立成组，**不参与正片的「5 秒响亮互斥」**——它就是开场第一击，
    # 若纳入正片规则，会和 1.7s 左右的正片第一个重点字音效互斥掉一个。
    # 组内也**不能套正片的最小间隔**：重击 + 紧接能量蓄满本来就是隔 0.08s 的连击设计，
    # 所以这里只防「完全同时」（0.04s）。
    if bst and BS_PRESET and cfg.get("sfx", True) and bst.get("sfx", True):
        _bs_acc = []
        for _nn, _tt in BS_PRESET.get("sfx") or []:
            if _nn not in SFX_I:
                print(f"  开场冲击: 音效 {_nn} 不在音色池，已跳过")
                continue
            _tt = max(0.0, min(float(_tt), max(0.0, BS_DUR - 0.05)))
            if any(abs(_tt - _a) < 0.04 for _a in _bs_acc):
                print(f"  开场冲击: 音效 {_nn}@{_tt:.2f}s 与前一音效几乎同时，已跳过")
                continue
            _bs_acc.append(_tt)
            events.append((_tt, _nn))
            print(f"  音效: {_nn} @ {_tt:.2f}s  [开场冲击]")
    # 指定时刻音效（★2026-10-21）：config "sfx_at": [[秒, 音色], ...]
    # 用途：镜 1「扫描重建」这类**画面对点有硬节拍**的动效（0.46 白闪归位 / 1.00 二次故障…），
    #       靠关键词触发根本对不上。给的是绝对时刻，独立成组：
    #       · 不参与正片「5 秒响亮互斥」（它是镜 1，正片第一个重点字原本就会被挤掉）
    #       · 组内**不做最小间隔**——白闪 + 沉闷响本来就是同时叠的两层
    for _it in (cfg.get("sfx_at") or []):
        try:
            _tt, _nn = float(_it[0]), str(_it[1])
        except (TypeError, ValueError, IndexError):
            print(f"  sfx_at: 跳过格式不对的项 {_it!r}")
            continue
        if _nn not in SFX_I:
            print(f"  sfx_at: 音色 {_nn} 不在音色池（查 sfx_files 是否注册），已跳过")
            continue
        _tt = max(0.0, _tt + HO)
        events.append((_tt, _nn))
        print(f"  音效: {_nn} @ {_tt:.2f}s  [指定时刻]")
    sfx_lbls = []
    # 音效回声（可选）：cfg.sfx_echo = {"delays":"250|500","decays":"0.45|0.22","in_gain":1.0,"out_gain":0.95}
    # 用于"音效延长约 0.5 秒、音量逐渐缩小的回声"
    echo = cfg.get("sfx_echo")
    echo_f = ""
    if echo:
        echo_f = (f",aecho={echo.get('in_gain', 1.0)}:{echo.get('out_gain', 0.95)}:"
                  f"{echo.get('delays', '250|500')}:{echo.get('decays', '0.45|0.22')}")
    # 音效低频清理（可选）：去掉 rumble，避免放大量后发闷
    hp = cfg.get("sfx_highpass")
    hp_f = f",highpass=f={hp}" if hp else ""
    for n_, (tt, sfx) in enumerate(events):
        ms = int(tt * 1000); lbl = f"s{n_}"
        vol = (cfg.get("sfx_volume", {}).get(sfx)
               or cfg.get("sfx_volume", {}).get(sfx.rstrip("0123456789"))
               or DEFAULT_SFX_VOL.get(sfx, DEFAULT_SFX_VOL.get(sfx.rstrip("0123456789"), 0.8)))
        fc.append(f"[{SFX_I[sfx]}:a]aformat=channel_layouts=stereo{hp_f},volume={vol}{echo_f},adelay={ms}|{ms}[{lbl}]")
        sfx_lbls.append(lbl)

    has_bgm = bool(bgm_path)
    _ln_raw = cfg.get("loudnorm")
    if _ln_raw is None:
        # config 没写 loudnorm → **默认开启**（同 BGM 配平一样是内置规则，不指望 config 记得写）。
        # 2026-09-22 诺特老款片踩坑：config 漏写 loudnorm，整条归一被跳过，成片只有 -20.8 LUFS。
        ln = {"I": -11.5, "TP": -1.0, "LRA": 11}
        print("  响度: config 未写 loudnorm → 用内置默认（基础 -11.5 + boost）")
    elif _ln_raw is False:
        ln = None
    else:
        ln = _ln_raw
    if ln:
        # 整体响度 = loudnorm 目标 + 整体提升(loudness_boost_db)，并夹在实测天花板内。
        # 想整体更响就调这一个数（而不是去动 vo_gain——那个改了等于没改）。
        _base = float(ln["I"])
        _boost = float(cfg.get("loudness_boost_db", DEFAULT_LOUDNESS_BOOST_DB))
        _I = _base + _boost
        if _I > MASTER_LUFS_MAX:
            print(f"  响度: 目标 {_I:.1f} LUFS 超出实测天花板 {MASTER_LUFS_MAX} LUFS "
                  f"→ 收到 {MASTER_LUFS_MAX}（再推只挤压、不增益）")
            _I = MASTER_LUFS_MAX
        print(f"  响度: loudnorm 目标 {_base:.1f} {_boost:+.1f}dB = {_I:.1f} LUFS")
        ln = {**ln, "I": _I}
    ln_f = (f",loudnorm=I={ln['I']}:TP={ln['TP']}:LRA={ln.get('LRA', 11)},aresample=44100" if ln else "")
    # 末级真峰值兜底（见 DEFAULT_PEAK_GUARD 注释）。放在 loudnorm 之后、aresample 之前。
    _pg = cfg.get("peak_guard", DEFAULT_PEAK_GUARD)
    if _pg is not False and _pg is not None:
        if ln:
            ln_f = (f",loudnorm=I={ln['I']}:TP={ln['TP']}:LRA={ln.get('LRA', 11)},"
                    f"alimiter=limit={float(_pg)}:level=0,aresample=44100")
        else:
            ln_f = f",alimiter=limit={float(_pg)}:level=0"
        print(f"  真峰值保护: alimiter limit={float(_pg)} (≈{20*__import__('math').log10(float(_pg)):.2f} dBFS)")
    if sfx_lbls:
        fc.append("".join(f"[{x}]" for x in sfx_lbls) + f"amix=inputs={len(sfx_lbls)}:normalize=0,volume=0.9[sfx]")
    if has_bgm:
        inputs += ["-i", bgm_path]; BGM_I = in_i; in_i += 1
        # BGM 音量：显式给了 bgm_volume 就用手调的；否则（默认）现场测口播与 BGM 的电平，
        # 自动把 BGM 摆到「口播 - bgm_offset_db」的位置。这样换任何 BGM（包括没处理过的）
        # 都不会盖住配音——原曲"后段突然变响"的毛病在混音端也再兜一次。
        bvol = cfg.get("bgm_volume")
        if bvol is not None:
            print(f"  BGM 音量: 手调 volume={bvol}（跳过自动配平）")
        elif cfg.get("bgm_auto_gain", True):
            vo_db = audio_mean_db(FF, cfg["vo"], af=f"volume={VO_GAIN}", limit=TOTAL_ALL)
            bg_db = audio_mean_db(FF, bgm_path, limit=TOTAL_ALL)
            if vo_db is not None and bg_db is not None:
                off = float(cfg.get("bgm_offset_db", BGM_OFFSET_DB))
                gain_db = (vo_db - off) - bg_db
                bvol = round(max(0.02, min(1.2, 10 ** (gain_db / 20.0))), 4)
                print(f"  BGM 配平: 口播 {vo_db:.1f}dB / BGM {bg_db:.1f}dB → "
                      f"目标 {vo_db - off:.1f}dB，增益 {gain_db:+.1f}dB (volume={bvol})")
        if bvol is None:
            bvol = DEFAULT_BGM_VOL
            print(f"  BGM 音量: 自动配平不可用 → 固定 volume={bvol}")
        duck = dict(BGM_DUCK); duck.update(cfg.get("bgm_duck") or {})
        fc.append(
            f"[{BGM_I}:a]aformat=channel_layouts=stereo,aresample=44100,atrim=0:{TOTAL_ALL:.2f},"
            f"volume={bvol},afade=t=in:d=0.4,"
            f"afade=t=out:st={TOTAL_ALL-1.0:.2f}:d=1.0[bgm0]")
        # 闪避：口播一出声就把 BGM 压下去，句间自动恢复（threshold 越低越灵敏）
        fc.append(f"[bgm0][vok]sidechaincompress=threshold={duck['threshold']}:ratio={duck['ratio']}"
                  f":attack={duck['attack']}:release={duck['release']}[bgmd]")
        mix = "[vo][bgmd]" + ("[sfx]" if sfx_lbls else "")
        n_in = 3 if sfx_lbls else 2
        if own_lbls:
            mix += "".join(f"[{x}]" for x in own_lbls)
            n_in += len(own_lbls)
        fc.append(f"{mix}amix=inputs={n_in}:duration=first:normalize=0,alimiter=limit=0.95{ln_f}[aout]")
    else:
        _mix2 = "[vo]" + ("[sfx]" if sfx_lbls else "")
        _n2 = 2 if sfx_lbls else 1
        if own_lbls:
            _mix2 += "".join(f"[{x}]" for x in own_lbls)
            _n2 += len(own_lbls)
        if _n2 > 1:
            fc.append(f"{_mix2}amix=inputs={_n2}:duration=first:normalize=0,alimiter=limit=0.95{ln_f}[aout]")
        else:
            fc.append(f"[vo]anull{ln_f}[aout]")

    # ---------- 6.5 封面帧（2026-09-18 用户需求：抖音封面与首帧一致）----------
    # 把一张竖版（推荐精确 3:4）图铺在视频最开头，平台取首帧做封面时就是它。
    # 用 overlay + enable 走「覆盖式」：不改变总时长、不推迟口播（1 帧~0.1s 级无感）。
    # 布局：blur=两侧用原图模糊铺满（抖音裁中间 3:4 正好是干净原图）；pad=纯黑边。
    vout = "[vsub]"
    # 默认带封面：config 不写 cover 就用技能内置的账号固定封面图（用户 2026-09-18 拍板）。
    # "cover": false 关闭；写 {"src": "..."} 换图；其余参数（dur/fit）可覆盖。
    cov = cfg.get("cover")
    if cov is False or (isinstance(cov, dict) and cov.get("enabled") is False):
        cov = None
    else:
        cov = dict(cov) if isinstance(cov, dict) else {}
        if not cov.get("src"):
            cov["src"] = DEFAULT_COVER
        if not os.path.exists(cov["src"]):
            print(f"  封面帧: 跳过（找不到 {cov['src']}）")
            cov = None
    ddur = float(cov.get("dur", DEFAULT_COVER_DUR)) if cov else 0.0
    if cov:
        _fps = float(cfg.get("fps", 30))
        cdur = max(1.0 / _fps, ddur)
        cfit = (cov.get("fit") or "blur").lower()
        inputs += ["-loop", "1", "-framerate", str(int(_fps)),
                   "-t", f"{cdur:.3f}", "-i", cov["src"]]
        CI = in_i; in_i += 1
        if cfit == "pad":
            fc.append(f"[{CI}:v]scale=-2:1080,setsar=1,"
                      "pad=1920:1080:(ow-iw)/2:0:color=black[ckv]")
        else:
            fc.append(f"[{CI}:v]split=2[ck1][ck2]")
            fc.append("[ck1]scale=-2:1080,setsar=1[ckf]")
            fc.append("[ck2]scale=1920:1080:force_original_aspect_ratio=increase,"
                      "crop=1920:1080,gblur=sigma=60,eq=brightness=-0.06,setsar=1[ckb]")
            fc.append("[ckb][ckf]overlay=(W-w)/2:0,setsar=1[ckv]")
        fc.append(f"{vout}[ckv]overlay=0:0:eof_action=pass:enable='lt(t,{cdur:.3f})'[vfinal]")
        vout = "[vfinal]"
        print(f"  封面帧: {os.path.basename(cov['src'])} 前 {cdur:.3f}s（{cfit} 布局，总时长不变）")

    # ---------- 7. 渲染 ----------
    # AD_PREVIEW_SEC=N：预览模式，只出前 N 秒（试开场动效/音效时用，省掉整片渲染时间）。
    # 预览产物默认落到 <workdir>/preview/，**不进素材盘**（素材盘只放最终成片）。
    _pv = os.environ.get("AD_PREVIEW_SEC")
    PV = 0.0
    if _pv:
        try:
            PV = max(0.5, float(_pv))
        except ValueError:
            print(f"  预览: AD_PREVIEW_SEC「{_pv}」不是数字，已忽略")
    out_path, out_t = cfg["output"], TOTAL_ALL
    if PV:
        out_t = min(PV, TOTAL_ALL)
        _pd = os.environ.get("AD_PREVIEW_DIR") or os.path.join(workdir, "preview")
        os.makedirs(_pd, exist_ok=True)
        _b = re.sub(r"\.mp4$", "", os.path.basename(cfg["output"]), flags=re.I)
        out_path = os.path.join(_pd, f"{_b}_预览{out_t:g}s.mp4")
    cmd = [FF, "-y", "-v", "error"] + inputs + [
        "-filter_complex", ";".join(fc),
        "-map", vout, "-map", "[aout]",
        "-c:v", "h264_videotoolbox", "-q:v", "72",
        "-c:a", "aac", "-b:a", "192k", "-t", f"{out_t:.3f}", out_path]
    print("总时长:", round(TOTAL_ALL, 2), "s"
          + (f"（片头钩子 {HO:.2f}s + 正片 {TOTAL:.2f}s）" if HO > 0 else ""))
    if PV:
        print(f"  预览模式: 只出前 {out_t:g}s → {out_path}")
    dbg = os.path.join(workdir, "filter_debug.txt")
    with open(dbg, "w", encoding="utf-8") as _f:
        _f.write("INPUTS:\n" + "\n".join(inputs) + "\n\nFILTERS:\n" + ";\n".join(fc) + "\n\nCMD:\n" + " ".join(cmd) + "\n")
    if os.environ.get("AD_DRY_RUN"):      # 只导出滤镜链不渲染，便于排查动效/音效问题
        print("DRY RUN ->", dbg)
        return
    sh(cmd)
    print("完成:", out_path, os.path.getsize(out_path) // 1024, "KB")

if __name__ == "__main__":
    main()
