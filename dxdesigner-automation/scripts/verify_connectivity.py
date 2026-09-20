# -*- coding: utf-8 -*-
r"""verify_connectivity.py -- 从 icdb 导出目录验收"电气连接是否真的成立"。

为什么要单独写这个：在 DxDesigner 里"看着连上了"完全不可信，NET 计数也不可信
（`AddNet` 画一根悬空线也会让 NET +1）。**唯一裁判是导出的引脚级网表。**

它回答三件事：
  1. 图纸有没有被设计收录（不在 database.sht 里的图纸，画了也白画）
  2. 器件有没有绑定（Part_Name / Part_Number 为空 = 符号图形，不是器件）
  3. 网络是不是真的连上了（**一条网上出现 >=2 个引脚才算连上**；只有 1 个 =
     悬空线头，一个引脚都没有 = 空网）

用法：
    # 先导出（导出器在同级技能 ee-icdb-export 里，以本技能根目录为工作目录执行）
    python ../ee-icdb-export/scripts/icdb_export.py --prj "<工程>.prj" --csv-only --out "E:\tmp\exp1"
    # 再验收
    python scripts/verify_connectivity.py "E:\tmp\exp1"
    python verify_connectivity.py "E:\tmp\exp1" --sheet AI_SCHEMATIC
    python verify_connectivity.py "E:\tmp\exp1" --only-issues      # 只看有问题的
    python verify_connectivity.py "E:\tmp\exp1" --json             # 机器可读

只用标准库。不碰 EE、不碰 COM。
"""
import argparse
import json
import os
import sys

TAB = "\t"


def read_table(path):
    """iCDB 导出表是 tab 分隔、带表头。返回 [dict]，键是表头列名。"""
    with open(path, "r", encoding="utf-8-sig", errors="replace") as fh:
        rows = [ln.rstrip("\r\n").split(TAB) for ln in fh if ln.strip()]
    if not rows:
        return [], []
    head = rows[0]
    out = []
    for r in rows[1:]:
        r = r + [""] * (len(head) - len(r))
        out.append(dict(zip(head, r)))
    return head, out


def find_work(root):
    """容错：给导出根目录或 work 目录都行。"""
    for cand in (os.path.join(root, "work"), root):
        if os.path.isfile(os.path.join(cand, "database.ppn")):
            return cand
    return None


def col(d, *names):
    for n in names:
        if n in d:
            return (d.get(n) or "").strip()
    return ""


