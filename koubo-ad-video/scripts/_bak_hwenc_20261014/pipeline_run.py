#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
★★出片总编排 pipeline_run.py v2（2026-10-09 重写定稿）
─────────────────────────────────────────────────────────────────────
用法:
  python3 pipeline_run.py --config <品类.json> --text <文案.txt> --name <条目名>
                          [--skip-tts] [--skip-banner]

流程:
  1. 切镜（句切+守恒+长句拆分）          ~1s
  2. F5 配音（批量+裁幽灵+拼接）          ~2.5'
  3. 3D 卷播渲染（concurrency=3）         ~1'
  4. token 时间轴（F5 句界直出）          ~1s
  5. 底片渲染（build_ad_video）           ~3'
  6. 合成（banner 混音同源 + 拼接）       ~1'
  7. qc_all 一键质检                      ~1'
fx 层（动效附加层）v2 接入：品类配置 fx.enabled=true 时启用。
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import time

import numpy as np

SKILL = "/Users/mac/.workbuddy/skills/koubo-ad-video"
FF = "/Users/mac/.local/bin/ffmpeg"
FP = "/Users/mac/.local/bin/ffprobe"
PY = "/Users/mac/.workbuddy/binaries/python/envs/default/bin/python"
F5_PY = "/Users/mac/.workbuddy/models/mlx-audio/venv/bin/python"
NODE = "/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin/npx"
PUNCT = re.compile("[，。！？、：；—…·～「」『』《》”“‘’（）()\\s]")
# ★2026-10-09 灿爹拍板：文案含定场句「今天我们讲，X」时，这句及它之前的所有内容
# 全部归入开头轮播（轮播切点 = 定场句尾 + 0.40）；没有定场句才默认只盖第一句。
BANNER_MARK = "今天我们讲"


def sh(cmd, **kw):
    print(f"  ▸ {str(cmd)[:110]}", flush=True)
    r = subprocess.run(cmd, shell=isinstance(cmd, str), **kw)
    if r.returncode != 0:
        raise RuntimeError(f"命令失败: {str(cmd)[:140]}")


def split_long(out, max_len=32):
    """★长句拆分：超长镜在居中逗号处拆（防单镜超长 + F5 长句漏读/幻觉）。
    ★队列式递归（2026-10-09 sC 文案实锤）：旧写法 body[:c+1] append 后不再检查，
    逗号分布偏斜时前半段仍 >32 直通（43 字巨镜）。现在每一段入队重查，直到全合格。"""
    res = []
    for body in out:
        queue = [body]
        while queue:
            b = queue.pop(0)
            if len(PUNCT.sub("", b)) <= max_len or not re.search(r"[，、]", b):
                res.append(b)
                continue
            commas = [m.start() for m in re.finditer(r"[，、]", b)]
            c = min(commas, key=lambda x: abs(x - len(b) / 2))
            a1 = b[:c + 1].rstrip("，、： ")
            if not a1.endswith(("。", "！", "？", "；")):
                a1 += "。"
            a2 = b[c + 1:].lstrip("，、： ")
            if a2 and a2[-1] not in "。！？；":
                a2 += "。"
            queue = [a1, a2] + queue          # 队头插入保序：a1 拆完才是 a2
    return res


def cut_phrases(text, min_len=18):
    raw = [x.strip() for x in re.split(r"(?<=[。！？；])", text) if x.strip()]
    fixed = []
    for x in raw:
        body = x[:-1] if x and x[-1] in "。！？；" else x
        fixed.append(body.rstrip("，、： ") + "。")
    lens = [len(PUNCT.sub("", x)) for x in fixed]
    # ★定场句独立成镜：含「今天我们讲」的句子前后强制断组，不被 min_len 合并吞掉
    #   （轮播要精确盖到定场句尾，镜界必须落在它后面）
    groups, cur = [], []
    for s, L in zip(fixed, lens):
        if BANNER_MARK in s and cur:
            groups.append(cur)
            cur = []
        cur.append((s, L))
        if BANNER_MARK in s:
            groups.append(cur)
            cur = []
    if cur:
        groups.append(cur)
    merged = []
    for g in groups:
        acc, k = "", 0
        for s, L in g:
            acc += s
            k += L
            if k >= min_len:
                merged.append(acc)
                acc, k = "", 0
        if acc:
            merged.append(acc)
    out = []
    for s in merged:
        body = s[:-1] if s and s[-1] in "。！？；" else s
        body = re.sub(r"(?<=[^，、：])。(?=[^，、：])", "，", body)
        out.append(body.rstrip("，、： ") + "。")
    return split_long(out)


