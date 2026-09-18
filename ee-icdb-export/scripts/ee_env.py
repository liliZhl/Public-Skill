#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Finding Expedition EE on this machine, and running its CLI.

SDD_HOME discovery and the icdb2csv invocation. Standard library only, no
hard-coded install paths -- see icdb_export.py for the background.
"""

from __future__ import print_function

import csv
import glob
import hashlib
import io
import os
import re
import string
import sys


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
