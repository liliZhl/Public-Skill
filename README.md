# Public-Skill

公开版 Agent Skills 合集。收录可复用于通用场景的 Agent Skill，**已做脱敏处理**——所有公司内网标识与真实环境参数均已替换为占位符。

## 技能清单

| 技能 | 说明 |
|---|---|
| `eda-host-control` | 通过 SSH 驱动一台 Windows 主机：执行命令与 PowerShell、上传下载文件、读目录与日志、探测会话权限等级 |
| `windows-lan-remote-access` | 排查并修复 Windows 局域网内「远程控制另一台电脑」失败的问题：ping 单向不通、RDP 凭据不工作、域账号(NetBIOS/UPN)登录名格式、主机侧远程桌面授权与防火墙、RDP 证书/资源重定向警告、无外网环境下第三方远控选型、EDA 类节点锁定许可与远程会话冲突 |

## 占位符对照表（重要）

本仓库所有文档与脚本示例中的以下占位符，**使用时请替换为你自己的实际值**：

| 占位符 | 含义 | 参考替换值 |
|---|---|---|
| `<HOST_IP>` | 目标主机 IP 地址 | `192.0.2.10` |
| `<HOSTNAME>` | 目标主机名 | `WIN-HOST01` |
| `<DOMAIN>` | AD 域名的 NetBIOS 名 | `CORP` |
| `<USER>` | 域账号用户名 | `alice` |
| `<SHARE>` | SMB 共享名 / 示例目录名 | `share` |
| `<VENV_PYTHON>` | 装有 paramiko 的 Python 解释器完整路径 | `C:\venv\Scripts\python.exe` |

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
└── windows-lan-remote-access/
    └── SKILL.md
```

## 安装

复制到 Agent 的用户级技能目录（以 WorkBuddy 为例）：

```powershell
Copy-Item -Recurse .\eda-host-control, .\windows-lan-remote-access "$env:USERPROFILE\.workbuddy\skills\"
```

## 使用前提

`eda-host-control` 需要目标主机满足：

- 已启用 OpenSSH Server，且允许**密码认证**（该技能不依赖公钥）
- 目标主机上存在一个可用的系统账户，且具备执行所需操作的权限
- 本机 Python 环境安装 `paramiko`

凭据不写入仓库，由运行时从外部配置文件读取；首次使用请运行：

```
python scripts/ssh_setup.py
```

## 说明

本仓库为脱敏公开版，便于分享与复用。涉及具体环境的配置请自行填写，占位符含义见上方对照表。

技能内容基于真实排障过程整理，但环境差异较大，落地前请先在你的目标主机上验证。
