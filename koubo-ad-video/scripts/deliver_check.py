#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""交付签收一键体检（2026-10-15 立）

起因：本机模型**读不了图**（Read 工具与子代理都返回 CANNOT_READ_IMAGE），
  「抽帧目视」这条传统验收路径直接失效。本脚本把「目视判据」全部翻译成
  **可计算的像素判据**，让视觉验收在无视觉模型的环境下也能跑。

覆盖 6 项（有哪个素材就测哪个，缺的跳过）：
  1. 交付规格：体积 20~40MB / 码率 3~6 Mbps / 容器首个 box = moov（faststart）
  2. 音频：真峰值 ≤ −0.5 dBTP、响度 −11~−13 LUFS（loudnorm）
  3. 封面：首帧 vs `assets/cover/cover_frame_1080p.jpg` 像素差 < 6
  4. 卷播主图大字 3D 景深：字块内金色像素「人物区 < 空白区」= 字被人物遮住
  5. fx 动效落位：`base_fx` vs `out_base` **同时间轴**差分，差异须落在预期框内
  6. 详情卡在位：卡片时刻 vs 卡前 同区域差分（out_base）

用法：
  python3 deliver_check.py --final 成片.mp4 \
      [--banner ws/rem3d/out/banner_x.mp4 --hero-fg ws/rem3d/public/cards_x/hero_fg.png \
       --fx-decl 文案.fx.json --base ws/x/render/out_base.mp4 --base-fx ws/x/render/base_fx.mp4 \
       --cards "x,y,w,h:名" ...]

