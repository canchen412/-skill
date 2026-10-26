#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""开工前内存体检（koubo-ad-video · P0 流程闸门）

为什么需要（2026-09-27 实测教训）：
  同一台机器、同一个 TTS 模型，仅因为「配音期间还在跑别的重内存任务」，
  段落速度从 20–28 秒/段 掉到 320–763 秒/段（4–8 倍）。
  4 条 55 段硬下限 ≈ 22 分钟，实际跑了 106 分钟 —— 79% 时间浪费在内存换页上。
  所以：**配音启动前必须先体检**，而不是跑到一半才发现。

用法：
  python3 preflight.py                 # 只看体检报告
  python3 preflight.py --n 4           # 按「本次要出 N 条」估算配音耗时与内存需求
  python3 preflight.py --close         # 自动关闭白名单里的可关程序（会先打印清单）
  python3 preflight.py --close --yes   # 跳过确认直接关（非交互场景用）

退出码：0 = 可以开工；1 = 建议先处理（内存吃紧或有大程序在跑）
"""
import argparse
import json
import os
import subprocess
import sys

# 常见「占内存且与出片无关」的程序：应用名 -> osascript 退出用的进程名
CLOSABLE = {
    "QQBrowser": "QQ浏览器",
    "Google Chrome": "Google Chrome",
    "Microsoft Edge": "Microsoft Edge",
    "Safari": "Safari",
    "WeChat": "WeChat",
    "QQ": "QQ",
    "NeteaseMusic": "NeteaseMusic",
    "Music": "Music",
    "IINA": "IINA",
    "Photoshop": "Adobe Photoshop",
    "Adobe Premiere": "Adobe Premiere Pro",
    "Final Cut": "Final Cut Pro",
    "Docker": "Docker",
    "JianYing": "剪映专业版",
    "CapCut": "CapCut",
    "Lark": "Lark",
    "Feishu": "飞书",
    "DingTalk": "钉钉",
    "Doubao": "Doubao",
    "BaiduNetdisk": "百度网盘",
}

# 段数经验值：一条口播 ≈ 13~14 段；健康速度 20~28 秒/段（内存充足时）
SEG_PER_VIDEO = 13.5
SEC_PER_SEG_OK = 26.0      # 内存充足
SEC_PER_SEG_BAD = 320.0    # 内存吃紧

try:
    import psutil
except ImportError:
    print("需要 psutil：pip install psutil")
    sys.exit(2)


def mem_gb(x):
    return x / 1073741824.0


def swap_info():
    """返回 (total_MB, used_MB)；读不到返回 (None, None)"""
    try:
        out = subprocess.check_output(["/usr/sbin/sysctl", "vm.swapusage"]).decode()
        # vm.swapusage: total = 6144.00M  used = 5043.44M  free = 1100.56M
        seg = out.split("used =")[1].split("free =")
        used = float(seg[0].strip().rstrip("M"))
        tot = float(out.split("total =")[1].split("used =")[0].strip().rstrip("M"))
        return tot, used
    except Exception:
        return None, None


def running_closable():
    """返回正在运行且**确实占内存**的可关程序 {进程名: (个数, 占用MB)}
    注：只保留 >30MB 的 —— 微信/QQ/豆包这类后台驻留通常只有 2~3MB，关了没收益，
    不该混进「建议关闭」清单里干扰判断。"""
    hits = {}
    for key in CLOSABLE:
        tot_mb = 0.0
        cnt = 0
        for p in psutil.process_iter(["name", "memory_info", "exe", "cmdline"]):
            try:
                blob = " ".join(filter(None, [
                    p.info.get("exe") or "",
                    " ".join(p.info.get("cmdline") or []),
                    p.info.get("name") or "",
                ]))
                if key.lower() in blob.lower():
                    cnt += 1
                    tot_mb += p.info["memory_info"].rss / 1048576.0
            except Exception:
                pass
        if cnt and tot_mb >= 30:      # ★ 低于 30MB 的不值得列（驻留进程）
            hits[key] = (cnt, tot_mb)
    return hits


BUSY_MARKS = (
    "cosyvoice_batch", "cosyvoice_clone", "build_ad_video",
    "whisper_small_align", "align4.py", "align_vo.py",
    "qc_motion", "qc_burst", "qc_vo_pause",
)


def busy_tasks():
    """检测是否已有配音/渲染/QC 在跑（防止并发抢内存 —— P0 铁律）"""
    found = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            cmd = " ".join(p.info.get("cmdline") or [])
            if "preflight" in cmd:
                continue
            for m in BUSY_MARKS:
                if m in cmd:
                    found.append((p.info["pid"], m, cmd[:80]))
                    break
        except Exception:
            pass
    return found


def top_groups(n=6):
    groups = {}
    for p in psutil.process_iter(["name", "memory_info", "exe"]):
        try:
            exe = p.info.get("exe") or ""
            rss = p.info["memory_info"].rss / 1048576.0
            if "WorkBuddy.app" in exe:
                k = "WorkBuddy（本工程）"
            elif "/System/" in exe or "/usr/" in exe:
                k = "macOS 系统"
            elif exe.startswith("/Applications/"):
                k = "应用: " + exe.split("/")[2]
            else:
                k = "其他"
            groups[k] = groups.get(k, 0.0) + rss
        except Exception:
            pass
    return sorted(groups.items(), key=lambda x: -x[1])[:n]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="本次要出的视频条数（用于估算）")
    ap.add_argument("--close", action="store_true", help="关闭白名单里的可关程序")
    ap.add_argument("--yes", action="store_true", help="关闭前不再确认")
    a = ap.parse_args()

    vm = psutil.virtual_memory()
    stot, sused = swap_info()

    print("=" * 62)
    print("  开工前内存体检")
    print("=" * 62)
    print("物理内存  %.1f GB 已用 / %.1f GB 总计（%.0f%%）  空闲 %.2f GB"
          % (mem_gb(vm.used), mem_gb(vm.total), vm.percent, mem_gb(vm.available)))
    if stot:
        print("Swap      %.0f MB 已用 / %.0f MB 总计（%.0f%%）"
              % (sused, stot, 100.0 * sused / stot))
    print()
    print("内存占比 TOP：")
    for k, v in top_groups():
        print("   %8.0f MB  %s" % (v, k))

    warn = False

    busy = busy_tasks()
    if busy:
        print()
        print("⚠️  检测到已有重任务在跑 —— 配音期间必须独占，别重复启动：")
        for pid, m, cmd in busy[:6]:
            print("   pid=%-7d %-22s %s" % (pid, m, cmd[:52]))
        warn = True

    hits = running_closable()

    print()
    if hits:
        print("检测到可关闭的程序（与出片无关）：")
        for k, (cnt, mb) in sorted(hits.items(), key=lambda x: -x[1][1]):
            print("   %-16s %2d 个进程   %6.0f MB" % (CLOSABLE[k], cnt, mb))
        warn = True
    else:
        print("可关闭的第三方程序：无（已经关干净了）✅")

    # 内存水位判断
    print()
    if vm.percent >= 80 or (stot and sused / stot > 0.80):
        print("⚠️  内存吃紧：物理内存已用 %.0f%%、swap 已用 %s"
              % (vm.percent, ("%.0f%%" % (100.0 * sused / stot)) if stot else "—"))
        print("    配音可能掉到 300+ 秒/段。建议关掉上面的程序；若已关干净，考虑重启一次系统再开工。")
        warn = True
    else:
        print("✅ 内存水位正常")

    if a.n > 0:
        segs = a.n * SEG_PER_VIDEO
        print()
        print("本次预计 %d 条 / 约 %.0f 段配音：" % (a.n, segs))
        print("   内存充足 ≈ %.0f 分钟   |   内存吃紧 ≈ %.0f 分钟"
              % (segs * SEC_PER_SEG_OK / 60.0, segs * SEC_PER_SEG_BAD / 60.0))
        print("   ⚠️ 配音期间禁止并行：PIL 拼图 / whisper / 渲染 / 第二个 TTS")

    if a.close and hits:
        if not a.yes:
            ans = input("\n确认关闭上面这些程序？(y/N) ").strip().lower()
            if ans != "y":
                print("已取消")
                return 1 if warn else 0
        print()
        for k in hits:
            app = CLOSABLE[k]
            r = subprocess.run(["osascript", "-e", 'tell application "%s" to quit' % app],
                               capture_output=True, text=True)
            if r.returncode == 0:
                print("   已请求退出：%s" % app)
            else:
                print("   ❌ 退出失败（系统可能拒绝，需你手动关）：%s  —  %s"
                      % (app, (r.stderr or "").strip()[:60]))
        # 复查
        import time
        time.sleep(4)
        left = running_closable()
        if left:
            print("   仍在运行：%s" % "、".join(CLOSABLE[k] for k in left))
        else:
            print("   ✅ 都已退出")

    print()
    print("结论：%s" % ("⚠️ 建议先处理上面的问题再启动配音" if warn else "✅ 可以开工（记得：素材核验/config/对应表 全部做完，最后才启动配音）"))
    return 1 if warn else 0


if __name__ == "__main__":
    sys.exit(main())
