# -*- coding: utf-8 -*-
"""
分镜图人物抠图 + 背景修复，产出可用于分层视差的 fg(带alpha) / bg(干净背景)。

用法:
  python3 prepare_cutout.py 原图.png 抠图结果.png 输出目录 [--iters 80] [--blur 4]

背景修复用「邻域扩散」：反复对整图高斯模糊、只把人物区域替换成模糊值，
非人物区域始终还原原图。对纯色/渐变/虚化等干净背景效果接近内容识别填充；
复杂背景会得到柔和色块 —— 所以只建议对背景干净的图使用本流程。
"""
import sys, os, time
import numpy as np
from PIL import Image, ImageFilter


def inpaint(src_rgb, mask, iters=80, radius=4):
    """迭代扩散填补 mask 区域"""
    work = np.array(src_rgb, dtype=np.float32)
    orig = work.copy()
    known = ~mask
    for i in range(iters):
        blurred = np.array(
            Image.fromarray(work.astype(np.uint8)).filter(ImageFilter.GaussianBlur(radius)),
            dtype=np.float32)
        work[mask] = blurred[mask]
        if i % 20 == 0:
            work[known] = orig[known]      # 周期性地把已知区域钉回原值，防止整体糊掉
    work[known] = orig[known]
    return Image.fromarray(work.clip(0, 255).astype(np.uint8))


def main():
    src_path, cut_path, out_dir = sys.argv[1], sys.argv[2], sys.argv[3]
    iters = int(sys.argv[sys.argv.index("--iters") + 1]) if "--iters" in sys.argv else 80
    radius = float(sys.argv[sys.argv.index("--blur") + 1]) if "--blur" in sys.argv else 4
    os.makedirs(out_dir, exist_ok=True)
    name = os.path.splitext(os.path.basename(src_path))[0]

    src = Image.open(src_path).convert("RGB")
    cut = Image.open(cut_path).convert("RGBA")
    if cut.size != src.size:
        cut = cut.resize(src.size, Image.LANCZOS)

    alpha = np.array(cut)[:, :, 3]
    mask = alpha > 110                       # 只填实心人物区，保留羽化边不污染背景
    ratio = 100.0 * mask.mean()
    print(f"人物占画面 {ratio:.1f}%  尺寸 {src.size}")

    t0 = time.time()
    bg = inpaint(src, mask, iters, radius)
    print(f"背景修复完成 {time.time()-t0:.1f}s")

    # 背景层整体轻微虚化：既模拟景深，也掩盖修复区与周边的接缝
    bg_soft = bg.filter(ImageFilter.GaussianBlur(6))
    bg.save(os.path.join(out_dir, f"{name}_bg.png"))
    bg_soft.save(os.path.join(out_dir, f"{name}_bg_soft.png"))
    cut.save(os.path.join(out_dir, f"{name}_fg.png"))
    print("输出:", out_dir)


if __name__ == "__main__":
    main()
