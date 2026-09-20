# 电气连接（NET）与器件完整性 —— 实测修正版 2026-09-18（晚）

这一节回答一个具体问题：**为什么"把器件放上去、把线画上去"之后，图看起来对，
但网表里没有任何连接、料号是空的？**

结论先行：

> 1. **放件必须用 `AddPartInstance`，且第 2 个参数要传【料号】，不是器件名。**
> 2. 第 2 参传料号 → 引擎去**中心库器件库**（`PartsDBLibs\*.pdb`）查唯一记录，
>    自动带出 `Part Name` + `Part Number` + `Part Label`（规格描述）。
>    **这是"直接调用中心库元器件"，不需要复制现成器件。**
> 3. 第 2 参传器件名 → 查不到，字符串被塞进 `Part Number` 属性，
>    `Part Name` 为空（**半绑定**：网络能用，但出不了正式 BOM）。
> 4. 符号文件里**没有 `REFDES=` 行**的符号（如 `n-channel-mosfet`），
>    放出来的器件没有位号 → **打包时被整条丢弃**，网表里查不到。
>    补位号只能用 `comp.AddAttribute("Ref Designator=Q?", 0, 0, 0)`。

---

## 0. 属性号 → 属性名 → 导出列 的对照（关键索引）

DxDesigner 的器件属性在 iCDB 里按**属性号**存储。查法：
`<导出>/work/database.prm`（属性号↔属性名）、`database.spr`（符号↔属性值）。

| 属性号 | 属性名 | `database.sym` 的列 | 真器件 `<R6>` 的值 |
|---|---|---|---|
| 697 | `Cell Name`（OAT） | — | `<PART_NAME>_0402` |
| 704 | `Part Label` | — | `47K` |
| **705** | **`Part Name`** | **`Part_Name`** | `<PART_NAME>` |
| **706** | **`Part Number`** | **`Part_Number`** | `<PART_NO>` |
| 707 | `Ref Designator` | `Reference_Designator` | `<R6>` |
| 710 | `Value` | — | `47K` |
| 1 | `Instance Name` | `Symbol_Reference` | `<AUTO_NET>` |

**`Part_Name`(=器件名) 与 `Part_Number`(=料号) 是两个不同的东西。**
`Part_Name` 是器件库里那条记录的名字（一堆器件共用，如 `<PART_NAME>` 出现 1213 次）；
`Part_Number` 是**唯一键**（`<PART_NO>` 全库只出现 1 次）。
**做 BOM 要的是 `Part_Number`；`Part_Name` 空 = 器件没绑到库里。**

> ⚠ `database.prt` 表的列名叫 `Part_Number`，但里面装的是**器件名/料号的混合清单**，
> 别拿它当权威——`database.sym` + `database.spr` 才是。

---

## 1. 先解决"画面上看不到 / 在框外"

两个叠加的坑，任何一个都会让成果"消失"：

| 项 | 新图纸默认 | 真图（正确） |
|---|---|---|
| `block.SheetSize` | **5** | **3** |
| 纸张范围 `GetBboxPoint(0..2)` | **1169 × 826** | **3400 × 2200** |
| 坐标可用空间 | 0~1169 / 0~826 | **0~3400 / 0~2200** |

- `block.GetBboxPoint(i)` 返回的是**纸张边界**（0 和 2 是左下/右上），不是内容范围。
- **新图纸必须显式设纸型**：`block.SheetSize = 3`（或 `SetZSheetSize(3400, 2200)`，
  后者会把 SheetSize 记为 10 = 自定义）。
- **坐标量级必须按纸张来**。用 2000~7400 这种量级 → 对象写进库了、计数也 +1 了，
  但**飘在纸外，画面上什么都没有**。

### 元件本身有多大（实测 bbox）

