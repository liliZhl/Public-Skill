---
name: dxdesigner-automation
description: 通过 COM 自动化驱动 Mentor/Siemens DxDesigner（内部名 ViewDraw）——打开工程与图纸、遍历设计对象、直接从中心库调用元器件（AddPartInstance 第 2 参传【料号】才拿到完整器件名+料号）/画图形/改属性/补位号，并验证画出来的网络在网表里是否真的连上。当需要「脚本改原理图」「批量放器件」「从库里取器件画原理图」「自动化改图」「DxDesigner 脚本/插件」「VBScript 驱动 EE」「一叶知秋这类菜单脚本」「原理图上的线有没有连上」「读网表验收」「器件没引脚/接不上」「Part_Name 是空的」时使用。已在 EE 7.9.5 实机验证。
license: MIT
agent_created: true
---

# DxDesigner (ViewDraw) COM 自动化

用脚本驱动 DxDesigner，而不是手点。**读侧**用 `ee-icdb-export`（icdb2csv，离线、无需桌面）；
**写侧**（放器件、画线、改属性、重排位号）只能走本技能的 COM 自动化。

## 何时用

- 批量放器件 / 批量建原理图内容
- 批量改属性、批量重排位号（refdes）、交叉引用清理
- 生成自定义菜单/工具栏命令（`RegisterOLECommand`）
- 把外部数据（Excel/CSV/网表）灌进原理图

## 八条铁律（违反其一必失败）

1. **必须用后期绑定。** 类型库绑定的包装里 `IVdDocs.Open()` 只声明一个参数，
   而运行时要 `(name, page)`；早期绑定会在调用发出前就拒绝。
   一律走 `win32com.client.dynamic.Dispatch`。
   ```python
   app = wc.dynamic.Dispatch("ViewDraw.Application")     # 新建
   app = wc.dynamic.Dispatch(app._oleobj_)               # 附加后强制转换
   ```
2. **必须有交互式桌面会话。** COM 自动化要建窗口，SSH（Network 登录、不建桌面会话）
   **跑不了**——只能在 VNC / 物理控制台会话里跑。
3. **必须先有工程上下文。** 没有打开工程时 `Documents.Add()` 返回空、`ActiveView` 为 None。
   **`NewProject` 在 7.9.5 已废弃**（LastErrorId 670）；
   **GUI 的新建工程向导同样建不出来**，会弹 `Cannot create project`。
   建工程只能"复制现成工程"，原因与各条拒绝条件见 `references/new-project-failure.md`。
4. **图纸按名字开（不是页码），坐标量级必须匹配纸张。** 这两条错了都不会报错，
   只会让你"写成功却什么都看不到"。详见下面「两个会让成果消失的坑」。
5. **放件的第 2 个参数要传【料号】，不是器件名。**
   两种放件接口画面上长得一样，但只有 `AddPartInstance` 能把器件绑进库里、
   让引脚拿到编号、从而被 `AddNet` 真正接上：

   | 接口 | 器件绑定 | 引脚编号 | 能否产生真实网络 |
   |---|---|---|---|
   | `AddSymbolInstance(符号分区, 符号, x, y)` | ❌ | 无 | ❌ 只是符号图形 |
   | **`AddPartInstance(符号分区, 料号, 符号, x, y)`** | ✅ **完整** | ✅ `1/2/3` 或 `D/G/S` | ✅ **可以** |
   | `AddPartInstance(符号分区, 器件名, 符号, x, y)` | ⚠️ 半绑定 | ✅ 有 | ✅ 能连，但 `Part_Name` 空 |

   ```python
   block.AddPartInstance("<COMPANY>", "<PART_NO>",   "res01", 1000, 1300)  # 47K
   block.AddPartInstance("<COMPANY>", "<PART_NO>", "cap01", 1300, 1300)  # 100nF
   block.AddNet(1050, 1300, 1300, 1300, NULL, NULL, 0)
   # -> database.sym: Part_Name=<PART_NAME> / Part_Number=<PART_NO>
   # -> database.ppn: `2 R? $15Nxx` + `1 C? $15Nxx`
   ```

   引擎拿**料号**去中心库器件库（`PartsDBLibs\*.pdb`）查**唯一记录**，命中后自动带出
   `Part_Name` + `Part_Number` + `Part_Label`（规格描述）。**这就是"直接调用中心库元器件"，
   不需要复制现成器件。**
   - **料号是唯一键**（`<PART_NO>` 全库出现 1 次）；
     **器件名不是键**（`<PART_NAME>` 在 `RESISTOR.pdb` 里出现 1213 次 → 查不到）。
   - 第 1 参是**符号库分区**（引擎拼 `<分区>/sym/<符号>.1`）。器件库分区与符号库分区是
     **两套独立命名空间**：器件 `<PART_NO>` 住在 `IC.pdb`，符号却是 `IC:<SYMBOL>`；
     电阻符号在 `SymbolLibs\<COMPANY>`，器件记录在 `PartsDBLibs\RESISTOR.pdb`。

   实测 8/8 全部完整解析（含跨分区、3 脚、11 脚器件），证据见
   `references/electrical-connectivity.md` §3。

   ⚠ 别拿 `GetJointLocs` 接点数当"有没有引脚"的判据——它只反映 wire 结构；
   电气合法的器件接点数可以是 0。**验收只能靠导出网表**（铁律 6）。

