# -*- coding: utf-8 -*-
"""
dxd.py -- a small, late-bound COM driver for Mentor/Siemens DxDesigner (ViewDraw).

Why late bound: the typelib-bound wrapper for IVdDocs.Open() declares ONE
argument, but the runtime accepts (name, page).  Early binding therefore
rejects the call before it ever reaches DxDesigner.  Everything here goes
through win32com.client.dynamic so argument counts are not policed locally.

Verified live on EE 7.9.5 (Application.Version == '2007.12.0').

Requires: pywin32, an INTERACTIVE desktop session, and a licence.
Not usable over plain SSH (no desktop session -> COM automation unavailable).
"""
import time

import pythoncom
import win32com.client as wc

PROGID = "ViewDraw.Application"
CLSID = "{EA4ABD70-84B0-11CE-8237-00001B4D36B5}"

# ---- VdObjectTypeMask (for View.Query) ------------------------------------
VDM_LINE = 1
VDM_BOX = 2
VDM_TEXT = 4
VDM_CIRCLE = 8
VDM_ARC = 16
VDM_NET = 32
VDM_ATTR = 64
VDM_COMP = 128
VDM_LABEL = 256
VDM_PIN = 512
VDM_OAT = 1024
VDM_BLOCK = 2048
VDM_COMPPIN = 4096
VDM_SEGMENT = 8192
VDM_ALL = 16384

# ---- VdAllOrSelected (2nd arg of View.Query) ------------------------------
VD_ALL = 0
VD_SELECTED = 1

# ---- VdOpenMode ----------------------------------------------------------
VDM_READ_WRITE = 0
VDM_READ_LOCK = 1
VDM_READ_ONLY = 2

# ---- VdNameType (View/Block .GetName(flag)) ------------------------------
FULL_PATH_NAME = 0
SHORT_NAME = 1
FULL_PATH_FROM_BLOCK = 2
ICDB_FULL_PATH_NAME = 3


def connect(visible=True, silent=True):
    """Attach to a running DxDesigner, or start one. Always returns a
    late-bound object.

    Returns (app, created) -- created is True when we started the process and
    are therefore responsible for quitting it.
    """
    pythoncom.CoInitialize()
    app, created = None, False
    try:
        app = wc.GetActiveObject(PROGID)
        # GetActiveObject hands back a typelib-bound wrapper; re-wrap it.
        app = wc.dynamic.Dispatch(app._oleobj_)
    except Exception:
        app = wc.dynamic.Dispatch(PROGID)
        created = True
    if visible:
        try:
            app.Visible = True
        except Exception:
            pass
    if silent:
        try:
            app.SilentMode = 1
        except Exception:
            pass
    return app, created


def open_project(app, prj):
    """Open a project (.prj).  Returns True on success.
    Raises nothing: inspect CurrentProject afterwards if you need proof."""
    t0 = time.time()
    ok = app.OpenProject(prj)
    return ok, time.time() - t0


def as_list(x, limit=200):
    """COM collection / array / IStringList -> list[str]."""
    out = []
    if x is None:
        return out
    for probe in (lambda: [str(x.Item(i)) for i in range(1, x.Count + 1)],
                  lambda: [str(v) for v in x],
                  lambda: [str(x[i]) for i in range(len(x))]):
        try:
            out = probe()[:limit]
            if out:
                return out
        except Exception:
            continue
    return out


def list_schematics(app):
    """Names of the schematics the open project offers."""
    ssd = app.SchematicSheetDocuments()          # NOTE: it is a METHOD
    return as_list(ssd.GetAvailableSchematics())


def list_sheets(app, schematic):
    """Sheet names of one schematic."""
    ssd = app.SchematicSheetDocuments()
    return as_list(ssd.GetAvailableSheets(schematic))


def open_sheet(app, schematic, sheet):
    """Open a sheet and return (doc, view, block).

    ⚠⚠ 第二个参数是【图纸名】，不是页码！
    取值必须来自 list_sheets()（如 '01_Power Tree'、'03_CPU'）。
    传页码（'1'、'2'…）或不存在的名字**不会报错**，DX 会静默新建一张
    同名的空白图纸，于是你画的全都落在一张没人会打开的空页上。
    2026-09-18 实测踩过：传 '1' 后 sheets 列表多出一项 '1'，画了 29 个对象，
    在真实图纸（01_Power Tree 131 个对象）上什么都看不到。

    接口走 SchematicSheetDocuments.Open(原理图, 图纸名)；doc.Activate() 会把
    DxDesigner 窗口切到那张图上（想让人看到成果就靠它）。
    """
    ssd = app.SchematicSheetDocuments()
    doc = ssd.Open(schematic, str(sheet))
    doc.Activate()
    view = app.ActiveView
    block = view.Block if view is not None else None
    return doc, view, block


