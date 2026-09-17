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
| Python 3.9+ | 跑 `scripts/gh_env.py` | 已在位 |

`gh_env.py` 只依赖标准库，无需 pip 安装。

## 目录结构

```
github-control/
├── SKILL.md                      # 主文件：工作流 + 速查 + 坑表
├── scripts/
│   └── gh_env.py                 # 环境自检（自动处理 PATH 注入）
└── references/
    ├── local-env-notes.md        # 本机硬坑详解（含诊断过程与证据）
    └── diagram.md                # 设备码授权方向图 + 常见走错路
```

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