6. **验收只能靠导出网表。** "在 DX 里看着连上了"完全不可信。跑
   `icdb2csv` 看 `<out>/work/database.ppn`，**一条网上出现 ≥2 个带编号的引脚才算连上**：

   ```bash
   python ../ee-icdb-export/scripts/icdb_export.py --prj "<工程>.prj" --csv-only --out "<空目录>"
   python scripts/verify_connectivity.py "<导出目录>"      # 一张表看完
   ```

   ⚠ 导出器**不在本技能内**——它在同级技能 `ee-icdb-export` 里。命令一律以本技能根目录
   为工作目录执行（`scripts/` 指本技能，`../ee-icdb-export/scripts/` 指隔壁技能）。

   细节、陷阱与完整证据见 `references/electrical-connectivity.md`。

7. **符号没有 `REFDES=` 定义时，放出来的器件会被打包器静默丢弃。**
   `res01.1` 里有 `U 18 -4 6 0 1 3 REFDES=R?`，放件自动有位号；
   但 `n-channel-mosfet.1` 只有 `U -10 -10 10 0 1 0 FORWARD_PCB=1`，**没有 REFDES 行**
   → 器件没有位号 → `database.sym` 里**根本查不到它**（不报错，只是消失）。
   补救（唯一的属性写入口）：

   ```python
   c = dxd.comp_at(view, 990, 1390)                 # 按 bbox 定位（它没有位号可用）
   dxd.add_attribute(c, "Ref Designator", "Q?")     # = c.AddAttribute("Ref Designator=Q?", 0, 0, 0)
   ```

   ☠️ **绝不要用 `block.PromoteSymbolNumbers(...)` 批量补位号** ——
   它会把**整个设计**（含真图上手工编好的位号）重编号。

8. **料号配方只填满了 BOM 侧，PCB 侧的 `Cell Name`（属性 697）没带上。**
   同一批 AI 放的件：705 `Part Name` / 706 `Part Number` / 704 `Part Label` /
   707 `Ref Designator` 都是 **8/8**，但 **697 `Cell Name` 是 0/8**；
   真图 496 颗里有 476 颗（96%）带 697。
   同一个料号 `<PART_NO>`：真图件 `<R6>` 带 `697 = <PART_NAME>_0402`，AI 件的没有。
   → **"能出 BOM" ≠ "能下 PCB"**。要用在 PCB 流程前，先在 Expedition 里验一次
   Cell 能不能自动解出；显式补写也只该在确认过库内 Cell 名之后做（值必须逐字一致）。
   细节见 `references/electrical-connectivity.md` §3.5。

## 标准流程

```python
import sys; sys.path.insert(0, r"<此技能的 scripts 目录>")
import dxd

app, created = dxd.connect()                      # 附加或新建
dxd.open_project(app, r"D:\proj\my.prj")          # 注意：打开工程 ≠ 打开图纸

schematics = dxd.list_schematics(app)             # 通常是顶层块名，如 ['<ROOT_BLOCK>']
sheets = dxd.list_sheets(app, schematics[0])      # ['01_Power Tree', '03_CPU', ...]

doc, view, block = dxd.open_sheet(app, schematics[0], sheets[0])   # ⚠ 三参是【图纸名】
#   doc   -> IVdDoc
#   view  -> IVdView
#   block -> IVdBlock   ← 写操作全在它上面

before = dxd.census(view)                         # 写前快照
block.SheetSize = 3                               # ⚠ 新图纸默认 5 = 1169x826，太小
block.AddPartInstance("<COMPANY>", "<PART_NO>", "res01", 1000, 1300)
block.AddNet(1050, 1300, 1300, 1300, NULL, NULL, 0)
view.Refresh()
after = dxd.census(view)
print(dxd.delta(before, after))                   # 用计数证明写进去了

dxd.save(app)                                     # app.Documents.SaveAll()
dxd.close(app, created)
```

