#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Export Mentor/Siemens Expedition EE (iCDB) data from outside the GUI.

Runs the official Mentor CLI exporter (icdb2csv) against a design's iCDB
directory, then optionally collapses the result into BOM CSVs.

Why this exists
---------------
The obvious command

    icdb2csv.exe -icdb=<dir> -project=<prj> -output=<dir> -offline

fails with "ERROR: no properties found in prp file!" and produces nothing.
The missing piece is ``-properties`` (documented as "created additional
prop_ID on demand"). That flag is the whole trick; everything else here is
robustness around it.

Design notes
------------
* Standard library only -- no pip installs, so any agent can run it.
* No hard-coded install paths. SDD_HOME is discovered from flags, env vars,
  a config file, the Windows registry, or a bounded filesystem scan.
* ``-project=`` is always passed as an ABSOLUTE path. With a bare filename
  the tool only works when cwd happens to be the project directory.
* Success is verified by the existence of ``<out>/work/database.esf``,
  never by the exit code: icdb2csv returns 0 for some hard failures.
* The export rewrites the project's ``icdb.dat`` (session bookkeeping --
  size-stable, exported data unaffected). The file's hash is reported
  before/after so this is never a surprise.

Usage
-----
    python icdb_export.py --prj "<project>/<design>.prj" --bom
    python icdb_export.py --prj "<project>" --out out --bom
    python icdb_export.py --find "E:/designs"     # locate EE projects
    python icdb_export.py --pick "E:/designs"     # candidates as JSON, for a picker
    python icdb_export.py --interactive           # guided, menu-driven run
    python icdb_export.py --doctor                # check the environment

