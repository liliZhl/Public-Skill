# -*- coding: utf-8 -*-
r"""demo_draw.py -- 从工程挂载的【中央库】画一张【电气合法】的示意原理图。

和上一版的关键差别：放件改用 `AddPartInstance`（带真实器件名），
所以画出来的网络**真的会进网表**，而不是"看着连上了"。

用途：
  1. 自检：验证共享中央库是否可达、能否取出符号与器件、能否产生真实 NET
  2. 演示：--step 1~2.5 时动作之间停顿，操作者能在桌面上**肉眼看到**器件逐个出现
  3. 画图：默认画 +3V3 -> R -> C -> 0VD 一条链路（3 条网络）

用法：
    python demo_draw.py --prj "E:\path\proj.prj"
    python demo_draw.py --prj "E:\path\proj.prj" --new-sheet "99_AI_DEMO" --step 2.5

⚠⚠ 六个会让成果"消失"或"白干"的坑（2026-09-18 全部实测踩过）：

  1. **图纸按【名字】开，不能按页码。** 传 '1' 不报错，DX 会静默新建一张叫 "1" 的
     空图纸，你画的东西全落在那张没人打开的空页上。
  2. **新图纸默认太小。** 默认 `SheetSize=5`，纸只有 **1169 x 826**；真图是
     `SheetSize=3`，纸 **3400 x 2200**。本脚本会显式设成 3。
     纸外的对象照样写进 iCDB、census 照样 +1，但画面上**永远看不到**。
  3. **元件本身只有 20~150 单位大**（res01=50x6、cap01=30x18、2led=110x91）。
     间距用 200~400，别用 2000+。
  4. **放件的第 2 个参数要传【料号】，不是器件名。**
        block.AddPartInstance(符号分区, 料号, 符号名, x, y)
     引擎拿料号去中心库器件库 (`PartsDBLibs\*.pdb`) 查**唯一记录**，命中后自动
     带出 Part_Name + Part_Number + Part_Label（规格描述）。
        · 料号是唯一键：`<PART_NO>` 全库只出现 1 次
        · 器件名不是键：`<PART_NAME>` 在 RESISTOR.pdb 里出现 **1213** 次 -> 查不到
     传器件名不会报错，但只落个**半绑定**：网络能用，导出网表里 Part_Name 为空。
     第 1 参是**符号库分区**（引擎拼 `<分区>/sym/<符号>.1`）；传器件库分区名
     （`RESISTOR`）会报 `Symbol RESISTOR:res01.1 not found`。
  5. **符号文件里没有 `U ... REFDES=` 行的符号，放出来没有位号，打包时被整条丢弃。**
     如 `<COMPANY>:n-channel-mosfet`（而 `res01` 有 `U 18 -4 6 0 1 3 REFDES=R?`）。
     补救：`dxd.add_attribute(comp, "Ref Designator", "Q?")`。
     ☠️ 千**万不要**用 `block.PromoteSymbolNumbers()` —— 它会把整个设计（含真图）
     重编号。
  6. **别用 `GetJointLocs` 接点数判断"有没有引脚"** —— 它只反映 wire 结构，
     电气合法的器件接点数可以是 0。**验收只能导出网表看 `database.ppn`**，
     一条网上出现 >=2 个引脚才算连上（还要看 Pin_Number 非空）。

关键调用（本脚本会用到）：
  * 纸型        `block.SheetSize = 3`
  * **放器件**  `block.AddPartInstance(符号分区, 料号, 符号, x, y)`   ← 完整料号 + 引脚
  * 放电源符号  `block.AddSymbolInstance(符号分区, 符号, x, y)`        ← 靠 NETNAME 命名
  * 补属性      `comp.AddAttribute("Ref Designator=Q?", 0, 0, 0)`      ← 唯一可用写入口
  * **画网络**  `block.AddNet(x1,y1,x2,y2, NULL, NULL, 0)`
                其中 `NULL = VARIANT(pythoncom.VT_DISPATCH, None)` —— 第 5/6 参
                **必须**是"类型正确但值为空"的 IDispatch；传 None/''/0 报"类型不匹配"，
                传 `GetConnections()` 的对象会**把 DxDesigner 打崩**。
  * 引脚坐标    世界坐标 = 放置点 + 符号本地引脚坐标（读中心库 .1 文件的 P 行）
                连线是**几何的**：wire 端点落在引脚坐标上就算接上。

注意：
  * 只在**工程副本**上跑 —— iCDB 图元写入是即时提交的，不 SaveAll 也会进库。
  * 结束后默认不 Quit，窗口留在桌面上（便于观察）；要收紧就加 --quit。
  * 画完**务必导出网表验收**（见脚本末尾提示的命令）。
"""
import argparse
import sys
import time
import traceback

