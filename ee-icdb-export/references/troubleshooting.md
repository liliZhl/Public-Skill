# 排错与实测记录

本文件的内容全部来自**在真机上的对照实验**（EE7.9.5，工程 `<PROJECT_ID>`），
不是推测。遇到问题先查 §1 的报错表。

## 1. 报错原文 → 根因 → 处置

| 报错原文（逐字） | 退出码 | 根因 | 处置 |
|---|---|---|---|
| `ERROR: no properties found in prp file!` | 1 | 命令行**漏了 `-properties`**。报错会误导你去 `.prj` 里找属性文件，但那个文件根本不需要 | 加上 `-properties`。本脚本已默认带 |
| `ERROR: project file (.prj) access scan fail.` | 1 | `-project=` 传了**相对文件名**，而工作目录不是工程目录 | `-project=` 一律传**绝对路径** |
| `Project database [...] is inconsistent. It has been manually copied while the iCDB Server was running. Please use iCDB Project Backup 'Repair project' functionality to fix this issue.` | 1 | 在**工程副本**上导出。iCDB 在 `icdb.dat` 里记录会话/路径状态，副本会被判定不一致 | 在**原位**导出。见 §4 |
| `ERROR: Can not create output file, try other filename as program parameter (See -help)` | 0 | `-file=` / `-output=` 指向的**目录不存在**（`icdb2ascii` 常见） | 先建目录 |
| 无任何错误输出，`work\` 未生成 | 0 | `-icdb` 指向的**目录不存在**；或上一次 iCDB 会话仍占用数据库 | 核对 `-icdb`；或等几秒重试（脚本自动重试 3 次） |
| 无任何错误输出，`work\` 未生成 | 1 | 不加 `-offline` 时工具会去连真实 iCDB 服务，服务起不来或残留占用 | **始终加 `-offline`** |

### 退出码不可信

`icdb2csv` **对部分硬失败返回 0**。实测：

| 情形 | 退出码 | 是否产出数据 |
|---|---|---|
| 正常导出 | 0 | 是 |
| `-icdb` 指向不存在的目录 | **0** | 否 |
| `-output` 指向不存在的目录 | **0** | 否（某次实测为 0，另一次为 1——**更说明不能依赖它**） |
| `-project` 指向不存在的文件 | 1 | 否 |
| 漏 `-properties` | 1 | 否 |

**唯一可靠的判据是 `work\database.esf` 是否存在。** 脚本据此判定成功。

## 2. `-properties` 到底做了什么

Mentor 自己的帮助文本（`icdb2csv.exe -help`，原文）：

```
  -icdb=argument   (ON=.)      icdb directory
  -snap=argument   (ON=DCDV)   snapshot name
  -file=argument   (OFF)       report filename
  -output=argument (ON=rep)    directory to place output files
  -clog=argument   (OFF)       redirect log filename
  -project=argument(OFF)       project file
  -properties      (OFF)       created additional prop_ID on demand
  -offline         (OFF)       start iCDB server offline
  -server=argument (OFF)       dedicated iCDB server name
  -nostrip         (OFF)       leave semicolon in component name
```

`-properties` = **"created additional prop_ID on demand"**，即按需生成属性号。
缺它时导出的表里没有任何属性，工具直接判定"没有属性"而放弃。

> 首轮导出若因它新增了属性号，`icdb.dat` 会**一次性增大**约 4 KB，
> 并在 `database\cdbback\` 生成一个完整备份。这是正常现象，只发生一次。

## 3. `-snap` 在离线模式下无影响（实测）

同一工程、同样参数，只改 `-snap`，产出**逐字节对照**：

| `-snap` 取值 | rc | `database.sym` | `database.prt` | `database.ppn` | `database.spr` |
|---|---|---|---|---|---|
| 不传 | 0 | 468 | 114 | 1345 | 4198 |
| `DxD` | 0 | 468 | 114 | 1345 | 4198 |
| `DCDV` | 0 | 468 | 114 | 1345 | 4198 |

结论：**离线导出读的是本地 `icdb.dat`，与快照参数无关。**
脚本仍按 `.prj` 里声明的 `FrontEndSnapshot` 传入，以便在有快照语义的环境下保持正确。

注意两个工具的默认值并不一致：`icdb2csv` 默认 `DCDV`，`icdb2ascii` 默认 `DxD`。

## 4. 工程数据库会被改写（重要）

### 实测证据

| 观察项 | 结果 |
|---|---|
| 文件大小 | 连续多次导出稳定在 4061844 字节，**不变** |
| 内容 | SHA256 **每次都变**（`835289…` → `9bc6a8…` → `ab38a2…`） |
| 变化的字节 | 54 个小区段，大多 1–10 字节，集中在**会话记录区**（该区域在文件中明文含主机名 `<HOSTNAME>` 与用户名 `<USER>`），另有 1 处约 446 字节 |
| 导出的数据 | 连续 3 次运行，`database.sym/.spr/.prt/.ppn/.sht` **SHA256 完全一致** |
| 唯一差异 | `database.inf` 第 2 列含导出时间戳（`09/17/26 04:24:56` vs `04:25:05`） |

**结论**：改写的是 iCDB 的会话簿记（谁在什么时候访问），不触碰原理图数据。
这也是 §1 里"副本被判 inconsistent"的同一个机制——iCDB 用这份状态判断数据库是否被外部动过。

### 因此

- 导出前后各记一次哈希，改动就不会被悄悄吞掉（脚本已做）。
- **关闭 EE 再导出**，数据库是单写入者。
- EE 自带的完整备份：`<工程>\database\cdbback\<时间戳>.zip`，解压即一个完整 `icdb.dat`。
  实测该 zip 只含 `icdb.dat` 一个条目。
- 想要绝对只读，只能先对整个工程目录做外部快照；**但不要在副本上跑导出**。

### 副本为什么不行

`iCDBProjectBackup.exe` 就是报错里提到的那个修复工具，它的命令行接口只有：

```
USAGE: iCDBProjectBackup.exe [-p <string>] [-ver] [-h]
   -p <string>,  -project <string>   project file
