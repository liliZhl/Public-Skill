# Public-Skill

公开版 Agent Skills 合集。收录可复用于通用场景的 Agent Skill，**已做脱敏处理**——所有环境标识与真实参数均已替换为占位符。

## 技能清单

| 技能 | 说明 |
|---|---|
| `eda-host-control` | 通过 SSH 驱动一台 Windows 主机：执行命令与 PowerShell、上传下载文件、读目录与日志、探测会话权限等级 |
| `windows-lan-remote-access` | 排查并修复 Windows 局域网内「远程控制另一台电脑」失败的问题：ping 单向不通、RDP 凭据不工作、域账号(NetBIOS/UPN)登录名格式、主机侧远程桌面授权与防火墙、RDP 证书/资源重定向警告、无外网环境下第三方远控选型、EDA 类节点锁定许可与远程会话冲突 |
| `github-control` | 在本机直接管理 GitHub：gh CLI 授权（设备码方向说明）、git 身份与凭据配置、仓库增删改、提交推送、Issue 与 PR、Actions 日志排查；附环境自检、**转公开前的敏感信息体检**、**已推送内容的历史改写补救**与技能双仓同步工具，以及 Windows 平台故障绕行（PATH 未注入、`refs/remotes` 写入被丢弃、沙箱内 clone 不落地） |

## 占位符对照表（重要）

本仓库所有文档与脚本示例中的以下占位符，**使用时请替换为你自己的实际值**：

### 主机 / 远程访问类

| 占位符 | 含义 | 参考替换值 |
|---|---|---|
| `<HOST_IP>` | 目标主机 IP 地址 | `192.0.2.10` |
| `<HOSTNAME>` | 目标主机名 | `WIN-HOST01` |
| `<DOMAIN>` | AD 域名的 NetBIOS 名 | `CORP` |
| `<USER>` | **域账号**用户名（出现在 `DOMAIN\USER` 这类写法中） | `alice` |
| `<SHARE>` | SMB 共享名 / 示例目录名 | `share` |
| `<VENV_PYTHON>` | 装有 paramiko 的 Python 解释器完整路径 | `C:\venv\Scripts\python.exe` |

### GitHub 类

| 占位符 | 含义 | 参考替换值 |
|---|---|---|
| `<GH_LOGIN>` | GitHub 账号名 | `octocat` |
| `<GH_ID>` | GitHub 数字账号 ID（提交邮箱里用） | `583231` |
| `<GH_NAME>` | GitHub 显示名（提交作者名） | `Mona Lisa` |
| `<LOCAL_UID>` | 本机 Windows 用户 ID | `1001` |
| `<ACCOUNT>` | 域账号用户名（与上表 `<USER>` 同义，仅命名维度不同） | `alice` |

> **注意 `<USER>` 有两种用法，靠上下文区分**：在 `CORP\<USER>`、`<DOMAIN>\<USER>` 这类写法中指**域账号**；在 `C:\Users\<USER>\...` 这类**文件路径**中指 **Windows 系统用户名**。
> 两者通常不同，替换时别搞混。

文档中出现的 `<HOST_IP>`、`CORP\alice`、`user@domain.com`、`\\host\share` 等均为**通用示例值**，与任何真实环境无关。

## 目录结构

标准 Agent Skills 格式：每个技能一个目录，入口为 `SKILL.md`（含 YAML frontmatter）。

```
.
├── eda-host-control/
│   ├── SKILL.md
│   ├── README.md
│   ├── eda-ssh.py                 # 兼容转发壳
│   ├── references/
│   │   └── windows-ssh-notes.md   # 实测踩坑记录
│   └── scripts/
│       ├── ssh_ctl.py             # 主编排工具
│       └── ssh_setup.py           # 配置向导 / 自检
├── github-control/
│   ├── SKILL.md
│   ├── README.md
│   ├── references/
│   │   ├── local-env-notes.md     # Windows 平台硬坑详解
│   │   └── diagram.md             # 设备码授权方向图
│   └── scripts/
│       ├── gh_env.py              # 环境自检（自动处理 PATH 注入）
│       ├── scan_sensitive.py      # 仓库转公开前的敏感信息体检
│       ├── scrub_git_history.py   # 已推送敏感内容的历史改写补救
│       └── sync_skill_repos.py    # 技能双仓同步（私有原样 / 公开脱敏）
└── windows-lan-remote-access/
    └── SKILL.md
```

## 安装

复制到 Agent 的用户级技能目录（以 WorkBuddy 为例）：

```powershell
Copy-Item -Recurse .\eda-host-control, .\github-control, .\windows-lan-remote-access "$env:USERPROFILE\.workbuddy\skills\"
```

## 使用前提

### `eda-host-control`

- 目标主机已启用 OpenSSH Server，且允许**密码认证**（该技能不依赖公钥）
- 目标主机上存在一个可用的系统账户，且具备执行所需操作的权限
- 本机 Python 环境安装 `paramiko`

凭据不写入仓库，由运行时从外部配置文件读取；首次使用请运行：

```
python scripts/ssh_setup.py
```

### `github-control`

- 已安装 `gh` CLI：`winget install --id GitHub.cli -e`
- 已安装 `git`
- Python 3.9+（`gh_env.py` 只用标准库，无需 pip 安装）

首次使用先跑自检，它会告诉你缺什么：

```
python scripts/gh_env.py
```

## 说明

本仓库为脱敏公开版，便于分享与复用。涉及具体环境的配置请自行填写，占位符含义见上方对照表。

技能内容基于真实排障过程整理，但环境差异较大，落地前请先在你的目标主机上验证。