**关键调用点**（全部在 7.9.5 实测通过）：

| 目的 | 调用 |
|---|---|
| 打开工程 | `app.OpenProject(prj_path)` → True/False |
| 列原理图 | `app.SchematicSheetDocuments().GetAvailableSchematics()` |
| 列图纸 | `app.SchematicSheetDocuments().GetAvailableSheets(名字)` |
| 打开图纸 | `SchematicSheetDocuments.Open(原理图, 图纸名)` → doc；然后 `doc.Activate()` |
| 视图全显 | `view.ViewFull()` —— 缩放到显示全部内容，做演示必备 |
| 列图纸 | `SchematicSheetDocuments().GetAvailableSheets(名字)` |
| 拿视图 | `app.ActiveView` |
| 拿 Block | `view.Block`（或 `view.TopBlock`） |
| 工程数据 | `app.GetProjectData()` → `IProjectData` |
| 保存 | `app.Documents.SaveAll()` |
| 关工程 | `app.CloseProject()`；关程序 `app.Quit()` |

## 两个会让成果"消失"的坑（2026-09-18 实测踩全了）

### 坑一：图纸按名字开，不要按页码

`Documents.Open(原理图, '1')` **不报错**，但它不是"打开第 1 张图"——DX 把 `'1'`
当成**图纸名**，没找到就**静默新建一张空的**。后果：`GetAvailableSheets` 从 13 项变 14 项
（多出个 `'1'`），你在那张空页上画了几十个对象，而在真实的 `01_Power Tree`
（131 个对象）上**什么都看不到**。

```python
# ✗ 会造出一张叫 "1" 的空图纸
app.Documents.Open('<ROOT_BLOCK>', '1')

# ✓ 按名字开（名字来自 GetAvailableSheets）
ssd = app.SchematicSheetDocuments()
d = ssd.Open('<ROOT_BLOCK>', '01_Power Tree')
d.Activate()          # 把窗口切到这张图 —— 演示成果靠它
```

想从零画一张干净的，故意用一个不存在的名字即可（`ssd.Open(原理图, '99_AI_DEMO')`
会新建），但**必须自己确认这个名字不会撞上真实图纸**。

### 坑二：新图纸纸型太小 + 坐标量级不对

**新图纸默认 `SheetSize = 5`，纸张只有 1169 × 826**；真图是 `SheetSize = 3`，
纸张 **3400 × 2200**。用 2000~7400 这种量级，对象**照样写进 iCDB、`census` 照样 +1**，
但全在纸外 —— 画布上看到的就是"器件飘在图框右上角外面"。

```python
block.GetBboxPoint(0)   # (0,0)       ← 纸张左下
block.GetBboxPoint(2)   # (3400,2200) ← 纸张右上
block.SheetSize = 3     # 新图纸必须先设纸型，否则只有 1169x826
```

真图上人工摆的元件坐标都在 0~3400 / 0~2200 内，**元件本身只有 20~110 单位大**
（`res01` 是 50×6、`cap01` 是 30×18、`2led` 是 110×91、`3v3` 是 40×20、`0vd` 是 40×40），
所以**器件间距用 200~400，别用 2000+**。

⚠ 上一版这里写的"单位是 mil、A3 图框 16535×11693"是**错的**。
2026-09-18 用 `GetBboxPoint` 实测真图纸张 = 3400×2200 后已更正。
（`AddNet` 内部换算暴露比值 25400，即内部单位 ≈ 1/25400 inch。）

### 怎么及早发现

1. `GetAvailableSheets()` 的名字/数量有没有变多 —— 多了就是误建了空图
2. 打开的图纸 `census` 是不是全 0 —— 真实图纸几乎不可能全 0
   （<PROJECT> 的 `01_Power Tree` 有 131 个对象，`03_CPU` 有 617 个）