| 符号 | bbox 尺寸（单位） | 引脚（本地接点） |
|---|---|---|
| `<COMPANY>:res01` | 50 × 6 | `P1(0,0)` `P2(50,0)` |
| `<COMPANY>:res03` | 50 × 6 | `P1(0,0)` `P2(50,0)` |
| `<COMPANY>:cap01` | 30 × 18 | `P1(0,0)` `P2(30,0)` |
| `<COMPANY>:l01` | 70 × 6 | `P1(0,0)` `P2(70,0)` |
| `<COMPANY>:n-channel-mosfet` | 55 × 60 | `P1(30,50)` `P2(-10,10)` `P3(30,-10)` |
| `IC:<SYMBOL>` | 120 × 150 | 11 脚，如 `P19(70,0)` `P37(90,0)` |
| `<COMPANY>:2led` | 110 × 91 | `P2(0,30)` `P3(110,50)` |
| `temp:3v3` | 40 × 20 | `P1(20,0)` |
| `temp:0vd` | 40 × 40 | `P1(20,40)` |

**元件只有几十个单位大**，所以器件间距也应该在 200~400 量级。

---

## 2. `AddNet` —— 唯一能产生 NET 的入口（以及正确调用姿势）

```python
import pythoncom
from win32com.client import VARIANT

NULL = VARIANT(pythoncom.VT_DISPATCH, None)     # ← 关键

block.AddNet(x1, y1, x2, y2, NULL, NULL, 0)     # 0 = wire, 1 = bus
```

签名：`AddNet(Locationx1, Locationy1, Locationx2, Locationy2, CompPin1, CompPin2, BusOrWire)`

**第 5、6 个参数必须传「类型为 IDispatch 但值为 NULL」的 VARIANT。**

| 传什么 | 结果 |
|---|---|
| `None` / `pythoncom.Empty` / `""` / `0` | `类型不匹配`（参数索引 5） |
| `VARIANT(VT_DISPATCH, None)` | **OK，NET 计数 +1** ✅ |
| `GetConnections(n)` 返回的对象 | ❌ **DxDesigner 直接崩溃** |
| 第 7 参数传 `None` | `类型不匹配`（参数索引 7）→ 必须给 0/1 |

**连线的本质是几何的**：wire 端点落在器件的引脚坐标上就算接上——不需要
`CompPin1` / `CompPin2`。所以**引脚坐标必须算准**（见 §5）。

### ☠️ 会让 DxDesigner 崩溃的调用（实测）

| 调用 | 症状 |
|---|---|
| `AddNet(x1,y1,x2,y2, conn1, conn2, 0)`，其中 `conn = comp.GetConnections(n)` | 第一次 `服务器出现意外情况`，第二次起 `RPC 服务器不可用` → **进程死亡** |
| `IProjectData.GetPinNumbers(分区, 符号, 料号, slot)` 连查几个之后 | `OLE error 0xc015000f` → 随后 `RPC 服务器不可用` → **进程死亡** |

**判断进程是否已死**：`RPC 服务器不可用`（0x800706BA）。此后所有 COM 调用
返回 `None`（`census` 全 `None` 就是典型信号）。清理：
`taskkill /F /IM viewdraw.exe`。
**好消息**：崩溃不会写坏数据——崩溃前 `SaveAll()` 过的内容完好，
重新 `OpenProject` 即可（实测 9~11 秒）。

---

## 3. ⭐⭐ 正解：`AddPartInstance(符号分区, 料号, 符号名, x, y)`

### 3.1 钉死的配方

```python
block.AddPartInstance("<COMPANY>", "<PART_NO>",   "res01",  1000, 1300)  # 47K 电阻
block.AddPartInstance("<COMPANY>", "<PART_NO>", "cap01",  1300, 1300)  # 100nF 电容
block.AddPartInstance("<COMPANY>", "<PART_NO>", "res03",  1700, 1300)  # 0805 电阻
block.AddPartInstance("<COMPANY>", "<PART_NO>", "n-channel-mosfet", 1000, 1400)  # 3 脚
block.AddPartInstance("IC",       "<PART_NO>", "<SYMBOL>",   2200, 1400)  # 11 脚，跨分区
```

**参数语义（实测确定）**

| # | 参数 | 含义 | 说明 |
|---|---|---|---|
| 1 | `PartPartitionName` | **符号库分区** | 引擎拼 `<分区>/sym/<符号>.1`。传器件库名（`RESISTOR`）会报 `Symbol RESISTOR:res01.1 not found`。类型库里的参数名是**误导** |
| 2 | `DeviceName` | **料号** | 拿去中心库**器件库**查唯一记录。传器件名查不到 |
| 3 | `SymbolName` | 符号名 | 如 `res01` |
| 4/5 | x, y | 放置点 | 引脚世界坐标 = 放置点 + 符号文件 `P` 行的本地坐标 |