Guided use
----------
Agents should not guess which project to export. ``--find`` / ``--pick``
enumerate the candidates so the agent can ask, and ``--interactive`` is a
terminal wizard for a human driving the tool directly. See SKILL.md
"Guided use" for the protocol.
"""
from __future__ import print_function

import argparse
import collections
import csv
import glob
import hashlib
import io
import os
import re
import shutil
import string
import subprocess
import sys
import time

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------

#: Path of the exporter, relative to SDD_HOME.
EXPORTER_REL = os.path.join("iCDB", "win32", "bin", "icdb2csv.exe")

#: Alternative exporter, useful when icdb2csv cannot be used.
ASCII_REL = os.path.join("iCDB", "win32", "bin", "icdb2ascii.exe")

#: Filename written by a successful export; our success oracle.
SUCCESS_MARKER = os.path.join("work", "database.esf")

#: Errors that a retry will not fix.
FATAL_MARKERS = (
    "no properties found in prp file",
    "access scan fail",
    "is inconsistent",
    "can not create output file",
)

#: Errors that indicate transient contention (another iCDB session).
RETRYABLE_MARKERS = (
    "in use",
    "locked",
    "busy",
    "unable to",
    "cannot open",
    "can not open",
)

#: Config file search order (first readable wins for any given key).
CONFIG_PATHS = (
    os.environ.get("ICDB_EXPORT_CONFIG", ""),
    os.path.join(os.getcwd(), "icdb-export.json"),
    os.path.join(os.path.expanduser("~"), ".icdb-export.json"),
    os.path.join(os.path.expanduser("~"), ".config", "icdb-export", "config.json"),
    os.path.join(os.environ.get("APPDATA", ""), "icdb-export", "config.json"),
)

#: Registry value holding the install root on Windows.
REG_ENV_KEY = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def out(msg=""):
    """Print without ever dying on a non-UTF8 console (cp936, cp1252...)."""
    try:
        print(msg)
    except UnicodeEncodeError:
        enc = getattr(sys.stdout, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc, "replace"))


def load_config():
    """Read the first readable JSON config file. Missing files are fine."""
    import json
    for p in CONFIG_PATHS:
        if p and os.path.isfile(p):
            try:
                with io.open(p, "r", encoding="utf-8-sig") as fh:
                    data = json.load(fh)
                if isinstance(data, dict):
                    data["_source"] = p
                    return data
            except Exception as exc:
                out("  [warn] unreadable config %s: %s" % (p, exc))
    return {}


def sha_short(path):
    """Hash a file, or describe why it could not be read."""
    if not os.path.isfile(path):
        return "missing"
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return "%d bytes/%s" % (os.path.getsize(path), h.hexdigest()[:12])


def looks_like_sdd(path):
    """A directory is an SDD_HOME iff it contains the exporter."""
    if not path:
        return False
    return os.path.isfile(os.path.join(path, EXPORTER_REL))


def candidate_drives():
    """Existing drive roots. stdlib only, so probe letters directly."""
    if os.name != "nt":
        return [os.path.abspath(os.sep)]
    found = []
    for letter in string.ascii_uppercase:
        root = letter + ":\\"
        if os.path.isdir(root):
            found.append(root)
    return found


def registry_sdd_home():
    """Read SDD_HOME from the machine environment block. Windows only."""
    if os.name != "nt":
        return None
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, REG_ENV_KEY)
        try:
            val = winreg.QueryValueEx(key, "SDD_HOME")[0]
        finally:
            winreg.CloseKey(key)
        val = os.path.expandvars(val)
        return val if looks_like_sdd(val) else None
    except Exception:
        return None


def resolve_sdd_home(cli_value, cfg):
    """Find SDD_HOME. Later sources in the documented order win; every
    candidate is validated by the presence of the exporter."""
    tried = []

    def check(value, label):
        if not value:
            return None
        value = os.path.expandvars(str(value)).rstrip("\\/")
        tried.append((label, value))
        return value if looks_like_sdd(value) else None

    for value, label in (
        (cli_value, "--sdd"),
        (os.environ.get("EE_SDD_HOME"), "env EE_SDD_HOME"),
        (os.environ.get("SDD_HOME"), "env SDD_HOME"),
        (cfg.get("sdd_home"), "config sdd_home"),
        (registry_sdd_home(), "registry SDD_HOME"),
    ):
        hit = check(value, label)
        if hit:
            return hit, tried

    # Bounded filesystem scan: the installer's usual habits.
    patterns = []
    for root in candidate_drives():
        patterns += [
            os.path.join(root, "MentorGraphics", "*EE*", "SDD_HOME"),
            os.path.join(root, "APP", "MentorGraphics", "*EE*", "SDD_HOME"),
            os.path.join(root, "Program Files", "*Mentor*", "*EE*", "SDD_HOME"),
            os.path.join(root, "*Mentor*", "*", "SDD_HOME"),
        ]
    for pat in patterns:
        for hit in glob.glob(pat):
            if looks_like_sdd(hit):
                tried.append(("scan", hit))
                return hit, tried
    return None, tried


def build_env(sdd):
    """Environment for the exporter.

    EE reads a pile of variables that a plain shell may not have. We only
    fill in the ones we can derive, and never override what is already set.
    """
    env = dict(os.environ)
    env.setdefault("SDD_HOME", sdd)
    env.setdefault("SDD_PLATFORM", "win32")

    # VBEST14PATH is referenced by *.prj files but is not always registered.
    env.setdefault("VBEST14PATH", os.path.join(sdd, "standard"))

    # WDIR wants the user dir first, then the shipped standard dir.
    if not env.get("WDIR"):
        install_root = os.path.dirname(os.path.dirname(sdd))
        user_wdir = ""
        for cand in (os.path.join(install_root, "Mentor_WDIR"),
                     os.path.join(os.path.dirname(install_root), "Mentor_WDIR"),
                     os.path.join(os.path.dirname(install_root), "..", "Mentor_WDIR")):
            if os.path.isdir(cand):
                user_wdir = os.path.abspath(cand)
                break
        env["WDIR"] = (user_wdir + os.pathsep if user_wdir else "") + \
                      os.path.join(sdd, "standard")

    # MGC_HOME sits beside SDD_HOME.
    if not env.get("MGC_HOME"):
        cand = os.path.join(os.path.dirname(sdd), "MGC_HOME.ixn")
        if os.path.isdir(cand):
            env["MGC_HOME"] = cand

    # Licence file: probe the conventional spots, do not invent one.
    if not env.get("MGLS_LICENSE_FILE"):
        for cand in (os.path.join(os.environ.get("ProgramData", ""),
                                  "mgc", "win32", "LICENSE.dat.TXT"),
                     r"C:\ProgramData\mgc\win32\LICENSE.dat.TXT"):
            if cand and os.path.isfile(cand):
                env["MGLS_LICENSE_FILE"] = cand
                break
    return env


# --------------------------------------------------------------------------
# project handling
# --------------------------------------------------------------------------

def find_prj(target):
    """Accept a .prj file, a project directory, or a directory containing one."""
    if os.path.isfile(target) and target.lower().endswith(".prj"):
        return os.path.abspath(target)
    if os.path.isdir(target):
        hits = sorted(glob.glob(os.path.join(target, "*.prj")))
        if len(hits) == 1:
            return os.path.abspath(hits[0])
        if len(hits) > 1:
            sys.exit("目录下有多个 .prj，请明确指定其中一个:\n  " +
                     "\n  ".join(hits))
        sub = sorted(glob.glob(os.path.join(target, "*", "*.prj")))
        if sub:
            sys.exit("该目录下没有直接可用的 .prj，找到这些:\n  " +
                     "\n  ".join(sub))
        sys.exit("在 %s 下找不到 .prj" % target)
    sys.exit("路径不存在: %s" % target)


def read_prj(prj_path):
    """Pull the keys we need out of a .prj.

    Format is line oriented: ``KEY <Name> "<value>"``.
    """
    with io.open(prj_path, "r", encoding="latin-1", errors="replace") as fh:
        txt = fh.read()

    def grab(key):
        m = re.search(r'^[ \t]*KEY[ \t]+' + re.escape(key) +
                      r'[ \t]+"?([^"\r\n]*?)"?[ \t]*$', txt, re.M)
        return m.group(1).strip() if m else ""

    return {
        "icdb": grab("iCDBDir") or "database",
        "snap": grab("FrontEndSnapshot"),
        "root": grab("RootBlock"),
        "name": grab("DesignName") or os.path.splitext(os.path.basename(prj_path))[0],
    }


# --------------------------------------------------------------------------
# project discovery -- shared by --find, --pick and --interactive
# --------------------------------------------------------------------------

def project_label(proj):
    """Short name a human can choose from: ``<folder> / <design>``."""
    return "%s / %s" % (os.path.basename(proj["project_dir"]), proj["design"])


def project_detail(proj):
    """One-line summary of a project's export readiness."""
    return "根块=%s  快照=%s  iCDB=%s %s" % (
        proj["root_block"] or "?", proj["snapshot"] or "?",
        os.path.basename(proj["icdb_dir"]) or proj["icdb_dir"],
        "(存在)" if proj["icdb_exists"] else "(**缺失**)")