3. 画完调 `view.ViewFull()` —— 按内容缩放，能立刻暴露"内容跑到老远"

### 坐标怎么读（2026-09-18 更正）

- `IVdComp.GetBboxPoint(0..3)` 可用 —— 返回包围盒角点，读 `.X` / `.Y` 得到坐标。
  本轮"器件为何飘在框外"就是靠它定案的。
- `IVdComp.GetLocation()` 报"找不到成员"（不可用）。
- `block.GetBboxPoint(0/2)` = **纸张**左下/右上，**不是内容范围**。
- `View.GetJointLocs(ALL, n)` 返回的是**wire 接点**，**不是器件引脚**——用它数
  "有没有引脚"是错的（电气合法的器件接点数可以是 0）。判据只能用导出的网表。
- **想知道引脚位置，别问 COM** —— 直接读中心库的符号文件（纯文本），
  格式与各符号实测引脚见 `references/electrical-connectivity.md` §5。
- `IVdComp.Refdes` 读空**不代表**器件有问题（真图上也有读空的），别拿它当健康检查。

## 写操作（IVdBlock，80 个方法 + 验证结论）

| 方法 | 签名 | 实测 |
|---|---|---|
| `AddText` | `(Text, x, y)` | ✅ |
| `AddLine2` | `(x1, y1, x2, y2)` | ✅ |
| `AddCircle` | `(x, y, r)` | ✅ |
| `AddArc` | `(x1, y1, x2, y2, x3, y3)` | ✅ |
| `AddBox` | `(x1, y1, x2, y2)` | ⚠️ 报服务器异常（0x80010105），改用 `AddLine2` 画矩形 |
| `AddPartInstance` | `(符号分区, 料号, 符号名, x, y)` | ✅ **★ 放件首选**：料号命中中心库器件库 → 自动带出 `Part_Name`+`Part_Number`+`Part_Label`。第 1 参按**符号分区**解析（类型库里写作 `PartPartitionName` 是误导） |
| `AddSymbolInstance` | `(符号分区, 符号名, x, y)` | ✅ 从中心库放符号；⚠ **放出来的是"符号图形"，无 Part 定义、无引脚** |
| `AddComponent` | `(符号名, x, y)` | ✅ |
| `AddNet` | `(x1,y1,x2,y2,CompPin1,CompPin2,BusOrWire)` | ✅ **已破解**：第 5/6 参传 `VARIANT(VT_DISPATCH,None)` → NET +1 |
| `DeleteSelected` | `(delUnc)` | ✅ 配合 `SelectByName2` / `_SetSelected` 使用 |
| `SetZSheetSize` | `(Width, Height)` | ✅ 等价于设纸型（SheetSize 记为 10） |
| `PromoteSymbolNumbers` | `(SelectedOnly, Slot)` | ☠️ 能调通但会**重编号整个设计**（含真图），生产工程禁用 |
| `SetAttributeValue` | `(ObjName, ObjType, Attr, Value, IsOat)` | ❌ **已作废**（error 670）。写属性改用 `IVdComp.AddAttribute` |

## 写器件属性：只有 `IVdComp.AddAttribute` 有效（2026-09-18 实测）

```python
c.AddAttribute("Ref Designator=Q?", 0, 0, 0)   # ✅ 成功，格式 "属性名=值"，支持带空格的名
```

| 写法 | 结果 |
|---|---|
| **`comp.AddAttribute("名=值", x, y, vis)`** | ✅ **唯一可用** |
| `comp.Refdes = "Q?"` | ❌ 只读（`Property can not be set`） |
| `comp.AddBatchAttributes("名=值\r…")` | ⚠️ 返回 `True` 但**实测无效**；`\n` 分隔会被当成一个属性名，多词名会被截断成 `Name=` |
| `block.SetAttributeValue(...)` | ❌ error 670 已作废 |

读回用 `comp.FindAttribute("Part Name")` → `IVdAttr`（`.Name` / `.Value`），
或 `comp.Attributes` 集合。⚠ **`GetBatchAttributes` / `GetBatchOats` 是属性不是方法**
（加括号报 `'str' object is not callable`），格式 `<flag> <名>=<值>\r`，只可读不可写。

## 调用中央库元器件（★★ 2026-09-18 实测打通）

**不需要先 `AddLibrary()`，也不需要复制现成器件。** 只要工程的中央库路径可达，
`AddPartInstance` 会自己去库里取：

