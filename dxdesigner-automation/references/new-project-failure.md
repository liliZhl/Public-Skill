# 新工程建不了 —— "Cannot create project" 取证记录

日期：2026-09-18　机器：<HOSTNAME>（个人笔记本）
问题：在 DxDesigner 里新建工程，弹 `Error / Cannot create project`。

## 一、先把"是哪个程序弹的"钉死

GUI 弹窗只有一句话，没有任何来源信息。做法：**在这台机器的安装根目录里做字节级字符串扫描**。

结果（决定性）：

| 扫描范围 | 文件数 | 命中 |
|---|---|---|
| `E:\APP`（排除 `MentorGraphics`） | 276,121 | **0** |
| `E:\APP\MentorGraphics\7.9.5EE` | 70,322 | **14** |

→ 弹窗**只可能出自 Mentor EE**。DeepSeek Harness、WorkBuddy、facewinunlock 等已全部排除。

命中的 14 个模块（`SDD_HOME` 下）：

```
common\win32\lib\cesviw70.dll
common\win32\lib\DxConstraintsEditor.ocx
common\win32\lib\DxDmSvr70.dll
common\win32\lib\ExpeditionNewProject.dll     ← 新建工程向导（Expedition 流程）
common\win32\lib\Navigator.ocx
common\win32\lib\NetlistNewProject.dll        ← 新建工程向导（网表流程）
common\win32\lib\PADSNewProject.dll           ← 新建工程向导（PADS 流程）
common\win32\lib\sae.ocx
common\win32\lib\vec70.dll  veclt70.dll  vecide75.dll  vecaddslt75.dll
wv\win32\bin\dash.exe
wv\win32\bin\viewdraw.exe
```

三个 `*NewProject.dll` 就是向导本体（按流程分插件）。**到此可以确定：报错来自"新建工程向导"，不是许可证、不是权限弹窗。**

## 二、向导的失败条件（从 DLL 里挖出的原始串）

`ExpeditionNewProject.dll` 中 `Cannot create project` 附近的字符串资源是连续的一整组，
按顺序列出即向导的校验清单：

```
Cannot create project                      ← 兜底（裸报错对应这条）
Selected Central Library is not compatible with Expedition flow.
  The selected Central Library has not been updated to the current release
  and cannot be used to create a new design. Select a different Central Library or update
  the specified Central Library by opening it in Library Manager in Standalone mode or use the
  Update Central Library utility before continuing. Once the library has been updated,
  it can be selected as a Central Library for new projects.
Cannot open Central Library configuration file
Cannot create project, folder already exists
Central library does not exist
Server name is empty.
No library was specified.
No location was specified.
No name was specified.
```

同文件还暴露了向导读写的全部资源键：