def scan_projects(target, max_depth=6):
    """Describe every .prj under *target*, well enough to choose between them.

    A project is *usable* when the iCDB directory the .prj declares actually
    exists; without it there is nothing to export.
    """
    target = os.path.abspath(target)
    root_depth = target.rstrip("\\/").count(os.sep)
    found = []
    for dirpath, dirnames, filenames in os.walk(target):
        if dirpath.count(os.sep) - root_depth > max_depth:
            dirnames[:] = []
            continue
        dirnames[:] = [d for d in dirnames
                       if d.lower() not in ("cdbsvr", "cdbback", ".git",
                                            "__pycache__")]
        for fn in sorted(filenames):
            if not fn.lower().endswith(".prj"):
                continue
            prj = os.path.join(dirpath, fn)
            info = read_prj(prj)
            icdb = os.path.join(dirpath, info["icdb"])
            found.append({
                "prj": prj,
                "project_dir": dirpath,
                "design": info["name"],
                "root_block": info["root"],
                "snapshot": info["snap"],
                "icdb_dir": icdb,
                "icdb_exists": os.path.isdir(icdb),
            })
    return found


# --------------------------------------------------------------------------
# the export itself
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# reading the result
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# the whole job -- callable from the CLI and from the interactive wizard
# --------------------------------------------------------------------------

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


# --------------------------------------------------------------------------
# subcommands
# --------------------------------------------------------------------------

