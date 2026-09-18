#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The multi-sheet Excel diff report."""

from __future__ import print_function

import os
import re
import sys

# xlsx_writer 就在同目录：.xlsx 是 XML 的 zip，不需要第三方包。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    from xlsx_writer import Sheet, write_workbook
    HAVE_XLSX = True
except ImportError:                 # xlsx_writer.py absent
    Sheet = write_workbook = None
    HAVE_XLSX = False

from ee_diff import DIFF_KINDS, _expand_changes, _key_items, _lead, _norm_bom, _norm_nets, _norm_pins, _sheet_histogram

#: Worksheet names, one sheet per kind of change.
#:
#: The single long table this replaces put all four layers behind a 分类
#: column, which is exactly why it read badly: four incompatible row shapes
#: under one header, so sorting by any column scrambled the others and a
#: filter matched rows from layers nobody was looking at. A sheet per layer
#: keeps each table's columns meaningful on its own.
XLSX_OVERVIEW = "概览"


XLSX_BOM = "元件级差异"


XLSX_PIN = "引脚改接"


XLSX_NET = "网络变化"


XLSX_PART = "料号视角"


#: Change kind -> colour token, matching the HTML badges. Additions read red,
#: removals green; the two kinds that always need a human eye (换料 and
#: 引脚改接) are red as well so they stand out even inside a filtered sheet.
_KIND_TOKEN = {
    "新增元件": "red", "新增接点": "red", "新增网络": "red", "仅新版有": "red",
    "删除元件": "green", "断开接点": "green", "删除网络": "green",
    "仅旧版有": "green",
    "更换料号": "red-b", "引脚改接": "red-b",
    "命名漂移": "grey",
}


def _kind_token(kind):
    """Colour token for a change kind; unknown kinds stay plain bold."""
    return _KIND_TOKEN.get(kind, "b")


def _s(*tokens):
    """Combine style tokens, dropping the empty ones."""
    return ",".join(t for t in tokens if t)


def _plain(text):
    """Strip the inline markup the HTML layer adds."""
    return (re.sub(r"<[^>]+>", "", text or "")
            .replace("&amp;", "&").replace("&lt;", "<")
            .replace("&gt;", ">").strip())


def _change_text(changes):
    """"字段 旧 → 新" for every changed field, joined on one line."""
    parts = []
    for label, old, new in changes:
        seg = "%s → %s" % (old or "(空)", new or "(空)")
        parts.append("%s %s" % (label, seg) if label else seg)
    return "；".join(parts)


def _band(sheet, text):
    """A section heading running across the sheet."""
    cols = max(2, len(sheet.widths))
    sheet.row([(text, "h2")] + [("", "h2")] * (cols - 1))
    return sheet