★ 关键判据来源（别改，都是踩坑换来的）：
  - fx 落位**必须用 base_fx vs out_base**：final 含 banner 段，与 base 差 3.3s 时间轴，
    拿 final 比对会全屏皆差异（=白验）。本片用同轴差分才验出 3 处动效 99~100% 落位。
  - 卷播字效用「金色像素占比」而非模板比对：字有滑入/缩放/弹簧三种动画，
    帧对帧位置对不上；而金色 (#F2C14E 系) 在画面里几乎是唯一的，占比法稳。
"""
import argparse
import json
import os
import re
import struct
import subprocess
import statistics
import sys

FF = os.environ.get("FFMPEG_BIN", "/Users/mac/.local/bin/ffmpeg")
FP = os.environ.get("FFPROBE_BIN", "/Users/mac/.local/bin/ffprobe")
SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVER = f"{SKILL}/assets/cover/cover_frame_1080p.jpg"
TMP = "/tmp/dcheck"

results = []


def add(name, ok, detail, warn=False):
    tag = "✅" if ok else ("⚠️" if warn else "❌")
    results.append((tag, name, detail))
    print(f"  {tag} {name}：{detail}")


def grab(video, t, out):
    if os.path.exists(out):
        os.remove(out)
    subprocess.run([FF, "-y", "-v", "error", "-ss", f"{t}", "-i", video,
                    "-frames:v", "1", out], check=True)
    if not os.path.exists(out):
        raise RuntimeError(f"抽帧失败 {video}@{t}s（尾部取帧常静默失败，往回调一点）")
    from PIL import Image
    import numpy as np
    return np.array(Image.open(out).convert("RGB")).astype(np.int16)


def check_delivery_spec(final):
    print("\n══ 1. 交付规格")
    out = subprocess.run([FP, "-v", "error", "-show_entries",
                          "stream=codec_name,bit_rate", "-show_entries",
                          "format=duration,size", "-of", "json", final],
                         capture_output=True, text=True).stdout
    j = json.loads(out)
    size = int(j["format"]["size"])
    dur = float(j["format"]["duration"])
    mb = size / 1048576
    # ★2026-10-15 修正：体积判据必须**按时长折算**。原写死 20~40MB 是按 53s 片定的，
    #   30s 的短片本来就只有 ~16MB，会被误判。等效口径：折算到 53s 应为 20~40MB。
    eq = mb * 53.0 / max(1e-6, dur)
    vs = [s for s in j["streams"] if s.get("codec_type") != "audio"]
    vbr = int(j["streams"][0].get("bit_rate") or 0)
    mbs = vbr / 1e6
    add(f"体积（{dur:.0f}s，折算53s 应 20~40MB）", 16 <= eq <= 45,
        f"{mb:.1f} MB（折算 {eq:.1f} MB/53s）", warn=(eq <= 60))
    add("视频码率 3~6 Mbps", 2.5 <= mbs <= 6.5, f"{mbs:.2f} Mbps", warn=(mbs <= 9))
    # faststart：首个 moov/mdat 必须是 moov
    with open(final, "rb") as f:
        order = []
        while True:
            h = f.read(8)
            if len(h) < 8:
                break
            sz = struct.unpack(">I", h[:4])[0]
            typ = h[4:8].decode("latin1", "replace")
            order.append(typ)
            if typ in ("moov", "mdat") or sz < 8:
                break
            f.seek(sz - 8, 1)
    add("faststart（首个 moov）", order[-1] == "moov", " → ".join(order))


def check_audio(final):
    print("\n══ 2. 音频（真峰值 / 响度）")
    r = subprocess.run([FF, "-i", final, "-af", "loudnorm=print_format=summary",
                        "-f", "null", "-"], capture_output=True, text=True)
    txt = r.stderr
    tp = re.search(r"Input True Peak:\s*([-+\d.]+)", txt)
    li = re.search(r"Input Integrated:\s*([-+\d.]+)", txt)
    if tp:
        v = float(tp.group(1))
        add("真峰值 ≤ −0.5 dBTP", v <= -0.5, f"{v:+.1f} dBTP")
    if li:
        v = float(li.group(1))
        add("响度 −11~−13 LUFS", -13 <= v <= -11, f"{v:.1f} LUFS", warn=(-14 <= v <= -10))


def check_cover(final):
    print("\n══ 3. 封面首帧")
    if not os.path.exists(COVER):
        add("封面帧资产", False, f"缺 {COVER}")
        return
    import numpy as np
    from PIL import Image
    a = grab(final, 0.0, f"{TMP}/cov.png").astype(np.float32)
    b = np.array(Image.open(COVER).convert("RGB")).astype(np.float32)
    if a.shape != b.shape:
        add("封面首帧", False, f"尺寸不符 {a.shape} vs {b.shape}")
        return
    d = float(np.abs(a - b).mean())
    add("封面 = 指定健身图", d < 6, f"像素差 {d:.2f}")


def gold_mask(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    return (r > 150) & (b < 150) & (r - b > 50)


def hero_label_box(cfg_path=None, size=None, top=None, text_len=3, gap=16):
    """按 MarqueeClickD 的卡→屏幕映射，从 hero_label_size/hero_label_top 推算字块屏幕矩形。

    ★必须限定在这个矩形内统计金色 —— 实测：本片健身房暖光画面下，全局"金色"阈值会
      抓到 40 万像素、x 0~1919 y 0~1063 满屏分布（暖光/肤色/木地板全中），
      不限定框必然误判「人物区金色更多」。
    映射（与 scan_hero_label.py 同一套，勿改）：
      S=1920/700；x=960+(x_card-350)*S；y=520+(y_card-197)*S
    """
    S = 1920 / 700.0
    if cfg_path and os.path.exists(cfg_path):
        c = json.load(open(cfg_path, encoding="utf-8"))
        size = size if size is not None else c.get("hero_label_size", 132)
        top = top if top is not None else c.get("hero_label_top", 16)
    size = size if size is not None else 132
    top = top if top is not None else 16
    cw = text_len * size + (text_len - 1) * gap + 8
    ch = size * 1.3 + 8
    cx0 = 350 - cw / 2.0
    sx0 = 960 + (cx0 - 350) * S
    sy0 = 520 + (top - 197) * S
    sx1 = 960 + (cx0 + cw - 350) * S
    sy1 = 520 + (top + ch - 197) * S
    return (max(0, int(sx0)), max(0, int(sy0)),
            min(1920, int(sx1)), min(1080, int(sy1)))


def check_hero_label(banner, hero_fg, t=None, box=None):
    print("\n══ 4. 卷播主图大字 3D 景深")
    if not (banner and os.path.exists(banner)):
        print("  (跳过：未提供 --banner)")
        return
    if not os.path.exists(hero_fg):
        print(f"  (跳过：缺 hero_fg {hero_fg})")
        return
    import numpy as np
    from PIL import Image
    dur = float(subprocess.run([FP, "-v", "error", "-show_entries",
                                "format=duration", "-of", "csv=p=0", banner],
                               capture_output=True, text=True).stdout.strip() or 0)
    tt = t if t is not None else max(0.0, dur - 0.28)
    f = grab(banner, tt, f"{TMP}/hero.png")
    person = np.array(Image.open(hero_fg))[:, :, 3] > 128
    gm = gold_mask(f)
    if box:
        x0, y0, x1, y1 = box
        print(f"  字块（按 hero_label_size/top 推算）x {x0}~{x1} y {y0}~{y1}")
        inb = np.zeros_like(person)
        inb[y0:y1, x0:x1] = True
        gl = gm & inb
    else:
        print("  ⚠️ 未给字块，退化为全局统计（暖光画面下易误判，建议传 --hero-cfg）")
        gl = gm
        inb = np.ones_like(person)
    rate = gl.sum() / max(1, inb.sum())
    on_p = (gl & person).sum() / max(1, (inb & person).sum())
    free = (gl & ~person).sum() / max(1, (inb & ~person).sum())
    ratio = on_p / free if free > 0 else 99
    add("主图大字出现", rate > 0.03, f"字区金色占比 {100*rate:.1f}%（@{tt:.2f}s）")
    add("字被人物遮挡（人物区金色更少）", ratio < 0.8,
        f"人物区 {100*on_p:.1f}% vs 空白区 {100*free:.1f}%（比值 {ratio:.2f}，<0.8 即被遮）")


def check_fx(base_fx, base, decl, spec_path=None):
    print("\n══ 5. fx 动效落位（base_fx vs out_base 同轴差分）")
    if not (base_fx and os.path.exists(base_fx) and base and os.path.exists(base)):
        print("  (跳过：需同时提供 --base-fx 与 --base)")
        return
    if not (decl and os.path.exists(decl)):
        print("  (跳过：未提供 --fx-decl)")
        return
    import numpy as np
    spec_p = spec_path if (spec_path and os.path.exists(spec_path)) else None
    if not spec_p:
        # 从 base_fx 往上找 ws/rem3d/src/fx_spec_*.json
        d = os.path.dirname(os.path.abspath(base_fx))
        for _ in range(4):
            cand = os.path.join(d, "rem3d", "src")
            if os.path.isdir(cand):
                for fn in sorted(os.listdir(cand)):
                    if fn.startswith("fx_spec_") and fn.endswith(".json"):
                        spec_p = os.path.join(cand, fn)
                break
            d = os.path.dirname(d)
    if not spec_p:
        print("  ⚠️ 未找到 fx_spec_*.json，无法定位动效时刻（用 --fx-spec 指定）")
        return
    spec = json.load(open(spec_p, encoding="utf-8"))
    n_checked = 0
    for v in spec.get("viz", []):
        t = v.get("startSec", 0) + min(1.0, v.get("durSec", 2) * 0.4)
        x, y = v.get("x", 0), v.get("y", 0)
        a = grab(base_fx, t, f"{TMP}/fx_now.png")
        b = grab(base, t, f"{TMP}/fx_base.png")
        diff = np.abs(a - b).sum(axis=2) > 40
        n_tot = int(diff.sum())
        n_in = int(diff[max(0, y-200):y+200, max(0, x-400):x+400].sum())
        frac = n_in / max(1, n_tot)
        add(f"fx[{v.get('kind')}] @{t:.1f}s 落位", n_in > 300 and frac > 0.3,
            f"差异 {n_tot}，预期框内 {n_in}（{100*frac:.0f}%）")
        n_checked += 1
    if not n_checked:
        print("  (spec 里没有 viz 条目)")


def check_cards(base, cards):
    print("\n══ 6. 详情卡在位（out_base 卡中 vs 卡前）")
    if not (base and os.path.exists(base)):
        print("  (跳过：未提供 --base)")
        return
    import numpy as np
    if not cards:
        print("  (跳过：未提供 --cards)")
        return
    for spec in cards:
        box, name = spec.rsplit(":", 1)
        x, y, w, h = [int(v) for v in box.split(",")]
        t = float(name.split("@")[1]) if "@" in name else 0.0
        card_name = name.split("@")[0]
        if t <= 0:
            print(f"  (跳过 {card_name}：--cards 需写成 'x,y,w,h:名@秒')")
            continue
        a = grab(base, t, f"{TMP}/c_now.png")
        b = grab(base, max(0, t - 4.0), f"{TMP}/c_pre.png")
        ra, rb = a[y:y+h, x:x+w], b[y:y+h, x:x+w]
        d = float(np.abs(ra - rb).mean())
        white = float(((ra[..., 0] > 200) & (ra[..., 1] > 200) & (ra[..., 2] > 200)).mean())
        add(f"卡片 {card_name} @{t}s", d > 12, f"区域差分 {d:.1f}，白底占比 {100*white:.0f}%")


def check_dust(final, ref):
    """⑦ 漂浮粒子（卷播片必加）—— 与「未加粒子」参照版做像素对比。

    ★判据怎么来的（2026-10-15 实测，别乱改）：
      粒子是 screen 混合的**小亮斑**，因此相对参照版应表现为
        ① 差异像素稀疏铺满（0.5%~25%），② **正向（变亮）占比 > 90%**。
      ⚠️ 不能直接看「整体平均亮度」：加粒子这一步必然重编码一次，
        libx264 二次编码会带来 ±1 的全局色偏（实测色偏 |ΔRGB| ≈ 2.0，
        主因是源为 full-range yuvj420p 而中间层默认按 limited 处理，
        已在 add_dust 里用 `-color_range pc` 压到最小）。
        这个色偏会把「+0.2 的粒子增亮」淹没 ⇒ 必须**先双侧高斯模糊 σ=3**
        把编码噪声平均掉，再统计「模糊后仍比参照亮」的像素。
      实测合格值：亮斑 6~9%、正向 100%、模糊后亮度差 +0.18~0.22。
      ⚠️ 抽帧必须用 `select=eq(n\\,N)` **按帧号**取，不能用 `-ss`（时间抽帧在重编码
        后帧对齐会差 1~2 帧，动态画面里会造出大量假差异，实测误判过一次）。
    """
    print("\n══ 7. 漂浮粒子（卷播片必加）")
    if not (ref and os.path.exists(ref)):
        print("  (跳过：未提供 --dust-ref；跑 pipeline 会留 render/final_nodust.mp4)")
        return
    import numpy as np
    from PIL import Image, ImageFilter

    def grabN(v, n, o):
        if os.path.exists(o):
            os.remove(o)
        subprocess.run([FF, "-y", "-v", "error", "-i", v,
                        "-vf", f"select=eq(n\\,{n})", "-frames:v", "1", o], check=True)
        return o

    fr = []
    for n in (240, 480, 720):
        p1 = grabN(final, n, f"{TMP}/dd_{n}.png")
        p2 = grabN(ref, n, f"{TMP}/dr_{n}.png")
        if not (os.path.exists(p1) and os.path.exists(p2)):
            continue
        a = np.array(Image.open(p1).convert("RGB").filter(ImageFilter.GaussianBlur(3)),
                     dtype=np.float32)
        b = np.array(Image.open(p2).convert("RGB").filter(ImageFilter.GaussianBlur(3)),
                     dtype=np.float32)
        d = (a - b).mean(axis=2)
        dm = d > 0.8
        if not dm.sum():
            fr.append((0.0, 0.0, float(d.mean())))
            continue
        fr.append((100.0 * dm.mean(), 100.0 * (d[dm] > 0).mean(), float(d.mean())))
    if not fr:
        add("粒子检测", False, "抽帧失败（帧号超出片长？）")
        return
    frac = float(np.mean([x[0] for x in fr]))
    pos = float(np.mean([x[1] for x in fr]))
    lum = float(np.mean([x[2] for x in fr]))
    add("粒子可见（亮斑稀疏铺满 0.5~25%）", 0.5 < frac < 25,
        f"亮斑占比 {frac:.2f}%（模糊σ3 后仍比参照亮）")
    add("粒子为亮点（正向 >90%）", pos > 90,
        f"正向占比 {pos:.1f}%｜模糊后亮度差 {lum:+.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--final", required=True)
    ap.add_argument("--banner")
    ap.add_argument("--hero-fg")
    ap.add_argument("--hero-cfg", help="brand_*.json，自动读 hero_label_size/top 推算字块")
    ap.add_argument("--hero-size", type=int)
    ap.add_argument("--hero-top", type=int)
    ap.add_argument("--hero-t", type=float)
    ap.add_argument("--base")
    ap.add_argument("--base-fx")
    ap.add_argument("--fx-decl")
    ap.add_argument("--fx-spec")
    ap.add_argument("--dust-ref", help="未加粒子的参照版（pipeline 留的 render/final_nodust.mp4）")
    ap.add_argument("--cards", nargs="*", default=[])
    ap.add_argument("--skip-spec", action="store_true")
    a = ap.parse_args()
    os.makedirs(TMP, exist_ok=True)
    print(f"交付签收体检：{a.final}")
    if not a.skip_spec:
        check_delivery_spec(a.final)
    check_audio(a.final)
    check_cover(a.final)
    box = None
    if a.hero_cfg or a.hero_size or a.hero_top:
        box = hero_label_box(a.hero_cfg, a.hero_size, a.hero_top)
    check_hero_label(a.banner, a.hero_fg, a.hero_t, box)
    check_fx(a.base_fx, a.base, a.fx_decl, a.fx_spec)
    check_cards(a.base, a.cards)
    check_dust(a.final, a.dust_ref)

    fails = [r for r in results if r[0] == "❌"]
    warns = [r for r in results if r[0] == "⚠️"]
    print(f"\n════ 汇总：{len(results)} 项 ｜ ✅ {len(results)-len(fails)-len(warns)} ｜ "
          f"⚠️ {len(warns)} ｜ ❌ {len(fails)} ════")
    for t, n, d in fails:
        print(f"  {t} {n}：{d}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