### 3.2 实测证据：8/8 全部完整解析

在 `<ROOT_BLOCK>.AI_SCHEMATIC`（3400×2200）放 8 个器件，**器件名/料号三件套全程零属性调用**
（唯一一次 `AddAttribute` 是给表格末行那颗缺 `REFDES` 的 MOSFET 补位号 ——
不补它压根不会出现在这 8 行里），关 DX 导网表：

| Symbol_ID | RefDes | `Part_Name` | `Part_Number` | 符号 | 说明 |
|---|---|---|---|---|---|
| 400 | `R?` | <PART_NAME> | <PART_NO> | <COMPANY>:res01 | |
| 401 | `C?` | CAPC0603X30N | <PART_NO> | <COMPANY>:cap01 | |
| 406 | `R?` | <PART_NAME>_0805 | <PART_NO> | <COMPANY>:res03 | |
| 407 | `C?` | CAPC1608X90N | <PART_NO> | <COMPANY>:cap01 | |
| **408** | **`Q?`** | **<PART_NAME>** | **<PART_NO>** | <COMPANY>:n-channel-mosfet | 补位号后才进表 |
| 409 | `R?` | <PART_NAME> | <PART_NO> | <COMPANY>:res01 | |
| **410** | **`U?`** | **DFN50P300X300X080-11AN** | **<PART_NO>** | **IC:<SYMBOL>** | **跨符号分区** |
| 411 | `C?` | CAPC0603X30N | <PART_NO> | <COMPANY>:cap01 | |

**引脚级证据**（`database.ppn`）——引脚名是从器件库带出来的，不是编号：

```
1152  D  Q?  <AUTO_NET>      ← MOSFET 漏极
1155  1  R?  <AUTO_NET>      ← 电阻引脚 1       ★ 真正的两点电气连接
1158  2  U?  <AUTO_NET>      ← LED 驱动 IC 引脚 2
1168  1  C?  <AUTO_NET>      ← 电容引脚 1
```

**`Part Label`（规格描述）也是自动带出的**，可以直接用来核对选型：

| 料号 | 自动带出的 `Part Label` |
|---|---|
| `<PART_NO>` | `47K` |
| `<PART_NO>` | `0.6 OHM +/-1%` |
| `<PART_NO>` | `C/1NF/50V +/-10% X7R` |
| `<PART_NO>` | `10UF/6.3V` |
| `<PART_NO>` | `M-N-CH/SHY` |
| `<PART_NO>` | `JW1127` |

### 3.3 对照：传器件名 = 半绑定

```python
block.AddPartInstance("<COMPANY>", "<PART_NAME>", "res01", 300, 1550)   # ✗ 器件名
```

导出结果：

| Symbol_ID | `Part_Name` | `Part_Number` | 引脚编号 | 判决 |
|---|---|---|---|---|
| **385**（传器件名） | **空** | `<PART_NAME>` | 有（1/2） | 半绑定：网络能用，`Part_Name` 空 |
| **398**（传料号） | `<PART_NAME>` | `<PART_NO>` | 有 | ✅ 完全解析 |

**为什么？** 料号是器件库里的**唯一键**；器件名是**共享名**。
扫描实测（`PartsDBLibs\*.pdb` 二进制内字符串计数）：

| 串 | `RESISTOR.pdb` | `CAPACITOR.pdb` | 其它 | 解释 |
|---|---|---|---|---|
| `<PART_NO>` | **1** | 0 | 0 | 唯一键 → 可作为查询条件 |
| `<PART_NO>` | 0 | **1** | 0 | 唯一键 |
| `<PART_NAME>` | 1213 | 0 | `Disable.pdb` 1614 | 共享名 → **无法定位** |

### 3.4 器件库分区 ≠ 符号库分区（两套独立命名空间）

中心库 `mentor_lib.lmc` 声明了两套：