def _xlsx_overview(result, part_rows, net_result, meta_a, meta_b, scope):
    """First sheet: the numbers, and what to look at, before any detail."""
    stats = result["stats"]
    total = sum(stats[name] for name, _ in DIFF_KINDS)
    do_bom = scope in ("bom", "all")
    do_net = net_result is not None

    sh = Sheet(XLSX_OVERVIEW, widths=[26, 16, 48, 24])
    sh.row([("ECO 差异报告", "title")])
    sh.row([("旧版 " + meta_a["label"], "k"),
            ("%d 元件 / %d 料号" % (meta_a["count"], meta_a["parts"]), ""),
            (meta_a["work"], "grey")])
    sh.row([("新版 " + meta_b["label"], "k"),
            ("%d 元件 / %d 料号" % (meta_b["count"], meta_b["parts"]), ""),
            (meta_b["work"], "grey")])

    lead = _lead(meta_a, meta_b, result, net_result, part_rows, scope)
    if lead:
        sh.blank()
        _band(sh, "结论")
        for line in lead:
            sh.row([("", ""), ("", ""), (_plain(line), "wrap")])

    if do_bom:
        sh.blank()
        _band(sh, "元件级差异")
        sh.row([("类型", "head"), ("数量", "head")])
        for name, shown in DIFF_KINDS:
            sh.row([(shown, _kind_token(shown)), (stats[name], "num")])
        sh.row([("差异合计", "b"), (total, "num,b")])
        sh.row([("完全一致", ""), (stats["unchanged"], "num")])
        if result["no_refdes"]:
            sh.row([("无法比对（无位号）", "grey"),
                    (result["no_refdes"], "num,grey")])
        if part_rows:
            sh.row([("受影响料号", ""), (len(part_rows), "num")])

    if do_net:
        ns = net_result["stats"]
        sh.blank()
        _band(sh, "网络级变更")
        sh.row([("项目", "head"), ("数量", "head")])
        sh.row([("引脚改接", "red-b"), (ns["pin_moved"], "num,b")])
        sh.row([("连接变化合计", "b"), (ns["pin_moved"], "num,b")])
        for key, shown in (("conn_new", "新增接点"), ("conn_lost", "断开接点"),
                           ("net_mod", "网络构成变化"), ("net_ren", "网络改名"),
                           ("net_add", "新增网络"), ("net_del", "删除网络")):
            sh.row([(shown, _kind_token(shown)), (ns[key], "num")])
        if ns["drift"]:
            sh.row([("命名漂移（自动命名网重编号，未计差异）", "grey"),
                    (ns["drift"], "num,grey")])
        sh.row([("网络总数", "grey"),
                ("%d → %d" % (net_result["net_count_a"],
                              net_result["net_count_b"]), "grey")])
        sh.row([("连接点数", "grey"),
                ("%d → %d" % (net_result["pin_count_a"],
                              net_result["pin_count_b"]), "grey")])

    keys = _key_items(result, net_result)
    if keys:
        sh.blank()
        _band(sh, "需人工确认")
        sh.row([("类型", "head"), ("对象", "head"), ("变更", "head"),
                ("位置", "head")])
        for it in keys:
            sh.row([(it["kind"], _kind_token(it["kind"])),
                    (it["obj"], "b"),
                    (_change_text(it["changes"]), ""),
                    (it["where"] or "—", "grey")])

    hist = _sheet_histogram(result) if do_bom else []
    if hist:
        sh.blank()
        _band(sh, "差异按图纸页分布")
        sh.row([("图纸页", "head"), ("处数", "head")])
        for name, count in hist:
            sh.row([(name or "(未标注)", ""), (count, "num")])
    return sh


def _xlsx_bom(result):
    """Component changes. One row per changed field, striped per refdes.

    A reference designator that changed in two ways gets two rows -- that is
    what keeps the 变更项 column meaningful and lets the sheet be filtered to
    "every part-number swap" in one click.
    """
    sh = Sheet(XLSX_BOM, widths=[12, 10, 12, 30, 30, 22],
               freeze=1, autofilter=True)
    sh.row(["差异类型", "位号", "变更项", "旧版本", "新版本", "图纸页"],
           style="head")
    group, last = -1, None
    for d in _norm_bom(result):
        kind = d["kind"]
        if d["refdes"] != last:
            group += 1                 # one stripe per reference designator
            last = d["refdes"]
        zebra = "zebra" if group % 2 else ""
        for i, (label, old, new) in enumerate(_expand_changes(d["changes"])):
            first = (i == 0)
            sh.row([
                (kind if first else "", _s(_kind_token(kind), zebra)),
                (d["refdes"] if first else "", _s("b", zebra)),
                (label, zebra),
                (old or "(空)", _s("grey", zebra)),
                (new or "(空)", zebra),
                (d["sheet"] if first else "", _s("grey", zebra)),
            ])
    if len(sh.rows) == 1:      # header only -- an empty grid reads
        sh.row([("（无差异）", "grey")])   # as a failed report
    return sh


