# -*- coding: utf-8 -*-
"""产品图 → 画面叠层 PNG（一次性预处理，供渲染器叠加）。两种形态：

  --style card     （默认）圆角 + 白边 + 投影的「卡片」——适合详情图/参数表
  --style sticker  （2026-09-19 新增）抠白底 + 粗白描边 + 大投影 + 微倾斜的「贴纸」
                   ——★产品展示首选。产品以贴纸形态切入动漫画面，不割裂（用户拍板）

为什么独立成脚本：渲染器 build_ad_video.py 坚持零第三方依赖（系统 python3 直接能跑），
而抠底/抗锯齿/投影模糊用 Pillow+numpy 做最干净可靠，所以把这一步拆出来（同 make_cutout.py 的思路）。

用法:
  # 圆角卡片（详情图）
  python make_cards.py 详情图.jpg --w 480 --out <输出目录>
  # 产品贴纸（白底产品实拍图 → 抠底成贴纸）
  python make_cards.py 产品白底图.png --style sticker --w 520 --tilt -2.5 --out <输出目录>

产物:
  <out>/<图名>_<style>_w<N>.png   带 alpha 的叠层（含投影扩边）
  <out>/cards.json                元数据：vis_w/vis_h=可见尺寸(定位用)，pad=投影扩边，
                                  png=文件名，w/h=PNG 总尺寸，style=card|sticker
                                  → 渲染器按 vis 尺寸落位，overlay 时 x = vis_x - pad
"""
import argparse
import json
import os
import sys

try:
    from PIL import Image, ImageDraw, ImageFilter
except ImportError:
    sys.exit("需要 Pillow：/Users/mac/.workbuddy/binaries/python/envs/default/bin/python make_cards.py ...")


# ---------------------------------------------------------------- 贴纸：抠白底
def cut_white_bg(im, thr=38):
    """抠掉「从画面四边连通进来的白底」→ 返回 L 通道（255=保留主体）。

    为什么不用亮度阈值一刀切：产品包装上常有白字/白高光（如红罐上的「肌酸粉」），
    按亮度抠会把字打成洞。所以只抠**与画布边缘连通的**白区（种子=四条边上的近白像素，
    再迭代膨胀求连通域），主体内部的白字因为不连通而完整保留。
    """
    try:
        import numpy as np
    except ImportError:
        return None                      # 没 numpy 就退回不抠底（调用方处理）

    a = np.asarray(im.convert("RGB")).astype(np.int16)
    mx, mn = a.max(axis=2), a.min(axis=2)
    # 近白 = 足够亮 且 足够灰（低饱和），避免误伤浅色但有色相的主体
    near_white = (mn >= 255 - thr) & ((mx - mn) <= int(thr * 0.7))

    conn = np.zeros_like(near_white)
    conn[0, :] |= near_white[0, :]
    conn[-1, :] |= near_white[-1, :]
    conn[:, 0] |= near_white[:, 0]
    conn[:, -1] |= near_white[:, -1]
    for _ in range(4000):                # 迭代膨胀求连通（8 邻域，含对角以保证斜边不漏）
        nxt = conn.copy()
        nxt[1:, :] |= conn[:-1, :]
        nxt[:-1, :] |= conn[1:, :]
        nxt[:, 1:] |= conn[:, :-1]
        nxt[:, :-1] |= conn[:, 1:]
        nxt[1:, 1:] |= conn[:-1, :-1]
        nxt[:-1, :-1] |= conn[1:, 1:]
        nxt[1:, :-1] |= conn[:-1, 1:]
        nxt[:-1, 1:] |= conn[1:, :-1]
        nxt &= near_white
        if np.array_equal(nxt, conn):
            break
        conn = nxt
    return Image.fromarray(np.where(conn, 0, 255).astype("uint8"), "L")


