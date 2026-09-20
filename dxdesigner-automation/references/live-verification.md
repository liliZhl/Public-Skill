# 实机验证记录 —— EE 7.9.5 / DxDesigner COM 自动化

日期：2026-09-18
机器：<HOSTNAME>（个人笔记本，非域主机）
测试工程：`E:\<PROJECT_ROOT>\<PROJECT>\A-Hardware\01-工程文件\<PROJECT_ID>`（**只读，从未打开**；
所有写操作都在一份副本上进行）

## 环境

| 项 | 值 |
|---|---|
| 安装根 | `E:\APP\MentorGraphics\7.9.5EE` |
| 可执行 | `SDD_HOME\wv\win32\bin\viewdraw.exe` |
| 类型库 | `SDD_HOME\wv\win32\bin\viewdraw.tlb`（188,040 B） |
| ProgID / CLSID | `ViewDraw.Application` / `{EA4ABD70-84B0-11CE-8237-00001B4D36B5}` |
| 类型库 GUID | `{4ADEF4E1-690A-11CE-9261-0020C5E26659}` v1.1 |
| `Application.Version` | `2007.12.0` |
| 许可 | `C:\ProgramData\mgc\win32\LICENSE.dat.TXT`，本机 node-locked，server = <HOSTNAME>，有效至 2036-08-29 |
| 驱动方式 | Python 3.13 + pywin32，**后期绑定** |

类型库规模：**223 个类型信息 / 86 COCLASS / 58 DISPATCH 接口 / 79 枚举**。
`IVdApp` 122 个方法 + 36 属性；`IVdBlock` 80 个方法；`IVdView` 39 个方法；`IVdAppEvents` 53 个事件。

## 结论矩阵

| 能力 | 结果 | 证据 |
|---|---|---|
| 创建/附加自动化实例 | ✅ | `EnsureDispatch` 与 `dynamic.Dispatch` 均可 |
| 读应用属性 | ✅ | `Version`=2007.12.0, `Name`=DxDesigner, `Visible`, `SilentMode`, `LicenseMode` |
| 写应用属性 | ✅ | `Visible=True`、`SilentMode=1`、`Interactive=True` |
| 打开工程 | ✅ | `OpenProject(prj)` → True（4.9–20 s） |
| 读工程数据 | ✅ | `GetProjectData()` → `IProjectData`；`GetProjectFilePath()` |
| 列原理图 | ✅ | `SchematicSheetDocuments().GetAvailableSchematics()` → `['<ROOT_BLOCK>']` |
| 列图纸 | ✅ | `GetAvailableSheets('<ROOT_BLOCK>')` → 13 张（`01_Power Tree`…`05_SENSOR`） |
| 打开图纸 | ✅ | `SchematicSheetDocuments.Open('<ROOT_BLOCK>','01_Power Tree')` → 131 个对象。**第二个参数是图纸名，不是页码**（踩过，见文末） |
| 拿 Block | ✅ | `view.Block` → `GetName(0)='<ROOT_BLOCK>'`, `SheetNum='1'` |
| 画图形 | ✅ | `AddText`/`AddLine2`/`AddCircle`/`AddArc`，计数 +5 吻合 |
| 放器件 | ✅ | `AddPartInstance`/`AddComponent`/`AddSymbolInstance`，COMPONENT 0→8 |
| **加载共享中央库** | ✅ | `\\<HOST_IP>\eda\Mentor_lib_EE7.9\mentor_lib.lmc`（16 MB），`CentralLibraryPath` 读到全路径 |
| **从中央库放符号** | ✅ | `AddSymbolInstance('<COMPANY>','res01')`/`('temp','3v3')` 等 6 个符号全部成功，COMPONENT 0→6 |
| 用 PDB 分区名放器件 | ❌ | `AddPartInstance('RESISTOR','','res01',…)` → `Symbol RESISTOR:res01.1 not found`（第 1 参须是**符号**分区） |
| UI 可见 | ✅ | 主窗口 class `Afx:`，`IsWindowVisible=True`，标题 `DxDesigner - <prj> - [<ROOT_BLOCK>.1]` |
| 保存 | ✅ | `Documents.SaveAll()`；副本 `database/icdb.dat` 3,911,856 B @ 15:33:07（**此体积证据已被推翻，见文末追加实验**） |
| 从零建工程 | ❌ | `NewProject` 五种参数组合全部失败，LastErrorId **670**（已作废） |
| 从零建工程（GUI 向导） | ❌ | 同样建不出来，弹 `Cannot create project`（取证见 `new-project-failure.md`） |
| 复制现成工程 | ✅ | `scripts/new_project.py`；253 文件 / 96.5 MB / 0.6 s，含 `.prj` 改名，改名后 iCDB 链接未断 |
| 无工程建文档 | ❌ | `Documents.Add()` 返回空，`ActiveView` 为 None |
| 画矩形 | ⚠️ | `AddBox` 报 0x80010105 服务器异常；用 `AddLine2` 替代 |