import pythoncom
from win32com.client import VARIANT

import dxd

NULL = VARIANT(pythoncom.VT_DISPATCH, None)      # AddNet 第 5/6 参必须用这个

# 纸型：真图纸张（SheetSize=3）
TRUE_SHEET_SIZE = 3

# 料号来自本工程真实器件（database.sym 的 Part_Number 列）+ 器件库 .pdb 实测：
#   RESISTOR.pdb  : <PART_NO>(47K) / <PART_NO>(0805)
#   CAPACITOR.pdb : <PART_NO>(100nF) / <PART_NO>(10uF)
# 器件名只作参考，不要拿它当查询条件（见脚本头 坑四）。
#
# 引脚本地坐标（读中心库 .1 文件的 P 行）：
#   res01  P1(0,0)  P2(50,0)
#   cap01  P1(0,0)  P2(30,0)
#   3v3    P1(20,0)                       电源符号，NETNAME=+3V3
#   0vd    P1(20,40)                      电源符号，NETNAME=0VD
#
# 摆放：让所有引脚落在 y=1200 这条水平线上 -> 三根 wire 全是直线
#   3v3   @(200,1200) -> pin (220,1200)
#   res01 @(400,1200) -> pin (400,1200) / (450,1200)
#   cap01 @(700,1200) -> pin (700,1200) / (730,1200)
#   0vd   @(900,1160) -> pin (920,1200)
PARTS = [
    ("part",  "<COMPANY>", "<PART_NO>",   "res01",  400, 1200),   # 47K
    ("part",  "<COMPANY>", "<PART_NO>", "cap01",  700, 1200),   # 100nF
    ("power", "temp",     "",                 "3v3",    200, 1200),
    ("power", "temp",     "",                 "0vd",    900, 1160),
]
WIRES = [
    (220, 1200, 400, 1200),      # 3V3.pin  -> R.pin1     => net "+3V3"
    (450, 1200, 700, 1200),      # R.pin2   -> C.pin1     => 自动命名网
    (730, 1200, 920, 1200),      # C.pin2   -> 0VD.pin    => net "0VD"
]
TEXTS = [
    ("AUTO-DRAWN SCHEMATIC  (DxDesigner COM / central library)", 200, 1900),
    ("parts placed by AddPartInstance -> pins have numbers -> real nets", 200, 1800),
    ("paper 3400x2200 (SheetSize=3)   verify: export netlist, read database.ppn",
     200, 1700),
    ("+3V3", 200, 1330), ("R", 400, 1330),
    ("C", 700, 1330), ("0VD", 900, 1310),
]