def census(view):
    """Count objects by type -- the reliable way to prove a write landed.

    Query(flags, selected) is the only whole-sheet query that works here.
    """
    tags = (("ALL", VDM_ALL), ("TEXT", VDM_TEXT), ("LINE", VDM_LINE),
            ("BOX", VDM_BOX), ("CIRCLE", VDM_CIRCLE), ("ARC", VDM_ARC),
            ("COMPONENT", VDM_COMP), ("NET", VDM_NET), ("PIN", VDM_PIN),
            ("ATTR", VDM_ATTR), ("LABEL", VDM_LABEL), ("OAT", VDM_OAT))
    res = {}
    for tag, mask in tags:
        try:
            o = view.Query(mask, VD_ALL)
            res[tag] = o.Count if o is not None else 0
        except Exception:
            res[tag] = None
    return res


def delta(before, after):
    """{tag: after-before} for the tags that changed."""
    out = {}
    for k in before:
        b, a = before.get(k), after.get(k)
        if b is None or a is None:
            continue
        if a != b:
            out[k] = a - b
    return out


def save(app):
    """Persist.  NOTE: doc.Save() and app.Documents.SaveAll() differ --
    use SaveAll().  doc.Save() is obsolete on 7.9.5 (error 670)."""
    app.Documents.SaveAll()


def close(app, created):
    """Close the project; quit only if we started the process."""
    try:
        app.CloseProject()
    except Exception:
        pass
    if created:
        try:
            app.Quit()
        except Exception:
            pass


# --------------------------------------------------------------------------
# write helpers -- every one of these was verified live on 7.9.5
# --------------------------------------------------------------------------

def null_idispatch():
    """AddNet 第 5/6 参必须传「类型是 IDispatch、值是 NULL」的 VARIANT。
    传 None / pythoncom.Empty / '' / 0 都会报"类型不匹配"(参数 5)。
    导入放函数里，这样没装 pywin32 时也能 import dxd 做只读分析。"""
    import pythoncom
    from win32com.client import VARIANT
    return VARIANT(pythoncom.VT_DISPATCH, None)


def add_text(block, text, x, y):
    return block.AddText(text, x, y)


def add_line(block, x1, y1, x2, y2):
    return block.AddLine2(x1, y1, x2, y2)


def add_circle(block, x, y, r):
    return block.AddCircle(x, y, r)


def add_arc(block, x1, y1, x2, y2, x3, y3):
    return block.AddArc(x1, y1, x2, y2, x3, y3)


def add_net(block, x1, y1, x2, y2, is_bus=0):
    """画一条真正产生 NET 的网络线（AddLine2 只画图形线，不产生 NET）。

    连线是**几何的**：端点落在器件引脚坐标上就算接上，所以坐标必须算准
    （世界坐标 = 放置点 + 符号 .1 文件里 P 行的本地坐标）。
    第 5/6 参（CompPin1/CompPin2）传 NULL IDispatch 即可；
    ⚠ 绝不能把 comp.GetConnections(n) 的返回值传进去 —— 会把 DX 打崩。
    """
    return block.AddNet(x1, y1, x2, y2, null_idispatch(), null_idispatch(), is_bus)


def place_symbol(block, symbol, x, y, partition="<COMPANY>"):
    """放【符号图形】。⚠ 无器件绑定、引脚没有编号，wire 接不上它。
    只适合画装饰图形，或放**电源符号**（如 temp:3v3 / temp:0vd，
    它们靠符号里的 NETNAME= 给网络命名，本来就不是器件）。

    ⚠ 符号文件里没有 `U ... REFDES=` 行的符号（如 <COMPANY>:2led、
      <COMPANY>:n-channel-mosfet），放出来的东西**连位号都没有**，
      打包时会被整条丢弃。"""
    return block.AddSymbolInstance(partition, symbol, x, y)