| 键/串 | 含义 |
|---|---|
| `SDD_HOME` + `\templates\dxdesigner\expedition` + `*.prj` | 模板目录（`default.prj` 里 `CentralLibrary ""` 是**空的**，必须由用户指定库） |
| `*.lmc` / `Central Library File` | 中央库文件 |
| `CentralLibrary` `DesignInfo` `FlowType` `"EE2007"` `FlowVersion` `.cfg` | 写进新工程 `.prj` 的内容 |
| `ProjectBackup` `iCDB` `Designs` `database` | 新工程的目录结构 |
| `Bus_Contents` `BorderSymbols` `PinComponents` | 从中央库读的配置 |
| `Software\Mentor Graphics\DxDesigner\` + `LastNewProjectLocation` | 注册表：上次成功新建工程的位置 |

**判据：`HKCU\Software\Mentor Graphics\DxDesigner` 键在本机根本不存在**
→ 向导成功后才会写 `LastNewProjectLocation`，键不存在说明**这台机器上向导从没成功建过一个工程**。
与 COM 侧 `NewProject` 已废弃（LastErrorId 670）互相印证：7.9.5 的"从零建工程"整条路都是坏的。

## 三、向导要的三样输入，在本机的实际状态

| 输入 | 状态 |
|---|---|
| 模板 `%SDD_HOME%\standard\templates\dxdesigner\expedition\*.prj` | ✅ 存在（`default.prj`、`edmflow.prj`、`HLA Library.prj`、`HLA Eldo Library.prj`） |
| 中央库 `.lmc` | 本机 9 个（`examples\SampleLib2007\SampleLib.lmc`、`templates\pcb\Central Library\Central Library.lmc`、`wg\tutorial\LibMgr\…`）；公司库 `\\<HOST_IP>\eda\Mentor_lib_EE7.9\mentor_lib.lmc`（16 MB）**当时可达** |
| 目标文件夹 | 必须**不存在**，已存在直接报 `folder already exists` |

公司库的 `MENTOR_LIB.cfg` 内容（向导要读的那个 cfg）：

```
SECTION LibraryManager
KEY FlowType "DX"
KEY FlowVersion "EE2007"
ENDSECTION
```

**时序旁证**：`MENTOR_LIB.cfg` mtime 15:30、`mentor_lib.lmc` mtime 15:42，
而弹窗出现在 15:38。共享盘上的库当天被写过——若是同事在更新库，
`Selected Central Library is not compatible…`（库没更新到当前 release）这条会更容易触发。

### 向导真正拒绝的原因清单（从各模块原文汇总）

| 报错原文 | 触发条件 | 出处模块 |
|---|---|---|
| `Cannot create project`（裸） | 兜底／通用失败 | ExpeditionNewProject.dll、viewdraw.exe |
| `Cannot create project '%s'. The path name is too long.` | **路径名太长** | DxConstraintsEditor.ocx |
| `Cannot create project, folder already exists` | **目标文件夹已存在** | ExpeditionNewProject.dll |
| `Unable to create the specified project. Possible reasons include existence of duplicate project, unusually long project names, project names containing special characters etc.). Please verify the Project Name and try again.` | **工程名重复／过长／含特殊字符** | dash.exe |
| `Selected Central Library is not compatible with Expedition flow. …` | 中央库没更新到当前 release | ExpeditionNewProject.dll |
| `Cannot open Central Library configuration file` / `Central library does not exist` | 库打不开／不存在 | ExpeditionNewProject.dll |
| `Space characters are not allowed in Library paths.` / `… in a Library alias.` | **库路径含空格** | dash.exe |
| `The Project directory is already in the project list. Please specify a different directory.` | 目录已在工程列表 | dash.exe |
| `The Library path may not be the same as the Primary Library's path.` | 库路径＝主库路径 | dash.exe |
| `No name / No location / No library was specified` | 没填名字／位置／库 | ExpeditionNewProject.dll |

## 三·补、本机最可疑的一条：默认"新建工程位置"指向不存在的目录

DxDesigner 自己的配置文件在 `<WDIR>\DxDesigner.xml`
（本机 = `E:\APP\MentorGraphics\Mentor_WDIR\DxDesigner.xml`，系统环境变量
`WDIR = E:\APP\MentorGraphics\Mentor_WDIR;E:\APP\MentorGraphics\7.9.5EE\SDD_HOME\standard`）：

```xml
<key name="NEW_PROJECT_LOCATION" value="E:/APP/MentorGraphics/Mentor_WDIR/DxProjects"/>
<key name="MRU_PROJECTS">
  <value>…\ee_auto_poc\proj_copy\<TEST_PROJECT>\<DESIGN>.prj|DX</value>
  <value>H:\XPI\Project\<PROJECT>\A-Hardware\01-工程文件\<PROJECT_ID>\<PROJECT>.prj|DX</value>
</key>
```

两个问题同时存在：

1. `NEW_PROJECT_LOCATION` = `E:\APP\MentorGraphics\Mentor_WDIR\DxProjects`
   —— **这个目录不存在**，而且它落在 **EE 安装树内部的 WDIR 里**。
   把工程建在 WDIR 里本身就危险（WDIR 是搜索路径之一，`dash.exe` 会报
   "The Project directory is already in the project list"）。
2. MRU 里第二个工程在 `H:\XPI\Project\...`，而 **`H:` 盘当前不存在**
   （`Z:` 才是 `\\<HOSTNAME>\XPI`）。陈旧盘符会在打开/新建时直接失败。

