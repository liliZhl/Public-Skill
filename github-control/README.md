# github-control

让 agent 直接管理本机的 GitHub —— 授权、仓库、提交推送、Issue/PR、Actions 排查。

首次配置已经做完；这个技能的价值主要在**故障绕行**：本机 shell 与沙箱层有几个
反直觉的坑，踩过一次就再也不想重新推演。

## 安装

标准 Agent Skills 格式（一个目录 + `SKILL.md` 入口）。复制整个目录到目标工具的
技能目录即可：

| 工具 | 技能目录 |
|---|---|
| WorkBuddy / CodeBuddy | `~/.workbuddy/skills/` |
| Claude Code | `~/.claude/skills/` |
| 其他 | 见对应工具的 skills 目录约定 |

```bash
cp -r github-control ~/.workbuddy/skills/
```

## 依赖

| 组件 | 用途 | 安装 |
|---|---|---|
| `gh` CLI | 全部 GitHub 操作 | `winget install --id GitHub.cli -e` |
| `git` | 本地版本管理 | 已在位（PortableGit） |
| Python 3.9+ | 跑 `scripts/` 下的自检 / 体检 / 同步工具 | 已在位 |

`scripts/` 下的脚本只依赖标准库（历史改写另需 `git` 的 `filter-branch`），无需 pip 安装。

## 目录结构

```
github-control/
├── SKILL.md                      # 主文件：工作流 + 速查 + 坑表
├── scripts/
│   ├── gh_env.py                 # 环境自检（自动处理 PATH 注入）
│   ├── scan_sensitive.py         # 公开仓前体检：个人路径 / 内网标识 / 凭据
│   ├── scrub_git_history.py      # 历史改写补救：把标识从「全部提交」中抹除
│   └── sync_skill_repos.py       # 技能双仓同步（私有原样 / 公开脱敏）
└── references/
    ├── local-env-notes.md        # 本机硬坑详解（含诊断过程与证据）
    └── diagram.md                # 设备码授权方向图 + 常见走错路
```

## 配套工具

| 脚本 | 用途 | 典型用法 |
|---|---|---|
| `gh_env.py` | 环境自检，告诉你缺什么 | `python scripts/gh_env.py` |
| `scan_sensitive.py` | **仓库转公开前**的体检：扫个人路径、内网 IP、域账号形状、密钥字面量、AI 工具目录；退出码 0/1 便于接进脚本 | `python scripts/scan_sensitive.py <仓库路径> [--history]` |
| `scrub_git_history.py` | 已被推送的敏感内容补救：逐提交重写历史 → 清陈旧 ref → 回收对象 → 独立复查 | `python scripts/scrub_git_history.py --repo <路径> [--push]` |
| `sync_skill_repos.py` | 把技能目录同步到「私有原样 + 公开脱敏」双仓 | `python scripts/sync_skill_repos.py [--push] [--only public]` |

后两个脚本**内部不含任何环境标识**，替换规则在运行时从 `gh api user`、环境变量与
凭据文件推导，可直接分享。注意它们有「脚本白名单跳过」机制以防自伤，具体见 SKILL.md。

## 快速验证

```bash
python scripts/gh_env.py
```

看 `verdict` 行：

| verdict | 含义 | 下一步 |
|---|---|---|
| `READY` | 环境就绪 | 直接干活 |
| `NOT_AUTHED` | 未授权 | 走 SKILL.md §2 设备码流程 |
| `NO_GH` | gh 未安装 | `winget install --id GitHub.cli -e` |
| `NO_GIT` | git 未找到 | 检查 PortableGit 是否在位 |

退出码与 verdict 对应：`0`=READY，`1`=NO_GH，`2`=NO_GIT，`3`=NOT_AUTHED。

脚本同时落盘到 `<tempdir>/gh_env_last.txt`（`--json` 时另写 `.json`）——
本机 shell 有吞输出/转码的毛病，读文件比读 stdout 可靠。

## 授权状态

本机已完成授权（账号 `<GH_LOGIN>`），令牌由 gh 加密存于 **Windows 凭据管理器**
（keyring，非明文）。随时可用 `gh auth logout` 收回。

令牌 scope：`repo` / `gist` / `read:org` —— **不含 `workflow`**。
需要改 `.github/workflows/` 时补权限：

```bash
gh auth refresh -h github.com -s workflow
```

## 注意

- **两种认证方式不要混用。** 本机走 HTTPS + gh 凭据助手，不要额外配 SSH 密钥。
- `~/.workbuddy/secrets/` 下的凭据文件**绝不入库**。
- 建公开仓库、推送公开仓等**对外可见**的操作，先向用户确认。