def main():
    ap = argparse.ArgumentParser(
        description='Draw a demo schematic from the central library.')
    ap.add_argument('--prj', required=True, help='工程 .prj 全路径（务必是副本）')
    ap.add_argument('--sheet', default=None,
                    help='图纸名（来自 GetAvailableSheets）；默认第一张。⚠ 别传页码')
    ap.add_argument('--new-sheet', default=None,
                    help='新建一张同名图纸再画（推荐，最干净）')
    ap.add_argument('--step', type=float, default=0.0,
                    help='每步之间的停顿秒数；0=最快，1~3=肉眼可见（默认 0）')
    ap.add_argument('--quit', action='store_true', help='结束后 Quit（会释放许可）')
    a = ap.parse_args()

    def log(*x):
        print(*x, flush=True)

    app, created = dxd.connect(visible=True, silent=True)
    log('attached  created=%s  Version=%s  Visible=%s'
        % (created, getattr(app, 'Version', '?'), getattr(app, 'Visible', '?')))

    cur = ''
    try:
        cur = str(app.CurrentProject)
    except Exception:
        pass
    if a.prj.replace('/', '\\').lower() not in cur.replace('/', '\\').lower():
        ok, dt = dxd.open_project(app, a.prj)
        log('OpenProject = %s (%.1f s)' % (ok, dt))
        if not ok:
            return 1
    else:
        log('Project already open: %r' % cur)

    pd = app.GetProjectData()
    cl = getattr(pd, 'CentralLibraryPath', None)
    if callable(cl):
        cl = cl()
    log('project      = %s' % pd.GetProjectName())
    log('central lib  = %s' % cl)

    sch = dxd.list_schematics(app)[0]
    sheets = dxd.list_sheets(app, sch)
    log('schematic    = %s' % sch)
    log('sheets(%d)   = %s' % (len(sheets), sheets[:14]))

    target = a.new_sheet or a.sheet or sheets[0]
    doc, view, block = dxd.open_sheet(app, sch, target)
    log('opened       = %r' % doc.Name)

    # ---- 坑二：纸型 ----
    try:
        block.SheetSize = TRUE_SHEET_SIZE
    except Exception as e:
        log('SheetSize 设置失败（%s），后续坐标可能超界' % str(e)[:50])
    p0, p2 = block.GetBboxPoint(0), block.GetBboxPoint(2)
    log('paper        = (%.0f,%.0f) .. (%.0f,%.0f)' % (p0.X, p0.Y, p2.X, p2.Y))

    before = dxd.census(view)
    log('before       = %s' % before)

    fails = 0

    def step(label, fn):
        nonlocal fails
        try:
            fn()
            log('  + %-34s OK' % label)
        except Exception as e:
            fails += 1
            log('  ! %-34s ERR %s: %s' % (label, type(e).__name__, str(e)[:120]))
        if a.step:
            time.sleep(a.step)

    for kind, part, pno, sym, x, y in PARTS:
        if kind == 'part':
            step('AddPartInstance %s:%s (料号=%s)' % (part, sym, pno),
                 lambda part=part, pno=pno, sym=sym, x=x, y=y:
                 block.AddPartInstance(part, pno, sym, x, y))
        else:
            step('AddSymbolInstance %s:%s (power)' % (part, sym),
                 lambda part=part, sym=sym, x=x, y=y:
                 block.AddSymbolInstance(part, sym, x, y))

    for i, (x1, y1, x2, y2) in enumerate(WIRES):
        step('AddNet #%d (%d,%d)-(%d,%d)' % (i + 1, x1, y1, x2, y2),
             lambda x1=x1, y1=y1, x2=x2, y2=y2: block.AddNet(x1, y1, x2, y2, NULL, NULL, 0))

    for t, x, y in TEXTS:
        step('AddText %r' % t[:26], lambda t=t, x=x, y=y: block.AddText(t, x, y))

    try:
        view.Refresh()
    except Exception:
        pass
    after = dxd.census(view)
    log('after        = %s' % after)
    log('delta        = %s' % dxd.delta(before, after))
    log('failures     = %d' % fails)
    log('>>> NET = %s <<<   (=0 说明没有电气网络；真图 03_CPU 是 148)' % after.get('NET'))

    try:
        view.ViewFull()
        log('ViewFull     = OK')
    except Exception as e:
        log('ViewFull err: %s' % str(e)[:100])

    try:
        app.Documents.SaveAll()
        log('SaveAll      = OK')
    except Exception as e:
        log('SaveAll err: %s' % str(e)[:120])

    log('')
    log('== 验收（NET 计数不等于"连上了"，必须导网表）==')
    log('   1) 关 DX:  SaveAll -> CloseProject -> Quit')
    log('   2) python ../ee-icdb-export/scripts/icdb_export.py --prj "<工程>.prj" --csv-only --out "<空目录>"')
    log('   3) python scripts/verify_connectivity.py "<导出目录>"')
    log('   期望 database.sym 里两个件都有 Part_Name + Part_Number（完整料号）：')
    log('        RefDes  Part_Name       Part_Number')
    log('        R?      <PART_NAME>       <PART_NO>')
    log('        C?      CAPC0603X30N    <PART_NO>')
    log('   期望 database.ppn 里每条网 >=2 个**带编号**的引脚：')
    log('        Pin  RefDes  Net')
    log('        2    R?      $15Nxx')
    log('        1    C?      $15Nxx')

    if a.quit:
        dxd.close(app, created)
        log('quit.')
    else:
        try:
            doc.Activate()
        except Exception:
            pass
        log('DONE —— 窗口保持打开，已停在该图纸（用 --quit 可关闭）')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(2)