def _xlsx_pins(net_result):
    """One row per pin. A grouped row could not be read across, and its count
    stopped matching the total printed in the overview."""
    sh = Sheet(XLSX_PIN, widths=[12, 10, 10, 28, 28, 34],
               freeze=1, autofilter=True)
    sh.row(["类型", "位号", "引脚", "旧版网络", "新版网络", "说明"],
           style="head")
    for r in _norm_pins(net_result):
        sh.row([(r["kind"], _kind_token(r["kind"])),
                (r["refdes"], "b"), (r["pin"], ""),
                (r["a_net"] or "(无)", "grey"),
                (r["b_net"] or "(无)", ""),
                (r["note"], "grey")])
    if len(sh.rows) == 1:      # header only -- an empty grid reads
        sh.row([("（无差异）", "grey")])   # as a failed report
    return sh


def _xlsx_nets(net_result):
    """The net view: same events seen by net instead of by pin.

    引脚数 stays blank for a net present on one side only -- that net already
    lists every pin it has, so "0 → 2" beside the list says it twice.
    """
    sh = Sheet(XLSX_NET, widths=[26, 14, 10, 10, 40, 40, 30],
               freeze=1, autofilter=True)
    sh.row(["网络", "状态", "旧引脚数", "新引脚数", "移除引脚", "新增引脚",
            "说明"], style="head")
    for r in _norm_nets(net_result):
        sh.row([(r["name"], "b"), (r["status"], _kind_token(r["status"])),
                (r["pin_a"], "num,grey"), (r["pin_b"], "num"),
                (r["dels"], "green"), (r["adds"], "red"),
                (r["note"], "grey")])
    if len(sh.rows) == 1:      # header only -- an empty grid reads
        sh.row([("（无差异）", "grey")])   # as a failed report
    return sh


def _xlsx_parts(part_rows):
    """Material view: which part numbers changed their reference set."""
    sh = Sheet(XLSX_PART, widths=[30, 12, 10, 10, 10, 34, 34],
               freeze=1, autofilter=True)
    sh.row(["料号", "状态", "旧版用量", "新版用量", "变化", "新增位号",
            "移除位号"], style="head")
    for r in part_rows:
        token = "red" if r["delta"] > 0 else ("green" if r["delta"] < 0
                                              else "grey")
        sh.row([(r["pn"], "b"), (r["status"], _kind_token(r["status"])),
                (r["a_qty"], "num,grey"), (r["b_qty"], "num"),
                (r["delta"], _s("num", token)),
                (r["added"], _s("red", "wrap")),
                (r["removed"], _s("green", "wrap"))])
    if len(sh.rows) == 1:      # header only -- an empty grid reads
        sh.row([("（无差异）", "grey")])   # as a failed report
    return sh


def build_diff_workbook(result, part_rows, net_result, meta_a, meta_b,
                        scope="all"):
    """The whole Excel report as a list of worksheets."""
    if not HAVE_XLSX:
        raise RuntimeError("xlsx_writer.py 不在 scripts/ 目录，无法生成 xlsx")
    scope = scope or "all"
    do_bom = scope in ("bom", "all")
    do_net = net_result is not None and scope in ("net", "all")

    sheets = [_xlsx_overview(result, part_rows,
                             net_result if do_net else None,
                             meta_a, meta_b, scope)]
    if do_bom:
        sheets.append(_xlsx_bom(result))
    if do_net:
        sheets.append(_xlsx_pins(net_result))
        sheets.append(_xlsx_nets(net_result))
    if do_bom and part_rows:
        sheets.append(_xlsx_parts(part_rows))
    return sheets


def write_diff_excel(result, part_rows, net_result, out_dir, meta_a, meta_b,
                     scope="all"):
    """Write the .xlsx report and return its path."""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    path = os.path.join(out_dir, "diff_report.xlsx")
    write_workbook(path, build_diff_workbook(result, part_rows, net_result,
                                             meta_a, meta_b, scope),
                   title="ECO 差异报告 %s vs %s"
                         % (meta_a["label"], meta_b["label"]))
    return path