```python
block.AddPartInstance("<COMPANY>", "<PART_NO>",   "res01", 1000, 1300)  # 47K 电阻
block.AddPartInstance("IC",       "<PART_NO>", "<SYMBOL>", 2200, 1400)  # LED 驱动 IC
```

两个参数分属**两套独立命名空间**，这是最容易搞错的地方：

| 参数 | 取自 | 例 |
|---|---|---|
| 第 1 参（分区） | `SymbolLibs\<分区>\sym\<符号>.1` | `<COMPANY>` / `IC` / `temp` |
| 第 2 参（料号） | `PartsDBLibs\*.pdb` 里的唯一记录 | `<PART_NO>` |
| 第 3 参（符号） | `SymbolLibs\<分区>\sym\<符号>.1` | `res01` / `cap01` |

**同一颗器件的符号和器件记录可以分属不同分区**：
符号 `IC:<SYMBOL>`（在 `SymbolLibs\IC`）+ 器件 `<PART_NO>`（在 `PartsDBLibs\IC.pdb`）；
电阻符号在 `SymbolLibs\<COMPANY>`，器件记录却在 `PartsDBLibs\RESISTOR.pdb`。

第 1 参为什么是符号分区（用报错反推，很有说服力）：

```
block.AddPartInstance('RESISTOR', '', 'res01', x, y)
  -> com_error: Symbol RESISTOR:res01.1 not found, empty or a block.
```

即引擎把它拼成 `<分区>/sym/<符号>.1` 去找文件。

### 怎么查一个器件该填什么

```bash
# 1) 料号：从设计里抄最方便（导出的 database.sym 第 7 列）
awk -F'\t' '$8 ~ /:res01/{print $2,$6,$7}' work/database.sym     # RefDes Part_Name Part_Number
# 2) 料号住在哪个 .pdb（二进制里直接搜字符串；每个料号只出现 1 次）
#    脚本可参考 references/electrical-connectivity.md §3.3 的扫描方法
# 3) 符号名与引脚坐标：直接读文本
grep -E "^(D|P) " "//<HOST_IP>/eda/Mentor_lib_EE7.9/SymbolLibs/<COMPANY>/sym/res01.1"
```

**先确认中央库路径**（也顺便确认共享盘通不通）：

```python
pd = app.GetProjectData()
pd.CentralLibraryPath          # ⚠ 后期绑定下是【属性】，不是方法！写成 () 会报 'str' object is not callable
pd.GetProjectName()            # 这个才是方法
```

中央库路径写在 `.prj` 的 `SECTION DesignInfo / KEY CentralLibrary`。共享盘不可达时，
可**改工程副本**的该键指向本地库（如 `SDD_HOME\standard\examples\SampleLib2007\SampleLib.lmc`）再打开。

**枚举一个中央库里有什么**（不必开 DxDesigner，直接看目录）：

```bash
ls "//<server>/<share>/<lib>/SymbolLibs/"          # 符号分区名 = 这里的子目录名
ls "//<server>/<share>/<lib>/SymbolLibs/<COMPANY>/sym/"   # 符号名 = 文件名去掉 .1
ls "//<server>/<share>/<lib>/PartsDBLibs/"         # 器件库分区 = *.pdb（查料号用）
```

公司库实测规模（`\\<HOST_IP>\eda\Mentor_lib_EE7.9`）：
符号分区 `<COMPANY>`(5987 个符号) / `IC`(1937) / `temp`(293) / `S905L3S`(186) / `SWITCH`(3)；
器件库分区 22 个 `.pdb`：`RESISTOR` / `CAPACITOR` / `IC`(34MB) / `CONNECTOR` / `DIODE` /
`LED` / `MODULE` / `<COMPANY>` / `TRANSISTOR` / `CRYSTAL` / `FUSE` / `SWITCH` / `TUNER` …
工程 `.prj` 里的 `LIST PDBs` / `LIST Symbols` 都是**空的**——分区清单全部继承自
`mentor_lib.lmc`，所以 **`.lmc` 是分区名的权威来源**（二进制，但里面能直接 `strings` 出分区名）。

## 验证写入是否真的生效（别只看返回值）

`Add*` 返回对象 ≠ 内容真的进图了。唯一可靠判据是**对象计数**：

```python
before = dxd.census(view)
... 写入 ...
view.Refresh()
after = dxd.census(view)
# 增量必须与调用次数吻合，例如 +2 TEXT / +1 LINE
```