```
PartsDBLibs\  CAPACITOR.pdb  COIL.pdb  CONNECTOR.pdb  CRYSTAL.pdb  DIODE.pdb
              IC.pdb(34MB)   JACK SOCKET.pdb  LED.pdb  MODULE.pdb  RESISTOR.pdb
              <COMPANY>.pdb   SWITCH.pdb  TRANSISTOR.pdb ...
SymbolLibs\   IC/  <COMPANY>/  SWITCH/  temp/  Temp_a/  S905L3S/  CES/
```

同一颗器件：**符号来自 `SymbolLibs`，器件记录来自 `PartsDBLibs`**，两者名字可以完全不同。
例：LED 驱动 IC = 符号 `IC:<SYMBOL>` + 器件 `<PART_NO>`（住在 `IC.pdb`，不在 `<COMPANY>.pdb`）。

工程 `.prj` 里的 `LIST PDBs` / `LIST Symbols` 都是**空的** ——
分区清单全部继承自 `KEY CentralLibrary "...\mentor_lib.lmc"`。

### 3.5 ⚠️ 料号配方没带出来的那一条：PCB 侧 `Cell Name`（属性 697）

料号配方把 **BOM 侧** 三件套填满了（705 `Part Name` / 706 `Part Number` / 704 `Part Label`），
但 **PCB 侧** 的 `Cell Name` 没跟上。把终审快照 `database.spr` 按器件分组统计：

| 属性号 | 属性名 | AI 放的 8 颗 | 真图 496 颗 |
|---|---|---|---|
| 705 / 706 / 704 / 707 | Part Name / Part Number / Part Label / Ref Designator | **8 / 8** | 496 / 496 |
| **697** | **`Cell Name`（OAT）** | **0 / 8** | 476 / 496（96%） |

同一个料号 `<PART_NO>`：真图件 `<R6>` 带 `697 = <PART_NAME>_0402`，
AI 放的 400 号同料号、同 `Part Label`（`47K`），**没有这一条**。

**怎么用这条信息**

- 出原理图/BOM：不受影响，AI 件在这一层是完整的。
- 下 PCB（Expedition 取 Cell / 封装单元）：**没验过**。真图件 96% 带 697，
  AI 件 0%，差异稳定。不要在没跑过一次 PCB 流程前断言"能出 BOM = 能下 PCB"。
- 要补的话，先确认 697 该由流程自动解出（料号 → Cell）还是必须显式写；
  显式写用 `comp.AddAttribute("Cell Name=<PART_NAME>_0402", 0, 0, 0)`，
  但值必须与库内 Cell 名逐字一致，写错比不写更麻烦。

---

## 4. 写属性：`AddAttribute` 是正路（`AddBatchAttributes` 不行）

`res01` 之类符号自带 `REFDES=R?`，放件时自动有位号；
但**有些符号没有**，放出来的器件没位号 → 打包时被丢。补位号实测：

| 写法 | 结果 |
|---|---|
| `comp.Refdes = "Q?"` | ❌ `Property '<unknown>.Refdes' can not be set.`（**只读**） |
| `comp.AddBatchAttributes("3 REFDES=Q?\r")` | ❌ 返回 `True` 但**无任何效果** |
| `comp.AddBatchAttributes("Part Name=<PART_NAME>\nPart Number=...")` | ❌ 用 `\n` 分隔会被当成**一个属性名** |
| `comp.AddBatchAttributes("0 Part Name=X\r...")` | ❌ 多词属性名被**截断**成 `Name=` |
| `block.SetAttributeValue(uid, T, "Ref Designator", "Q?", False)` | ❌ `error 670 此方法/特性已作废` |
| **`comp.AddAttribute("Ref Designator=Q?", 0, 0, 0)`** | ✅ **成功**，`comp.Refdes` 立刻读回 `Q?` |
| `comp.AddAttribute("REFDES=Q?", 0, 0, 0)` | ✅ 建了属性，但名字是遗留名 `REFDES`，导出认不出 |

**签名**：`IVdComp.AddAttribute(String, X, Y, Visibility)`，`String` 格式 `"属性名=值"`。
**支持带空格的属性名**（`Part Name`、`Ref Designator`）。

### `GetBatchAttributes` / `GetBatchOats` 是**属性**，不是方法

