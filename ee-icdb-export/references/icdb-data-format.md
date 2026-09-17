# iCDB 导出数据的格式说明

本文件是 `icdb2csv` 产出的**权威列结构**记录，全部字段名逐字来自实测导出
（EE7.9.5，工程 `<PROJECT_ID>`）。要写解析代码时以这里为准，不要凭印象。

## 1. 输出格式声明：`work/database.esf`

导出完成后，`work\database.esf` 是**格式定义与表索引**。它同时是判成功与否的标记文件。

```ini
[System Data]
TextSeparator="none"
FieldDelimit="Tab"
DatabaseName="..\output\database.mds"
SchemaName="vbdc.sch"
Version="2.2"

[Tables]
TInformation=database.inf
TPaths=database.pth
TSymbols=database.sym
TSheets=database.sht
TSymbol_Pins=database.spn
TPhysical_Pins=database.ppn
TSymbol_Properties=database.spr
TSymbol_Pin_Properties=database.ppr
TParts=database.prt
TProperty_Name_Map=database.prm
THier_Net_Properties=database.thn
TFlat_Net_Properties=database.npr
```

三条关键信息：

| 项 | 值 | 含义 |
|---|---|---|
| `TextSeparator` | **`none`** | 字段**不加引号**。值里若含 Tab 会破坏列对齐，解析时按 Tab 硬切即可 |
| `FieldDelimit` | **`Tab`** | Tab 分隔 |
| `DatabaseName` | `..\output\database.mds` | 离线模式下 `output\` 是**空目录**，不要去找这个文件 |

`work\database` 是一个纯索引文件，逐行 `file:database.xxx`，列的是本次产出的表。

## 2. 表清单与列结构

<PROJECT> / `<PROJECT_ID>` 的实测行数一并列出，便于判断导出是否完整。

| 文件 | 行数 | 列 |
|---|---|---|
| `database.sym` | 468 | `Symbol_ID` `Reference_Designator` `Path_ID` `Sheet_ID` `Symbol_Reference` `Part_Name` `Part_Number` `Symbol_Pin_Set_Name` |
| `database.spr` | 4198 | `Symbol_Property_ID` `Symbol_ID` `Property_Number` `Property_Value` |
| `database.prm` | 22 | `Property Number` `Property Name` |
| `database.sht` | 12 | `Sheet_ID` `Sheet_Name` |
| `database.prt` | 114 | `Part_ID` `Part_Number` `Description` `Shape_Name` |
| `database.spn` | 1345 | `Symbol_Pin_ID` `Symbol_ID` `Pin_Name` `Pin_Number` `Flat_Net_Name` `Ace_Pin_Type` `Hier_Net_Name` |
| `database.ppn` | 1345 | `Physical_Pin_ID` `Pin_Number` `Reference_Designator` `Flat_Net_Name` |
| `database.ppr` | 4247 | `Symbol_Pin_Property_ID` `Symbol_Pin_ID` `Property_Number` `Property_Value` |
| `database.npr` | 982 | `Flat_Net_Name` `Property_Name` `Property_Value` `Order_Counter` |
| `database.thn` | 0 | `Hier_Net_Name` `Property_Number` `Property_Value` `Order_Counter` |
| `database.pth` | 1 | `Path_ID` `Path_Name` |
| `database.inf` | 1 | `Design Name` `System Name` `System Version` `System_Date` `Company_Name` … （含**导出时间戳**，不要用它做校验） |

行数不含表头。`database.sym` 首行样例：

```
1  RN05  1  3  $6I4851  <PART_NAME>  <PART_NO>  <COMPANY>:res01
```

注意 `Reference_Designator`、`Part_Name`、`Part_Number` **在 `sym` 里就有**，
只有 Value 之类要走 `spr` + `prm`。`Sheet_ID` 需查 `sht` 才能换成图纸页名。

## 3. 属性号映射：`database.prm`

`database.spr` / `.ppr` 里的 `Property_Number` 是**数字**，必须查 `database.prm`
才知道含义。本设计的完整映射（原文照抄，含 Mentor 内部的 `$$` 私有项）：

| 号 | 名称 | 号 | 名称 |
|---|---|---|---|
| 1 | Pin Name | 12 | **Value** |
| 2 | `$$NotCommonProperty_Pin Type` | 13 | **Ref Designator** |
| 3 | Pin Number | 14 | **Part Name** |
| 4 | Order | 15 | **Cell Name**（封装） |
| 5 | `@SHEETTOTAL` | 16 | `$$NotCommonProperty_PKG_TYPE` |
| 6 | `$$NotCommonProperty_Pin Visibility` | 17 | Level |
| 7 | Instance Name | 18 | PARTS |
| 8 | `$$Internal_Resolution` | 19 | ENGINEER |
| 9 | `$$Internal___BlkDate` | 20 | HETERO |
| 10 | **Part Number** | 21 | **Value1** |
| 11 | Part Label | 22 | `$$NotCommonProperty_PINOFF` |

### 不要写死这些数字

编号是**设计相关**的——由工程自身的属性定义决定，`$$NotCommonProperty_*` 这类
私有项的存在就说明编号会随设计变化，不同 EE 版本也可能不同。

**正确做法**：先读 `database.prm` 建立 `属性号 → 属性名` 表，再按**名称**取号。

```python
name2id = {row[1].strip().lower(): row[0] for row in load("database.prm")[1:]}
value_id = name2id["value"]          # 而不是写 12
```

`scripts/icdb_export.py` 的 `resolve_prop_ids()` 就是这么做的，且对
`Value` / `Value1` 做了精确匹配（用 `"value"` 不会误命中 `"value1"`）。

## 4. 组装 BOM 的连接关系

```
database.sht  ──Sheet_ID──┐
                          ├──> 位号 / 器件名 / 料号 / 图纸页