def banner_mix(S, base_video, dur, scroll_end, click_at, out_wav, sfx_scroll, sfx_dong, sfx_as_is=False, sfx_gain=0.72):
    """★卷播混音同源定式：底片音轨前段为基底 + 滚动音0.45 + 咚0.40，峰值限0.92
    sfx_as_is=True（2026-10-09 灿爹拍板）：滚动音原样铺、不重采样伸缩——
    「突然加速到暂停」这类自带节奏结构的音效，拉伸会毁掉它的加减速感。
    音效从 0s 起播自然放完，超出 dur 的部分截断；原模式下才压缩铺满 scroll_end。"""
    import wave as wv
    SR = 48000
    N = int(dur * SR)
    tmp = "/tmp/_bm_base.wav"
    subprocess.run([FF, "-y", "-v", "error", "-i", base_video, "-t", str(dur),
                    "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", tmp], check=True)
    with wv.open(tmp, "rb") as w:
        mix = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768
    if len(mix) < N:
        mix = np.pad(mix, (0, N - len(mix)))

    def rd(p):
        t = "/tmp/_bm_sfx.wav"
        subprocess.run([FF, "-y", "-v", "error", "-i", p, "-ar", str(SR), "-ac", "1",
                        "-c:a", "pcm_s16le", t], check=True)
        with wv.open(t, "rb") as w:
            return np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768

    sfx = rd(sfx_scroll)
    if sfx_as_is:
        e = min(len(sfx), N)
        mix[:e] += sfx[:e] * sfx_gain            # ★片头音效要响亮突出（2026-10-09 灿爹），增益 config sfx_scroll_gain 可调
    else:
        tgt = int(scroll_end * SR)
        idx = np.linspace(0, len(sfx) - 1, tgt)
        mix[:tgt] += np.interp(idx, np.arange(len(sfx)), sfx).astype(np.float32) * 0.45
    dong = rd(sfx_dong)[:int(0.6 * SR)]
    if len(dong):
        dong = dong / max(float(np.abs(dong).max()), 1e-9) * 0.40
        s0 = int(click_at * SR); n2 = min(len(dong), N - s0)
        mix[s0:s0 + n2] += dong[:n2]
    # ★软限幅（2026-10-09 灿爹「0.72 不够响、1.2 直接炸」）：高增益瞬态用 tanh
    #   软压——响度感上去、尖峰不硬削（硬 clip=「炸」，tanh=「推」）。写文件前的
    #   np.clip 保留作最后保险，正常不再触发。
    mix = np.tanh(mix * 1.3) / np.tanh(1.3)
    pk = float(np.abs(mix).max())
    if pk > 0.95:
        mix *= 0.95 / pk
    with wv.open(out_wav, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(mix, -1, 1) * 32767).astype("<i2").tobytes())
    print(f"  banner 混音 ✓ {dur}s（底片同源基底）", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--text", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--skip-banner", action="store_true")
    ap.add_argument("--skip-tts", action="store_true", help="复用已有配音（断点续跑）")
    ap.add_argument("--tts-only", action="store_true",
                    help="★先配音后渲画面（2026-10-11 灿爹）：只跑切镜+配音即退出，"
                         "供脉络图确认期间先出音；确认后加 --skip-tts 续跑渲画面")
    ap.add_argument("--shot-map", help="分镜语义映射 JSON：数组长度=镜数-1（镜2..镜N），"
                    "元素=素材相对路径（图）或 {src, kind:'imgvid', motion}（视频镜）；不给则均匀采样")
    a = ap.parse_args()

    C = json.load(open(a.config, encoding="utf-8"))
    text = open(a.text, encoding="utf-8").read().strip()
    W = C["workspace"]
    S = f"{W}/{a.name}"
    os.makedirs(f"{S}/audio/seg", exist_ok=True)
    os.makedirs(f"{S}/render", exist_ok=True)
    t_start = time.time()
    cut = click_at = scroll_end = 0.0
    banner = None

    # ── 1. 切镜 ──
    print("══ 1/7 切镜", flush=True)
    phrases = cut_phrases(text)
    n = len(phrases)
    open(f"{S}/phrases.txt", "w").write("\n".join(phrases) + "\n")
    print(f"  {n} 镜", flush=True)
    for i, p in enumerate(phrases, 1):
        print(f"   镜{i:02d} {len(PUNCT.sub('', p))}字  {p[:20]}", flush=True)

    shots = C["shots"]
    if a.shot_map:
        # ★语义映射（2026-10-09 灿爹「文案内容要和画面图片对齐，合适文案优先视频」）：
        # 映射由目视素材+语义判断产出，流水线照单执行；元素支持视频镜 kind=imgvid。
        sm = json.load(open(a.shot_map, encoding="utf-8"))
        # ★2026-10-10：len(sm)==n 表示「镜1 专用位」——skip-banner 且无旧卷播时，
        # 钩子镜(镜1)与定场句镜(镜2)不再共用 mapping[0]，画面可各自指定。
        if len(sm) not in (n - 1, n):
            raise RuntimeError(f"shot_map {len(sm)} 项 ≠ 内容镜 {n-1}（或含镜1 专用位的 {n}）")
        mapping = []
        for x in sm:
            e = x if isinstance(x, dict) else {"src": x}
            src = e["src"] if e["src"].startswith("/") else f"{W}/{e['src']}"
            mapping.append({"src": src, "shot": e.get("shot", "mid"),
                            "kind": e.get("kind", "img"), "motion": e.get("motion", "")})
        print(f"  (语义映射 {len(mapping)} 镜，其中视频镜 "
              f"{sum(1 for m in mapping if m['kind']=='imgvid')} 个)", flush=True)
    else:
        if n - 1 > len(shots):
            raise RuntimeError(f"内容镜 {n-1} > 素材 {len(shots)}")
        idx = [round(k * (len(shots) - 1) / max(1, (n - 2))) for k in range(n - 1)] if n > 1 else []
        mapping = [{"src": f"{W}/{shots[min(len(shots)-1, i)]}", "shot": "mid",
                    "kind": "img", "motion": ""} for i in idx]
    motion_t = C.get("motions", {})
    motions = [m["motion"] or motion_t.get(m["src"].split("/")[-1][:4], "zoom_in")
               for m in mapping]
    # ★文案侧车（2026-10-09）：与文案同名的 .kw.json —— keywords/trigger_sfx/video_map。
    #   video_map: {"文案片段关键词": "vids/xxx.mp4"} → 命中句改用视频镜（imgvid，优先视频素材）
    _kwf = os.path.splitext(a.text)[0] + ".kw.json"
    kwx = json.load(open(_kwf, encoding="utf-8")) if os.path.exists(_kwf) else {}
    vmap = kwx.get("video_map", {})
    if vmap:
        print(f"  video_map: {len(vmap)} 条视频映射", flush=True)

    # ── ★多音字校正表（2026-10-13）：config poly_map + 文案同名 .poly.json 侧车
    #    （P0 多音字校审产出；侧车优先级高于 config，可覆盖内置同义项）
    _polyf = os.path.splitext(a.text)[0] + ".poly.json"
    poly_map = dict(C.get("poly_map", {}))
    if os.path.exists(_polyf):
        with open(_polyf, encoding="utf-8") as _pf:
            poly_map.update(json.load(_pf))
        print(f"  poly: 载入项目多音字校正 {_polyf}（{len(poly_map)} 项）", flush=True)
    elif poly_map:
        print(f"  poly: 使用 config 多音字校正（{len(poly_map)} 项）", flush=True)

    # ── 2. F5 配音 ──
    print("══ 2/7 F5 配音", flush=True)
    if a.skip_tts and os.path.exists(f"{S}/audio/durations.json"):
        print("  (复用已有配音)", flush=True)
    else:
        sents = [{"text": s, "speed": 1.35 if i == 0 else 1.2} for i, s in enumerate(phrases)]
        # ★2026-10-14：必读词校验表。灿爹报「今天我们讲，碳蛋脸」被吞成「讲蛋脸」——
        #   F5 在长句尾段有吞字概率，事后没人发现即进成片。把**重点字/数字/产品名**
        #   交给 f5_batch 做 ASR 复判，缺字自动重合成（最多 3 次）。
        _must = []
        _declp = os.path.splitext(a.text)[0] + ".fx.json"
        if os.path.exists(_declp):
            try:
                _dj = json.load(open(_declp, encoding="utf-8"))
                _must += [w["text"] for w in _dj.get("words", []) if w.get("text")]
                _must += [c.get("text") or str(c.get("target", ""))
                          for c in _dj.get("counts", [])]
            except Exception:
                pass
        if C.get("product"):
            _must.append(C["product"])
        _must = [str(m) for m in _must if m and len(str(m)) >= 2]
        jobs = {"ref_audio": C["ref_audio"], "ref_text": C["ref_text"], "gap": 0.22,
                "norm_map": C.get("norm_map", {}),
                "poly_map": poly_map,
                "must_words": _must,
                "out": f"{S}/audio/vo.wav", "durations": f"{S}/audio/durations.json",
                "sentences": sents}
        json.dump(jobs, open(f"{S}/f5_jobs.json", "w"), ensure_ascii=False, indent=1)
        r = subprocess.run([F5_PY, f"{SKILL}/scripts/f5_batch.py", "--jobs", f"{S}/f5_jobs.json"],
                           env={**os.environ, "HF_HUB_OFFLINE": "1"},
                           capture_output=True, text=True)
        print(r.stdout[-500:], flush=True)
        if r.returncode != 0:
            print(r.stderr[-800:]); raise RuntimeError("F5 失败")
    durs = json.load(open(f"{S}/audio/durations.json", encoding="utf-8"))["sentences"]
    assert len(durs) == n, f"配音句数 {len(durs)} ≠ 镜数 {n}"
    # ★轮播覆盖范围：有定场句 → 盖到定场句尾（含）；无 → 默认盖第一句
    banner_k = next((i for i, p in enumerate(phrases) if BANNER_MARK in p), 0)
    hook_dur = durs[banner_k]["end"]

    # ── ★TTS-only 出口：配音完成即返回，渲画面等脉络图确认后 --skip-tts 续跑 ──
    if a.tts_only:
        print(f"\n══ TTS-only 完成 {time.time()-t_start:.0f}s → {S}/audio/vo.wav"
              f"（确认脉络图后加 --skip-tts 续跑）", flush=True)
        return

    # ── 3. 3D 卷播 ──
    if not a.skip_banner:
        print("══ 3/7 3D 卷播", flush=True)
        cut = round(hook_dur + 0.40, 2)          # ★切点=轮播覆盖段句尾+0.40（语音缝内）
        click_at = round(cut - 0.95, 2)          # clickAt+zoomDur(0.55) ≤ CUT-0.40
        scroll_end = round(click_at - 0.40, 2)
        # ★2026-10-09 灿爹拍板：轮播长短跟文案/配音走 → 卡数按滚动时长扩
        #   （约 0.45s/张的滚动节奏，池子不够就封顶；生成端同步替换 M.nScroll）
        _pool = sorted(glob.glob(f"{W}/rem3d/public/{C.get('cards_dir', 'cards_d')}/c*.jpg"))
        n_scroll = max(7, min(len(_pool) - 3, int(scroll_end / 0.45) + 1))
        R3 = f"{W}/rem3d"
        src = open(f"{R3}/src/MarqueeClickD.tsx").read()
        tblock = (f"export const T = {{\n  scrollEnd: {scroll_end},\n  cursorIn: {scroll_end},\n"
                  f"  clickAt: {click_at},\n  zoomDur: 0.55,\n  totalS: {cut},\n"
                  f"  focusAt: {scroll_end},\n}};")
        s2 = re.sub(r"export const T = \{[^}]+\}", tblock, src, count=1)
        s2 = re.sub(r"nScroll: \d+,", f"nScroll: {n_scroll},", s2, count=1)
        s2 = s2.replace("MarqueeClickD", f"MarqueeClick{a.name.upper()}")
        open(f"{R3}/src/MarqueeClick{a.name.upper()}.tsx", "w").write(s2)
        ce = open(f"{R3}/src/click_entry_d.tsx").read().replace("MarqueeClickD", f"MarqueeClick{a.name.upper()}")
        ce = re.sub(r'id="MClick\w+"', f'id="MClick{a.name.upper()}"', ce)
        # ★卡池参数化：CARDS 块按 cards_dir + n_scroll 重新生成（E条定式：
        #   c00..c{N-1} 滚动 → 索引 N 主图 → 右侧 3 张陪衬）
        cd = C.get("cards_dir", "cards_d")
        ce = re.sub(r"const SCROLL = \[[^\]]*\];[\s\S]*?const CARDS = \[[\s\S]*?\];",
                    f"const N = {n_scroll};\nconst CARDS = [\n"
                    f"  ...Array.from({{length: N}}, (_, i) => ({{src: `{cd}/c${{String(i).padStart(2, '0')}}.jpg`}})),\n"
                    f"  {{src: `{cd}/hero.jpg`}},   // ★ 索引 N = 主图，滚动终点\n"
                    f"  ...Array.from({{length: 3}}, (_, i) => ({{src: `{cd}/c${{String(N + i).padStart(2, '0')}}.jpg`}})),\n"
                    f"];", ce, count=1)
        # ★Hero 三层字效（2026-10-09 灿爹定稿）：分层图存在时注入产品名字效参数；
        #   缺 hero_bg/hero_fg 则不注入，组件自动退回普通 Card（老卡池不受影响）。
        cd_p = f"{W}/rem3d/public/{cd}"
        if os.path.exists(f"{cd_p}/hero_fg.png") and os.path.exists(f"{cd_p}/hero_bg.jpg"):
            import json as _j
            _hl = _j.dumps(C.get("hero_label", C.get("product", "")), ensure_ascii=False)
            ce = ce.replace("defaultProps={{cards: CARDS}}",
                f"defaultProps={{{{cards: CARDS, heroLabel: {_hl}, "
                f"heroBg: `{cd}/hero_bg.jpg`, heroFg: `{cd}/hero_fg.png`}}}}", 1)
            print(f"  Hero 字效: {_hl}（{cd}/hero_bg+hero_fg）", flush=True)
        open(f"{R3}/src/click_entry_{a.name}.tsx", "w").write(ce)
        shutil.rmtree(f"{R3}/node_modules/.cache", ignore_errors=True)
        env_r = {**os.environ,
                 "PATH": "/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin:" + os.environ["PATH"],
                 "NODE_OPTIONS": ""}
        env_r.pop("CODEBUDDY_BROKERED_FS_HOOK_ENABLED", None)
        sh([NODE, "remotion", "render", f"src/click_entry_{a.name}.tsx",
            f"MarqueeClick{a.name.upper()}", f"out/banner_{a.name}.mp4",
            "--concurrency=3", "--log=error"], cwd=R3, env=env_r)
        banner = f"{R3}/out/banner_{a.name}.mp4"
    else:
        # ★--skip-banner 复用模式：不重渲，读旧 banner + 旧 T 时序做切点。
        #   切点必须跟 banner 内容走（totalS 渲进画面了），不能再用新 hook_dur 推，
        #   否则视频轨/音频轨长度不一致 → 音画错位（2026-10-09 补）。
        _tsx = f"{W}/rem3d/src/MarqueeClick{a.name.upper()}.tsx"
        _old = f"{W}/rem3d/out/banner_{a.name}.mp4"
        if os.path.exists(_old) and os.path.exists(_tsx):
            _tb = re.search(r"export const T = \{([^}]+)\}", open(_tsx).read())
            _kv = dict(re.findall(r"(\w+):\s*([\d.]+)", _tb.group(1)))
            cut = float(_kv["totalS"])
            scroll_end = float(_kv["scrollEnd"])
            click_at = float(_kv["clickAt"])
            banner = _old
            print(f"  (复用已有卷播 cut={cut} scrollEnd={scroll_end} clickAt={click_at})", flush=True)
        else:
            print("  ⚠️ --skip-banner：无可用旧卷播，本次无片头卷播", flush=True)

    # ── 4. token ──
    print("══ 4/7 token 时间轴", flush=True)
    sh([PY, f"{SKILL}/scripts/mk_tokens_f5.py", "--durations", f"{S}/audio/durations.json",
        "--phrases", f"{S}/phrases.txt", "--out-dir", f"{S}/audio"])

    # ── 5. 底片渲染 ──
    print("══ 5/7 底片渲染", flush=True)
    # ★2026-10-10 镜1 专用位：无卷播且 shot_map 给满 n 项时，镜1=mapping[0]，
    #   镜2..镜N=mapping[1..n-1]；否则维持旧行为（mapping[0] 由镜1/镜2 共用）。
    _m1 = 1 if (banner is None and len(mapping) == n) else 0
    # ★2026-10-14 BUG 修复：原写法 `"bgm": kwx.get("bgm", C.get("bgm", ""))` 在用户
    #   没指定 BGM 时写入空串 ""，而 build_ad_video 把 ""/none/false 一律判为「已关闭」，
    #   结果 4 条成片全部静默没 BGM（灿爹 2026-10-14 报）。正确做法：没指定就不写这个键，
    #   让 build_ad_video 走「曲池随机」默认（含 ensure_bgm_len 铺满 + 闪避）。
    _bgm_lock = kwx.get("bgm") or C.get("bgm") or None
    if isinstance(_bgm_lock, str) and _bgm_lock.strip().lower() in ("", "none", "off", "false"):
        _bgm_lock = None
    cfg = {"workdir": S, "output": f"{S}/render/out_base.mp4",
           "brand": C["brand"], "product": C["product"],
           "vo": f"{S}/audio/vo.wav", "tokens": f"{S}/audio/vo_mapped_v2.json",
           "opening_burst": False, "transition_whoosh": [],
           "keywords": kwx.get("keywords", []),
           # ★2026-10-10：详情卡/白名单从文案侧车 .kw.json 读入（直渲路线同名配置语义）
           "product_cards": kwx.get("product_cards", []),
           "card_src_ok": kwx.get("card_src_ok", []),
           "card_src_ok_prefixes": kwx.get("card_src_ok_prefixes", []),
           "card_min_sec": kwx.get("card_min_sec", 2.0),
           "trigger_sfx": kwx.get("trigger_sfx", []),
           # ★2026-10-13：sfx_files 侧车透传（叙事音效 narr_* 按绝对路径临时注册，
           #   与 vorse s2/s3 定式一致；不给则维持空 dict，老路不受影响）
           "sfx_files": kwx.get("sfx_files", {}),
           # ★2026-10-13：卡拉OK字幕的红色重点字表（与 fx 层 words 同源；
           #   不走 keywords——那会触发 ASS 中央大字层与 fx 层重影）
           "karaoke_kw": kwx.get("karaoke_kw", []),
           # ★2026-10-13 灿爹定稿：字幕效果体系——sub_style 默认 "random"（渲染时随机一款打日志），
           #   侧车 .kw.json 或 config 可写死 style id 复现；sub_karaoke 只在显式配置时透传，
           #   不再强制 False（否则随机默认永远进不来）
           "sub_style": kwx.get("sub_style", C.get("sub_style", "random")),
           # ★2026-10-13：bgm 透传（#231④ BGM 锁定——不透传则 build 曲池随机换曲，
           #   重渲同一条片 BGM 变味的根因；config 顶层或 .kw.json 侧车写绝对路径）
           # ★2026-10-14：未显式指定时**不写该键**（写 "" 会被判成关闭，见上方注释）
           **({"bgm": _bgm_lock} if _bgm_lock else {}),
           **({"sub_karaoke": kwx.get("sub_karaoke", C["sub_karaoke"])}
              if ("sub_karaoke" in C or "sub_karaoke" in kwx) else {}),
           "swing_shots": [], "sub_highlights": [], "sub_skip_shots": [],
           "sub_size": C.get("sub_size", 78), "fps": 30, "width": 1920, "height": 1080,
            "phrases": (
               # ★卷播镜禁止 own_audio：Remotion 卷播带的是 -91dB 静音轨，
               #   会被 build_ad_video 误判为「自带混音」→ 屏蔽全局 vo 的钩子段
               #   → 开头轮播没人声（2026-10-09 实测翻车）。混音一律走 banner_mix
               #   同源定式（底片音轨前段为基底 + 滚动音 + 咚），钩子人声天然在内。
               # ★cover_until/extend 也不要：会顶开后续镜头顺延 0.2s+，音画错位；
               #   底片镜1 画面反正被 banner 整段盖住，无需声明。
               [{"text": phrases[0], "kind": "prod", "video": banner or "",
                 "src": [0.0, cut]}] if banner else
               [{"text": phrases[0], "src": mapping[0]["src"], "shot": "mid",
                 "motion": motions[0], "kind": mapping[0]["kind"]}]
           ) + [({"text": t, "src": os.path.join(W, next(v for p, v in vmap.items() if p in t)),
                  "kind": "imgvid"}
                 if (not a.shot_map and any(p in t for p in vmap)) else
                 {"text": t, "src": mapping[i-1+_m1]["src"], "shot": mapping[i-1+_m1]["shot"],
                  "motion": motions[i-1+_m1], "kind": mapping[i-1+_m1]["kind"]})
                for i, t in enumerate(phrases[1:], 1)]}
    json.dump(cfg, open(f"{S}/config.json", "w"), ensure_ascii=False, indent=1)
    r = subprocess.run([PY, f"{SKILL}/scripts/build_ad_video.py", f"{S}/config.json"],
                       capture_output=True, text=True)
    # ★2026-10-11：卡片/sfx 的关键日志必须回显——build 曾出现「卡片落位非确定性静默
    #   丢失」，成功路径全吞 stdout 导致无人察觉（爱兰纯二渲翻车）。命中行逐条打印。
    for _ln in (r.stdout or "").splitlines():
        if re.search(r"卡片门禁|卡片叠加|卡片.*落位|❌|WARN|trigger_sfx|音效:|BGM:", _ln):
            print("  " + _ln.strip()[:150], flush=True)
    if not os.path.exists(f"{S}/render/out_base.mp4"):
        print(r.stdout[-500:]); print(r.stderr[-500:])
        raise RuntimeError("底片渲染失败")
    print("  底片 ✓", flush=True)

    # ── 6. 合成（fx 层 overlay + banner 混音同源 + 拼接；底片已含封面帧）──
    print("══ 6/7 合成", flush=True)
    base_fx = f"{S}/render/out_base.mp4"
    # ★2026-10-09 灿爹拍板：重点字视觉全走 Remotion fx 层（PNG 序列，v22 定式），
    #   ASS 大字停用（keywords 一律 []）。音效仍由 build_ad_video 的 trigger_sfx 出。
    #   侧车 .fx.json（words/counts 落位+配色）→ make_fx_spec.py 用 vo token 实测锚点
    #   → 生成 fx_entry_{name}.tsx → remotion --sequence 渲 PNG → ffmpeg overlay 底片。
    if C.get("fx", {}).get("enabled"):
        _decl = os.path.splitext(a.text)[0] + ".fx.json"
        _fxdir = f"{W}/rem3d/out/fx_{a.name}"
        if os.path.exists(_decl):
            # ★2026-10-09 动效库收编（灿爹样片带选型 1/2/3 号）：渲前从技能资产
            #   同步 fx 三件套（KwOverlay/DataViz/fx_comp_j）——技能更新自动进工作区
            _libsrc = f"{SKILL}/assets/rem3d_src"
            if os.path.isdir(_libsrc):
                for _f in ("KwOverlay.tsx", "DataViz.tsx", "fx_comp_j.tsx"):
                    _dst = f"{W}/rem3d/src/{_f}"
                    _new = open(f"{_libsrc}/{_f}", encoding="utf-8").read()
                    if not os.path.exists(_dst) or open(_dst, encoding="utf-8").read() != _new:
                        open(_dst, "w", encoding="utf-8").write(_new)
                        print(f"  动效库同步: {_f}", flush=True)
            sh([PY, f"{SKILL}/scripts/make_fx_spec.py",
                "--mapped", f"{S}/audio/vo_mapped_v2.json",
                "--phrases", f"{S}/phrases.txt",
                "--decl", _decl, "--out", f"{W}/rem3d/src/fx_spec_{a.name}.json"])
            _dur = float(subprocess.run(
                [FP, "-v", "error", "-show_entries", "format=duration",
                 "-of", "csv=p=0", f"{S}/render/out_base.mp4"],
                capture_output=True, text=True).stdout.strip() or 0)
            _spec = json.load(open(f"{W}/rem3d/src/fx_spec_{a.name}.json", encoding="utf-8"))
            _spec["totalFrames"] = int(_dur * 30) + 2
            json.dump(_spec, open(f"{W}/rem3d/src/fx_spec_{a.name}.json", "w", encoding="utf-8"),
                      ensure_ascii=False)
            # ★2026-10-14 灿爹返P Lane「3D景深效果糟糕」根治：behind 字**按底片里人物的
            #   实际轮廓自动落位**（plan_behind_pos，Vision 显著性取人物 bbox → 字跨到轮廓
            #   外侧零少女 ⇢ 一半藏一半露）。手写坐标 + 静态椭圆正是上一版翻车的原因。
            if any(k.get("behind") for k in _spec.get("kw", [])):
                try:
                    sh([PY, f"{SKILL}/scripts/plan_behind_pos.py",
                        "--base", f"{S}/render/out_base.mp4",
                        "--spec", f"{W}/rem3d/src/fx_spec_{a.name}.json"])
                except Exception as _e:
                    print(f"  ⚠️ behind 自动落位失败，沿用 decl 坐标：{_e}", flush=True)
            _spec = json.load(open(f"{W}/rem3d/src/fx_spec_{a.name}.json", encoding="utf-8"))
            _entry = f"""import React from 'react';
import {{registerRoot, Composition}} from 'remotion';
import {{loadFont}} from '@remotion/fonts';
import {{staticFile, delayRender, continueRender}} from 'remotion';
import {{FxLayerJ}} from './fx_comp_j';
import spec from './fx_spec_{a.name}.json';

const h = delayRender('加载新青年体');
loadFont({{family: 'WenYue XinQingNianTi', url: staticFile('新青年体.ttf'), weight: '800'}})
  .then(() => continueRender(h))
  .catch(() => continueRender(h));

const FPS = 30;
// ★2026-10-14 灿爹点名「重点字3D景深、被人物遮住一点」：behind 重点字单独一个 comp，
//   pipeline 渲两条 PNG 序列，ffmpeg 先 behind(在下)+底片人物(在上) 合成一层，
//   再把 front(重点字+viz) 盖回去 —— 字被人物遮住一点，有景深。
const allKw = (spec.kw as any[]);
const behindKw = allKw.filter((k: any) => k.behind);
const frontKw = allKw.filter((k: any) => !k.behind);
const hasBehind = behindKw.length > 0;
registerRoot(() => (
  <>
    {{hasBehind && (
      <Composition id="FxLayer{a.name.capitalize()}Behind" component={{FxLayerJ}}
        durationInFrames={{spec.totalFrames}} fps={{FPS}} width={{1920}} height={{1080}}
        defaultProps={{{{kw: behindKw, viz: []}}}} />
    )}}
    <Composition id="FxLayer{a.name.capitalize()}" component={{FxLayerJ}}
      durationInFrames={{spec.totalFrames}} fps={{FPS}} width={{1920}} height={{1080}}
      defaultProps={{{{kw: frontKw, viz: spec.viz}}}} />
  </>
));
"""
            open(f"{W}/rem3d/src/fx_entry_{a.name}.tsx", "w").write(_entry)
            shutil.rmtree(f"{W}/rem3d/node_modules/.cache", ignore_errors=True)
            env_fx = {**os.environ,
                      "PATH": "/Users/mac/.workbuddy/binaries/node/versions/22.22.2-6/bin:" + os.environ["PATH"],
                      "NODE_OPTIONS": ""}
            env_fx.pop("CODEBUDDY_BROKERED_FS_HOOK_ENABLED", None)
            sh([NODE, "remotion", "render", f"src/fx_entry_{a.name}.tsx",
                f"FxLayer{a.name.capitalize()}", f"out/fx_{a.name}",
                "--sequence", "--log=error"],
               cwd=f"{W}/rem3d", env=env_fx)
            _has_behind = any(k.get("behind") for k in _spec.get("kw", []))
            sh([NODE, "remotion", "render", f"src/fx_entry_{a.name}.tsx",
                f"FxLayer{a.name.capitalize()}", f"out/fx_{a.name}",
                "--sequence", "--log=error"],
               cwd=f"{W}/rem3d", env=env_fx)
            if _has_behind:
                sh([NODE, "remotion", "render", f"src/fx_entry_{a.name}.tsx",
                    f"FxLayer{a.name.capitalize()}Behind", f"out/fx_{a.name}_behind",
                    "--sequence", "--log=error"],
                   cwd=f"{W}/rem3d", env=env_fx)
                print("  behind 景深序列 ✓", flush=True)
            # ★Remotion --sequence 编号位数随总帧数自适应（≥1000 帧 %04d，否则 %03d），
            #   overlay 输入模板必须按实际产物探测（2026-10-09 v2 944 帧 %03d 踩坑）
            _fxfiles = sorted(glob.glob(f"{_fxdir}/element-*.png"))
            if not _fxfiles:
                raise RuntimeError(f"fx 序列空：{_fxdir}")
            _pad = len(os.path.basename(_fxfiles[0]).split("-")[1].split(".")[0])
            if _has_behind:
                # ① 人物遮挡蒙版：从**已渲染的底片**逐帧抽前景做 Vision 显著性，
                #   白=无人（保留字 alpha），黑=有人（抠掉字 alpha → 露出 base 人体）。
                #   ★2026-10-14：旧版用静态椭圆近似人物，跟着运镜/AI 动态视频素材必然
                #   错位（「字被切一块」观感）。逐帧分割天然跟随画面。
                #   ⚠️ alphamerge 会用蒙版值替换 PNG 全部 alpha —— 蒙版绝不能做成
                #     「全白底+黑椭圆」（非字区会被设成不透明黑，黑块盖死整帧）。
                _maskdir = f"{W}/rem3d/out/fx_{a.name}_mask"
                _frdir = f"{W}/rem3d/out/fx_{a.name}_frames"
                shutil.rmtree(_frdir, ignore_errors=True)
                os.makedirs(_frdir, exist_ok=True)
                for _k in _spec.get("kw", []):
                    if not _k.get("behind"):
                        continue
                    _f0 = int(round(_k["startSec"] * 30)) - 2
                    _f1 = int(round((_k["startSec"] + _k.get("durSec", 1.8)) * 30)) + 3
                    sh([FF, "-y", "-v", "error", "-i", f"{S}/render/out_base.mp4",
                        "-vf", f"trim=start_frame={max(0, _f0)}:end_frame={_f1},setpts=PTS-STARTPTS",
                        "-start_number", str(max(0, _f0)),
                        f"{_frdir}/frame-%0{_pad}d.png"])
                sh([PY, f"{SKILL}/scripts/make_behind_mask.py",
                    "--spec", f"{W}/rem3d/src/fx_spec_{a.name}.json",
                    "--frames", _frdir, "--out", _maskdir, "--pad", str(_pad)])
                # ② behind 序列 alphamerge 蒙版 → 垫到 base 下 ⇒ 人体遮住字一点（3D 景深）
                sh([FF, "-y", "-v", "error", "-framerate", "30",
                    "-i", f"{_fxdir}_behind/element-%0{_pad}d.png",
                    "-i", f"{_maskdir}/mask-%0{_pad}d.png",
                    "-i", f"{S}/render/out_base.mp4",
                    "-filter_complex",
                    "[0:v][1:v]alphamerge[b];[2:v][b]overlay=0:0:eof_action=endall:format=auto[v]",
                    "-map", "[v]", "-c:v", "libx264", "-crf", "18",
                    "-preset", "fast", f"{S}/render/base_fx_mid.mp4"])
                # ③ front 重点字 + viz 盖回最上层；音频从原始底片带回
                sh([FF, "-y", "-v", "error", "-framerate", "30",
                    "-i", f"{_fxdir}/element-%0{_pad}d.png",
                    "-i", f"{S}/render/base_fx_mid.mp4",
                    "-i", f"{S}/render/out_base.mp4",
                    "-filter_complex",
                    "[1:v][0:v]overlay=0:0:eof_action=endall:format=auto[v]",
                    "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-crf", "18",
                    "-preset", "fast", "-c:a", "copy", f"{S}/render/base_fx.mp4"])
                print("  fx 层 overlay ✓（behind 景深 + front 双层合成）", flush=True)
            else:
                sh([FF, "-y", "-v", "error", "-framerate", "30",
                    "-i", f"{_fxdir}/element-%0{_pad}d.png", "-i", f"{S}/render/out_base.mp4",
                    "-filter_complex",
                    "[1:v][0:v]overlay=0:0:eof_action=endall:format=auto[v]",
                    "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-crf", "18",
                    "-preset", "fast", "-c:a", "copy", f"{S}/render/base_fx.mp4"])
                print("  fx 层 overlay ✓", flush=True)
            base_fx = f"{S}/render/base_fx.mp4"
        else:
            print(f"  ⚠️ fx.enabled 但缺 {_decl}，本次无 fx 层", flush=True)
    if banner:
        banner_mix(S, base_fx, cut, scroll_end, click_at,
                   f"{W}/banner/banner_final_{a.name}.wav",
                   C["sfx_scroll"], C["sfx_dong"],
                   sfx_as_is=bool(C.get("sfx_scroll_as_is")),
                   sfx_gain=float(C.get("sfx_scroll_gain", 0.72)))
        sh([FF, "-y", "-v", "error", "-i", banner,
            "-i", f"{W}/banner/banner_final_{a.name}.wav",
            "-ss", str(cut), "-i", base_fx,
            "-filter_complex",
            "[0:v]fps=30,settb=AVTB[v0];[1:a]aresample=48000,asettb=AVTB[a0];"
            "[2:v]fps=30,settb=AVTB[v1];[2:a]aresample=48000,asettb=AVTB[a1];"
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]",
            "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "18",
            "-preset", "fast", "-c:a", "aac", "-b:a", "192k", f"{S}/render/final.mp4"])
        # ★轮播段字幕二次烧录（2026-10-08 定式：卷播盖底片头会吃掉前几句字幕）
        #   从 subs.ass 截「start < cut」的事件（含跨缝句：轮播段显示句头、底片续句尾）
        if os.path.exists(f"{S}/subs.ass"):
            _fd = f":fontsdir={SKILL}/assets/fonts" if os.path.isdir(f"{SKILL}/assets/fonts") else ""
            _keep = []
            for _ln in open(f"{S}/subs.ass", encoding="utf-8").read().splitlines():
                if _ln.startswith("Dialogue:"):
                    _p = _ln.split(",", 9)
                    try:
                        _h, _m, _sec = _p[1].split(":")
                        if int(_h) * 3600 + float(_m) * 60 + float(_sec) < cut + 0.05:
                            _keep.append(_ln)
                    except (ValueError, IndexError):
                        pass
                else:
                    _keep.append(_ln)
            open(f"{S}/banner_subs.ass", "w", encoding="utf-8").write("\n".join(_keep) + "\n")
            print(f"  轮播字幕二次烧录：{len(_keep) - sum(1 for l in _keep if not l.startswith('Dialogue:'))} 条事件", flush=True)
            sh([FF, "-y", "-v", "error", "-i", f"{S}/render/final.mp4",
                "-vf", f"subtitles={S}/banner_subs.ass{_fd}",
                "-c:a", "copy", f"{S}/render/final_bsub.mp4"])
            try:
                os.replace(f"{S}/render/final_bsub.mp4", f"{S}/render/final.mp4")
            except PermissionError as _pe:
                print(f"  ⚠️ rename 被拦截（{_pe}）——final_bsub.mp4 保留，最终成片用它", flush=True)
    else:
        sh(["cp", base_fx, f"{S}/render/final.mp4"])

    # ── 6.5 封面帧（2026-10-08 灿爹定式：成片最前贴 1 帧常用封面，零音频）──
    #   ★带轮播的片子必须在这一步补：封面贴在底片头 0.1s，会被卷播整段
    #     替换吃掉（2026-10-09 v1-v3 三条封面丢失根因）。1 帧 0.034s@30fps，
    #     只占首帧供平台抓封面，不铺任何声音，音频原样 copy。
    _cover = f"{SKILL}/assets/cover/cover_frame_1080p.jpg"
    if os.path.exists(_cover):
        sh([FF, "-y", "-v", "error",
            "-loop", "1", "-t", "0.034", "-r", "30", "-i", _cover,
            "-i", f"{S}/render/final.mp4",
            "-filter_complex",
            "[0:v]settb=AVTB,fps=30[c];[1:v]settb=AVTB,fps=30[f];"
            "[c][f]concat=n=2:v=1:a=0[v]",
            "-map", "[v]", "-map", "1:a",
            "-c:v", "libx264", "-crf", "18", "-preset", "fast", "-c:a", "copy",
            f"{S}/render/final_cov.mp4"])
        try:
            os.replace(f"{S}/render/final_cov.mp4", f"{S}/render/final.mp4")
        except PermissionError as _pe:
            print(f"  ⚠️ rename 被拦截（{_pe}）——final_cov.mp4 保留，最终成片用它", flush=True)
        print("  封面帧 1 帧已贴 ✓", flush=True)
    else:
        print(f"  ⚠️ 缺封面帧资产 {_cover}，本次未贴封面", flush=True)

    # ── 7. 质检 ──
    print("══ 7/7 质检", flush=True)
    qc = subprocess.run(["bash", f"{SKILL}/scripts/qc_all.sh", f"{S}/render/final.mp4", S,
                         str(cut if banner else 3.7)], capture_output=True, text=True)
    print(qc.stdout, flush=True)

    print(f"\n════ 全流程完成 {time.time()-t_start:.0f}s → {S}/render/final.mp4", flush=True)


if __name__ == "__main__":
    main()