```python
s = comp.GetBatchAttributes        # ← 不要加括号！加了报 'str' object is not callable
# '3 Value=47K\r0 Part Label=47K\r0 Part Name=<PART_NAME>\r0 Part Number=<PART_NO>\r3 Ref Designator=<R6>\r'
```
格式：`<flag> <属性名>=<值>\r`，**CR 分隔**。`flag` 3 = 图上可见（`Value`/`Ref Designator`），
0 = 隐藏。**但这个格式只可读不可写**（见上表）——别想用它反推写入口。

### 读单个属性

```python
a = comp.FindAttribute("Part Name")    # 返回 IVdAttr 或 None（不存在时返回 None）
a.Name, a.Value                        # 都是属性；Value 可读，但写入请用 AddAttribute
```

`Attributes` 集合也可遍历：`comp.Attributes.Count` / `comp.Attributes.Item(i)`。

### 符号缺 `REFDES=` 的完整处理

```
n-channel-mosfet.1:                 res01.1:
  U -10 -10 10 0 1 0 FORWARD_PCB=1   U 18 -4 6 0 1 3 REFDES=R?
  （没有 REFDES 行！）                U 20 -16 10 0 1 0 DEVICE=
```

→ 放件后 `GetBatchAttributes` 里**没有 `Ref Designator` 行**、`Refdes` 读回 `''`、
**打包器直接丢弃它**（`database.sym` 里查不到）。
补一句 `comp.AddAttribute("Ref Designator=Q?", 0, 0, 0)` 后重新导出，
它就正常进表了（见 §3.2 的 Symbol_ID 408）。

⚠ **绝不要用 `block.PromoteSymbolNumbers(...)` 来批量补位号** ——
它会把**整个设计**（含 1~12 页真图）重编号。真图上用手工位号，重编号会破坏一致性。

---

## 5. 符号引脚定义：直接从中心库文件读

符号文件是**纯文本**，路径：`<中心库>\SymbolLibs\<分区>\sym\<符号名>.1`

```
res01.1:
  D 0 3 50 -3                    ← 本体范围 (x1,y1)-(x2,y2)
  P 1 0 0 10 0 0 2 0             ← 引脚：编号 1，连接点 (0,0)，引线画到 (10,0)
  P 2 50 0 40 0 0 3 0            ← 引脚：编号 2，连接点 (50,0)
```

- `P <编号> <x1> <y1> <x2> <y2> <...>`，**电气连接点是 (x1, y1)**（靠外侧那端）
- `D` 行给本体范围，与实测 `GetBboxPoint` 完全吻合（`res01` = 50×6）
- **世界坐标 = 放置点 + 本地坐标**
- `U` 行是属性定义：`U <x> <y> <字号> <...> <可见性> <名>=<默认值>`

**反算放置点**，让相邻器件引脚落在同一水平线上，wire 全是直线：

```python
block.AddPartInstance("<COMPANY>", "<PART_NO>",  "res01", 1000, 1300)  # pin1(1000,1300) pin2(1050,1300)
block.AddPartInstance("<COMPANY>", "<PART_NO>","cap01", 1300, 1300)  # pin1(1300,1300) pin2(1330,1300)
block.AddNet(1050, 1300, 1300, 1300, NULL, NULL, 0)                        # ★ 真网络
```

---

## 6. 读网表验证 —— 唯一的裁判

在 DX 里"看着连上了"完全不可信。**唯一可靠的验收方式是导出网表看表。**

```bash
# 1) 先关 DX（数据库单写者）：SaveAll() -> CloseProject() -> Quit()
# 2) 导出（用同级技能 ee-icdb-export，输出目录必须是空的）
python ../ee-icdb-export/scripts/icdb_export.py --prj "<工程>\<设计>.prj" --csv-only --out "<空目录>"
# 3) 一张表看完
python scripts/verify_connectivity.py "<导出目录>"
```

| 表 | 看什么 | 合格标准 |
|---|---|---|
| `database.sht` | 图纸清单 → `Sheet_ID` | 自建图纸要出现在这里 |
| `database.sym` | `Part_Name` / `Part_Number` | 两个都非空 = 完全解析 |
| `database.spr` | 属性号 705 / 706 | 与 `sym` 表互为印证 |
| `database.ppn` | `Pin_Number / Reference_Designator / Flat_Net_Name` | **一条网上 ≥2 个引脚才算连上** |