def cmd_doctor(sdd, tried, cfg):
    out("环境自检")
    out("=" * 62)
    out("  Python      : %s (%d-bit)" % (sys.version.split()[0],
                                        64 if sys.maxsize > 2 ** 32 else 32))
    out("  平台        : %s" % sys.platform)
    out("  SDD_HOME    : %s" % (sdd or "** 未找到 **"))
    if sdd and tried:
        out("  来源        : %s" % tried[-1][0])
    if cfg:
        out("  配置文件    : %s" % cfg.get("_source"))
    if not sdd:
        out("")
        out("  已尝试的候选：")
        for label, value in tried:
            out("    [%s] %s" % (label, value))
        out("")
        out("  未找到 SDD_HOME。用 --sdd <路径> 指定 EE 的 SDD_HOME。")
        out("  SDD_HOME 的特征是里面同时有 iCDB\\ 和 standard\\ 两个目录。")
        return 1
    out("  icdb2csv    : %s"
        % os.path.join(sdd, EXPORTER_REL))
    out("  可用        : %s"
        % ("是" if looks_like_sdd(sdd) else "否"))
    # Show the values actually handed to the exporter, not just the ambient
    # environment -- several of them are derived, and a bare shell has none.
    effective = build_env(sdd)
    for var in ("WDIR", "MGC_HOME", "MGLS_LICENSE_FILE", "VBEST14PATH"):
        out("  %-16s: %s" % (var, effective.get(var) or "(未设置)"))
    out("")
    out("  注意：导出需要 32/64 位均可，本工具是独立 exe，不依赖 COM。")
    return 0


def cmd_find(target, numbered=False, as_json=False):
    """List EE projects under a directory tree."""
    projects = scan_projects(target)
    if as_json:
        import json
        out(json.dumps({"scanned": os.path.abspath(target),
                        "count": len(projects),
                        "projects": projects},
                       ensure_ascii=False, indent=2))
        return 0 if projects else 1

    out("扫描 EE 工程: %s" % os.path.abspath(target))
    out("=" * 62)
    if not projects:
        out("  未找到 .prj")
        return 1
    for i, proj in enumerate(projects, 1):
        if numbered:
            out("  %d) %s" % (i, proj["prj"]))
        else:
            out("  %s" % proj["prj"])
        out("      %s" % project_detail(proj))
    out("")
    out("  共 %d 个工程（可用 %d）"
        % (len(projects), sum(1 for p in projects if p["icdb_exists"])))
    return 0