### 已作废（LastErrorId 670，`GetLastErrorString()` = "此方法/特性已作废。"）

`NewProject` · `GetLibraries` · `SynchronizesViewBase` · `IVdDoc.Save()` · `IVdDoc.Saved`

## 两个必须知道的坑

1. **早期绑定会吃掉合法调用。** 类型库绑定的 `IVdDocs.Open()` 只声明一个参数，
   而运行时要 `(name, page)`；早期绑定在本地就抛
   `takes from 1 to 2 positional arguments but 3 were given`。
   同理 `SchematicSheetDocuments` 在 VBS 里可当属性用，Python 必须写成 `()`。
2. **写入是否生效只能靠对象计数验证。** `Add*` 返回 `CDispatch` 并不代表内容进图了。
   ⚠️ 但反过来，`database/icdb.dat` 的体积/mtime 变了**不能**当落盘证据——光打开工程就会改它
   （见文末追加实验）。另外 iCDB 写入是**即时提交**的：不调 `SaveAll()` 也会留在库里。
   `View.Query(VDM_*, VD_ALL)` 的计数增量是唯一可靠判据——本次 +5 与 5 次成功调用精确吻合。

## 未验证（留给后续）

- `SetAttributeValue` / `GetAttributeValues`（改属性）
- `PromoteSymbolNumbers(SelectedOnly, Slot)`（批量重排位号）
- `RegisterOLECommand` + `IVdAppEvents` 事件回调（自定义菜单/插件）
- `AddNet(x1,y1,x2,y2,CompPin1,CompPin2,BusOrWire)` 的 CompPin 参数形态 ——
  已试两种均失败：4 参报「非选择性的参数」，7 参 `(…,'','',0)` 报「类型不匹配」，
  疑为需要叶脚 IDispatch 对象
- `SelectObject(ObjectType, Expression, SelectOwner, RegExp, AddSelect)` 按表达式批量选择
- 结果能否被 `icdb2csv`（ee-icdb-export 技能）回读，形成"写—验—比"闭环

## 追加实验（2026-09-18 下午）：建工程 + iCDB 提交语义

### 复制法建工程（已交付）

`scripts/new_project.py`：复制现成工程 → 可改 `.prj` 文件名 → 可改 CentralLibrary。

实测 src `…\<PROJECT_ID>` → `E:\<PROJECT_ROOT>\_DX_AUTO\AUTOTEST_<PROJECT>`：
253 文件 / 96.5 MB / 0.6 s；`<DESIGN>.prj` → `AUTOTEST_<PROJECT>.prj`。
副本 `OpenProject` 返回 True（8.9 s），拿到 `<ROOT_BLOCK>` / 13 张图纸，写测试增量吻合
→ **改 `.prj` 文件名是安全的，iCDB 链接不会断**。

落点两条纪律：**放在 EE 安装树之外**；**目标目录必须原本不存在**
（`Cannot create project, folder already exists` 本身就是向导的拒绝条件之一）。

### 两条被推翻的旧认知