# ---------------------------------------------------------------- 卡片（原样式）
def build_card(src, width, radius_ratio=0.055, border=3, shadow=12, bg_alpha=0.55):
    """返回 (RGBA图, meta)。可见区 = 图 + 白边；PNG 再向外扩 shadow*2 放投影。"""
    im = Image.open(src).convert("RGB")
    w = int(width)
    h = int(round(im.height * w / im.width))
    h += h % 2
    im = im.resize((w, h), Image.LANCZOS)

    r = max(6, int(round(radius_ratio * w)))
    bd = int(border)
    vis_w, vis_h = w + 2 * bd, h + 2 * bd

    # 圆角卡片本体
    card = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], radius=r, fill=255)
    card.paste(im, (0, 0), mask)

    # 白色描边：外圈圆角矩形托底，再把卡片压上去
    vis = Image.new("RGBA", (vis_w, vis_h), (0, 0, 0, 0))
    ImageDraw.Draw(vis).rounded_rectangle([0, 0, vis_w - 1, vis_h - 1],
                                          radius=r + bd, fill=(255, 255, 255, 255))
    vis.alpha_composite(card, (bd, bd))

    if not shadow:
        return vis, {"vis_w": vis_w, "vis_h": vis_h, "pad": 0, "w": vis_w, "h": vis_h}

    # 投影：取可见区 alpha → 高斯模糊 → 压到黑色上，整体向下偏 1/4 模糊半径
    pad = int(shadow) * 2
    blur = shadow
    a = vis.split()[3].filter(ImageFilter.GaussianBlur(blur))
    a = a.point(lambda v: int(v * bg_alpha))
    sh = Image.new("RGBA", (vis_w, vis_h), (0, 0, 0, 0))
    sh.putalpha(a)

    out = Image.new("RGBA", (vis_w + pad * 2, vis_h + pad * 2), (0, 0, 0, 0))
    out.alpha_composite(sh, (pad, pad + blur // 4))
    out.alpha_composite(vis, (pad, pad))
    return out, {"vis_w": vis_w, "vis_h": vis_h, "pad": pad,
                 "w": out.width, "h": out.height}


# ---------------------------------------------------------------- 贴纸（新样式）
def build_sticker(src, width, border=None, shadow=None, bg_alpha=0.5,
                  tilt=0.0, bg_thr=38, cutout=True):
    """白底/纯色底产品图 → 贴纸：抠底 + 粗白描边 + 大投影 + 可选微倾斜。

    border 默认按宽度自适应（≈2.6% 宽，最少 6px）——贴纸描边必须比卡片粗，
    否则在动的画面里「贴纸感」出不来。
    """
    im0 = Image.open(src)
    if im0.mode in ("RGBA", "LA", "P"):
        im0 = im0.convert("RGBA")
        # 已是透明底 → 直接用自带 alpha，不再抠
        has_alpha = im0.getextrema()[3][0] < 250
    else:
        im0 = im0.convert("RGB")
        has_alpha = False

    w = int(width)
    h = int(round(im0.height * w / im0.width))
    h += h % 2
    im = im0.resize((w, h), Image.LANCZOS)

    if has_alpha:
        sub_alpha = im.split()[3]
        rgb = im.convert("RGB")
    elif cutout:
        sub_alpha = cut_white_bg(im, bg_thr)
        rgb = im.convert("RGB")
        if sub_alpha is None:            # 无 numpy → 不抠底，退回白边卡片观感
            sub_alpha = Image.new("L", (w, h), 255)
    else:
        sub_alpha = Image.new("L", (w, h), 255)
        rgb = im.convert("RGB")

    # 边缘抗锯齿：轻微模糊 alpha（主体本来就带渐变边缘时也安全）
    sub_alpha = sub_alpha.filter(ImageFilter.GaussianBlur(0.6))

    bd = int(border) if border else max(6, int(round(0.026 * w)))
    sh_r = int(shadow) if shadow is not None else max(14, int(round(0.045 * w)))

    # 白色描边：主体 alpha 形态学膨胀 → 白色实心
    dil = sub_alpha.point(lambda v: 255 if v >= 110 else 0)
    dil = dil.filter(ImageFilter.MaxFilter(2 * bd + 1))
    dil = dil.filter(ImageFilter.GaussianBlur(0.9))

    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    body.paste(rgb, (0, 0), sub_alpha)                     # 主体
    white = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    vis = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    vis.alpha_composite(white)                             # 先铺白
    vis.putalpha(dil)                                      # 白只出现在膨胀区内
    vis.alpha_composite(body)                              # 主体压上去 → 露出的白就是描边

    if tilt:                                               # 微倾斜：贴纸「随手一贴」的歪
        vis = vis.rotate(float(tilt), resample=Image.BICUBIC, expand=True)
    vis_w, vis_h = vis.size

    if not sh_r:
        return vis, {"vis_w": vis_w, "vis_h": vis_h, "pad": 0, "w": vis_w, "h": vis_h,
                     "style": "sticker", "cutout": bool(has_alpha or cutout)}

    # 投影：白边整体一起投影，柔和且偏移明显（贴纸是「贴在画面上」的）
    pad = sh_r * 2
    a = vis.split()[3].filter(ImageFilter.GaussianBlur(sh_r))
    a = a.point(lambda v: int(v * bg_alpha))
    sh = Image.new("RGBA", (vis_w, vis_h), (0, 0, 0, 0))
    sh.putalpha(a)

    out = Image.new("RGBA", (vis_w + pad * 2, vis_h + pad * 2), (0, 0, 0, 0))
    out.alpha_composite(sh, (pad, pad + sh_r // 3))
    out.alpha_composite(vis, (pad, pad))
    return out, {"vis_w": vis_w, "vis_h": vis_h, "pad": pad,
                 "w": out.width, "h": out.height,
                 "style": "sticker", "cutout": bool(has_alpha or cutout)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("images", nargs="+")
    ap.add_argument("--style", choices=["card", "sticker"], default="card",
                    help="card=圆角卡片(详情图) / sticker=抠底贴纸(产品图，默认首选)")
    ap.add_argument("--w", type=int, default=480, help="可见宽度(px)")
    ap.add_argument("--radius", type=float, default=0.055, help="card: 圆角半径占宽度比例")
    ap.add_argument("--border", type=int, default=None,
                    help="描边宽度；card 默认 3，sticker 默认按宽度自适应(≈2.6%%)")
    ap.add_argument("--shadow", type=int, default=None,
                    help="投影模糊半径，0=不要投影；不填时 card=12 / sticker=自适应")
    ap.add_argument("--tilt", type=float, default=0.0, help="sticker: 微倾斜角度，如 -2.5")
    ap.add_argument("--bg-thr", type=int, default=38, help="sticker: 白底容差(越大抠得越狠)")
    ap.add_argument("--no-cutout", action="store_true", help="sticker: 不抠底(源图本身是纯色背景)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    meta_path = os.path.join(args.out, "cards.json")
    meta = json.load(open(meta_path, encoding="utf-8")) if os.path.exists(meta_path) else {}

    for src in args.images:
        if not os.path.exists(src):
            print(f"跳过(不存在): {src}")
            continue
        if args.style == "sticker":
            img, m = build_sticker(src, args.w, args.border, args.shadow,
                                   tilt=args.tilt, bg_thr=args.bg_thr,
                                   cutout=not args.no_cutout)
        else:
            img, m = build_card(src, args.w, args.radius,
                                args.border if args.border is not None else 3,
                                args.shadow if args.shadow is not None else 12)
        stem = os.path.splitext(os.path.basename(src))[0]
        name = f"{stem}_{args.style}_w{args.w}.png"
        img.save(os.path.join(args.out, name))
        m.update({"png": name, "src": os.path.abspath(src), "style": args.style})
        meta[os.path.abspath(src)] = m
        extra = f" 已抠底 tilt={args.tilt}°" if args.style == "sticker" else f" 圆角{int(args.radius*args.w)}px"
        print(f"{stem}[{args.style}]: 可见 {m['vis_w']}x{m['vis_h']} 投影边 {m['pad']}{extra} → {name}")

    json.dump(meta, open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("元数据 →", meta_path)


if __name__ == "__main__":
    main()