def cmd_pick(target):
    """Emit the candidate projects as JSON, ready to become a selection UI.

    Only projects whose iCDB directory exists are offered; the rest come back
    under ``unusable`` with the reason, so the caller can explain the skip
    rather than silently hiding a project the user can see.
    """
    import json
    projects = scan_projects(target)
    usable = [p for p in projects if p["icdb_exists"]]
    choices = []
    for i, proj in enumerate(usable, 1):
        choices.append({
            "index": i,
            "label": project_label(proj),
            "prj": proj["prj"],
            "project_dir": proj["project_dir"],
            "design": proj["design"],
            "root_block": proj["root_block"],
            "snapshot": proj["snapshot"],
            "icdb_dir": proj["icdb_dir"],
        })
    payload = {
        "scanned": os.path.abspath(target),
        "count": len(projects),
        "usable_count": len(usable),
        "choices": choices,
        "unusable": [{"prj": p["prj"],
                      "reason": "iCDB 目录不存在: %s" % p["icdb_dir"]}
                     for p in projects if not p["icdb_exists"]],
    }
    out(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if usable else 1


# --------------------------------------------------------------------------
# interactive wizard -- for a human driving the tool directly
# --------------------------------------------------------------------------

#: Returned by :func:`_ask` when the user aborts (Ctrl-C / EOF).
CANCEL = object()

try:                       # Python 2
    _read_line = raw_input
except NameError:          # Python 3
    _read_line = input


def _ask(prompt, default=None):
    """Prompt for one line. Empty input yields *default*; abort yields CANCEL."""
    shown = " [%s]" % default if default else ""
    try:
        raw = _read_line("%s%s: " % (prompt, shown))
    except (EOFError, KeyboardInterrupt):
        out("")
        return CANCEL
    raw = raw.strip()
    return raw if raw else default


def _ask_choice(question, options, default=1):
    """Numbered menu over a list of ``(label, value)``. Returns the value."""
    out("")
    out("  %s" % question)
    for i, (label, _value) in enumerate(options, 1):
        out("    %d) %s" % (i, label))
    while True:
        sel = _ask("  选择编号", str(default))
        if sel is CANCEL:
            return CANCEL
        if str(sel).isdigit() and 1 <= int(sel) <= len(options):
            return options[int(sel) - 1][1]
        out("    请输入 1-%d" % len(options))


def auto_scan_roots(find_root=None):
    """Directories to try, cheapest first.

    A drive root is deliberately skipped: walking an entire volume to find a
    project costs far more than simply asking for the path.
    """
    if find_root:
        return [os.path.abspath(find_root)]
    roots = []
    for cand in (os.getcwd(), os.path.dirname(os.getcwd())):
        if not cand:
            continue
        cand = os.path.abspath(cand)
        if os.path.dirname(cand) == cand:      # a drive root
            continue
        if cand not in roots:
            roots.append(cand)
    return roots or [os.getcwd()]


def cmd_interactive(sdd, find_root=None):
    """Menu-driven run: pick project -> pick output -> confirm -> export."""
    out("")
    out("EE 工程取数向导")
    out("=" * 62)

    if not sdd:
        out("  [x] 未找到 EE 的 SDD_HOME，无法继续。")
        out("      用 --sdd 指定，或先跑 --doctor 看诊断。")
        return 1

    # ---- 1/4 工程 -------------------------------------------------------
    out("")
    out("[1/4] 选择工程")
    root, projects = None, []
    for cand in auto_scan_roots(find_root):
        found = [p for p in scan_projects(cand) if p["icdb_exists"]]
        out("  扫描 %s -> %d 个可用工程" % (cand, len(found)))
        if found:
            root, projects = cand, found
            break

    while not projects:
        root = _ask("  没找到可用工程。请输入工程所在目录（回车=取消）", None)
        if root is CANCEL or not root:
            out("  已取消。")
            return 1
        if not os.path.isdir(root):
            out("  目录不存在: %s" % root)
            continue
        projects = [p for p in scan_projects(root) if p["icdb_exists"]]
        if not projects:
            out("  该目录下没有可用工程（需同时存在 .prj 与其 iCDB 目录）。")

    if len(projects) == 1:
        chosen = projects[0]
        out("  唯一工程: %s" % project_label(chosen))
    else:
        out("  目录: %s" % root)
        chosen = _ask_choice(
            "选择要导出的工程:",
            [("%s   (%s)" % (project_label(p), project_detail(p)), p)
             for p in projects],
            default=1)
        if chosen is CANCEL:
            out("  已取消。")
            return 1

    # ---- 2/4 内容 -------------------------------------------------------
    out("")
    out("[2/4] 输出内容")
    mode = _ask_choice("要导出什么:", [
        ("原始数据表 + BOM 两份 CSV（推荐）", "bom"),
        ("只要原始数据表（不生成 BOM）", "csv"),
    ], default=1)
    if mode is CANCEL:
        out("  已取消。")
        return 1
    want_bom = (mode == "bom")

    # ---- 3/4 输出目录 ---------------------------------------------------
    out("")
    out("[3/4] 输出目录")
    default_out = os.path.join(chosen["project_dir"], "icdb_export")
    out("  默认: %s" % default_out)
    typed = _ask("  直接回车用默认，或输入其它目录")
    if typed is CANCEL:
        out("  已取消。")
        return 1
    out_dir = (os.path.abspath(os.path.expandvars(os.path.expanduser(typed)))
               if typed else default_out)

    # ---- 4/4 确认 -------------------------------------------------------
    out("")
    out("[4/4] 确认")
    out("  工程    : %s" % chosen["prj"])
    out("  根块    : %s    快照: %s"
        % (chosen["root_block"] or "?", chosen["snapshot"] or "?"))
    out("  内容    : %s" % ("原始数据表 + BOM CSV" if want_bom
                             else "原始数据表"))
    out("  输出    : %s" % out_dir)
    out("")
    out("  注意: 导出会改写工程里的 icdb.dat（会话簿记，数据不受影响）。")
    out("        请先关闭 Expedition EE。EE 自带备份在:")
    out("          %s" % os.path.join(chosen["icdb_dir"], "cdbback"))
    ans = _ask("  开始导出？(Y/n)", "Y")
    if ans is CANCEL or str(ans).strip().lower() not in ("", "y", "yes"):
        out("  已取消。")
        return 1

    out("")
    try:
        rc = run_export(sdd, chosen["prj"], icdb=chosen["icdb_dir"],
                        out_dir=out_dir, bom=want_bom,
                        csv_only=not want_bom)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    if rc == 0:
        out("")
        out("下一步可选：")
        if want_bom:
            out("  · 用 Excel 打开汇总表  %s"
                % os.path.join(out_dir, "BOM_part_summary.csv"))
            out("  · 查看位号明细         %s"
                % os.path.join(out_dir, "BOM_reference_detail.csv"))
        out("  · 原始数据表目录         %s" % os.path.join(out_dir, "work"))
    return rc


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description="Export Expedition EE data via the official iCDB CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n"
               "  %(prog)s --prj \"E:/proj/board.prj\" --bom\n"
               "  %(prog)s --doctor\n"
               "  %(prog)s --find \"E:/designs\" --numbered\n"
               "  %(prog)s --pick \"E:/designs\"          # 候选工程 JSON\n"
               "  %(prog)s --interactive                # 向导模式\n")
    ap.add_argument("--prj", help="工程 .prj，或包含它的目录")
    ap.add_argument("--icdb", help="iCDB 目录（默认取 .prj 的 iCDBDir）")
    ap.add_argument("--out", help="输出目录（默认 <prj 同级>/icdb_export）")
    ap.add_argument("--sdd", help="EE 的 SDD_HOME（默认自动发现）")
    ap.add_argument("--snap", help="快照名（离线模式下无影响，默认取 .prj）")
    ap.add_argument("--bom", action="store_true", help="额外汇总 BOM CSV")
    ap.add_argument("--csv-only", action="store_true",
                    help="只导出原生表，不生成 BOM")
    ap.add_argument("--no-retry", action="store_true", help="失败即退出，不重试")
    ap.add_argument("--doctor", action="store_true", help="环境自检后退出")
    ap.add_argument("--find", metavar="DIR", help="扫描目录下的 EE 工程")
    ap.add_argument("--numbered", action="store_true",
                    help="配合 --find：给候选工程编号，便于用户按号选择")
    ap.add_argument("--json", action="store_true",
                    help="配合 --find：以 JSON 输出")
    ap.add_argument("--pick", metavar="DIR",
                    help="列出可选工程（JSON，仅含可用项，供弹窗选择）")
    ap.add_argument("--interactive", action="store_true",
                    help="交互向导：选工程 → 选内容 → 确认 → 导出")
    ap.add_argument("--find-root", metavar="DIR",
                    help="配合 --interactive：指定初始扫描目录")
    args = ap.parse_args()

    cfg = load_config()
    sdd, tried = resolve_sdd_home(args.sdd, cfg)

    if args.doctor:
        sys.exit(cmd_doctor(sdd, tried, cfg))
    if args.find:
        sys.exit(cmd_find(args.find, numbered=args.numbered, as_json=args.json))
    if args.pick:
        sys.exit(cmd_pick(args.pick))
    if args.interactive:
        sys.exit(cmd_interactive(sdd, find_root=args.find_root))
    if not args.prj:
        ap.error("需要 --prj，或改用 --doctor / --find / --pick / --interactive")
    if not sdd:
        out("未找到 EE 的 SDD_HOME。已尝试：")
        for label, value in tried:
            out("  [%s] %s" % (label, value))
        sys.exit("请用 --sdd 指定，例如 --sdd \"E:\\APP\\MentorGraphics\\7.9.5EE\\SDD_HOME\"")

    sys.exit(run_export(sdd, args.prj, icdb=args.icdb, out_dir=args.out,
                        snap=args.snap, bom=args.bom,
                        csv_only=args.csv_only, no_retry=args.no_retry))


if __name__ == "__main__":
    sys.exit(main())
