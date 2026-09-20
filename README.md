# Public-Skill

公开版 Agent Skills 合集。收录可复用于通用场景的 Agent Skill，**已做脱敏处理**——所有环境标识与真实参数均已替换为占位符。

## 技能清单

| 技能 | 说明 |
|---|---|
| `eda-host-control` | 通过 SSH 驱动一台 Windows 主机：执行命令与 PowerShell、上传下载文件、读目录与日志、探测会话权限等级 |
| `windows-lan-remote-access` | 排查并修复 Windows 局域网内「远程控制另一台电脑」失败的问题：ping 单向不通、RDP 凭据不工作、域账号(NetBIOS/UPN)登录名格式、主机侧远程桌面授权与防火墙、RDP 证书/资源重定向警告、无外网环境下第三方远控选型、EDA 类节点锁定许可与远程会话冲突 |
| `github-control` | 在本机直接管理 GitHub：gh CLI 授权（设备码方向说明）、git 身份与凭据配置、仓库增删改、提交推送、Issue 与 PR、Actions 日志排查；附环境自检、**转公开前的敏感信息体检**、**已推送内容的历史改写补救**与技能双仓同步工具，以及 Windows 平台故障绕行（PATH 未注入、`refs/remotes` 写入被丢弃、沙箱内 clone 不落地） |
| `ee-icdb-export` | 从 Mentor/Siemens Expedition EE 工程里直接导出 BOM 与引脚级网表（不打开 GUI、不用 COM、不用 32 位解释器，仅标准库），并把同一工程两个版本的差异做成自包含 HTML 报告与多工作表 Excel 报表，用于 ECO 变更核对 |
| `dxdesigner-automation` | 用脚本驱动 Mentor/Siemens DxDesigner 绘制原理图：按**料号**从中央库直接调用元器件（不复制现成器件）、连线、写器件属性，并以导出的引脚级网表验收连接是否成立；含新建工程失败的根因分析与只读环境探查 |

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

### 项目 / 设计数据类

`ee-icdb-export` 与 `dxdesigner-automation` 的文档与示例里，来自真实工程的数据一律换成下列占位符：

| 占位符 | 含义 | 参考替换值 |
|---|---|---|
| `<PROJECT_ROOT>` | 存放工程的总目录 | `D:\designs` |
| `<PROJECT>` | 产品 / 项目代号目录 | `MyProject` |
| `<PROJECT_ID>` | 工程目录全名（含版本号） | `PRJ-0001` |
| `<DESIGN>` | 设计文件名主体（`<DESIGN>.prj`） | `MyBoard` |
| `<ROOT_BLOCK>` | 顶层原理图块名 | `TopBlock` |
| `<COMPANY>` | 公司名。**两种用法**：出现在料号或元件属性里；也作为中央库的**符号库分区名**（如 `AddPartInstance("<COMPANY>", …)` 的第 1 参） | `ACME` |
| `<PART_NO>` | 物料号 | `PN-00001` |
| `<PART_NAME>` | 器件规格名 | `SamplePart` |
| `<SYMBOL>` | 中央库中的符号名 | `res01` |
| `<TEST_PROJECT>` | 测试用工程目录名 | `TEST_PRJ` |
| `<NET_EN>` `<NET_GPIO>` `<NET_CTRL>` `<NET_RAIL>` | 网络名 | `EN_SIGNAL` |
| `<AUTO_NET>` | EDA 自动生成的网络名 | `$1N00000` |
| `<Q1>` `<R1>`…`<R6>` `<C1>` `<U1>` `<U2>` `<D1>` | 位号（元件在板上的位置） | `U1`、`R1`… |

`<AUTO_NET>` 之外，`$` 开头的网络名都是工具自动生成、不含设计意图；位号与网络名
在文档里只作**示意**，与任何真实设计无关。

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
├── ee-icdb-export/
│   ├── SKILL.md
│   ├── README.md
│   ├── LICENSE
│   ├── assets/
│   │   └── diff_report.tpl.html   # 网页报告模板（改样式只动这里）
│   ├── scripts/                   # 8 个模块，仅标准库
│   │   ├── icdb_export.py         # CLI 入口 / 向导 / 报告调度
│   │   ├── ee_env.py              # SDD_HOME 发现、环境构建、工程扫描
│   │   ├── ee_export.py           # 跑 icdb2csv、读表、写 BOM CSV
│   │   ├── ee_diff.py             # 比对引擎 + 统一记录集
│   │   ├── report_html.py         # 网页报告
│   │   ├── report_xlsx.py         # 多工作表 Excel
│   │   ├── report_csv.py          # 扁平 CSV
│   │   ├── choice_ui.py           # 终端提问 / 复选框
│   │   └── xlsx_writer.py         # 手写 .xlsx（zip + XML）
│   └── references/
│       ├── diff-design.md         # 比对的设计决策与实测记录
│       ├── icdb-data-format.md    # 12 张表的列结构、属性号映射
│       └── troubleshooting.md     # 报错原文 → 根因 → 处置
├── dxdesigner-automation/
│   ├── SKILL.md
│   ├── references/
│   │   ├── electrical-connectivity.md  # 放件配方、属性写入、网表验收
│   │   ├── live-verification.md        # 实机能力验证记录
│   │   ├── new-project-failure.md      # 新建工程失败的取证与拒绝条件
│   │   └── typelib-api.txt             # COM 类型库接口清单（dump）
│   └── scripts/                        # 7 个脚本，后期绑定 COM
│       ├── dxd.py                      # 核心封装：放件 / 连线 / 属性 / 查找
│       ├── demo_draw.py                # 画一张电气合法的示意原理图
│       ├── verify_connectivity.py      # 网表验收（五档判决）
│       ├── new_project.py              # 复制现成工程来新建工程
│       ├── probe.py                    # 只读连通性探测
│       ├── find_msg.py                 # 报错原文 → 定位弹窗模块
│       └── dump_typelib.py             # 导出类型库接口清单
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
Copy-Item -Recurse .\eda-host-control, .\ee-icdb-export, .\github-control, .\windows-lan-remote-access, .\dxdesigner-automation "$env:USERPROFILE\.workbuddy\skills\"
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

### `ee-icdb-export`

- Mentor/Siemens Expedition EE 7.9.x（DxDesigner → Expedition 流程，iCDB 工程）
- Windows（导出器是 Windows 可执行文件）
- Python 3.7+，**只用标准库**，无需 pip 安装任何东西

先跑一次环境自检，它会自己找 EE 的安装位置：

```
python scripts/icdb_export.py --doctor
```

### `dxdesigner-automation`

- Mentor/Siemens Expedition EE 7.9.x（含 DxDesigner / ViewDraw）
- Windows，且**必须是交互式桌面会话** —— COM 自动化在无桌面的会话里不可用
- Python + `pywin32`（`win32com`），且**必须后期绑定**（`win32com.client.dynamic.Dispatch`）
- 需要有效的本机许可（node-locked 或 floating 均可）
- 脚本以**本技能根目录**为工作目录运行：文档里的 `scripts/xxx.py` 指本技能，
  `../ee-icdb-export/scripts/icdb_export.py` 指同一批技能里的网表导出器
- 网表验收依赖 `ee-icdb-export` 先导出 `database.sym` / `database.ppn` 等表

先跑只读探测确认环境可用：

```
python scripts/probe.py
```

## 说明

本仓库为脱敏公开版，便于分享与复用。涉及具体环境的配置请自行填写，占位符含义见上方对照表。

技能内容基于真实排障过程整理，但环境差异较大，落地前请先在你的目标主机上验证。
