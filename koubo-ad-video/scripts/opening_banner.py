#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
片头卷播横幅 (Conveyor Banner)
按参考教程 ref.mp4 复刻：
  段1 高速卷轴  v=+157px/帧@1920
  段2 惯性回滑  v=-43px/帧
  段3 定格主图  白框消失、中卡放大居中、字幕隐藏
黑底 + 竖卡列 + 固定白框取景窗 + 白/黄双色字幕
"""
import cv2, numpy as np, os, subprocess, argparse, math

def ease_in_out_cubic(u):
    """easeInOutCubic: u∈[0,1] → 0→1→0。取补函数 1−ease 得速度系数 1→0。
    ★ 全程渐慢用（2026-10-06 灿爹：「开始是这个速度，越后面越慢直到0」）"""
    u = min(1.0, max(0.0, u))
    if u < 0.5:
        return 4.0 * u * u * u
    return 1.0 - ((-2.0 * u + 2.0) ** 3) / 2.0



W, H, FPS = 1920, 1080, 30

# ---- 几何（从 ref.mp4 反推后按 1080 高度收敛，保证 2×CARD_H 也能进画面）----
# ★★ 2026-10-07 灿爹两处纠偏：
#   「16:9 上下很多黑边」⇒ 卡高顶到画面；
#   「框是对的但里面的图左右被切掉」（照片指出）⇒ **卡槽比例必须 = 主图比例**，
#   否则 contain 留黑边、cover 切内容，两头都不对。
#   ⇒ 卡槽取 16:9（900×506 ≈ 16:9），黑板图 1536×864 也是 16:9 ⇒ contain 后几乎零黑边。
# ★ 2026-10-07 对标参考视频实测调小（灿爹「效果差点」）：
#   原实测（568x320 参考视频）：卡节距/画面宽 26.4%、每屏 3.8 张、卡顶缘斜率 -0.215（3D 透视）
#   旧值：CARD_W 900 = 屏宽 46.9%，节距 960 = 屏宽 50%⇒ 每屏仅 2 张但卡过大、无透视感
#   新值：卡宽 560（屏宽 29%，接近参考的 27%）、节距 640（屏宽 33%）
#   ⇒ 每屏可见 ≈1920/640 = 3 张，与参考视频的 3.8 张同量级
CARD_W  = 560      # 横卡宽（对标参考视频 ≈27% 屏宽）
CARD_H  = 315      # 横卡高 ≈ 16:9
PITCH   = 640      # 卡片节距（卡宽 + 80 间隙）⇒ 屏宽 33%，每屏约 3 张
BOX_W   = 590      # 白框宽（卡宽 + 两侧白边）
BOX_H   = 345      # 白框高 ≈ 卡高 + 上下白边
BOX_LW  = 13       # 白框线宽
BOX_CY  = 540      # 白框中心 y（上下居中，字幕带留给 y≈950）
GAP     = PITCH - CARD_W

SUB_WHITE = (255, 255, 255)
SUB_YELLOW= (60, 232, 238)   # BGR ≈ #EEE83C 亮黄

FONT = "/System/Library/Fonts/Hiragino Sans GB.ttc"

def load_card(path, w=CARD_W, h=CARD_H, fit=False):
    """统一裁切填满卡槽，cover 不变形；返回 BGR + alpha

    ★fit=True（2026-10-07 灿爹「主图换成指着黑板有肌酸字」）：
      16:9 横图塞进 406×800 竖卡会 cover 裁掉两侧，
      「肌酸」大字正好在左上角 ⇒ 白框里只剩一片黑。
      主图必须**完整可见**，改为「等比缩放 + 居中留白」，
      卡槽上下补黑（黑底本来就黑，看不出接缝）。
    """
    im = cv2.imread(path)
    if im is None:
        raise SystemExit(f"读不到: {path}")
    ih, iw = im.shape[:2]
    if fit:
        # ★★★ 主图（卡列最后一张 = 白框最后框到的那张）必须**完整显示，绝不裁切**。
        #   2026-10-07 灿爹照片指出：「框是对的，但里面的图左右被切掉了」。
        #   根因：为了「铺满卡槽」按宽度 cover，900px 卡去裁 1536px 横图 ⇒ 左右各切 17%，
        #   黑板两侧木框没了，看着像"没框住主画面"。
        #   ⇒ contain（等比缩放 + 居中），上下/左右补黑。黑底本就黑，接缝看不出。
        s = min(w / iw, h / ih)
        nw, nh = max(1, int(round(iw * s))), max(1, int(round(ih * s)))
        rs = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
        out = np.zeros((h, w, 3), np.uint8)
        xo, yo = (w - nw) // 2, (h - nh) // 2
        out[yo:yo + nh, xo:xo + nw] = rs
        im = out
    else:
        s = max(w / iw, h / ih)
        nw, nh = int(math.ceil(iw*s)), int(math.ceil(ih*s))
        im = cv2.resize(im, (nw, nh), interpolation=cv2.INTER_AREA)
        x = (nw - w)//2; y = (nh - h)//2
        im = im[y:y+h, x:x+w]
    a = np.full((h, w), 255, np.uint8)
    # 1px 黑色描边，贴合教程的卡面质感
    b = np.zeros((h, w, 3), np.uint8); b[:] = (34,34,34)
    mask = np.zeros((h, w), np.uint8); mask[:] = 255
    cv2.rectangle(mask, (1,1), (w-2, h-2), 0, 1)
    return im, b, mask

NO_SUB = False   # ★ True = 不画卷播内字幕（接入成片时由渲染器统一出，避免双层字幕）

def draw_sub(img, text_white, text_yellow, alpha, y):
    """双色字幕：前段白字 + 后段黄字，整体居中（PIL 中文字体）"""
    if alpha <= 0.01 or NO_SUB: return
    from PIL import Image, ImageDraw, ImageFont
    fh = 84
    font = ImageFont.truetype(FONT, fh, index=0)
    pil = Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil)
    w1 = d.textlength(text_white, font)
    w2 = d.textlength(text_yellow, font)
    x0 = (W - (w1 + w2))//2
    a = int(255*alpha)
    d.text((x0, y), text_white, font=font, fill=(255,255,255,a))
    d.text((x0 + w1, y), text_yellow, font=font, fill=(238,232,60,a))
    img[:] = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)

# ★ 灿爹拍板的速度结构（分句配音实测，dur=3.00s 时）：
#   0 → 1.64s   「19块9承包你3个月的健身成本」恒速卷播（v1 可调，看得清为准）
#   1.64 → 2.48s「今天我们讲」速度线性降到 0
#   2.48s       「肌酸」框中图动态放大 + 水滴音
# 两个转折点由 --t-hold（恒速结束）与 --focus-at（定格帧）给出，随 dur 自动缩放。
REF_SEG = (1.64, 0.84)   # 段1时长、段2时长（合计 2.48s 到定格）

def render(cards, out, dur=2.9, hold=0.0, sub_a="3个月的健身成本", sub_b="只需要39块9",
           hero=0, v1=95.0, v_hold=7.0, sfx=True,
           ripple_amp=0.0, ripple_lam=240.0, ripple_k=9.0, rip_cx=None,
           shake_amp=0.0, hero_src=None, no_sub=False,
           an_dur=0.95, shake_k=2.4, shake_gain=1.0,
           sub_c="", sub_d="", split=None, focus_at=None, card_sec=0.30):
    # 时长自适应：按 dur 等比压缩三段，段1 速度同步放大保持滚过量不变
    REF_TOT = sum(REF_SEG)                # 2.48s：恒速段 + 减速段
    # focus_at 是「肌酸」实测发出的绝对秒数，dur 是整句配音总时长。
    # 两者同为绝对时间，KS = dur/2.48 只用于把 REF_SEG 的比例映射到本片时长。
    # 时间轴标定在 3.00s 实测配音上（REF_SEG 来自分句实测）。
    # KS 是全片缩放因子：dur=3.00 时 =1.0，短版/长版按比例缩放。
    KS = dur / 3.00
    if focus_at:
        t3 = focus_at                               # 「肌酸」定格时刻（实测）
        # ★ 全程渐慢：不再需要「恒速段+减速段」两段式，t1 仅作兼容保留
        t1 = min(REF_SEG[0] * KS, t3 * 0.90)
    else:
        t1 = REF_SEG[0] * KS
        t3 = REF_TOT * KS
    if rip_cx is None:
        rip_cx = W / 2.0
    # 段1 速度不随 KS 放大：v1 是「每帧位移」，由可读性直接指定
    # 段2 从 v1 线性降到 0，位移 = v1 * 段2时长 / 2（梯形面积）
    n = len(cards)
    hero_im_full = None
    if hero_src:
        _hf = cv2.imread(hero_src)
        if _hf is None:
            raise SystemExit(f"读不到 hero-src: {hero_src}")
        hero_im_full = _hf
        print(f"   段3 主图用完整图：{os.path.basename(hero_src)} "
              f"{_hf.shape[1]}x{_hf.shape[0]}（不裁切主体）")
    global NO_SUB
    NO_SUB = bool(no_sub)
    print(f"   卷播内字幕: {'关闭（由成片渲染器统一出）' if NO_SUB else '开启'}")
    # ★主图（卡列最后一张 = 白框最后框到的那张）用 fit 模式，
    #   16:9 黑板图压成竖卡会裁掉「肌酸」大字 ⇒ 白框里只剩黑。
    _hero_path = cards[-1] if hero_src is None else None
    total = dur + hold
    NF = int(round(total*FPS))
    t1 = min(t1, t3*0.92)                 # 保证段1 有减速段可走
    NF1 = max(1, int(round(t1*FPS)))
    NFF = max(NF1+1, int(round(t3*FPS)))

    # ── 白框中心固定的取景窗 ──
    box_cx = W//2
    # 先算「停住时（段1末+段2末）白框中心正对哪张卡」，段3 放大就放大这一张，
    # 保证「框住哪张 → 放大哪张」是同一张（灿爹要求）。
    n2f = max(1, NFF - NF1)
    # ══════════════════════════════════════════════════════════════
    # ★★★ 素材数量由配音时长动态计算（2026-10-07 灿爹拍板）★★★
    #   旧写法：卡列张数写死在 --cards-file，滚过量由 v1 决定 ⇒
    #     配音一变长，滚过的节距就会超过卡列张数 ⇒ 同一张卡被卷两遍（重复轮播）；
    #     配音一变短，卡还没滚完就定格 ⇒ 画面提前播完、与语音节奏脱节。
    #   新写法：**先按配音时长算要几张**，再用张数反解速度——
    #     T_roll  = 卷播段实际时长（0 → focus-at，即定格时刻）
    #     n_scroll= round(T_roll / card_sec)      ← 需要滚过的张数
    #     卡列    = 素材池取 n_scroll 张 + 主图压最后（共 n_scroll+1 张）
    #     ⇒ 每张平均展示 T_roll/n_scroll 秒，与配音精确对齐；
    #       且滚过量 = n_scroll×节距 < 卡列长度 ⇒ **绝不重复、绝不空转**。
    #   素材池不够时：宁可放慢（n_scroll = 池子张数），也绝不循环复用。
    # ══════════════════════════════════════════════════════════════
    T_roll = NFF / FPS
    _hero_path = hero_src or cards[-1]
    pool = [c for c in cards if os.path.abspath(c) != os.path.abspath(_hero_path)]
    n_scroll = max(1, int(round(T_roll / card_sec)))
    # ★ 边界修正：卡列是**首尾相接的环**，滚过量 k 与卡列张数 N 满足 k = N−1 时，
    #   开场第 0 帧左缘会露出主图的右半张（同一张在开头和结尾各出现一次 = 重复轮播）。
    #   ⇒ 素材池必须比滚动张数多留 1 张（N = k+2），开场左缘才是第一张素材。
    if n_scroll + 1 > len(pool):
        print(f"   ⚠️ 素材池只有 {len(pool)} 张，按 {T_roll/card_sec:.1f} 张算不够 → "
              f"降为 {max(1, len(pool)-1)} 张滚动（{T_roll/max(1,len(pool)-1):.2f}s/张），不循环复用")
        n_scroll = max(1, len(pool) - 1)
    cards = pool[:n_scroll + 1] + [_hero_path]
    n = len(cards)
    print(f"   素材量按配音算：卷播 {T_roll:.2f}s ÷ {card_sec:.2f}s/张 = {n_scroll} 张滚动"
          f"（＋留白1张＋主图 = {n} 张），每张实占 {T_roll/(n_scroll+1):.2f}s，无重复")
    imgs = [load_card(c, fit=(i == n - 1)) for i, c in enumerate(cards)]
    # ★ 段3 放大的卡，必须是「段2 停住时白框里那张」——用渲染时的同一套取样公式反推。
    # 渲染： want = (x0 + off) % span ; 屏幕上看到的卡 = strip[want//PITCH]
    # 而 x0 自身依赖 hero（x0 = box_cx - hero*PITCH - PITCH/2 - off_stop），
    # 联立可得： want = (box_cx - PITCH/2 - off_stop) % (PITCH*n)  ← 与 hero 无关！
    # 所以直接用这个式子算出 want，再取卡索引，全程不猜、不用 --hero。
    # ★ 全程渐慢下的总位移 = ∫v1·f(u)dt，f=1−easeInOutCubic 是对称函数
    #   ∫f du 在 [0,1] 上 = 0.5  ⇒  off = v1 · T · 0.5
    # ★ v1 是「每帧位移」，总位移 = v1 · Σf(i/NF)，不能乘秒数
    # ★★★ 2026-10-07 灿爹「白框核对图还是歪的」根因修复（两处叠加 45px 偏移，实测卡片
    #     左沿 466 / 白框中心 960，卡心 916 ⇒ 偏左 44.5px）：
    #   ① 旧 Σf 只累加 NF_span=NFF−1 项，而渲染循环实际累加 **NFF 项**（fi=0..NFF−1），
    #      对称级数下两者差正好 0.5（中项）⇒ 位移差 v1×0.5 ≈ 15px；
    #   ② x0 解算用「节距中心」(k+0.5)·PITCH 当卡中心，但卡心实际在
    #      k·PITCH + CARD_W/2 ⇒ 恒定偏差 (PITCH−CARD_W)/2 = 30px。
    #   修复：Σf 与渲染循环**逐帧一致**；再**反解 v1** 让实际累计位移恰为整节距
    #         （残差=0，而不是把残差吃掉）；x0 改按真实卡心对齐白框中心。
    _sumf = 0.0
    for _fi in range(NFF):
        _sumf += (1.0 - ease_in_out_cubic(_fi / max(1, NFF - 1)))
    _sumf = max(_sumf, 1e-6)
    T_span = NFF / FPS
    # ★ 位移不再由 v1 反推，而是**由上一步算出的张数决定**：
    #   滚过量 = n_scroll×节距（正好把 n_scroll 张卷过去、主图停在框心），
    #   再反解 v1 让逐帧位移之和精确等于它（零残差 + 不重复 + 不提前播完）。
    _k_pitch = n_scroll
    v1 = _k_pitch * PITCH / _sumf          # 反解 v1 ⇒ 实际累计位移 = 整节距，零残差
    off_stop = _k_pitch * PITCH
    off1 = off_stop
    n2 = max(1, NFF - NF1)
    v2_peak = v1
    off2 = 0.0        # 全程渐慢下无独立减速段，仅为兼容旧引用
    print(f"   全程渐慢: {v1:.1f}px/帧 → 0，跨 {NFF} 帧（{T_span:.2f}s）")
    print(f"   总位移 {off_stop:.0f}px = {off_stop/PITCH:.1f}×节距（Σf={_sumf:.2f}，v1 已反解对齐整节距）")
    # ★★★ 卡位对位（几何核心，2026-10-07 修正为按**真实卡心**对齐）★★★
    # 渲染取样：want = (x0 + off) % span；ix = floor(want)；屏幕 x 显示 strip[ix + x]。
    # 卡 k 占 strip[k*PITCH, k*PITCH+CARD_W)，**卡心在 k*PITCH + CARD_W/2**
    # （不能用 (k+0.5)·PITCH——那是节距中心，PITCH≠CARD_W 时差 30px，白框必歪）。
    # 要 off = off_stop 时卡心正好落在白框中心 box_cx：
    #   k*PITCH + CARD_W/2 - ix = box_cx
    # ⟹ x0 ≡ hero*PITCH + CARD_W/2 - box_cx - off_stop   (mod span)
    # 解出 x0 后，段3 放大 imgs[k] 就是白框里那张，保证同一张。
    span_ = PITCH * n
    # ★★★ 主图必须是「卡列的最后一张」——它才是最后被白框框到的那张。
    #    卷播从卡 0 向右流过，off_stop = 整节距，最后停在 cards[n-1]。
    #    这样「框住最后一张」与「跳出主图」是同一张，视觉才协调。
    hero = n - 1
    x0 = hero * PITCH + CARD_W / 2.0 - box_cx - off_stop
    # 复核：停住时白框中心显示的卡索引必须 == hero
    ix_chk = int((x0 + off_stop)) % span_
    idx_chk = int((ix_chk + box_cx) // PITCH) % n
    off_c = (hero * PITCH + CARD_W / 2.0) - (ix_chk + box_cx)   # 0 = 卡心精确对框心
    ix_stop = int((x0 + off_stop) % span_)      # 停住时的取样起点（供白框跟随）
    print(f"   x0={x0:.0f}  off_stop={off_stop:.0f}（{_k_pitch}×节距）")
    print(f"   ix_stop={ix_stop}  卡片左沿={hero*PITCH - ix_stop}px  "
          f"卡心={(hero*PITCH - ix_stop) + CARD_W/2:.0f}px（白框中心 {box_cx}）")
    print(f"   段2 末白框中心显示 imgs[{idx_chk}]，卡心偏移 {off_c:+.1f}px（应=0）")
    assert idx_chk == hero, f"卡位不一致 {idx_chk} != {hero}"
    print(f"   → 段3 放大的就是这一张：imgs[{hero}] = {os.path.basename(cards[hero])}")
    if hero_src:
        hb = os.path.basename(hero_src)
        cb = os.path.basename(cards[hero])
        if hb != cb:
            print(f"   ⚠️ 主图内容可能不一致！")
            print(f"      卡列最后一张: {cb}")
            print(f"      --hero-src : {hb}")
            print(f"      → 段2 框住的是上面这张，段3 跳出的是下面那张，看着会不协调")
        else:
            print(f"   ✓ 主图一致：卡列最后一张 == --hero-src")

    # 卡列条带（BGR + alpha）。复制 REPEAT 份，保证总宽远超最大滚动量，
    # 否则卷到末尾会回环（视觉上突然倒回，非常不丝滑）
    max_scroll = off_stop + abs(v_hold) * max(0, NF - NFF) + W
    REPEAT = max(2, int(math.ceil(max_scroll / (PITCH*n))) + 1)
    strip = np.zeros((CARD_H, PITCH*n*REPEAT, 3), np.uint8)
    strip_a = np.zeros((CARD_H, PITCH*n*REPEAT), np.uint8)
    for r in range(REPEAT):
        for i, (im, bcol, msk) in enumerate(imgs):
            o = (r*n + i)*PITCH
            strip[:, o:o+CARD_W] = im
            strip_a[:, o:o+CARD_W] = msk
    strip_pm = np.dstack([strip, strip_a])
    print(f"   卡列 {n} 张 × {REPEAT} 轮 = {strip_pm.shape[1]}px 宽（最大滚动 {max_scroll:.0f}px）")

    ff = subprocess.Popen(
        ["ffmpeg","-y","-f","rawvideo","-pix_fmt","bgr24","-s",f"{W}x{H}",
         "-r",str(FPS),"-i","-","-c:v","h264_videotoolbox","-q:v", "90","-pix_fmt","yuv420p","-movflags","+faststart",out],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    off = 0.0
    for fi in range(NF):
        img = np.zeros((H, W, 3), np.uint8)
        if fi < NFF:
            # ★★★ 全程渐慢（灿爹 2026-10-06：「开始是这个速度，但是越后面越慢
            #     直到0，用一个让人适应的舒服的变慢过程」）
            # v(u) = v1 · (1 − easeInOutCubic(u))，u = fi/(NFF−1) ∈[0,1]
            # 起步不突然、末尾不突兀，中段最快 → 视觉上「慢慢卷、优雅收住」
            u = fi / max(1, NFF - 1)
            off += v1 * (1.0 - ease_in_out_cubic(u))
        else:
            # 段3：卡列继续缓慢转动（背景不静止），jump 出来的卡浮在上层
            off += v_hold

        # 卡列平铺：亚像素横移。
        # ⚠️ 不能用 warpAffine + 一次性长条：shift 取模后会采样到卡列边界外，
        #    导致画面出现整段黑屏（实测 0.97-1.40s 中央全黑）。
        # 正确做法：按整数偏移在 strip 上取样，再用 warpAffine 做小数级亚像素补偿。
        span = strip_pm.shape[1]
        y0 = BOX_CY - CARD_H//2
        want = (x0 + off) % span                    # 无符号起点
        ix = int(math.floor(want))                  # 整数部分
        frac = want - ix                            # 小数部分 ∈[0,1)
        # 从 ix 起铺满屏宽 + 1 张余量
        need = W + PITCH + 2
        src = strip_pm[:, ix:ix+need]
        if src.shape[1] < need:                     # 尾部接回头部
            src = np.concatenate([src, strip_pm[:, :need-src.shape[1]]], axis=1)
        if frac > 1e-6:
            M = np.float32([[1, 0, -frac], [0, 1, 0]])
            src = cv2.warpAffine(src, M, (need, CARD_H), flags=cv2.INTER_LINEAR,
                                 borderMode=cv2.BORDER_WRAP)
        sub = src[:, 0:W]
        # ★ 水波纹：真正做法是「按列做横向位移」，让竖直卡边被扭成波浪。
        #    dx(x) = A·sin(2π(x-x0)/λ + φ(t))，φ 随时间推进 → 波纹向外扩散。
        #    用 remap 一次成型（比逐行 warpAffine 快一个数量级）。
        if fi >= NFF and ripple_amp > 0.5:
            tt = (fi - NFF) / FPS
            # 波纹振幅随时间衰减（教程是「一击涟漪」后逐渐平复）
            amp_t = ripple_amp * (1.0 - 0.45 * min(1.0, tt / 0.9))
            xs = np.arange(W, dtype=np.float32)
            phase = 2*np.pi * ((xs - rip_cx) / ripple_lam) - ripple_k * tt
            map_x = (xs + amp_t * np.sin(phase)).astype(np.float32)
            map_y = np.arange(CARD_H, dtype=np.float32)
            MX, MY = np.meshgrid(map_x, map_y)
            bgr = cv2.remap(sub[:, :, :3], MX, MY,
                            cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            alp = cv2.remap(sub[:, :, 3], MX, MY,
                            cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT)
            sub = np.dstack([bgr, alp[:, :, None]])
        pa = sub[:, :, 3:4].astype(np.float32)/255.0
        pxc = sub[:, :, :3].astype(np.float32)
        img[y0:y0+CARD_H, :] = (pxc*pa).astype(np.uint8)

        if fi < NFF:
            # 白框
            b = (255,255,255)
            x1 = box_cx - BOX_W//2; y1 = BOX_CY - BOX_H//2
            x2 = box_cx + BOX_W//2; y2 = BOX_CY + BOX_H//2
            # ★★ 白框必须「贴住停住时那张卡」，宽度由卡片实际屏幕位置决定，
            #    不能用独立参数（曾导致框比卡宽 56px、两侧露黑边，看着"歪"）。
            # 屏幕 x 显示 strip[ix + x]，卡片 k 占 [k*PITCH, k*PITCH+CARD_W)
            #   停住时 ix 固定 ⟹ 卡片左沿 cx_left = k*PITCH - ix（已由 x0 解算保证在框心）
            #   故白框左右沿 = 卡片左右沿 ± bdw（白边宽）
            bdw_t = max(3, int(round(9 * (1.0 - 0.10 * min(1.0, fi / max(1, NFF))))))
            # 停住时卡片在屏幕上的左沿（由 ix 推出）
            # 卡片 k 的屏幕左沿 = (k*PITCH) - ix_stop；ix_stop 已 % span，
            # 故这里再对 span 取一次模，保证落在 [-span, 0] 内可显示范围
            card_lx = ((hero * PITCH - ix_stop) % span_) - span_
            # 卡片中心应 ≈ box_cx；若偏差大说明对齐有问题，用实测校正
            card_lx = int(round(box_cx - CARD_W / 2.0))   # 由几何保证精确居中
            # 白框：段1 从 BOX_W 收到「刚好贴住卡片」，段2 保持
            if fi <= NF1:
                k_ = fi / max(1, NF1)
                w_now = BOX_W + ((CARD_W + 2 * bdw_t) - BOX_W) * k_
            else:
                w_now = CARD_W + 2 * bdw_t
            # 白框中心跟随卡片中心（不固定在 box_cx）
            box_cx_f = card_lx + CARD_W / 2.0
            hw = w_now / 2.0
            bh = CARD_H // 2
            # ★★ 白框实体绘制（2026-10-07 灿爹「框歪了」修复）：
            #   ① 用「卡片实际屏幕位置」定框，不依赖 box_cx（card_lx 已是实测左沿）
            #   ② ★ cv2.rectangle 带线宽时是**中心对称**扩线（±BOX_LW/2），
            #      所以要让**白边内沿**贴住卡片，外沿自然多出 bdw ⇒ 要减半个线宽。
            #      之前没减 ⇒ 左右向外各扩 5.5px，视觉上框比卡宽一截、看着歪。
            _lw2 = BOX_LW / 2.0
            x1 = int(round(box_cx_f - hw + _lw2))
            x2 = int(round(box_cx_f + hw - _lw2))
            y1 = int(round(BOX_CY - bh + _lw2))
            y2 = int(round(BOX_CY + bh - _lw2))
            cv2.rectangle(img, (x1, y1), (x2, y2), b, BOX_LW)
            # 字幕
            tsec = fi/FPS
            sp = (NFF/FPS) if focus_at else (split if split else 1e9)
            if tsec > 0.27*KS and tsec < sp:
                # 句A：淡入，接近 split 时淡出（避免硬切）
                a = min(1.0, (tsec-0.27*KS)/(0.16*KS))
                if sp < 1e8:
                    a *= min(1.0, max(0.0, (sp-tsec)/0.12))
                draw_sub(img, sub_a, sub_b, a, H - 130)
        else:
            # ── 段3：卡列继续转，框中那张「跳出来」浮在上层放大 + 白边相框 ──
            # 动画：0.52s 内完成「弹跳放大 → 过冲 → 回稳」，用 easeOutBack 曲线
            tsec = (fi - NFF) / FPS
            # ★ an_dur 自适应：不超过段3 可用时长（否则动画没播完就切走）
            _avail = max(0.12, (NF - fi) / FPS)
            an_dur = min(an_dur, _avail * 0.72)   # 留 28% 给抖动衰减
            # 抖动参数可调：慢衰减 = 抖得更久
            shake_k = shake_k         # ★ 指数衰减系数（越小抖得越久，默认 2.4）
            shake_amp = shake_amp * (shake_gain if shake_gain else 1.0)
            u = min(1.0, tsec / an_dur)
            # easeOutBack：末段有轻微回弹，视觉上像"跳出来"
            c1, c3 = 1.70158, 2.70158
            uu = u - 1.0
            ease = 1.0 + (c1+1.0)*uu**3 + c1*uu**2 if u < 1 else 1.0
            ease = max(0.0, ease)
            # 位移用不过冲的缓动（缩放过冲、位移不过冲，物理上更自然）
            u_e = min(1.0, u)
            # 从「段2 结束时的卡」连续放大到相框尺寸。
            # 段2 末卡在白框里：宽 = 白框宽(≈436)，高 = 卡高(≈800)。
            # 段3 目标：外框 471×899（卡 441×869 + 白边15）。倍率 ≈ 869/800 ≈ 1.086。
            # 连续放大：1.0 → 1.20。段2 末卡高 800，1.20× = 960，
            # 加白边后外框高 ≈ 990，可用高度 H-180=900 → 略超，故用 1.13×。
            # ★ 框形状跟随主图：16:9 用横框，否则竖卡。
            #    竖框塞横图会裁掉两侧主体 —— 这正是「图片不完整」的根因。
            is_wide = hero_im_full is not None and (
                hero_im_full.shape[1] / hero_im_full.shape[0] > 1.3)
            BASE_W, BASE_H = (1280, 720) if is_wide else (CARD_W, CARD_H)
            sc = 1.0 + (0.10 if is_wide else 0.30) * ease

            # 白边相框：随放大同步变粗
            # 白边相框宽度：随放大同步变粗（像相纸边框）
            bdw = max(5, int(round(11 * (1.0 + 0.5*ease))))
            # 外框尺寸 = 卡片 + 上下左右白边
            ch, cw = int(BASE_H*sc), int(BASE_W*sc)
            # 夹紧：给白边、投影、底部字幕留空间
            # ★ 允许更大：画面留出底部字幕带即可
            # ★ 1.30× 时卡高 1040 > 1080-字幕带，改为直接夹「卡高」到 940，
            #   白边叠在卡外沿之上（fy 留 bdw 即可），确保不越界且足够大。
            MAX_H = 820 if is_wide else 940
            if ch > MAX_H:
                k = MAX_H / ch
                ch, cw = int(ch*k), int(cw*k)
            max_oh = H - 120
            max_ow = W - 160
            oh, ow = ch + 2*bdw, cw + 2*bdw
            if oh > max_oh or ow > max_ow:
                k = min(max_oh/oh, max_ow/ow)
                bdw = max(4, int(bdw*k))
                ch, cw = int(ch*k), int(cw*k)
            # 相框左��角
            # ★★ 位移 + 缩放双动画：不能只放大。卡片从「白框中心」(box_cx, BOX_CY)
            #    滑到「画面视觉中心」，同时放大 —— 才有"跳出来"的感觉。
            #    用 easeOutCubic 让起步快、落位稳。
            ec = 1.0 - (1.0 - u_e) ** 3       # easeOutCubic
            # 起点：白框中心（段2 末卡片所在）；终点：画面中心略偏上
            from_pos_x = box_cx
            from_pos_y = BOX_CY
            to_pos_x   = W / 2.0
            to_pos_y   = (H - 2*bdw) * (0.44 if is_wide else 0.40)
            cxp = from_pos_x + (to_pos_x - from_pos_x) * ec
            cyp = from_pos_y + (to_pos_y - from_pos_y) * ec
            # ★ 落位后持续「余震抖动」再渐停：缩放/位移到位后叠加衰减振荡，
            #    振幅与频率同时衰减 → 看着像「当」的一声后画面还在颤。
            if shake_amp > 0.1:
                st = tsec                       # 从落位时刻算起
                if st > 0:
                    # 双频叠加：主振 13Hz + 副振 27Hz（避免机械感）
                    env_ = math.exp(-shake_k * st)      # 振幅衰减（shake_k 越小持续越久）
                    dxs = shake_amp * env_ * math.sin(2*math.pi*13.0*st)
                    dys = shake_amp * 0.62 * env_ * math.sin(2*math.pi*27.0*st + 0.9)
                    cxp += dxs
                    cyp += dys
                    # 抖动伴随极轻微的旋转感（用宽高微调模拟）
                    sc *= 1.0 + 0.010 * env_ * math.sin(2*math.pi*19.0*st)
            fx = int(round(cxp - (cw + 2*bdw) / 2.0))
            fy = int(round(cyp - ch / 2.0))
            fy = max(0, min(fy, H - ch - 2*bdw))     # 别越界
            # 卡片在内圈（留出白边）
            hx, hy = fx + bdw, fy + bdw
            # ★ 主图来源：--hero-src 指定的完整图（推荐，16:9 不会被裁切），
            #    否则回退到竖卡 strip 里那张。
            if hero_im_full is not None:
                # 完整图按「相框内圈」尺寸做 cover 缩放（不裁掉主体）
                tw2, th2 = cw, ch
                sc2 = max(tw2 / hero_im_full.shape[1], th2 / hero_im_full.shape[0])
                nw2, nh2 = int(hero_im_full.shape[1]*sc2+0.5), int(hero_im_full.shape[0]*sc2+0.5)
                r2 = cv2.resize(hero_im_full, (nw2, nh2), interpolation=cv2.INTER_AREA)
                ox2 = (nw2 - tw2)//2; oy2 = (nh2 - th2)//2
                hero_im = r2[oy2:oy2+th2, ox2:ox2+tw2]
            else:
                hero_im = cv2.resize(imgs[hero][0], (cw, ch), interpolation=cv2.INTER_CUBIC)
            # 投影：主卡轮廓外围一圈高斯模糊暗影，制造"浮起"层次。
            # 实现要点：先在与 region 同尺寸的画布上画，再做中心挖空，尺寸天然对齐。
            shp = cv2.GaussianBlur(hero_im, (0, 0), max(6.0, 26.0*ease))
            shp = (shp.astype(np.float32)*0.45).astype(np.uint8)
            pad = 14
            sy0, sx0 = max(0, fy-pad), max(0, fx-pad)
            sy1, sx1 = min(H, fy+ch+2*bdw+pad), min(W, fx+cw+2*bdw+pad)
            if sy1 > sy0 and sx1 > sx0:
                sh, sw = sy1-sy0, sx1-sx0
                # shp 是主卡尺寸（可能比 region 小），先按 region 尺寸补边再截取
                pad_h, pad_w = max(0, sh - shp.shape[0]), max(0, sw - shp.shape[1])
                if pad_h or pad_w:
                    shp = cv2.copyMakeBorder(shp, 0, pad_h, 0, pad_w,
                                             cv2.BORDER_CONSTANT, value=(0,0,0))
                layer = shp[:sh, :sw].copy()
                # 把主卡覆盖区挖空（那部分被主卡本身占据，不该有阴影）
                # 挖空整个相框区（白边也要挡住），否则阴影会压在卡面上把画面压暗
                hy0, hx0 = max(0, fy-sy0), max(0, fx-sx0)
                hy1, hx1 = min(sh, fy+ch+2*bdw-sy0), min(sw, fx+cw+2*bdw-sx0)
                layer[hy0:hy1, hx0:hx1] = 0
                m = layer.max(axis=2)
                region = img[sy0:sy1, sx0:sx1]
                sel = m > 14
                if sel.any():
                    region[sel] = (region[sel].astype(np.float32)*0.42
                                   + layer[sel].astype(np.float32)*0.58).astype(np.uint8)
            # ① 遮罩：把主卡区域以外压暗，突出主体（不靠纯黑背景）
            ov = max(0.0, min(1.0, tsec / 0.22))      # 0.22s 内压暗到位
            dim = 1.0 - 0.62 * ov                     # 最多压到 38% 亮度
            if ov > 0.01:
                dark = (img.astype(np.float32) * dim).astype(np.uint8)
                # 主卡外框区域保持原样
                m_y0, m_y1 = max(0, fy-30), min(H, fy+ch+2*bdw+30)
                m_x0, m_x1 = max(0, fx-30), min(W, fx+cw+2*bdw+30)
                img[m_y0:m_y1, m_x0:m_x1] = dark[m_y0:m_y1, m_x0:m_x1]
            # ② 白边相框：纯白实心矩形（比卡片大 bdw）
            cv2.rectangle(img, (fx, fy), (fx+cw+2*bdw-1, fy+ch+2*bdw-1),
                          (255,255,255), -1)
            # ③ 主卡嵌在白框内（略提亮 + 对比度，让主体跳出来）
            hh = cv2.convertScaleAbs(hero_im, alpha=1.10, beta=8)
            img[hy:hy+ch, hx:hx+cw] = hh
            # 长句承接：段3 显示第二句（若提供）
            if sub_c or sub_d:
                draw_sub(img, sub_c, sub_d, min(1.0, tsec/0.18), H - 130)
        ff.stdin.write(img.tobytes())
    ff.stdin.close()
    err = ff.stderr.read().decode('utf-8','ignore')
    if ff.wait() != 0:
        raise SystemExit('ffmpeg 失败:\n'+err[-500:])
    print(f"已生成 {out}  {total:.2f}s  {NF}帧  {n}张卡")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--cards", nargs="*", default=[],
                    help="卡图路径（也可用逗号分隔；建议把 --cards 放最后）")
    ap.add_argument("--cards-file", default="", help="卡图列表文件，每行一张")
    ap.add_argument("--out", required=True)
    ap.add_argument("--hero", type=int, default=0)
    ap.add_argument("--dur", type=float, default=2.9)
    ap.add_argument("--sub-a", default="3个月的健身成本")
    ap.add_argument("--sub-b", default="只需要39块9")
    ap.add_argument("--sub-c", default="")
    ap.add_argument("--sub-d", default="")
    ap.add_argument("--split", type=float, default=0.0)
    ap.add_argument("--focus-at", type=float, default=0.0)
    ap.add_argument("--v1", type=float, default=95.0, help="段1 每帧位移(px)。越大越快，看不清就调小")
    ap.add_argument("--v-hold", type=float, default=7.0, help="段3 卡列继续转动的速度(px/帧)，0=冻结")
    ap.add_argument("--ripple-amp", type=float, default=0.0, help="段3 背景水波纹振幅(px)，0=关闭")
    ap.add_argument("--ripple-lam", type=float, default=240.0, help="水波纹波长(px)，越大波越宽")
    ap.add_argument("--ripple-k", type=float, default=9.0, help="水波纹扩散速度(rad/s)")
    ap.add_argument("--shake-amp", type=float, default=0.0, help="段3 落位后抖动振幅(px)")
    ap.add_argument("--hero-src", default="", help="段3 主图用独立的完整图（如 16:9），不走竖卡 strip")
    ap.add_argument("--an-dur", type=float, default=0.95, help="段3 位移+缩放动画时长(秒)")
    ap.add_argument("--shake-k", type=float, default=2.4, help="抖动衰减系数，越小抖得越久")
    ap.add_argument("--shake-gain", type=float, default=1.0, help="抖动振幅倍率")
    ap.add_argument("--no-sub", action="store_true",
                    help="不画卷播内字幕。★接入 build_ad_video.py 成片时必须加，"
                         "否则会与渲染器的全局字幕叠成双层")
    ap.add_argument("--card-sec", type=float, default=0.30,
                    help="★单张素材的目标展示时长(秒)。脚本按「卷播段配音时长 ÷ 本值」"
                         "动态决定要用几张素材，卡列自动裁剪到这个数量（末位固定主图），"
                         "从而保证素材总展示时长与配音精确对齐：既不提前播完、也不重复轮播。"
                         "值越小切换越快（参考：0.22=极快，0.30=常规，0.40=偏慢看得清）")
    a = ap.parse_args()
    _cards = list(a.cards)
    if a.cards_file:
        _cards += [l.strip() for l in open(a.cards_file, encoding="utf-8")
                   if l.strip() and not l.startswith("#")]
    for _c in _cards[:]:
        if "," in _c:
            _cards.remove(_c)
            _cards += [x.strip() for x in _c.split(",") if x.strip()]
    if not _cards:
        print("❌ 没给卡图（--cards 或 --cards-file）", file=sys.stderr)
        sys.exit(1)
    print(f"   卡图 {len(_cards)} 张")
    render(_cards, a.out, dur=a.dur, hero=a.hero, v1=a.v1, v_hold=a.v_hold,
           ripple_amp=a.ripple_amp, ripple_lam=a.ripple_lam, ripple_k=a.ripple_k,
           shake_amp=a.shake_amp, hero_src=(a.hero_src or None),
           no_sub=a.no_sub, an_dur=a.an_dur, shake_k=a.shake_k, shake_gain=a.shake_gain,
           sub_a=a.sub_a, sub_b=a.sub_b,
           sub_c=a.sub_c, sub_d=a.sub_d,
           split=(a.split or None), focus_at=(a.focus_at or None), card_sec=a.card_sec)