| 旧记录 | 实测结论 |
|---|---|
| "写入并 `SaveAll()` 后 `icdb.dat` 体积/mtime 会变 → 可作落盘证据" | ❌ **光打开工程就会改 `icdb.dat`**。零写入的对照组同样从 3,754,400 涨到 3,788,864（+34,464）。该证据不成立 |
| 隐性认知："要 `SaveAll()` 才算落盘" | ❌ **iCDB 图元写入是即时提交的**。`AddText` 之后不调 `SaveAll()`、直接 `Quit()`，重新打开该对象仍在（测试组重开 TEXT=1；对照组两次打开都是 0）。`SaveAll()` 不是"是否落盘"的开关 |

推论（非常重要）：**自动化必须做在副本上。** 没存盘的写入也会进库，
对生产工程做任何试验都可能被永久写进去。

## 中央库调用实测（2026-09-18 傍晚）

### 库位置与规模

公司中央库 `\\<HOST_IP>\eda\Mentor_lib_EE7.9\mentor_lib.lmc`（16,179,084 B，
当天 16:47 被写过）。目录结构 = 一个 lmc + 若干**分区目录**：

| 目录 | 是什么 | 实测规模 |
|---|---|---|
| `SymbolLibs\<分区>\sym\*.1` | **符号库分区**（`AddSymbolInstance` 要的就是这个） | `<COMPANY>` 5987、`IC` 1937、`temp` 293、`S905L3S` 186、`SWITCH` 3、`CES`/`Temp_a`/`__ReusableBlocksSymb` |
| `PartsDBLibs\*.pdb` | 器件库（PDB）分区 | `RESISTOR.pdb`/`CAPACITOR.pdb`/`IC.pdb`/`LED.pdb`/`CONNECTOR.pdb`… |
| `CellDBLibs` / `MaterialDBLibs` / `Models` / `Templates` | PCB 侧资源 | — |
| `MENTOR_LIB.dproj` | 库自身的工程文件，`FlowType="DX"` / `FlowVersion="EE2007"` | — |

**符号名 = `SymbolLibs\<分区>\sym\<名字>.1` 去掉 `.1`**，不需要开 DxDesigner 就能列。

### 关键发现：`AddSymbolInstance` 第 1 参是**符号**分区

不需要预先 `AddLibrary()`，只要工程的 `CentralLibrary` 可达，引擎自己会去取。参数语义
是被报错信息直接揭示的：

```
block.AddPartInstance('RESISTOR', '', 'res01', x, y)
  -> com_error: Symbol RESISTOR:res01.1 not found, empty or a block.
```

它把第 1 参拼成了路径 `<分区>/sym/<符号>.1` —— 所以传 PDB 名的 `RESISTOR` 必然找不到，
传符号分区名 `<COMPANY>` 就能取到。

### 绘图结果

