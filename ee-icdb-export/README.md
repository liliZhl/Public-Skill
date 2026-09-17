# ee-icdb-export

**不打开 Expedition EE 的图形界面，直接从工程里把料号、位号、Value、封装、
图纸页和引脚级网络导出来。** 用的是 Mentor 自带的命令行导出器
`icdb2csv.exe`，不需要 COM、不需要 32 位解释器、不需要人工点菜单。

> English abstract: export schematic/BOM data from a Mentor/Siemens Expedition
> EE (iCDB) design using Mentor's own `icdb2csv.exe` — no GUI, no COM, no
> 32-bit interpreter, no pip dependencies. The one non-obvious detail is the
> mandatory `-properties` flag; see "为什么需要这个工具" below.

## 为什么需要这个工具

显而易见的命令是跑不通的：

```bash
icdb2csv.exe -icdb=<工程>\database -project=<工程>\<x>.prj -output=<出目录> -offline
# → ERROR: no properties found in prp file!     （rc=1，零产出）
```

它报的错会把人引向"`.prj` 里的属性文件路径没展开"，但**根本没有那个属性文件**。
真正的开关是 `-properties`——Mentor 自己的帮助里写的是
*"created additional prop_ID on demand"*，即"按需生成属性号"。加上它就能导出。

这个技能把那条命令封装成了带环境自检、工程扫描、失败重试、BOM 汇总和
安全提示的工具，并且**不写死任何本机路径**，可以直接分发给别人用。

## 环境要求

| 项 | 要求 |
|---|---|
| EE | Mentor/Siemens Expedition EE7.9.x（DxDesigner → Expedition 流程，iCDB 工程） |
| 系统 | Windows（导出器是 Windows 可执行文件） |
| Python | 3.7+，**只用标准库**，无需 pip 安装任何东西 |
| 其他 | 不需要 EE 正在运行；建议关闭 EE 后再导出 |

## 目录结构

```
ee-icdb-export/
├── SKILL.md                          给 AI 读的操作指令（Agent Skills 格式）
├── README.md                         本文件
├── scripts/
│   └── icdb_export.py                工具本体
└── references/
    ├── icdb-data-format.md           12 张表的完整列结构、属性号映射、文本网表的坑
    └── troubleshooting.md            报错原文 → 根因 → 处置；写库行为说明
```

## 快速开始

### 最省事：进向导

```bash
python scripts/icdb_export.py --interactive
# 自动扫当前目录找工程 → 编号选择 → 选内容 → 选输出目录 → 确认 → 导出
```

如果工程不在当前目录，先给个起点：

```bash
python scripts/icdb_export.py --interactive --find-root "E:/<PROJECT_ROOT>"
```

### 完全交互式：自己拼参数

```bash
# 1) 环境自检：确认能找到 EE，不用手动配任何东西
python scripts/icdb_export.py --doctor

# 2) 找工程（编号清单，也支持 --pick 出 JSON）
python scripts/icdb_export.py --find "E:/<PROJECT_ROOT>" --numbered

# 3) 导出原始表 + BOM
python scripts/icdb_export.py --prj "E:/<PROJECT_ROOT>/<PROJECT>/A-Hardware/01-工程文件/<PROJECT_ID>/<DESIGN>.prj" --bom
```

