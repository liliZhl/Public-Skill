#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The flat CSV diff report (one record per changed field)."""

from __future__ import print_function

import csv
import io
import os

from ee_diff import DIFF_REPORT_COLS, _expand_changes, _norm_bom, _norm_nets, _norm_pins

def build_report_rows(result, part_rows, net_result, scope="all"):
    """Every change as flat records, one per changed field.

    All four layers share one shape so a single CSV can hold them and a
    reader filters by 分类 instead of opening a second file. Record keys:
    ``layer`` 分类, ``kind`` 差异类型, ``obj`` 对象, ``label`` 明细,
    ``old``/``new`` 旧版本/新版本, ``note`` 说明.
    """
    rows = []

    def add(layer, kind, obj, fields, note, sheet=""):
        if not fields:
            fields = [("", "", "")]
        for label, old, new in fields:
            rows.append({"layer": layer, "kind": kind, "obj": obj,
                         "label": label, "old": old, "new": new,
                         "note": note, "sheet": sheet})

    if scope in ("bom", "all"):
        for d in _norm_bom(result):
            # _expand_changes splits an added/removed part into one row per
            # field, so the CSV keeps the same row shape as the workbook.
            add("元件", d["kind"], d["refdes"],
                _expand_changes(d["changes"]), d["note"], d["sheet"])

    if net_result is not None and scope in ("net", "all"):
        for r in _norm_pins(net_result):
            add("引脚", r["kind"], "%s.%s" % (r["refdes"], r["pin"]),
                [("所属网络", r["a_net"], r["b_net"])], r["note"])
        for r in _norm_nets(net_result):
            add("网络", r["status"], r["name"], r["fields"], r["note"])

    if scope in ("bom", "all"):
        for r in part_rows:
            detail = []
            if r["added"]:
                detail.append("新增位号 %s" % r["added"])
            if r["removed"]:
                detail.append("移除位号 %s" % r["removed"])
            note = "料号视角；%s；与元件级是同一批变化，不计入元件差异合计" % (
                "；".join(detail) if detail else "位号集合有变化")
            add("料号", r["status"], r["pn"],
                [("用量", "%d" % r["a_qty"], "%d" % r["b_qty"])], note)
    return rows


def write_diff_csv(result, part_rows, net_result, out_dir, meta_a, meta_b,
                   scope="all"):
    """Write the single report CSV (UTF-8-SIG so Excel opens it directly).

    One file, not four. A 分类 column replaces the four separate headers, and
    because rows are one-per-changed-field the file filters and pivots without
    any manual joining.
    """
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    path = os.path.join(out_dir, "diff_report.csv")
    rows = build_report_rows(result, part_rows, net_result, scope)
    with io.open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(DIFF_REPORT_COLS)
        for r in rows:
            w.writerow([r["layer"], r["kind"], r["obj"], r["label"],
                        r["old"], r["new"], r["note"]])
    return path
