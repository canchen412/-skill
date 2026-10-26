# wb-skills

灿爹的 WorkBuddy 技能仓库 —— 跨机器同步用（主号 Mac mini `mac` ↔ 小号 `apple`）。

## 当前收录

| 技能 | 文件数 | 体积 | 状态 |
|---|---:|---:|---|
| `koubo-ad-video` 带货口播成片 | 223 | 15.1 MB | ✅ 试点中 |

## 快速开始

### 主号（改完技能后发布）

```bash
cd ~/wb-skills
bash _tooling/push.sh "这次改了什么"
```

### 小号（首次）

```bash
# ① 拉取
git clone https://github.com/canchen412/-skill.git ~/wb-skills

# ② 连主号的素材盘：Finder → 前往 → 连接服务器 → smb://192.168.1.103 → 广告视频
#    挂载后是 /Volumes/广告视频

# ③ 路径对齐（关键！让技能里的 /Volumes/陈灿会展/广告视频/... 生效）
bash ~/wb-skills/_tooling/link-assets.sh
#    小号用户名是 apple → 加 --home 一并处理 /Users/mac 硬编码
bash ~/wb-skills/_tooling/link-assets.sh --home

# ④ 同步技能到 WorkBuddy 目录
bash ~/wb-skills/_tooling/pull.sh

# ⑤ 装环境
bash ~/wb-skills/_tooling/setup-mac.sh

# ⑥ 重启 WorkBuddy
```

### 小号（日常更新）

```bash
cd ~/wb-skills && bash _tooling/pull.sh && echo "记得重启 WorkBuddy"
```

## ⚠️ 两台机器的差异（已知）

| 项 | 主号 `mac`（Mac mini 192.168.1.103） | 小号 `apple` |
|---|---|---|
| 素材盘 | 外接 exFAT「陈灿会展」→ `/Volumes/陈灿会展` | 走 SMB，挂载为 `/Volumes/广告视频` |
| 路径修正 | 无需 | `link-assets.sh` 建软链补齐「陈灿会展」这一层 |
| 用户名 | `/Users/mac`（技能内 87 处引用） | `/Users/apple` → 需 `--home` 建别名 |
| ffmpeg | `~/.local/bin/ffmpeg` 9.0 | 需确认，`setup-mac.sh` 会查 |

## 已知限制

- **素材盘依赖主号开机 + 同网**。主号关机 → 小号 SMB 断线 → 软链悬空。
- **exFAT 无并发保护**。共享可读写，两台同时写 `广告视频/` 有覆盖风险 → 约定只主号写素材。
- **字体 `.otf/.ttf` 已入库**（约 5MB，渲染必需）。若仓库转为 Public 必须先删除，商用字体授权不允许公开分发。
- `.wav` 原始 BGM 未入库（`.m4a` 已保留）；`STHeiti Medium.ttc` 53MB 未入库。

## 工具

| 脚本 | 作用 |
|---|---|
| `_tooling/precheck.py` | 体检（体积/大文件/嵌套仓库/凭据/跨机路径/SKILL.md）+ 生成 MANIFEST |
| `_tooling/push.sh` | 主号发布：体检 → 提交 → 推送 → 打标签 |
| `_tooling/pull.sh` | 小号同步：判脏 → 备份 → 覆盖；支持 `--dry-run` / `--rollback` |
| `_tooling/link-assets.sh` | 双机路径对齐（素材盘软链 + 用户名别名） |
| `_tooling/setup-mac.sh` | 环境自检与安装 |

详细方案见工作区 `skills-git-sync/方案.md`。
