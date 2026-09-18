# ee-icdb-export

**不打开 Expedition EE 的图形界面，直接从工程里把料号、位号、Value、封装、图纸页和
引脚级网络导出来；再把同一工程两个版本的差异做成可读的 ECO 变更核对报告。**

用的是 Mentor 自带的命令行导出器 `icdb2csv.exe`——不需要 COM、不需要 32 位解释器、
不需要人工点菜单，也不需要 `pip install` 任何东西。

> **English.** Export BOM and pin-level netlist data out of a Mentor/Siemens
> Expedition EE design (DxDesigner → Expedition, iCDB) by driving Mentor's own
> `icdb2csv.exe` — no GUI, no COM, no 32-bit interpreter, no third-party
> packages. Also compares two revisions of the same design at both the component
> layer (keyed on refdes) and the netlist layer (keyed on pins), and writes a
> self-contained HTML report plus a multi-sheet Excel workbook.
> The one non-obvious detail is the mandatory `-properties` flag — see
> [它解决什么问题](#它解决什么问题).

<p>
  <img alt="Python" src="https://img.shields.io/badge/Python-3.7%2B-3776AB?logo=python&amp;logoColor=white">
  <img alt="dependencies" src="https://img.shields.io/badge/dependencies-none-2E7D32">
  <img alt="Mentor EE" src="https://img.shields.io/badge/Mentor%20EE-7.9.x-005386">
  <img alt="offline" src="https://img.shields.io/badge/network-not%20required-616161">
  <img alt="License" src="https://img.shields.io/badge/license-MIT-blue">
</p>

## 目录

- [它解决什么问题](#它解决什么问题)
- [快速开始](#快速开始)
- [产出长什么样](#产出长什么样)
- [怎么比对](#怎么比对)
- [环境要求](#环境要求)
- [安装](#安装)
- [命令参考](#命令参考)
- [给 AI 的交互协议](#给-ai-的交互协议)
- [配置](#配置)
- [安全须知](#安全须知)
- [已实测的环境](#已实测的环境)
- [已知限制](#已知限制)
- [许可](#许可)

## 它解决什么问题

显而易见的命令是跑不通的：

```bash
icdb2csv.exe -icdb=<工程>\database -project=<工程>\<x>.prj -output=<出目录> -offline
# → ERROR: no properties found in prp file!     （rc=1，零产出）
```

它报的错会把人引向"`.prj` 里的属性文件路径没展开"，但**根本没有那个属性文件**。
真正的开关是 `-properties`——Mentor 自己的帮助里写的是
*"created additional prop_ID on demand"*，即"按需生成属性号"。加上它就能导出。

这个技能把那条命令封装成了带环境自检、工程扫描、失败重试、BOM 汇总、版本比对和
安全提示的工具，并且**不写死任何本机路径**，可以直接分发给别人用。

## 快速开始

### 最省事：进向导

```bash
python scripts/icdb_export.py --interactive
# 先选任务：① 导出工程数据  ② 比对两个版本
# 选① → 扫工程 → 编号选择 → 选内容 → 选输出目录 → 确认 → 导出
# 选② → 选旧版/新版（工程或已有导出目录）→ 复选框挑比对内容与格式 → 确认 → 出报告
```

工程不在当前目录时，先给个起点：

```bash
python scripts/icdb_export.py --interactive --find-root "E:/<PROJECT_ROOT>"
```

### 或者自己拼参数

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

### 比对两个版本（ECO 变更核对）

```bash
# 两个已有的导出目录 —— 纯离线，不碰 EE、不改工程文件（推荐）
python scripts/icdb_export.py --diff "<旧版>/icdb_export" "<新版>/icdb_export"

# 也可以直接给两个工程：各自先导一次（会写 icdb.dat）
python scripts/icdb_export.py --diff "E:/.../<PROJECT_ID>" \
                                     "E:/.../<PROJECT_ID>" --label 1328 1428

# 不带路径 = 进向导（选框形式挑比对内容与报告格式）
python scripts/icdb_export.py --diff
```

路径自动识别：含 `work/database.sym` 当作导出目录直接读；含 `.prj` 就先导出。
报告默认落到 `./icdb_diff/`，用 `--out` 改。

**比对什么，用勾选决定**（向导里是空格勾选 / 方向键移动的复选框；宿主 AI 走
`--diff-options` 拿同一份清单去弹自己的多选框）：

```
   > [x] 元件级 BOM 对比     换料 / 改值 / 新增 / 删除，以位号为主键
     [x] 网络级对比          引脚改接 / 网络增删 / 构成变化，看连接有没有动
     [x] 网页报告            单文件自包含，浏览器直接打开，按钮切页，可打印
     [x] Excel 报表          一个文件多张工作表，每类差异一张；可筛选可透视
     [ ] CSV 长表            单张纯文本表，供脚本读取；给人看请用 Excel 报表

   空格=勾选/取消   ↑↓=移动   a=全选   n=全不选   Enter=确认   Esc=取消
```

只想要一层就只勾一层，对应 `--scope bom` / `--scope net`；不要某格式加
`--no-html` / `--no-excel`，想额外出 CSV 才勾最后一项。默认除 CSV 外全开。

终端摘要（两层各自成节，**不相加**——它们回答的是不同问题）：

```
  旧版 1328 : 468 元件 / 114 料号
  新版 1428 : 475 元件 / 123 料号

  更换料号      2
  变更参数     13
  新增元件      7
  删除元件      0
  -------------
  差异合计     22
  完全一致    451
  受影响料号 11 个（料号视角）

  网络层: 245 -> 249 个网络 / 1345 -> 1360 个连接点

  引脚改接      2
  -------------
  连接变化合计      2
  新增接点     15
  断开接点      0
  网络构成变化      4
  网络改名      0
  新增网络      4
  删除网络      0

  需人工确认: 更换料号 2 处、引脚改接 2 处（报告首页已单独列出）
```

## 产出长什么样

### 导出（`<out>/`）

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

### 差异报告（`<报告目录>/`）——默认两个文件

```
diff_report.html   给人读的。单文件自包含、零外部引用，离线双击可开。
                   首屏是一句话结论 + 可点击的计数块 + 需人工确认的变更清单；
                   按钮切页：概览 / 元件变更 / 网络变更 / 料号视角。
                   每张表自带筛选行，另有深色模式与打印样式（每节另起一页）
diff_report.xlsx   给人查的。五张工作表，每张只放一类内容：
                   概览 / 元件级差异 / 引脚改接 / 网络变化 / 料号视角
                   首行冻结 + 自动筛选；变更类型着色（新增红、断开绿）
diff_report.csv    只有加 --csv 才出。7 列长表，一行一个变化字段：
                   分类 / 差异类型 / 对象 / 明细 / 旧版本 / 新版本 / 说明
                   给脚本读的；给人看请用 Excel 报表
```

**为什么分成工作表，而不是一张表。** 上一版那份 CSV 把四类内容塞进一张表、
用一个「分类」列区分，这正是它读起来费劲的原因：四种行形状共用一个表头，
按任一列排序都会把别的打乱，筛选也会捞回不相干那一层的行。一类一张表之后，
每张表的列各自成立——想看换料，就在「元件级差异」里筛「变更项 = 料号」。

**Excel 是字段级，HTML 是对象级。** 一个位号改了两处，在 Excel 里是两行
（所以「变更项」可筛选），在 HTML 里仍是一行、只显示真正变了的字段。
新增元件也不再挤成一格：Excel 与 CSV 都拆成一行一个字段。两种格式行数
**故意不同**，它们回答不同的问题。

**行数可对账。** 拿 Excel 报表和 CSV 对一遍是值得做的自检：「元件」行数必须
相等，「引脚」「料号」也是；「网络」只可能 CSV 更多——一条网络既丢引脚又加引脚时，
CSV 是两行而 Excel 一行。

**xlsx 不需要任何第三方库。** `scripts/xlsx_writer.py` 用 `zipfile` 加字符串模板
手写整个工作簿（xlsx 本质就是 zip 包里的 XML），所以照样能在没 pip、没网络的
机器上打开。这个文件要是没跟着一起拷过去，工具会明说一句并改出 CSV，
而不是在最后一步失败。

## 怎么比对

### 元件层：按**位号**判定

位号是板上的物理位置，同位置换料正是 ECO 要抓的：

| 类型 | 判定 |
|---|---|
| 更换料号 | 同位号，料号不同 |
| 变更参数 | 同位号、同料号，Value / Value1 / 器件名 有变 |
| 新增元件 | 位号仅新版存在 |
| 删除元件 | 位号仅旧版存在 |

### 网络层：按**（位号, 引脚）**判定

这一层回答的是"连接有没有动"，而元件层答不了：元件层只会说"多了 7 个元件"，
网络层才说明它们接在哪、旧的哪根连接断了。数据来自 `work/database.ppn`
（物理引脚视角的扁平网表：一行一个引脚，带它所属的网络）。

| 类型 | 判定 |
|---|---|
| 引脚改接 | 同位号同引脚，网络名变了 —— **只有这类计入网络层合计** |
| 新增接点 / 断开接点 | 引脚只在一侧存在；一行一个引脚，属参考信息 |
| 网络构成变化 | 两侧同名网络，引脚集合不同 |
| 网络改名 | 未配对网络按端点重叠度（Jaccard ≥ 0.5）配对出的改名，而非"删一条+加一条" |
| 新增网络 / 删除网络 | 另一侧没有对应网络 |
| 命名漂移 | 自动命名网重编号、连接完全相同 —— 列出供核对，**不计为变更** |

**为什么主键是引脚，不是网络名。** EE 会自动生成 `<AUTO_NET>` 这类网络名，符号顺序一变
就重编号（实测工程里有 100 个）。按网络名比会被这类噪声淹没；按引脚比则天然免疫——
重新编号不会移动任何引脚。而且按名字比会把一次迁移拆成两条无关记录：
`<D1>.1` 从 `<NET_CTRL>` 挪到一个全新网络，读起来是"那条网少了个脚"加"多了条网"，
看不出意图。

判定规则的取舍、真实 ECO 的实测证据、以及报告层怎么自检，都在
[`references/diff-design.md`](references/diff-design.md)。

## 环境要求

| 项 | 要求 |
|---|---|
| EE | Mentor/Siemens Expedition EE7.9.x（DxDesigner → Expedition 流程，iCDB 工程） |
| 系统 | Windows（导出器是 Windows 可执行文件） |
| Python | 3.7+，**只用标准库**，无需 pip 安装任何东西 |
| 其他 | 不需要 EE 正在运行；建议关闭 EE 后再导出 |

## 安装

### 装进其他 AI 工具

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

### 当普通脚本用

目标工具不支持技能机制也没关系：

```bash
python /path/to/ee-icdb-export/scripts/icdb_export.py --prj "<工程>.prj" --bom
```

### 目录结构

```
ee-icdb-export/
├── SKILL.md                          给 AI 读的操作指令（Agent Skills 格式）
├── README.md                         本文件
├── LICENSE
├── assets/
│   └── diff_report.tpl.html          网页报告的模板：标记 + CSS + 脚本都在这里
├── scripts/
│   ├── icdb_export.py                CLI 入口：参数、命令分发、向导、报告调度
│   ├── ee_env.py                     找 EE（SDD_HOME）、环境构建、工程扫描
│   ├── ee_export.py                  跑 icdb2csv、读表、写 BOM CSV
│   ├── ee_diff.py                    比对引擎（元件层 + 网络层）+ 统一记录集
│   ├── report_html.py                网页报告：填模板、拼表格
│   ├── report_xlsx.py                多工作表 Excel 报表
│   ├── report_csv.py                 扁平 CSV（一行一个变更字段）
│   ├── choice_ui.py                  终端提问、复选框读取、候选扫描
│   └── xlsx_writer.py                手写 .xlsx（zip + XML），生成 Excel 报表用
└── references/
    ├── diff-design.md                比对的设计决策与实测记录
    ├── icdb-data-format.md           12 张表的完整列结构、属性号映射、文本网表的坑
    └── troubleshooting.md            报错原文 → 根因 → 处置；写库行为说明
```

**`scripts/` 和 `assets/` 必须一起拷贝。** 各模块互相 import，报告模板在出报告时
才读取；少了 `assets/` 会明确报错，少了 `xlsx_writer.py` 会降级写 CSV。

**改网页报告的样式，改 `assets/diff_report.tpl.html`。** 那里就是完整的标记、CSS
和脚本，只有 4 个 `{{占位符}}` 留给程序填，改版式不需要碰任何 Python。

## 命令参考

### 交互（面向使用者 / 宿主弹窗）

| 参数 | 说明 |
|---|---|
| `--interactive` | 终端向导：先选任务（单版导出 / 两版比对），再走对应流程 |
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
| `--out <目录>` | 输出目录；导出默认 `<工程目录>\icdb_export`，`--diff` 默认 `./icdb_diff` |
| `--bom` | 额外汇总两份 BOM CSV |
| `--csv-only` | 只要原始表，不生成 BOM |
| `--sdd <路径>` | 手动指定 EE 的 `SDD_HOME`（自动发现失败时用） |
| `--icdb <目录>` | 手动指定 iCDB 目录（默认读 `.prj` 的 `iCDBDir`） |
| `--snap <名字>` | 快照名；**离线模式下实测无影响**，默认读 `.prj` |
| `--no-retry` | 失败立即退出，不做重试 |
| `--doctor` | 环境自检后退出 |
| `--find <目录>` | 扫描该目录下的 EE 工程 |

### 版本比对（ECO）

| 参数 | 说明 |
|---|---|
| `--diff [<A> <B>]` | 比对两个版本；A/B 各自可以是导出目录或工程路径。**不带路径则进比对向导** |
| `--label <A> <B>` | 配合 `--diff`：给两端命名，报告里用这个名字 |
| `--scope all\|bom\|net` | 配合 `--diff`：比对哪一层，默认 `all`（两层都做） |
| `--no-html` / `--no-excel` | 配合 `--diff`：不生成对应格式的报告 |
| `--csv` | 配合 `--diff`：额外出单张 CSV 长表（供脚本读取） |
| `--diff-options [<目录>]` | 输出比对选项清单 JSON（含五个复选框项）；给目录则同时列出 `candidates[]` |

`--diff-options` 的 JSON 结构（宿主拿它去弹复选框，不用自己猜有哪些选项）：

```json
{
  "task": "diff",
  "question": "要对比哪些内容、要哪些格式的报告？",
  "multi": true,
  "options": [
    {"id": "bom",  "label": "元件级 BOM 对比", "detail": "...", "default": true},
    {"id": "net",  "label": "网络级对比",      "detail": "...", "default": true},
    {"id": "html",  "label": "网页报告",   "detail": "...", "default": true},
    {"id": "excel", "label": "Excel 报表", "detail": "...", "default": true},
    {"id": "csv",   "label": "CSV 长表",   "detail": "...", "default": false}
  ],
  "maps_to": { "scope": "--scope bom|net|all", "html": "--no-html",
               "excel": "--no-excel", "csv": "--csv" },
  "candidates": [
    {"kind": "project", "label": "<PROJECT_ID> / <DESIGN>", "path": "...", "usable": true}
  ]
}
```

`candidates[]` 里 `kind` 分两种，**两者差别值得让用户自己选**：`project`
（先导出，会写 `icdb.dat`）与 `export`（已有导出目录，离线直读、完全不碰工程）。

## 给 AI 的交互协议

这一节是给"调用本技能的 AI"看的。要害是：**不要一上来就问用户"工程在哪"**——
工程是可以自己扫出来的，路径不该由用户提供。

1. **先扫后问**。按 当前工作目录 → 其父目录 → 用户已提到的目录 → 配置文件 的顺序静默扫描。
2. **给候选，不猜**。扫到多个 → 编号让用户选；扫到一个 → 报出名字直接进行；扫到零 → 才问目录，并列出试过的地方。
3. **跑之前确认**。导出会改写 `icdb.dat`、且数据库是单写入者，必须报出「工程 / 输出目录 / 先关 EE」并拿到明确同意。
4. **收尾给下一步**，不要停在"完成"。
5. **取消就干净退出**，绝不擅自代跑。
6. **要对比，就把选框摆出来，别替用户定**。哪一层重要、要什么格式，取决于这次
   要核对的 ECO 是什么，所以对比是个**多选**而不是默认值。用你自己的多选 UI 问，
   选项就用 `--diff-options` 给的五个（`bom` / `net` / `html` / `excel` / `csv`）。
   勾选结果映射：`bom`+`net` → `--scope all`，只勾 `bom` → `--scope bom`，
   只勾 `net` → `--scope net`；没勾的格式加 `--no-html` / `--no-excel`，
   只有勾了 `csv` 才加 `--csv`。用户没偏好时默认除 CSV 外全勾。
   **不要静默砍掉网络层**——在实测的那个设计里，
   携带真实意图的正是网络层那一半。

宿主能力的对应做法：

| 宿主能力 | 做法 |
|---|---|
| 有原生选择组件（导出） | 调 `--pick <目录>`，把 `choices[].label` 做成选项，选中项的 `prj` 回传给 `--prj` |
| 有原生选择组件（对比） | 调 `--diff-options <目录>`，`candidates[]` 做两端选择、`options[]` 做复选框 |
| 只有终端 | 直接跑 `--interactive`，全流程已实现（含真复选框控件） |
| 只要对比 | `--diff`，或 `--diff --find-root <目录>` |
| 完全脚本化批处理 | `--prj ... --bom`，或 `--diff <A> <B> --scope net --no-html`，全程不交互 |

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
  iCDB 会判定副本 "inconsistent" 并拒绝（详见 [`references/troubleshooting.md`](references/troubleshooting.md) §4）。

## 已实测的环境

| 项 | 值 |
|---|---|
| EE | Mentor Graphics EE7.9.5 |
| 导出器 | `iCDB2Csv` build tag 505826，build 2012-06-05，Flow: EE7.9.4, 7 |
| 验证工程 | `<PROJECT_ID>`（<ROOT_BLOCK>，12 张图纸页） |
| 验证结果 | 468 个元件 / 143 个料号条目 / 料号覆盖 **100%** / 21 个元件无 Value |
| ECO 比对验证 | `1328` vs `1428`：2 更换料号 / 13 变更参数 / 7 新增 / 0 删除 / 451 一致 / 11 个受影响料号 |
| 网络层验证 | 同一对版本：245 → 249 个网络 / 1345 → 1360 个引脚；**2 引脚改接**（`<U1>.88` <NET_EN>→<NET_GPIO>、`<D1>.1` <NET_CTRL>→`<AUTO_NET>`）、15 新增接点、0 断开、4 网络构成变化、4 新增网络、0 删除、0 命名漂移 |
| 自检 | 同一导出目录比自身：468 元件全一致，网络表与引脚表**全零行**，HTML 报告零数据行，Excel 每张表只有表头加一行「（无差异）」 |
| 报告校验 | HTML 零外部引用（离线可开）；每张表的表头列数与数据行列数逐一核对通过；xlsx 用 openpyxl 回读校验工作表名 / 单元格 / 冻结 / 筛选 / 列宽，并与 CSV 对账行数 |
| 降级 | 拿掉 `scripts/xlsx_writer.py` 后仍正常出 HTML + CSV，且明确提示跳过 Excel |
| Python | 3.13（64 位），**64 位可用**——本工具不依赖 32 位 |

## 已知限制

- **`Cell Name` 与图纸页不参与差异判定**：实测 `Cell Name`（属性 15）与 `Part Name`
  同值、且常只在一版里填写，纳入判定会产生一批 `(空) -> XXX` 的属性补全噪声
  （去掉后差异从 20 条降到 13 条，且这 13 条全部为真实变更）。真实封装变化必然体现
  在 `Part Name` 或料号上。因此 **`Cell Name` 的新旧值不再出现在报告里**（旧版曾以
  两列并列输出，正是那六列自我重复把表撑到要横向滚动）；需要逐位看封装时，跑一次
  `--bom` 取 `BOM_reference_detail.csv`，那里有完整一列。图纸页仍作为一条**位置信息**
  列在报告里，但它参与判定，改名不会刷出伪差异。
- **多单元器件按多重集合比对**：同一位号下有多个符号（U1A/U1B）时不做逐行配对，
  而是比较整个取值集合，因此单元数变化会被报出来而不是被静默配对掉。空值在报告里
  显示为 `(空)`——若直接丢弃，`{"", "U1"}` 与 `{"U1"}` 会显示成一样，反而藏掉差异。
- **网络层需要 `database.ppn`**：正常导出一定有；若某个导出目录里没有它，网络比对会
  跳过并明确报出是哪一侧缺（不会静默当成"没有差异"）。重新导一次即可。
- **网络层靠端点重叠配对改名**：阈值 Jaccard ≥ 0.5。一次"既改名又大改成员"的网络
  可能落成"删除+新增"两条——这种情况下引脚改接那一节仍会把每个改接的引脚如实列出，
  所以真实变更不会丢。自动命名网（`<AUTO_NET>`）重编号且连接完全相同时归入「命名漂移」，
  列出但不计入差异。
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

## 许可

MIT License，见 [LICENSE](LICENSE)。
