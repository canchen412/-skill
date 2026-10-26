# -*- coding: utf-8 -*-
"""重点字音效去重/合并策略（sfx_policy）边界用例自检（2026-09-27）。

覆盖：连续重点字 / 跨句跨镜边界 / 邻近已有动效 / 密度上限 / 保留策略 / 关闭策略 / 手动权重。
跑法：python3 scripts/test_kw_sfx_policy.py   （不渲染、不烧积分，纯逻辑自检）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from build_ad_video import apply_kw_sfx_policy  # noqa: E402


def mk(kw, t, sfx="kw_tick", **extra):
    d = {"kw": kw, "t0": t, "sfx": sfx, "end": t + 1.5, "style": "pop"}
    d.update(extra)
    return d


def run(name, kws, cfg, others=()):
    print(f"\n── {name} ──")
    apply_kw_sfx_policy(kws, cfg, [], list(others))
    for e in sorted(kws, key=lambda x: x["t0"]):
        st = "响 " + str(e["sfx"]) if e["sfx"] else "静音（大字仍显示）"
        print(f"   {e['t0']:>6.2f}s  「{e['kw']}」  → {st}")
    return kws


Fails = []


def expect(name, cond, msg=""):
    print(("   ✅ " if cond else "   ❌ ") + name + ("" if cond else " " + msg))
    if not cond:
        Fails.append(name)


# A. 连续重点字（间隔 0.8s）：合并，留语义更重的「12500mg」（含数字）
k = run("A 连续重点字 0.8s（合并·留重）",
        [mk("钙片", 10.0), mk("12500mg", 10.8)],
        {"sfx_policy": {"kw_dedup_gap": 1.5}})
expect("A 普通词让位、数字词保留", k[0]["sfx"] is None and k[1]["sfx"] == "kw_tick")

# B. 跨句/跨镜边界（分属不同镜，但时间只差 1.2s）：按时间聚类，不按句分组
k = run("B 跨句边界 1.2s（仍合并）",
        [mk("牛奶", 20.0), mk("6大瓶", 21.2)],
        {"sfx_policy": {"kw_dedup_gap": 1.5}})
expect("B 跨句同样合并", k[0]["sfx"] is None and k[1]["sfx"] == "kw_tick")

# C. 邻近已有动效（转场唿哨 0.5s）：重点字让位
k = run("C 邻近转场唿哨 0.5s（让位已有动效）",
        [mk("21金维他", 16.5), mk("12500mg", 24.0)],
        {"sfx_policy": {"kw_near_window": 0.8}},
        others=[(16.0, "镜9转场唿哨")])
expect("C 邻避生效", k[0]["sfx"] is None and k[1]["sfx"] == "kw_tick")

# D. 密度：5 个重点字挤在 8s 内 → 窗口上限 3 个
k = run("D 密度控制（8s 内 5 个 → 上限 3）",
        [mk("钙", 10.0), mk("15mg", 11.0), mk("800mg", 12.0),
         mk("牛奶", 13.0), mk("6大瓶", 14.0)],
        {"sfx_policy": {"kw_density_window": 8.0, "kw_density_max": 3}})
n = sum(1 for e in k if e["sfx"])
expect(f"D 发声数 {n} ≤ 3", n <= 3)

# E. 保留策略切到 first（留时间靠前的）
k = run("E 保留策略 first（留靠前）",
        [mk("钙片", 10.0), mk("12500mg", 10.8)],
        {"sfx_policy": {"kw_dedup_gap": 1.5, "kw_keep": "first"}})
expect("E 留靠前者", k[0]["sfx"] == "kw_tick" and k[1]["sfx"] is None)

# F. 手动权重最高者优先（weight 压过数字规则）
k = run("F 手动 weight 优先",
        [mk("12500mg", 10.0), mk("小黄车", 10.8, weight=200)],
        {"sfx_policy": {"kw_dedup_gap": 1.5}})
expect("F weight 高者保留", k[0]["sfx"] is None and k[1]["sfx"] == "kw_tick")

# G. super/hero 权重最高（片头钩大字）
k = run("G super 最重（片头钩大字）",
        [mk("180以上的可以划走了", 0.0, super=True), mk("钙", 0.9)],
        {"sfx_policy": {"kw_dedup_gap": 1.5}})
expect("G super 保留", k[0]["sfx"] == "kw_tick" and k[1]["sfx"] is None)

# H. 关闭策略（sfx_policy: false）→ 一个都不静音
k = run("H 关闭策略（全部保留）",
        [mk("钙片", 10.0), mk("12500mg", 10.5)],
        {"sfx_policy": False})
expect("H 关闭后不干预", all(e["sfx"] for e in k))

# I. 间隔刚好等于阈值 → 不合并（阈值是严格小于）
k = run("I 间隔 == 阈值 1.5s（不合并）",
        [mk("钙片", 10.0), mk("12500mg", 11.5)],
        {"sfx_policy": {"kw_dedup_gap": 1.5}})
expect("I 边界不合并", all(e["sfx"] for e in k))

print("\n" + ("全部通过 ✅" if not Fails else f"失败 {len(Fails)} 项：{Fails}"))
sys.exit(1 if Fails else 0)