### 读网表时的四个陷阱

1. **`Sheet_ID` 会变**。加一张新图纸，其他图纸的 `Sheet_ID` 可能整体后移
   （实测 AI_SCHEMATIC 从 14 变成 15）。**每次用 `database.sht` 现查映射。**
2. **`IVdComp.Refdes` 读空 ≠ 器件有问题**。真图 `06_LED` 上也有若干 `refdes=''`。
   不要用它当健康检查。
3. **符号缺 `REFDES=` 的器件会被打包器静默丢弃**（不报错，只是查不到）。
4. **孤立图纸不进网表**。没被设计引用的图纸不会出现在 `database.sht` 里。

### COM 侧读网表的尝试：都不可用

| 接口 | 实测 |
|---|---|
| `IVdNet.Connections(PinNameFilter)` | `类型不匹配`（参数 1） |
| `IVdNet.GetConnectedNetName(Segment)` | `类型不匹配`（要真 Segment 对象） |
| `IVdComp.GetConnections()` | 无参报 `非选择性的参数`；传 `1` 返回对象但读不出 `Count` |
| `IVdBlock.GetPackagedName(GraphicalName, ObjectType)` | `非选择性的参数` |
| `IVdComp.Refdes` / `UID` / `Id` / `GetName(0..3)` | ✅ 可用（`GetName` 恒返回实例名 `<AUTO_NET>`） |
| `IVdComp.GetBboxPoint(0..3)` | ✅ 可用（`.X` / `.Y`） |
| `IVdComp.GetBatchAttributes` / `GetBatchOats` | ✅ 可用（**是属性，不加括号**） |
| `IVdComp.FindAttribute(name)` / `.Attributes` | ✅ 可用 |
| `IVdComp.AddAttribute(str, x, y, vis)` | ✅ **可用（唯一的属性写入口）** |
| `IVdComp.AddBatchAttributes(str)` | ⚠️ 返回 `True` 但实测无效 |
| `IVdBlock.SetAttributeValue(...)` | ❌ 已作废（error 670） |

### `GetJointLocs` 不能用来判断"有没有引脚"

`view.GetJointLocs(...)` 返回接点坐标串，看着像引脚位置——**但它不是判据**：

- 真图 `03_CPU`：416 个接点（type1=61 / type2=310 / type3=45）
- 成果图：12 个接点，**恰好就是若干 wire 的端点**
- 用 `AddPartInstance` 新放器件后，接点数 **+0**

而新放器件**在网表里是合法的、带编号引脚的连接**。
→ **接点计数反映的是 wire 结构，不是器件引脚。判据只能用网表。**

---

## 7. 一句话总结能力边界

| 能力 | 状态 |
|---|---|
| 打开工程/图纸、读结构、读纸张尺寸、读器件包围盒 | ✅ |
| **直接调用中心库元器件（`AddPartInstance` + 料号）→ 完整料号 + 引脚名** | ✅ **主力方案** |
| 画电气网络（`AddNet`，必须传 NULL IDispatch） | ✅ |
| 写任意属性（`AddAttribute("名=值", x, y, vis)`） | ✅ |
| 改纸张尺寸、存盘 | ✅ |
| 从中心库取符号、放符号图形、画图形线/文字/圆/弧 | ✅（不参与电气） |
| 复制现成真元件（`BufferCopy` / `BufferPasteXY`） | ✅（非必需；粘贴是相对平移） |
| 让器件出正式 BOM（Packager） | ✅（料号齐全即已解析，不再需要 GUI 补） |
| **PCB 侧 `Cell Name`（属性 697）** | ⚠️ **AI 放的件一律不带**，见 §3.5 |
| 单器件补位号 | ✅ `AddAttribute("Ref Designator=Q?")` |
| **批量**编位号 | ❌ 会重编号整个设计（含真图），禁用于生产工程 |
| 用 COM 读引脚号 / 读网络名 / 读打包名 | ❌ 调不通，`GetPinNumbers` 还会崩 DX |
| 验收连接是否成立 | ✅ 只能靠 `icdb2csv` 导出的 `database.ppn` |
| GUI 的新建工程向导 | ❌ 见 `new-project-failure.md` |