`View.Query(flags, selected)` 是这里唯一能用的全图查询（`flags` 取 `VDM_*` 掩码，
`selected` 取 `VD_ALL=0`）。

**两个反直觉的实测结论（都会影响你写脚本的方式）：**

- `database/icdb.dat` 的体积/mtime 变了**不能**当落盘证据——**光打开工程就会改它**
  （零写入的对照组也 +34 KB）。
- **iCDB 的图元写入是即时提交的。** `AddText` 之后不调 `SaveAll()`、直接 `Quit()`，
  重新打开那个对象**还在**。也就是说 `SaveAll()` 不是"是否落盘"的开关
  → **对生产工程做任何试验都可能被永久写进去，必须只在副本上做。**

## 用户能不能"实时看见"这些操作（2026-09-18 实测）

**能看见，但默认看不见"过程"。**

- `app.Visible = True` 可读可写；COM 起的 `viewdraw.exe` 主窗口**默认就是可见的**，
  class `Afx:00400000:...`，标题形如
  `DxDesigner - <工程路径> - [原理图.页码]`，`IsWindowVisible=True`。
- 但为了让操作者亲眼看到，**必须在动作之间 `time.sleep()`**：一个 `AddSymbolInstance`
  只要几毫秒，不留停顿的话整张图是"啪"地一次出现，人眼看不到逐笔过程。
  实测每步停 2.5 秒 → 20 个动作 ≈ 1 分钟，器件会一个个冒出来，效果很好。
- 判断有没有可见窗口：枚举**顶层**窗口再 `IsWindowVisible`。注意 `viewdraw.exe`
  名下有上百个辅助窗口（`tooltips_class32`、`ComboLBox`、`Default IME`、
  `OleDdeWndClass`、`IO Window`…）**全都不可见**，别被它们误导——认 `Afx:` 那个主窗口。
- 这同时也是它能跑的前提：COM 自动化需要**交互式桌面会话**（`SilentMode=1` 只是抑制
  对话框，不是隐身）。所以"操作者看得见"和"脚本能跑"是同一件事的两面。
- 想要一张现场图存档：PIL 装不上时可用 PowerShell + `System.Drawing.PrintWindow`
  截窗口（PrintWindow flag=2），不依赖任何第三方包。

## ☠️ 会把 DxDesigner 直接打崩的调用（2026-09-18 实测两次）

| 调用 | 症状 |
|---|---|
| `AddNet(x1,y1,x2,y2, c1, c2, 0)`，其中 `c = comp.GetConnections(n)` | 首次 `服务器出现意外情况`，随后 `RPC 服务器不可用` → **进程死亡** |
| `IProjectData.GetPinNumbers(分区, 符号, 料号, slot)` 连查多个后 | `OLE error 0xc015000f` → `RPC 服务器不可用` → **进程死亡** |
| `AddPartInstance` / `AddText` 参数用了**超出纸面很多**的坐标 | `Unable to start net at X=...`（可恢复） |

**识别**：`RPC 服务器不可用`（0x800706BA）= 进程已死；此后所有 COM 调用返回 `None`
（`census` 全部 `None` 就是典型信号）。清理：`taskkill /F /IM viewdraw.exe`。

**好消息**：崩溃**不写坏数据**。崩溃前 `SaveAll()` 过的内容完好，重新 `OpenProject`
即可（实测 9.3~11.5 秒打开，图纸与对象计数完全一致）。

**规矩**：`GetConnections` / `GetPinNumbers` 这类"查询型"API 一律当作禁区，
不要在自动化脚本里连着调。

## 已知废弃 / 不可用（LastErrorId 670 = "此方法/特性已作废"）

- `NewProject` —— **构建新工程的路断了**，用复制现成工程（GUI 向导也是一样的结论，
  报 `Cannot create project`；取证与拒绝条件清单见 `references/new-project-failure.md`）
- `IVdBlock.SetAttributeValue(...)` —— **写属性的正路是 `IVdComp.AddAttribute`**
- `IVdComp.Refdes = "Q?"` —— 类型库列了 PUT，实际是**只读**（`Property can not be set`）
- `GetLibraries`、`SynchronizesViewBase`
- `IVdDoc.Save()`（但 `Documents.SaveAll()` 正常）、`IVdDoc.Saved`
- 无工程上下文时 `Documents.Add()` 返回空
- ⚠️ 不是作废但**别用**：`IVdComp.AddBatchAttributes()` —— 返回 `True` 却实测无效
  （见「写器件属性」一节）
