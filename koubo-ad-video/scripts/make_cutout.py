# -*- coding: utf-8 -*-
"""
一键抠图分层：原图 -> 云端 matting -> fg(带alpha)/bg(补背景) 两层。

用法:
  python3 make_cutout.py 图1.png [图2.png ...] --token <临时token> [--out 输出目录] [--lib]

参数:
  --token   必填。由 connect_cloud_service 工具获取（每次会话会变，不要写死）。
  --out     输出目录，默认 ./layers
  --lib     附加模式：输出到图库 IP 目录下的 _分层/（与 --out 二选一）
  --iters   背景扩散迭代次数，默认 80
  --blur    背景层虚化半径，默认 6

注意：
  * 必须串行 —— 并行会撞产物名互相覆盖，且无法判断哪个产物对应哪张图。
  * 产物落在 /tmp（macOS 可能是 /private/tmp）processed_image_*.png，
    脚本用「调用前后的文件快照对比」抓最新产物，再改名归档。
  * 每张约 15-20s，多张请后台跑。
"""
import os, sys, glob, time, shutil, subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
PROC = "/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources/plugins/workbuddy-builtin/skills/buddy-image-processing/scripts/buddy-image-processing.py"
TMP_PATTERNS = ["/tmp/processed_image_*.png", "/private/tmp/processed_image_*.png"]


def snapshot():
    s = set()
    for p in TMP_PATTERNS:
        s |= set(glob.glob(p))
    return s


def newest_after(before, timeout=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        time.sleep(2)
        now = snapshot()
        new = now - before
        if new:
            cur = max(new, key=os.path.getmtime)
            # 再等 3s，确认文件大小稳定（写盘完成）
            if last == cur:
                s1 = os.path.getsize(cur)
                time.sleep(3)
                if os.path.getsize(cur) == s1 and s1 > 0:
                    return cur
            last = cur
    return None


def main():
    args = [a for a in sys.argv[1:]]
    if "--token" not in args:
        print("缺少 --token（用 connect_cloud_service 获取）"); return 1
    tk = args[args.index("--token") + 1]
    imgs = [a for a in args if not a.startswith("--") and a != tk
            and os.path.isfile(a)]
    if not imgs:
        print("没找到可处理的图片"); return 1

    if "--lib" in args:
        out = os.path.join(os.path.dirname(imgs[0]), "_分层")
    else:
        out = args[args.index("--out") + 1] if "--out" in args else "./layers"
    iters = args[args.index("--iters") + 1] if "--iters" in args else "80"
    os.makedirs(out, exist_ok=True)

    ok = []
    for img in imgs:
        name = os.path.splitext(os.path.basename(img))[0]
        print(f"=== {name} ===")
        before = snapshot()
        r = subprocess.run(
            [sys.executable, PROC, "image-edit", "--operation", "matting",
             "--image-file", img, "--token", tk],
            cwd="/tmp", capture_output=True, text=True, timeout=300)
        if r.returncode != 0:
            print("  抠图失败:", (r.stderr or r.stdout).strip()[-300:]); continue
        mat = newest_after(before)
        if not mat:
            print("  !! 未抓到产物"); continue
        mat_copy = os.path.join(out, f"{name}_mat.png")
        shutil.copy(mat, mat_copy)
        subprocess.run([sys.executable, os.path.join(HERE, "prepare_cutout.py"),
                        img, mat_copy, out, "--iters", iters], check=False)
        if os.path.isfile(os.path.join(out, f"{name}_fg.png")):
            ok.append(name)
            print(f"  ✓ {name} -> {out}/{name}_fg.png + {name}_bg_soft.png")
        else:
            print(f"  !! {name} 分层生成失败")

    print(f"\n完成 {len(ok)}/{len(imgs)}: {', '.join(ok)}")
    print("config 里该句写法:",
          '"src":"..._fg.png","bg":"..._bg_soft.png","fg":"..._fg.png",'
          '"fg_motion":"popin|float"')
    return 0


if __name__ == "__main__":
    sys.exit(main())
