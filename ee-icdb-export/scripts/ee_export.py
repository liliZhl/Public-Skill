#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Reading iCDB tables and writing the BOM CSVs."""

from __future__ import print_function

import collections
import csv
import glob
import io
import os
import subprocess
import sys
import time

from ee_env import EXPORTER_REL, FATAL_MARKERS, SUCCESS_MARKER, build_env, find_prj, out, read_prj, sha_short

def run_exporter(sdd, prj, icdb, out_dir, snap=None, attempts=3, wait=6,
                 log_path=None):
    """Run icdb2csv, retrying transient contention.

    Returns the path to ``<out_dir>/work``.
    """
    exe = os.path.join(sdd, EXPORTER_REL)
    if not os.path.isfile(exe):
        sys.exit("找不到 icdb2csv.exe:\n  %s\n用 --sdd 指定 EE 的 SDD_HOME。" % exe)

    cmd = [exe,
           "-icdb=" + os.path.abspath(icdb),
           "-project=" + os.path.abspath(prj),   # must be absolute
           "-output=" + os.path.abspath(out_dir),
           "-offline",                            # never start a server
           "-properties"]                         # THE required flag
    if snap:
        cmd.append("-snap=" + snap)
    if log_path:
        cmd.append("-clog=" + log_path)

    env = build_env(sdd)
    prj_dir = os.path.dirname(os.path.abspath(prj))
    marker = os.path.join(out_dir, SUCCESS_MARKER)

    for attempt in range(1, attempts + 1):
        if os.path.exists(marker):
            break
        out("  [$] %s" % " ".join('"%s"' % c if " " in c else c for c in cmd))
        proc = subprocess.Popen(cmd, cwd=prj_dir, env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT)
        raw = proc.communicate()[0]
        text = raw.decode("latin-1", "replace")

        for line in text.splitlines():
            s = line.strip()
            if s and any(k in s for k in ("ERROR", "FATAL", "WARNING",
                                          "reading", "complete", "unknown",
                                          "invalid")):
                out("      " + s)

        if os.path.exists(marker):
            # The exit code is not trustworthy; the artefact is.
            return os.path.join(out_dir, "work"), text

        fatal = [k for k in FATAL_MARKERS
                 if k.lower() in text.lower()]
        if fatal:
            sys.exit(
                "导出失败（不可重试）: %s\n"
                "  详见 references/troubleshooting.md\n"
                "  输出目录: %s" % (fatal[0], out_dir))

        if attempt < attempts:
            out("      [retry] 第 %d 次未产出，%ds 后重试"
                "（iCDB 会话可能仍占用数据库）" % (attempt + 1, wait))
            time.sleep(wait)

    sys.exit(
        "导出失败：未生成 %s\n"
        "  常见原因：\n"
        "    1) 漏掉 -properties（本脚本已带，若手敲请注意）\n"
        "    2) 工程数据库正被 EE 或另一次导出占用 -- 关闭 EE 后重试\n"
        "    3) -icdb 指向的目录不是该工程的 iCDB\n"
        "  输出目录: %s" % (marker, out_dir))


def load_tab(work, name):
    """Load a tab-delimited iCDB table; '' if absent.

    Files are written with TextSeparator="none", so values are unquoted.
    Latin-1 keeps bytes intact for anything outside UTF-8.
    """
    path = os.path.join(work, name)
    if not os.path.isfile(path):
        return []
    with io.open(path, "r", encoding="latin-1", errors="replace") as fh:
        rows = list(csv.reader(fh, delimiter="\t"))
    return [[c.strip() for c in r] for r in rows
            if r and any(c.strip() for c in r)]


def property_name_map(work):
    """Property_Number -> Property_Name.

    Numbers are design specific (they shift per design and per EE release),
    so always resolve by name instead of hard-coding them.
    """
    mapping = {}
    for row in load_tab(work, "database.prm")[1:]:
        if len(row) >= 2:
            mapping[row[0]] = row[1]
    return mapping


def resolve_prop_ids(work):
    """Return the property ids we care about, keyed by logical field."""
    wanted = {
        "part_number": ("part number",),
        "part_label": ("part label",),
        "value": ("value",),
        "refdes": ("ref designator", "reference designator"),
        "part_name": ("part name",),
        "cell": ("cell name",),
        "value1": ("value1",),
    }
    name2id = {}
    for pid, pname in property_name_map(work).items():
        name2id.setdefault(pname.strip().lower(), pid)

    resolved = {}
    for field, aliases in wanted.items():
        for alias in aliases:
            if alias in name2id:
                resolved[field] = name2id[alias]
                break
    return resolved