```

即**只能指定工程文件，然后弹出 GUI**，没有可脚本化的修复子命令。
所以"复制→修复→导出"这条安全隔离路线走不通，只能原位导出。

## 5. 会话争用与重试

实测现象：一次**不带 `-offline`** 的运行之后，紧接着的 4 次导出（包括参数完全正确的
命令）**全部返回 rc=1 且无任何错误输出**；约一分钟后重跑即恢复正常。

处置：

1. **永远加 `-offline`**，不要让它去启动真实 iCDB 服务。
2. 同一个工程不要并发导出。
3. 遇到"无输出的 rc=1"，隔几秒重试——脚本默认重试 3 次、间隔 6 秒；
   已知的**不可重试**错误（`no properties found` / `access scan fail` /
   `is inconsistent` / `can not create output file`）会立即失败并给出原因，不做无谓等待。

## 6. 同族的其他工具

| 工具 | 用途 | 关键限制 |
|---|---|---|
| `icdb2csv.exe` | **主用**。导出 13 张 Tab 分隔表 | 必须 `-properties` |
| `icdb2ascii.exe` | 另一种文本报告导出 | 需要 `-blks=<块名列表>` 才出数据，否则只输出一个 500 字节的报告头；同样会写库。参数：`-t`（属性表）`-c`（连接表）`-tabular -hdrs -i -p`（表格模式）`-cdbcompat` |
| `icdb2bom.exe` | BOM 生成器 | 默认 `-cfg` 指向的 `cdb2bom.asc` 在 7.9.x 安装里**通常不存在**，故默认配置会失败。BOM 建议自己从导出表汇总 |
| `icdb2spc.exe` / `icdb2vhdl.exe` / `icdb2vlog.exe` | 仿真网表导出 | 与 BOM 无关 |
| `iCDBProjectBackup.exe` | 工程备份/修复 | **只有 GUI**，不可脚本化 |
| `iCDBServerMonitor.exe` | 服务监视 | GUI |

## 7. 明确不可行的路径

| 路径 | 原因 |
|---|---|
| **COM 自动化** `MGCPCB.Application` | ProgID 确实已注册，类型库 `MGCPCB (AutoActive series)` 含 614 个类型，服务器是进程外的 `XtremeDesignSessionWG.exe`（带 `Programmable` 键）。但 CLSID **只注册在 `Wow6432Node`**，必须 **32 位进程**调用；64 位 Python/PowerShell 一律报 `Class not registered`。另有 `AcquireLicense` 方法，可能需要额外取许可。成本高于 CLI，收益相同 |
| `PCB\Logic\*.cce` | XML 但整体加密（`<CCZEncrypt><EncryptedData>`），无密钥不可读 |
| `database\icdb.dat`、`keyin.icdb\icdb.dat` | 专有二进制 |
| `Integration\LocalPartsDB.pdb`、`PCB\Layout\*.lyt/.lgc` | 专有二进制 |
| 解析 `.prj` 当作数据源 | `.prj` 是 `KEY 名 "值"` 的工程配置，只含路径/快照/库引用，不含元件数据 |

## 8. 环境相关

- EE 的机器级环境变量（`SDD_HOME`、`WDIR`、`MGC_HOME`、`MGLS_LICENSE_FILE`、
  `SDD_PLATFORM`、`SDD_VERSION` 等）安装时已写入
  `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`，
  脚本会读注册表兜底，因此**新开的 shell 缺少这些变量也能跑**。
- **`VBEST14PATH` 不在注册表里**。`.prj` 会引用 `${VBEST14PATH}\config\vbdc\...`，
  但这个变量名不是 EE 安装时注册的。实测：**它的缺失与 `-properties` 那个报错无关**
  （那是两条独立的线索，别被带偏），脚本仍会补一个 `SDD_HOME\standard` 作为默认值。
- 工程路径含中文（如 `01-工程文件`）**实测没有问题**，不必特意规避。
  但该 exe 内部是 ANSI，往日志里回显时会把中文显示成乱码（`¹¤³ÌÎÄ¼þ`），
  **不影响功能**，别被这行乱码误导。

## 9. 工程 `.prj` 中值得留意的字段

| 字段 | 示例值 | 说明 |
|---|---|---|
| `iCDBDir` | `database` | iCDB 目录，相对工程根 |
| `FrontEndSnapshot` | `DxD` | 快照名 |
| `RootBlock` | `<ROOT_BLOCK>` | 根块名 |
| `CentralLibrary` | `\\<HOST_IP>\eda\Mentor_lib_EE7.9\mentor_lib.lmc` | 中央库，可能在网络共享上。**不可达时离线导出仍可成功**（数据已在 `icdb.dat` 里），但要留意 |
| `BOM_Configuration` | `${VBEST14PATH}\config\vbdc\cdb2bom.asc` | 见上文——`cdb2bom.asc` 在本代安装中通常不存在 |