- ~~`AddNet` 参数形态~~ —— **已破解，移出本节**：第 5/6 参传
  `VARIANT(pythoncom.VT_DISPATCH, None)` 即产生 NET。但**绝不能**把
  `GetConnections(n)` 的返回值传进去（会让 DX 崩溃，见上一节）。

## 陷阱

- **网上大量「DxDesigner VBA 生成原理图」教程是假的**——用的是 Altium 的 API
  （`ActiveProject.SchDocuments.Add`、`.SchDoc`、`newPart_inst.Value`）。DxDesigner 没有这些对象。
  只认类型库里真实存在的成员名。
- **`SchematicSheetDocuments` 是方法不是属性**：VBS 里 `Set x = app.SchematicSheetDocuments`
  能自动调用，Python 必须写 `app.SchematicSheetDocuments()`。
- 早期绑定下 `Query`/`Open` 这类调用会被参数签名挡住，报的不是 COM 错而是
  Python 的 `takes N positional arguments`——见到这种错就换后期绑定。
- 用完 `Quit()`；脚本异常退出会留下 `viewdraw.exe` 占着许可，并让下次
  `GetActiveObject` 附带一个带着旧状态的实例。
- 自动化跑在**工程副本**上，别直接动生产工程。

## 环境取证（想先摸清本机版本时）

```bash
# 装在哪、什么版本
#   注册表 HKLM\SOFTWARE\WOW6432Node\Mentor Graphics
#   ProgID ViewDraw.Application 的 CLSID {EA4ABD70-84B0-11CE-8237-00001B4D36B5}
# 类型库（所有接口与方法的权威来源）
#   <SDD_HOME>\wv\win32\bin\viewdraw.tlb
# 官方脚本范例（照抄这些最安全）
#   <SDD_HOME>\standard\ee_utils\CopyCircuit.vbs    改电路、Refdes 可写
#   <SDD_HOME>\dx\tutor\dxdb\scripting_sample.vbs   官方脚本入门
#   <SDD_HOME>\IODesigner\dxdesigner\newdesign.vbs  开工程+开图纸
#   <SDD_HOME>\IODesigner\dxdesigner\rundxd.vbs     Documents.Open 用法
# 版本说明：Application.Version 报 '2007.12.0'（即 EE 2007 系，7.9.5）
```

`scripts/` 下另有：

| 脚本 | 用途 |
|---|---|
| `new_project.py` | **建新工程**（复制现成工程 → 可改 `.prj` 名 → 可改中央库），绕开坏掉的向导 |
| `demo_draw.py` | **画一张电气合法的示意原理图**（`AddPartInstance` 传**料号** + `AddNet` 连线，默认 +3V3→R→C→0VD 三条网）；顺带自检中央库可达性；`--step 2.5` 让操作者肉眼看到器件逐个出现 |
| `verify_connectivity.py` | **验收工具**：从 `icdb_export` 导出目录判定连接是否成立——器件是否绑定（`Part_Name`/`Part_Number`）、每条网的引脚清单、`OK / 半连 / 同件 / 悬空 / 空网` 五档判决。只用标准库，不碰 COM |
| `find_msg.py` | 给一句报错原文，扫安装树定位是哪个模块弹的，并 dump 邻近字符串（邻居就是失败条件清单） |
| `dump_typelib.py` | 列出类型库全部接口/方法/枚举 |
| `probe.py` | 只读连通性探测 |

`references/` 下：

| 文件 | 内容 |
|---|---|
| `electrical-connectivity.md` | **电气连接专章**：属性号↔属性名↔导出列对照表、`AddPartInstance` 传料号的完整配方与 8/8 实测证据、器件库分区 vs 符号库分区、`AddAttribute` 属性写入、符号缺 `REFDES=` 会被丢弃、`AddNet` 正确姿势与崩溃禁区、符号引脚文件格式、网表验证方法 |
| `typelib-api.txt` | 类型库全量接口/方法/枚举（223 types / 58 interfaces，68 KB） |
| `live-verification.md` | EE 7.9.5 实机验证矩阵（哪些能用、哪些不能） |
| `new-project-failure.md` | `Cannot create project` 的取证过程与向导拒绝条件清单 |