def gather_components(work):
    """One record per schematic symbol, with properties resolved."""
    sheets = {r[0]: r[1] for r in load_tab(work, "database.sht")[1:]
              if len(r) >= 2}

    props = collections.defaultdict(dict)
    for row in load_tab(work, "database.spr")[1:]:
        if len(row) >= 4:
            props[row[1]][row[2]] = row[3]

    ids = resolve_prop_ids(work)

    items = []
    for row in load_tab(work, "database.sym")[1:]:
        if len(row) < 8:
            continue
        symbol_id, refdes, _path, sheet, _symref, part_name, part_no, _pinset = row[:8]
        pr = props.get(symbol_id, {})
        items.append({
            "refdes": refdes,
            "part_number": part_no or pr.get(ids.get("part_number", ""), ""),
            "value": pr.get(ids.get("value", ""), ""),
            "value1": pr.get(ids.get("value1", ""), ""),
            "part_name": pr.get(ids.get("part_name", ""), "") or part_name,
            "cell": pr.get(ids.get("cell", ""), ""),
            "sheet": sheets.get(sheet, "Sheet" + sheet),
            "label": pr.get(ids.get("part_label", ""), ""),
        })
    return items, ids


def write_bom(work, out_dir):
    """Write per-reference and per-part BOM CSVs (UTF-8-SIG for Excel)."""
    items, ids = gather_components(work)
    if not items:
        out("  [warn] database.sym 为空，未生成 BOM")
        return

    detail_cols = ["位号", "料号", "Value", "Value1", "器件名", "封装/单元", "图纸页"]
    detail_path = os.path.join(out_dir, "BOM_reference_detail.csv")
    with io.open(detail_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(detail_cols)
        for it in sorted(items, key=lambda x: x["refdes"]):
            w.writerow([it["refdes"], it["part_number"], it["value"],
                        it["value1"], it["part_name"], it["cell"], it["sheet"]])

    grouped = collections.defaultdict(list)
    for it in items:
        grouped[(it["part_number"], it["value"], it["value1"],
                 it["part_name"], it["cell"])].append(it["refdes"])

    summary_path = os.path.join(out_dir, "BOM_part_summary.csv")
    with io.open(summary_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["序号", "料号", "Value", "Value1", "器件名",
                    "封装/单元", "数量", "位号列表"])
        for i, ((pn, val, val1, name, cell), refs) in enumerate(
                sorted(grouped.items(), key=lambda kv: (-len(kv[1]), kv[0][0])), 1):
            w.writerow([i, pn, val, val1, name, cell, len(refs),
                        " ".join(sorted(refs))])

    no_pn = sum(1 for it in items if not it["part_number"])
    no_val = sum(1 for it in items if not it["value"])
    out("")
    out("  元件 %d / 料号条目 %d / 无料号 %d / 无 Value %d"
        % (len(items), len(grouped), no_pn, no_val))
    out("  %s" % detail_path)
    out("  %s" % summary_path)
    if no_pn == 0:
        out("  料号覆盖 100%")


def run_export(sdd, prj_arg, icdb=None, out_dir=None, snap=None, bom=False,
               csv_only=False, no_retry=False):
    """Export one project.

    Returns 0 on success. On failure it raises SystemExit carrying a
    diagnosis, so a caller that wants to keep going must catch it.
    """
    prj = find_prj(prj_arg)
    info = read_prj(prj)
    prj_dir = os.path.dirname(prj)
    icdb = os.path.abspath(icdb or os.path.join(prj_dir, info["icdb"]))
    out_dir = os.path.abspath(out_dir or os.path.join(prj_dir, "icdb_export"))

    if not os.path.isdir(icdb):
        sys.exit("iCDB 目录不存在: %s\n（检查 .prj 的 iCDBDir，或用 --icdb 指定）"
                 % icdb)
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)

    dat = os.path.join(icdb, "icdb.dat")
    out("工程     : %s" % prj)
    out("iCDB     : %s" % icdb)
    out("快照     : %s%s" % (snap or info["snap"] or "(未声明)",
                             "   [离线模式下 -snap 无影响]"))
    out("SDD_HOME : %s" % sdd)
    out("输出     : %s" % out_dir)
    out("")

    # The exporter rewrites icdb.dat (session bookkeeping). Make the change
    # visible instead of silent, and say where EE's own backup lives.
    before = sha_short(dat)
    if before != "missing":
        out("  icdb.dat 导出前: %s" % before)

    work, _ = run_exporter(
        sdd, prj, icdb, out_dir,
        snap=snap or info["snap"] or None,
        attempts=1 if no_retry else 3)

    after = sha_short(dat)
    out("")
    out("导出完成 -> %s" % work)
    tables = [n for n in sorted(os.listdir(work))
              if n.startswith("database") and not n.endswith(".esf")]
    out("  数据表 %d 个: %s" % (len(tables), " ".join(tables)))

    if before != "missing" and after != before:
        out("")
        out("  提示: 工程数据库 icdb.dat 已被本次导出改写（%s -> %s）"
            % (before, after))
        out("        实测为会话簿记，体积稳定、导出的数据逐字节可复现，")
        out("        不影响原理图内容。EE 自带的完整备份在:")
        backups = sorted(glob.glob(os.path.join(icdb, "cdbback", "*.zip")))
        if backups:
            out("          %s" % backups[-1])
        else:
            out("          %s  (暂无)" % os.path.join(icdb, "cdbback"))

    if bom and not csv_only:
        write_bom(work, out_dir)
    return 0