def main():
    ap = argparse.ArgumentParser(description="验收 DxDesigner 工程的电气连接")
    ap.add_argument("export_dir", help="icdb 导出目录（或它的 work 子目录）")
    ap.add_argument("--sheet", default=None, help="只看某张图纸（名字，如 AI_SCHEMATIC）")
    ap.add_argument("--only-issues", action="store_true", help="只输出有问题的项")
    ap.add_argument("--json", action="store_true", help="以 JSON 输出")
    a = ap.parse_args()

    work = find_work(a.export_dir)
    if not work:
        print("找不到 <dir>/work/database.ppn —— 先跑 icdb_export.py 导出"
              "（同级技能 ee-icdb-export，见 SKILL.md 铁律 6）", file=sys.stderr)
        return 2

    def tbl(name):
        p = os.path.join(work, name)
        return read_table(p) if os.path.isfile(p) else ([], [])

    _, sht = tbl("database.sht")
    _, sym = tbl("database.sym")
    _, ppn = tbl("database.ppn")

    sheet_name = {}
    for r in sht:
        sheet_name[col(r, "Sheet_ID")] = col(r, "Sheet_Name")

    # ---- 器件 ----
    comps = []
    for r in sym:
        sid = col(r, "Sheet_ID")
        comps.append({
            "refdes": col(r, "Reference_Designator"),
            "sheet_id": sid,
            "sheet": sheet_name.get(sid, "?"),
            "symbol": col(r, "Symbol_Reference", "Symbol_Pin_Set_Name"),
            "pin_set": col(r, "Symbol_Pin_Set_Name"),
            "part_name": col(r, "Part_Name"),
            "part_number": col(r, "Part_Number"),
            "bound": bool(col(r, "Part_Name") or col(r, "Part_Number")),
        })

    # ---- 引脚 -> 网络 ----
    pin_by_net = {}
    net_by_refdes = {}
    for r in ppn:
        net = col(r, "Flat_Net_Name")
        rd = col(r, "Reference_Designator")
        pin = col(r, "Pin_Number")
        net_by_refdes.setdefault(rd, []).append({"pin": pin, "net": net})
        if net:
            pin_by_net.setdefault(net, []).append({"refdes": rd, "pin": pin})

    # ---- 判定网络 ----
    #   真正的电气连接需要：>=2 个引脚、落在 >=2 个不同器件上、且引脚有编号。
    #   引脚无编号 => 器件没真正绑定（AddSymbolInstance 或器件名不存在）。
    nets = []
    for name, pins in pin_by_net.items():
        real = len(pins)
        distinct_comp = len({p["refdes"] for p in pins})
        unnamed = sum(1 for p in pins if not p["pin"])
        if real >= 2 and distinct_comp >= 2:
            verdict = "PARTIAL" if unnamed else "OK"
        elif real >= 2:
            verdict = "SAME_COMP"       # 引脚都落在同一个器件上
        elif real == 1:
            verdict = "DANGLING"        # 悬空线头
        else:
            verdict = "EMPTY"
        nets.append({"net": name, "pins": pins, "pin_count": real,
                     "comp_count": distinct_comp, "unnamed_pins": unnamed,
                     "verdict": verdict})

    # ---- 应用 --sheet 过滤 ----
    keep_sheet = a.sheet
    if keep_sheet:
        sid_match = {k for k, v in sheet_name.items() if keep_sheet in v}
        comps = [c for c in comps if c["sheet_id"] in sid_match]
        if not sid_match:
            print("警告：database.sht 里找不到图纸 %r，可选：%s"
                  % (keep_sheet, sorted(sheet_name.values())), file=sys.stderr)

    if a.json:
        print(json.dumps({
            "work_dir": work,
            "sheets": sheet_name,
            "components": comps,
            "nets": nets,
        }, ensure_ascii=False, indent=2))
        return 0

    # ---- 人类可读报告 ----
    print("=" * 78)
    print("导出目录 : %s" % work)
    print("图纸清单 : %d 张" % len(sheet_name))
    for sid in sorted(sheet_name, key=lambda x: int(x) if x.isdigit() else 0):
        print("           Sheet_ID=%-4s %s" % (sid, sheet_name[sid]))

    print("")
    print("=" * 78)
    print("一、器件绑定（Part_Name / Part_Number 都空 = 只是符号图形，不是器件）")
    bad = [c for c in comps if not c["bound"]]
    print("   器件 %d 个，其中未绑定 %d 个" % (len(comps), len(bad)))
    show = bad if a.only_issues else comps
    if show:
        print("   %-8s %-6s %-26s %-20s %s" %
              ("RefDes", "Sheet", "Part_Name", "Part_Number", "符号"))
        for c in show:
            flag = "" if c["bound"] else "   <== 未绑定"
            print("   %-8s %-6s %-26s %-20s %s%s"
                  % (c["refdes"] or "(空)", c["sheet_id"], c["part_name"] or "(空)",
                     c["part_number"] or "(空)", c["symbol"], flag))
    else:
        print("   （全部已绑定）")

    print("")
    print("=" * 78)
    print("二、网络连接（>=2 个引脚、落在 >=2 个器件上、且引脚有编号 = 真的连上了）")
    order = {"OK": 0, "PARTIAL": 1, "SAME_COMP": 2, "DANGLING": 3, "EMPTY": 4}
    nets.sort(key=lambda n: (order.get(n["verdict"], 9), -n["pin_count"], n["net"]))
    ok = [n for n in nets if n["verdict"] == "OK"]
    prob = [n for n in nets if n["verdict"] != "OK"]
    print("   网络 %d 条：合格 %d 条，有问题 %d 条" % (len(nets), len(ok), len(prob)))
    show_nets = prob if a.only_issues else nets
    if show_nets:
        for n in show_nets:
            mark = {"OK": "OK  ", "PARTIAL": "半连", "SAME_COMP": "同件",
                    "DANGLING": "悬空", "EMPTY": "空网"}
            print("   [%-4s] %-22s 引脚%d 器件%d%s"
                  % (mark.get(n["verdict"], "?"), n["net"], n["pin_count"],
                     n["comp_count"],
                     ("  其中 %d 个引脚无编号" % n["unnamed_pins"])
                     if n.get("unnamed_pins") else ""))
            for p in n["pins"]:
                print("            %s . %s" % (p["refdes"] or "(空)", p["pin"] or "(无编号)"))
    else:
        print("   （没有有问题的网络）")

    print("")
    print("=" * 78)
    partial = [n for n in nets if n["verdict"] == "PARTIAL"]
    if ok and not prob:
        print("判决：电气连接全部成立。")
    elif ok:
        print("判决：有 %d 条网真的连上了；另有 %d 条需要处理（见上）。" % (len(ok), len(prob)))
        if partial:
            print("      其中 %d 条是'半连'（网上有 >=2 个引脚但没有引脚编号）——" % len(partial))
            print("      线接上了，器件却没真正绑定：放件时第 2 参传了器件名，应传【料号】。")
    else:
        print("判决：★ 没有任何一条网是真的两点连接 —— 线是画了，但没接上器件。")
        if bad:
            print("      根因很可能是器件未绑定（%d 个 Part_Name/Part_Number 为空）。" % len(bad))
            print("      修法：放件改用 AddPartInstance(符号分区, 料号, 符号, x, y)，")
            print("      第 2 参必须是【料号】（Part Number），传器件名只会得到半绑定；")
            print("      器件名不是唯一键，料号才是——见 references/electrical-connectivity.md §3。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