def place_part(block, part_number, symbol, x, y, partition="<COMPANY>"):
    """★★ 放件首选 —— 直接从中心库取器件。引脚拿到编号，能被 add_net 接上，
    并且**自动带出完整的器件名 + 料号 + 规格描述**。

        AddPartInstance(PartPartitionName, DeviceName, SymbolName, x, y)
                      └ 符号分区          └ 料号        └ 符号名

    ★ 第 2 参必须传【料号】(Part Number)，不是器件名。
      引擎拿它去中心库器件库 (PartsDBLibs\\*.pdb) 查**唯一记录**，命中后自动
      填好 Part Name / Part Number / Part Label。等价于 GUI 里"从库里选器件"。

    - 料号是唯一键：`<PART_NO>` 全库只出现 1 次；
      **器件名不是键**：`<PART_NAME>` 在 RESISTOR.pdb 里出现 1213 次 → 查不到。
    - 传器件名的后果 = **半绑定**：网络能用（引脚有编号），但导出网表里
      `Part_Name` 为空、器件名被塞进 `Part_Number`。做 BOM 不合格。
    - 第 1 参是【符号库分区】（引擎拼 `<分区>/sym/<符号>.1`）。
      传器件库分区名（`RESISTOR`）会报 `Symbol RESISTOR:res01.1 not found`。
      类型库里参数名写作 `PartPartitionName` 是**误导**。
    - 器件库分区与符号库分区是**两套独立命名空间**：器件 `<PART_NO>`
      住在 `IC.pdb`，符号却是 `IC:<SYMBOL>`；电阻 SymbolLibs 里在
      `<COMPANY>`，器件记录在 `RESISTOR.pdb`。

    实测（2026-09-18）：8/8 全部完整解析 ——
        place_part(block, "<PART_NO>",   "res01", 1000, 1300)
        place_part(block, "<PART_NO>", "cap01", 1300, 1300)
        add_net(block, 1050, 1300, 1300, 1300)
      -> database.sym: Part_Name=<PART_NAME> / Part_Number=<PART_NO>
      -> database.ppn: `2 R? $14Nxxx` + `1 C? $14Nxxx`（真正的两点连接）
    """
    return block.AddPartInstance(partition, part_number, symbol, x, y)


def add_attribute(comp, name, value, x=0, y=0, visibility=0):
    """★ 唯一可用的属性写入口。

        IVdComp.AddAttribute(String, X, Y, Visibility)   String = "属性名=值"

    支持带空格的属性名（`Part Name`、`Ref Designator`）。

    实测有效的用法 —— 给缺 `REFDES=` 定义的符号补位号（否则打包时被丢弃）：
        c = comp_at(view, 990, 1390)
        add_attribute(c, "Ref Designator", "Q?")

    ❌ 别用这几个（2026-09-18 实测）：
      - `comp.Refdes = "Q?"`            -> Property can not be set（只读）
      - `comp.AddBatchAttributes(...)`  -> 返回 True 但完全无效；用 \\n 分隔会被当成
                                           一个属性名，多词名会被截断成 `Name=`
      - `block.SetAttributeValue(...)`  -> error 670「此方法/特性已作废」
    """
    return comp.AddAttribute("%s=%s" % (name, value), x, y, visibility)


def get_attributes(comp):
    """读回实例的全部属性。

    ⚠ GetBatchAttributes 是**属性**不是方法 —— 加括号会报
      `'str' object is not callable`。
    返回格式：`<flag> <名>=<值>\\r`，flag 3 = 图上可见，0 = 隐藏。
    """
    try:
        return comp.GetBatchAttributes
    except Exception:
        return ""


def comp_at(view, x, y, tol=6, mask=None):
    """按 bbox 左下角坐标找器件（GetBboxPoint(0) 的 .X/.Y）。找不到返回 None。
    给"没有位号可依据"的器件定位用。"""
    o = view.Query(mask if mask is not None else VDM_COMP, VD_ALL)
    n = int(o.Count or 0)
    for i in range(1, n + 1):
        try:
            c = wc.dynamic.Dispatch(o.Item(i)._oleobj_)
            b = c.GetBboxPoint(0)
        except Exception:
            continue
        if abs(b.X - x) <= tol and abs(b.Y - y) <= tol:
            return c
    return None


def set_attr(block, obj_name, obj_type, attr, value, is_oat=False):
    """❌ 实测已作废：`error 670 此方法/特性已作废`。
    写属性请改用 add_attribute(comp, name, value)。保留仅为记录结论。"""
    return block.SetAttributeValue(obj_name, obj_type, attr, value, is_oat)


def promote_refdes(block, selected_only=False, slot=0):
    """☠️ 批量编位号 —— 会把【整个设计】重编号（含 1~12 页真图上的手工位号）。
    对生产工程**禁用**。要给单个器件补位号，用 add_attribute(c,"Ref Designator","Q?")。"""
    return block.PromoteSymbolNumbers(selected_only, slot)


def query_objects(view, mask=VDM_ALL, selected=VD_ALL):
    return view.Query(mask, selected)


def verify_netlist_hint(prj, out):
    """打印验收命令 —— NET 计数不能证明"连上了"，只有导出的网表能。"""
    return (
        'NET 计数 != 连上了。验收：\n'
        '  1) SaveAll -> CloseProject -> Quit（iCDB 是单写者）\n'
        '  2) python ../ee-icdb-export/scripts/icdb_export.py --prj "%s" --csv-only --out "%s"\n'
        '  3) 看 %s\\work\\database.ppn：一条网上出现 >=2 个引脚才算连上\n'
        '     awk -F"\\t" \'$4=="<网名>" {print $2,$3,$4}\' database.ppn'
        % (prj, out, out)
    )

