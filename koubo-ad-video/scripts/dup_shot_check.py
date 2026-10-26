# -*- coding: utf-8 -*-
"""分镜素材重复检测（#184 规则卡点）。

两类判定：
  ① **同一图片重复**：同一路径 或 同 md5 出现在同一条片多个镜位 ⇒ 硬重复（禁止）。
  ② **动作/构图高度相似**：素材文件名前缀相同（如 `产品_托新款粉瓶竖大拇指`）
     ⇒ 同一"持瓶系列"，即使文件名不同，构图/动作也高度雷同（实测：托瓶+竖大拇指
     vs 单指指向罐 vs 双手捧罐 vs 仰拍怼镜头，全部"正面+瓶在右下+暖灰背景+静态站姿"）。

用法：
    python3 dup_shot_check.py <config.json> [...]
    python3 dup_shot_check.py --dir <工程根>     # 自动扫 a/b/c/d/config.json
"""
import json, os, sys, hashlib, re
from collections import defaultdict

# 素材"系列"关键词 → 归一化系列名（同一系列=构图动作高度相似）
SERIES_RULES = [
    # 小绿人持瓶/展示系列（6 张全部同构图：正面+瓶在右下+暖灰渐变+静态站姿）
    (r"竖大拇指|托罐竖拇指", "持瓶_竖大拇指"),
    (r"单指指向",            "持瓶_单指指向"),
    (r"双手捧",              "持瓶_双手捧"),
    (r"仰拍手持|怼镜头",      "持瓶_仰拍怼镜"),
    (r"举.*过顶|高举",         "持瓶_高举过头"),
    (r"抱一摞|双臂抱",         "持瓶_抱多瓶"),
    (r"粉瓶配葡萄籽|葡萄籽红藻", "持瓶_配葡萄籽"),
    (r"指空蔬菜格",           "持瓶_指空蔬菜格"),
    (r"粉瓶配果蔬|果蔬坚果",    "持瓶_配果蔬坚果"),
    (r"倒粉色片剂|片剂到手心",  "持瓶_片剂特写"),
    (r"指空蔬菜|托瓶",         "持瓶_托瓶"),
    # 通用系列
    (r"蛋白餐堆过量|蛋白餐堆成山", "餐食_蛋白堆"),
    (r"深夜算账|日历抛硬币",       "算账_价格镜"),
    (r"对镜挤痘|痘印特写|照镜被吓到|镜里更健康|镜面碎裂", "皮肤_照镜系列"),
    (r"单杠悬垂|俯卧撑|健身房训练", "训练_视频镜"),
]


def series_of(basename: str):
    for pat, name in SERIES_RULES:
        if re.search(pat, basename):
            return name
    return None


def md5(p):
    if not os.path.isfile(p):
        return None
    h = hashlib.md5()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()


def check(cfg_path):
    cfg = json.load(open(cfg_path, encoding="utf-8"))
    ph = cfg["phrases"]
    tag = os.path.basename(os.path.dirname(cfg_path)).upper()

    # ① 同一图片重复（硬重复）
    by_path, by_hash = defaultdict(list), defaultdict(list)
    for i, p in enumerate(ph, 1):
        s = p["src"]
        by_path[s].append(i)
        h = md5(s)
        if h:
            by_hash[h].append(i)
    hard = []
    for s, ns in by_path.items():
        if len(ns) > 1:
            hard.append((ns, os.path.basename(s), "同一路径"))
    for h, ns in by_hash.items():
        if len(ns) > 1 and not any(ns == n for n, _, _ in hard):
            hard.append((ns, "(同内容不同名)", "同 md5"))

    # ② 动作/构图相似（同系列）
    by_series = defaultdict(list)
    for i, p in enumerate(ph, 1):
        ser = series_of(os.path.basename(p["src"]))
        if ser:
            by_series[ser].append(i)
    soft = []
    for ser, ns in by_series.items():
        if len(ns) > 1:
            files = [os.path.basename(ph[n - 1]["src"]) for n in ns]
            soft.append((ser, ns, files))

    return tag, len(ph), hard, soft


def main():
    args = sys.argv[1:]
    if args and args[0] == "--dir":
        root = args[1]
        cfgs = [os.path.join(root, k, "config.json")
                for k in "abcd" if os.path.isfile(os.path.join(root, k, "config.json"))]
    else:
        cfgs = args
    if not cfgs:
        print("用法: dup_shot_check.py <config.json>... | --dir <工程根>")
        return 1

    n_hard = n_soft = 0
    for c in cfgs:
        tag, n, hard, soft = check(c)
        print(f"\n═══════ {tag} 条（{n} 镜）═══════")
        if hard:
            n_hard += len(hard)
            for ns, f, why in hard:
                print(f"  ❌ 【硬重复·{why}】镜 {ns} 共用「{f}」")
        else:
            print("  ✅ 无同一图片重复")
        if soft:
            n_soft += len(soft)
            for ser, ns, files in soft:
                print(f"  ⚠️ 【相似·同系列】{ser}：镜 {ns}")
                for n, f in zip(ns, files):
                    print(f"        镜{n:2d}  {f}")
        else:
            print("  ✅ 无同系列雷同")
    print(f"\n═══ 汇总：硬重复 {n_hard} 处 / 相似 {n_soft} 处 ═══")
    return 0


if __name__ == "__main__":
    sys.exit(main())