`E:\<PROJECT_ROOT>\_DX_AUTO\DRAWDEMO_<PROJECT>\`（复制自 <PROJECT> 生产工程，**源工程全程只读**），
中央库继承共享库路径。在 `<ROOT_BLOCK>.1` 上画了一张 3V3→R1→LED→GND + 滤波电容的示意图：

| 动作 | 结果 |
|---|---|
| `AddSymbolInstance` × 6（`temp:3v3` `temp:0vd`×2 `<COMPANY>:res01` `<COMPANY>:2led` `<COMPANY>:cap01`） | 全部 OK |
| `AddText` × 7 / `AddLine2` × 6 | 全部 OK |
| `census` | `ALL 0→29`，`TEXT 0→7`，`LINE 0→6`，`COMPONENT 0→6`，`NET 0` |
| `SaveAll()` | OK |

⚠️ `NET` 始终为 0 —— 用 `AddLine2` 画的只是**图形线**，不是电气网络。真正的连线要
`AddNet`，而其参数形态尚未破解（见「未验证」）。

### UI 可见性

连接后主窗口 `IsWindowVisible=True`，class `Afx:00400000:8:00010003:00000000:33910A05`，
标题 `DxDesigner - <prj路径> - [<ROOT_BLOCK>.1]`，另外还有 `IO Window`（不可见）、
`查找` 对话框（不可见）和上百个 `tooltips_class32`/`ComboLBox`/IME 辅助窗口（全不可见）。

动作之间 `time.sleep(2.5)` → 20 个动作约 1 分钟，可在桌面上肉眼看到器件逐个出现；
不停顿则整图瞬间出现，看不到过程。

## 图纸与坐标：两个"写成功却看不到"的致命坑（2026-09-18 傍晚）

### 现象

用户反馈"图页上没有元器件"。可 `census` 明明显示 29 个对象都写进去了，
`SaveAll` 也成功。**写得进去 ≠ 看得见。**

### 坑一：第二个参数是图纸名，不是页码

```python
app.Documents.Open('<ROOT_BLOCK>', '1')    # ✗ 不报错，但会静默新建一张叫 "1" 的空图纸
```

后果：`GetAvailableSheets('<ROOT_BLOCK>')` 从 **13 项变成 14 项**，末尾多出个 `'1'`；
29 个对象全落在那张空页上，而用户打开的是真实图纸，自然什么都看不到。

```python
ssd = app.SchematicSheetDocuments()
d = ssd.Open('<ROOT_BLOCK>', '01_Power Tree')   # ✓ 按名字
d.Activate()                               # 窗口切到这张图
```

### 坑二：坐标单位是 mil

`AddText(..., 96000, 96000)` 这类调用**不报错**，对象也真的进库了，
但 **A3 图框只有 ≈ 16535 × 11693 mil** —— 全在图纸外面。

权威参照：`SDD_HOME\wv\samples\viewdraw\scripting\ToolbarButton.vbs` 里
`ActiveView.ActiveBlock.AddComponentMoveMode "74ls01", 100, 100`。
安全区间取 **X 1200~8000、Y 1200~7500**（A4 也够）。

### 真实图纸的内容量（用来判断"打开的图对不对"）

| 图纸 | ALL | COMPONENT | TEXT | LINE | BOX |
|---|---|---|---|---|---|
| `01_Power Tree` | **131** | 1 | 51 | 53 | 26 |
| `03_CPU` | **617** | 144 | 3 | 5 | — |
| `04_AUDIO_OUT` | **194** | 52 | 11 | 0 | — |
| `99_AI_DEMO`（新建后我画的） | 30 | 6 | 8 | 6 | 0 |
| `<ROOT_BLOCK>.1`（误建的空页） | 29 | 6 | 7 | 6 | 0 |

→ **打开的图纸 census 全 0，基本就是打错图纸了。**

### 读坐标的路全试过了，都不通

| 方法 | 结果 |
|---|---|
| `objs.Item(i).GetLocation()` | ❌ 找不到成员 |
| `block.GetBboxPoint(0)` | ⚠ 只对个别对象生效，返回 `(0,0)-(10,10)` 垃圾值 |
| `view.GetJointLocs(AllOrSelected, JointType)` | ❌ 返回空串，count=0（0~4 都试过） |
| `view.ComputeMBB(objs,0,0,0,0)` | ❌ 类型不匹配 / 非选择性的参数 |
| `viewport.UserToPixel(x,y)` / `PixelToUser` | ❌ 类型不匹配 |
| `block.SheetSize` | ⚠ 只给枚举整数（真图=3，新建图=5），不给尺寸 |
| `objs.GetType(i)` | ⚠ 只给类型码（如 7） |
| `objs.Item(i).UID` | ⚠ 只给 `'<AUTO_NET>'` 这种内部 ID |

`View.Query` 返回的 `Item(i)` 是**哑对象**：`Type` / `Id` / `UID` / `Refdes` /
`GetName(flag)` 可用，几何信息基本拿不到。
→ **别再往这条路上投时间，改去查官方示例脚本的坐标惯例。**

### 修正后的正确姿势

```python
ssd.Open(原理图, '99_AI_DEMO')   # 故意用不冲突的新名字，得到一张干净的图
# ... 用 mil 量级坐标画 ...
view.ViewFull()                  # 按内容缩放，一眼看出有没有跑出纸外
doc.Activate()                   # 窗口停在成果上
app.Documents.SaveAll()
```

## 复现方式

```bash
# 1) 摸清本机 API
python scripts/dump_typelib.py <SDD_HOME>\wv\win32\bin\viewdraw.tlb out.txt

# 2) 只读连通性探测
python scripts/probe.py probe_out.txt

# 3) 完整链路（务必在工程副本上）
python -c "import dxd; app,c=dxd.connect(); ..."
```