`--prj` 传 `.prj` 文件或包含它的目录都可以。默认输出到
`<工程目录>\icdb_export\`，用 `--out` 改。

产出：

```
<out>/
├── work/                            13 张表，Tab 分隔，入口索引 database.esf
│   ├── database.sym                 元件主表（位号 / 器件名 / 料号 / 图纸页）
│   ├── database.spr                 元件属性（按属性号索引，Value 在这里）
│   ├── database.prm                 属性号 → 属性名 映射（取值前必看）
│   ├── database.ppn / .spn          引脚级网络
│   └── ...
├── BOM_reference_detail.csv         位号明细（UTF-8-SIG，Excel 直接开）
└── BOM_part_summary.csv             按料号汇总（数量 + 位号列表）
```

## 命令参考

### 交互（面向使用者 / 宿主弹窗）

| 参数 | 说明 |
|---|---|
| `--interactive` | 终端向导：选工程 → 选内容 → 输出目录 → 确认 → 导出 |
| `--find-root <目录>` | 配合 `--interactive`，指定初始扫描目录（默认从当前目录及其父目录起扫） |
| `--pick <目录>` | 输出**候选工程 JSON**，只含可用项，供宿主的弹窗/选择组件直接消费 |
| `--find <目录> --numbered` | 人类可读的编号清单，用户可直接按号选择 |
| `--find <目录> --json` | `--find` 的 JSON 形式（含不可用项，便于解释为何跳过） |

`--pick` 的 JSON 结构（每个候选都带全齐信息，宿主不必再拼路径）：

```json
{
  "scanned": "E:\\<PROJECT_ROOT>",
  "count": 2,
  "usable_count": 2,
  "choices": [
    {
      "index": 1,
      "label": "<PROJECT_ID> / <DESIGN>",
      "prj": "E:\\<PROJECT_ROOT>\\...\\<PROJECT_ID>\\<DESIGN>.prj",
      "project_dir": "E:\\<PROJECT_ROOT>\\...\\<PROJECT_ID>",
      "design": "<DESIGN>",
      "root_block": "<ROOT_BLOCK>",
      "snapshot": "DxD",
      "icdb_dir": "E:\\<PROJECT_ROOT>\\...\\<PROJECT_ID>\\database"
    }
  ],
  "unusable": []
}
```

### 直接调用（无交互）

| 参数 | 说明 |
|---|---|
| `--prj <路径>` | 工程 `.prj` 或包含它的目录（**必填**，除 `--doctor`/`--find`/`--pick`/`--interactive`） |
| `--out <目录>` | 输出目录，默认 `<工程目录>\icdb_export` |
| `--bom` | 额外汇总两份 BOM CSV |
| `--csv-only` | 只要原始表，不生成 BOM |
| `--sdd <路径>` | 手动指定 EE 的 `SDD_HOME`（自动发现失败时用） |
| `--icdb <目录>` | 手动指定 iCDB 目录（默认读 `.prj` 的 `iCDBDir`） |
| `--snap <名字>` | 快照名；**离线模式下实测无影响**，默认读 `.prj` |
| `--no-retry` | 失败立即退出，不做重试 |
| `--doctor` | 环境自检后退出 |
| `--find <目录>` | 扫描该目录下的 EE 工程 |

## 给 AI 的交互协议

这一节是给"调用本技能的 AI"看的。要害是：**不要一上来就问用户"工程在哪"**——
工程是可以自己扫出来的，路径不该由用户提供。

1. **先扫后问**。按 当前工作目录 → 其父目录 → 用户已提到的目录 → 配置文件 的顺序静默扫描。
2. **给候选，不猜**。扫到多个 → 编号让用户选；扫到一个 → 报出名字直接进行；扫到零 → 才问目录，并列出试过的地方。
3. **跑之前确认**。导出会改写 `icdb.dat`、且数据库是单写入者，必须报出「工程 / 输出目录 / 先关 EE」并拿到明确同意。
4. **收尾给下一步**，不要停在"完成"。
5. **取消就干净退出**，绝不擅自代跑。

宿主能力的对应做法：

| 宿主能力 | 做法 |
|---|---|
| 有原生选择组件 | 调 `--pick <目录>`，把 `choices[].label` 做成选项，选中项的 `prj` 回传给 `--prj` |
| 只有终端 | 直接跑 `--interactive`，全流程已实现 |
| 完全脚本化批处理 | `--prj ... --bom`，全程不交互 |

## 安装到其他 AI 工具

本技能就是**开放 Agent Skills 格式**：一个带 `SKILL.md` 的普通文件夹。
把整个 `ee-icdb-export/` 复制到你所用工具扫描的目录即可：

| 工具 | 用户级 | 项目级 |
|---|---|---|
| **WorkBuddy** | `~/.workbuddy/skills/` | `.workbuddy/skills/` |
| **Claude Code** | `~/.claude/skills/` | `.claude/skills/` |
| **Cursor** | `~/.cursor/skills/` 或 `~/.agents/skills/` | `.cursor/skills/` 或 `.agents/skills/` |
| **Codex** | `~/.codex/skills/` | `.codex/skills/` |
| **任何 Agent Skills 宿主** | `~/.agents/skills/` | `.agents/skills/` |

```bash
cp -r ee-icdb-export ~/.claude/skills/
# 想只留一份给多个工具用：
ln -s ~/.cursor/skills/ee-icdb-export ~/.claude/skills/ee-icdb-export
```

Cursor 也会读 `.claude/skills/` 和 `.codex/skills/`，所以放一份可服务多个工具。
技能在会话启动时被扫描——**重启工具或让它重新扫描**之后才会生效。

如果目标工具不支持技能机制，也可以当作普通脚本直接用：

```bash
python /path/to/ee-icdb-export/scripts/icdb_export.py --prj "<工程>.prj" --bom
```

## 配置

`SDD_HOME` 的查找顺序（**先找到的先用，且每个候选都会被验证**——
所谓验证就是该目录下确实有 `iCDB\win32\bin\icdb2csv.exe`）：

1. `--sdd` 命令行参数
2. 环境变量 `EE_SDD_HOME`
3. 环境变量 `SDD_HOME`（EE 安装时会自己写进机器环境变量，通常已存在）
4. 配置文件 `sdd_home` 字段
5. Windows 注册表 `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment` 的 `SDD_HOME`
6. 常见安装位置扫描（各盘符下的 `MentorGraphics\*EE*\SDD_HOME` 等）

配置文件按以下顺序取**第一个可读**的：

```
$ICDB_EXPORT_CONFIG
./icdb-export.json
~/.icdb-export.json
~/.config/icdb-export/config.json
%APPDATA%\icdb-export\config.json
```

```json
{
  "sdd_home": "D:\\MentorGraphics\\9.5EE\\SDD_HOME"
}
```

`WDIR`、`MGC_HOME`、`MGLS_LICENSE_FILE`、`VBEST14PATH` 由脚本推导或沿用
现有环境变量，**不覆盖已设置的值**，也不会写死任何绝对路径。

## 安全须知

**导出会改写工程里的 `icdb.dat`。** 这不是 bug，是 iCDB 的会话簿记机制。
实测结论：

| 观察项 | 实测结果 |
|---|---|
| 文件体积 | 不变（连续多次导出均稳定） |
| 改动内容 | 会话记录区（内含主机名、用户名、访问时间戳） |
| 导出的数据 | **逐字节可复现**——连续 3 次导出，核心表 SHA256 完全一致 |
| 原理图内容 | 不受影响 |
| EE 自带备份 | `<工程>\database\cdbback\<时间戳>.zip`，内含完整 `icdb.dat` |

因此：

- 导出前**关闭 EE**，数据库是单写入者。
- 脚本会在导出前后各打印一次 `icdb.dat` 的哈希，改动不会被悄悄吞掉。
- 若确实需要绝对只读，只能整目录先快照到别处——但**不要试图在副本上跑导出**，
  iCDB 会判定副本 "inconsistent" 并拒绝（详见 `references/troubleshooting.md` §4）。

## 已实测的环境

| 项 | 值 |
|---|---|
| EE | Mentor Graphics EE7.9.5，装在 `E:\APP\MentorGraphics\7.9.5EE` |
| 导出器 | `iCDB2Csv` build tag 505826，build 2012-06-05，Flow: EE7.9.4, 7 |
| 验证工程 | `<PROJECT_ID>`（<ROOT_BLOCK>，12 张图纸页） |
| 验证结果 | 468 个元件 / 143 个料号条目 / 料号覆盖 **100%** / 21 个元件无 Value |
| Python | 3.13（64 位），**64 位可用**——本工具不依赖 32 位 |

## 已知限制

- **必须在工程原位导出**：副本会被 iCDB 拒绝，官方修复工具 `iCDBProjectBackup.exe`
  只有 GUI（命令行仅支持 `-p <project>`），无法脚本化。
- **单写入者**：EE 打开着工程时不要导出；上一次导出的 iCDB 会话退出前，
  紧接着的导出可能短暂失败——脚本会自动重试 3 次。
- **`-snap` 在离线模式下无效**：实测不传 / `DxD` / `DCDV` 三者产出一致。
  脚本仍按 `.prj` 声明的 `FrontEndSnapshot` 传入，以便在有快照语义的环境下保持正确。
- **COM 路径未打通**：`MGCPCB.Application` 的 CLSID 只注册在 `Wow6432Node`，
  需要 32 位进程；本工具不需要它，故未做验证。
- **`icdb2bom.exe` 的默认配置缺失**：7.9.x 安装里通常没有 `cdb2bom.asc`，
  所以 `-cfg` 会失败。BOM 由本工具从导出表自行汇总。
- **`icdb2ascii.exe`** 是另一个导出器，但需要 `-blks=<块名列表>` 才出数据，
  且同样会写库，能力不如 `icdb2csv`，仅作备用。