修法（任选）：
- 先把 `DxProjects` 建出来：`mkdir E:\APP\MentorGraphics\Mentor_WDIR\DxProjects`；
- 或把 `NEW_PROJECT_LOCATION` 改成真实存在、且不在安装树里的目录
  （例：`E:/<PROJECT_ROOT>/DxProjects`）——改前先关掉 DxDesigner，改后它会重写该文件；
- 别用 H: 这类已失效的映射盘。

## 四、已排除的嫌疑

| 嫌疑 | 排除依据 |
|---|---|
| 别的软件弹的窗 | `E:\APP` 外部扫描 0 命中 |
| iCDB 服务没起 | `HKLM\SYSTEM\CurrentControlSet\Services` 下无 icdb/mgc/mentor 服务；iCDB 走文件模式（`.prj` 里 `DedicatedServerName ""`），实测能正常写 `database/icdb.dat` |
| 程序还开着 / 占着许可 | 弹窗期间 `tasklist` 无 viewdraw/dxdesigner/expedition 进程 |
| 事件日志里有记录 | 该时段 Application 日志为空（EE 不走 Windows 事件日志） |
| 有日志文件可看 | `AppData\Local\MentorGraphics\7.9.5EE` 是空目录；15:20–15:55 之间 AppData 下无 EE 日志落盘 |

## 五、两条出路

**A. 修向导（要 GUI 走通时）**
1. 目标文件夹**新建时不存在**——填父目录 + 工程名，别指向已存在的工程目录；
2. 中央库选一个**与所选流程匹配**的 `.lmc`：选 Expedition 流程就得用已更新到当前 release 的库，
   公司那个 `mentor_lib.lmc` 的 cfg 是 `FlowType "DX"`，**DX 流程**才对得上；
3. 路径别过长、别落在受限或只读位置。

**B. 绕开向导（脚本/自动化场景，推荐）**

用 `scripts/new_project.py`：复制现成工程 → 改 `.prj` 名 → 按需改中央库。

```bash
python new_project.py \
  --src "E:\<PROJECT_ROOT>\<PROJECT>\A-Hardware\01-工程文件\<PROJECT_ID>" \
  --dst "E:\<PROJECT_ROOT>\_DX_AUTO\AUTOTEST_<PROJECT>" \
  --prj-name AUTOTEST_<PROJECT>
```

实测（2026-09-18）：

| 项 | 结果 |
|---|---|
| 复制 | 253 文件 / 96.5 MB / 0.6 s |
| `.prj` 改名 | `<DESIGN>.prj` → `AUTOTEST_<PROJECT>.prj`，**iCDB 链接未断** |
| 打开 | `OpenProject` True（8.9 s）；`<ROOT_BLOCK>` / 13 张图纸齐全 |
| 写入 | `AddText` 后对象计数 +1（TEXT 0→1、ALL 0→1） |
| 源工程 | 全程只读，`icdb.dat` 未变 |
| 副本纯净度 | `icdb.dat` 与源工程体积+mtime 完全一致（3,754,400 B @10:20:46） |

两条纪律：**落点在安装树之外**（别放进 `Mentor_WDIR`）；**目标目录必须原本不存在**。

局限：工程内部的 design 名（`Board1`）与根图纸名（`<ROOT_BLOCK>`）属于 iCDB 内容，脚本不改——
改它们要动 iCDB，风险高收益低。所以"新工程"= **新目录 + 新 `.prj` 名**，内部设计名沿用模板工程。

## 六、取证手法（可复用）

见 `scripts/find_msg.py`。要点：

- **ripgrep 在 70k 文件 / 十几 GB 的安装树上会 30 s 超时**，Grep 工具扛不住；
- 自己写**多线程字节扫描**，同时搜 `ASCII` 与 `UTF-16LE` 两种编码
  （Windows 资源串多是 UTF-16LE，只搜 ASCII 会漏）；
- 按扩展名跳过 `.zip/.msi/.asar`，按体积设上限（>250 MB 直接跳过）；
- 命中后再 dump **邻近字符串**（正负 28 条），失败条件基本都藏在邻居里——
  这比猜原因快得多。