database.sym  ────────────┘
     │
     │ Symbol_ID
     ▼
database.spr ──Property_Number──> database.prm ──> 属性名  →  Value / Value1 / 封装
     ▲
     │ Symbol_ID
database.spn ── Flat_Net_Name ──> 引脚级网络（元件挂在哪根线上）
   （或 database.ppn，按 Reference_Designator 直连）
```

要点：

- `sym.Part_Number` 已可用，无需经 `prt` 二次查。
- Value 必须走 `spr`；同一 `Symbol_ID` 会有多行（每个属性一行）。
- 网络名以 `$` 开头（如 `$6N548`）代表**未命名/内部网络**，非实际信号名；
  真实信号名如 `XTALO`。
- `npr` 是网络级属性（`FLATNETNAME`、`NETCLASS`、`MAXSTUBLEN` 等），做阻抗/等长规则审查时用得上。

## 5. 备选：纯文本网表 `Netlist.aug`

路径：`<工程>\PCB\Logic\Netlist.aug`。零依赖、纯 ASCII，可被任何程序直接解析，
但**字段少**。

结构（485 行，24 KB）：

| 段 | 内容 |
|---|---|
| `%net` / `%page=<页名>` | `\网络名\ \位号\-\引脚号\ …` |
| `%Part` | 在**文件末尾**：`\料号\ \位号\ …` |

### 三个必须知道的陷阱

1. **`*` 开头是续行标记**，其首字段仍属上一组的料号。
   不合并续行会让料号条目虚高约 4 倍（实测 442 → 实际 114）。
2. **没有 Value / PartName / 封装**。全文搜这些字段 **0 命中**。
   只能得到「料号 ↔ 位号」，出不了完整 BOM。
3. **有陈旧风险**。该文件由 EE 的网表导出动作产生，不在 EE 里导出就不更新。
   交叉验证时对一下同目录 `Integration\`、`PCB\` 的时间戳。

实测覆盖度：114 个料号条目、463 个位号；其中 112 个是规范<COMPANY>料号（覆盖 456 位号），
非料号形态只有 `MARK`（6 个）与 `<PART_NO>`（1 个）；
网络段出现的 457 个元件中**未登记料号的为 0**，即料号覆盖 100%。

> 该文件适合做**交叉校验**（拿它和 iCDB 导出的位号清单对一下），
> 不适合作为主数据源。
