#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Terminal prompts, checklists and candidate scanning.

Kept apart from icdb_export.py so the CLI stays readable: a raw-key
checkbox reader and a multi-select prompt are a lot of code that has
nothing to do with exporting EE data.
"""

from __future__ import print_function

import os
import re
import sys

from ee_env import out, project_detail, project_label, scan_projects

#: Returned by :func:`_ask` when the user aborts (Ctrl-C / EOF).
CANCEL = object()


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


#: What a comparison can do, as data: ``(id, label, detail, default_on)``.
#:
#: Kept as a table rather than baked into the wizard so the same list can be
#: rendered three ways -- as a terminal checkbox, as JSON for a host AI to
#: put in its own multi-select popup, or mapped back from command-line flags.
#: A host that drives this tool should offer these as checkboxes instead of
#: picking a default on the user's behalf: which layers matter depends on the
#: ECO under review.
DIFF_OPTIONS = (
    ("bom", "元件级 BOM 对比",
     "换料 / 改值 / 新增 / 删除，以位号为主键", True),
    ("net", "网络级对比",
     "引脚改接 / 网络增删 / 构成变化，看连接有没有动", True),
    ("html", "网页报告",
     "单文件自包含，浏览器直接打开，按钮切页，可打印", True),
    ("excel", "Excel 报表",
     "一个文件多张工作表，每类差异一张；双击即开，可筛选可透视", True),
    ("csv", "CSV 长表",
     "单张纯文本表，供脚本或程序读取；给人看请用 Excel 报表", False),
)


#: Option ids that choose the comparison layer, versus the report format.
DIFF_SCOPE_IDS = ("bom", "net")


DIFF_FORMAT_IDS = ("html", "excel", "csv")


def options_to_flags(chosen):
    """Map a set of option ids to the :func:`cmd_diff` arguments.

    Returns ``(scope, want_html, want_excel, want_csv)``.
    """
    if all(i in chosen for i in DIFF_SCOPE_IDS):
        scope = "all"
    elif "net" in chosen:
        scope = "net"
    else:
        scope = "bom"
    return (scope, "html" in chosen, "excel" in chosen, "csv" in chosen)


def diff_option_payload():
    """The checkbox list as JSON, for a host AI's own selection UI."""
    return {
        "task": "diff",
        "question": "要对比哪些内容、要哪些格式的报告？",
        "multi": True,
        "hint": "默认全选。只勾「网络级对比」则不做 BOM 比对，反之亦然。",
        "options": [{"id": oid, "label": label, "detail": detail,
                     "default": on} for oid, label, detail, on in DIFF_OPTIONS],
        "maps_to": {
            "scope": "--scope bom|net|all   (由 bom / net 两项推出)",
            "html": "--no-html             (未勾 html 时加此参数)",
            "excel": "--no-excel            (未勾 excel 时加此参数)",
            "csv": "--csv                  (仅在勾了 csv 时加此参数)",
        },
    }


def _raw_key_reader():
    """A callable returning one key token per press, or None if unsupported.

    Tokens: up / down / space / enter / esc / cancel / a / n, or the literal
    lower-cased character. Two implementations because there is no portable
    way to read a key without waiting for Enter in the standard library.
    """
    try:
        import msvcrt
    except ImportError:
        msvcrt = None

    if msvcrt is not None:
        def read_windows():
            ch = msvcrt.getch()
            if ch in (b"\x00", b"\xe0"):       # arrow-key prefix
                nxt = msvcrt.getch()
                return {b"H": "up", b"P": "down"}.get(nxt, "")
            code = ch[0] if ch else 0
            if code in (13, 10):
                return "enter"
            if code == 32:
                return "space"
            if code == 27:
                return "esc"
            if code == 3:
                return "cancel"
            try:
                return ch.decode("latin-1").lower()
            except Exception:
                return ""
        return read_windows

    try:
        import termios
        import tty
    except ImportError:
        return None

    def read_posix():
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            ch = os.read(fd, 1).decode("latin-1", "replace")
            if ch == "\x1b":
                nxt = os.read(fd, 1).decode("latin-1", "replace")
                if nxt == "[":
                    third = os.read(fd, 1).decode("latin-1", "replace")
                    return {"A": "up", "B": "down"}.get(third, "esc")
                return "esc"
        except Exception:
            return "esc"
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        if ch in ("\r", "\n"):
            return "enter"
        if ch == " ":
            return "space"
        if ch == "\x03":
            return "cancel"
        return ch.lower()
    return read_posix


def _render_checklist(question, options, picked, cursor):
    """The block of lines a checkbox prompt paints."""
    lines = ["", "  %s" % question]
    for i, (oid, label, detail, _d) in enumerate(options):
        lines.append("   %s [%s] %-16s %s"
                     % (">" if i == cursor else " ", "x" if oid in picked else " ",
                        label, detail))
    lines.append("")
    lines.append("   空格=勾选/取消   ↑↓=移动   a=全选   n=全不选   "
                 "Enter=确认   Esc=取消")
    return lines


def _ask_multi(question, options, defaults=None):
    """Checkbox multi-select over *options*. Returns a set of ids, or CANCEL.

    A real console gets a real checkbox -- arrow keys move, space toggles,
    Enter confirms, and the block is repainted in place. When stdin is not a
    terminal, which is the normal case when an AI host drives this tool,
    there is nothing to repaint, so the list is printed once and
    comma-separated numbers are accepted instead. Both routes return the
    same set, so callers do not branch on how the answer arrived.
    """
    if defaults is None:
        picked = set(o[0] for o in options if o[3])
    else:
        picked = set(o[0] for o in options if o[0] in defaults)

    interactive = False
    reader = None
    if sys.stdin.isatty() and sys.stdout.isatty():
        reader = _raw_key_reader()
        interactive = reader is not None

    if not interactive:
        for i, (oid, label, detail, _d) in enumerate(options, 1):
            out("   [%s] %d) %-16s %s"
                % ("x" if oid in picked else " ", i, label, detail))
        raw = _ask("  勾选编号（逗号分隔，回车=按默认）")
        if raw is CANCEL:
            return CANCEL
        if not raw:
            return picked
        picked = set()
        for part in re.split(r"[,\s，、/]+", str(raw)):
            if part.isdigit() and 1 <= int(part) <= len(options):
                picked.add(options[int(part) - 1][0])
        return picked

    cursor = 0
    holder = {"n": 0}

    def paint(lines):
        if holder["n"]:
            sys.stdout.write("\x1b[%dA" % holder["n"])
        for ln in lines:
            sys.stdout.write("\x1b[2K" + ln + "\n")
        holder["n"] = len(lines)
        sys.stdout.flush()

    paint(_render_checklist(question, options, picked, cursor))
    while True:
        key = reader()
        if key == "up":
            cursor = (cursor - 1) % len(options)
        elif key == "down":
            cursor = (cursor + 1) % len(options)
        elif key == "space":
            oid = options[cursor][0]
            picked.symmetric_difference_update([oid])
        elif key == "a":
            picked = set(o[0] for o in options)
        elif key == "n":
            picked = set()
        elif key == "enter":
            break
        elif key in ("esc", "cancel"):
            sys.stdout.write("\n")
            sys.stdout.flush()
            return CANCEL
        else:
            continue
        paint(_render_checklist(question, options, picked, cursor))

    ticked = sorted(o[1] for o in options if o[0] in picked)
    sys.stdout.write("   -> %s\n" % ("、".join(ticked) if ticked else "(全不选)"))
    sys.stdout.flush()
    return picked


def scan_diff_candidates(root, max_depth=5):
    """Find everything comparable under *root*.

    Either side of a comparison may be an EE project still to be exported or
    a folder that already holds exported tables, and they differ in an
    important way the user should get to choose: the project path re-exports
    (writing icdb.dat), the export path is read-only. So both are offered
    rather than the tool guessing.
    """
    found = []
    project_dirs = set()

    for proj in scan_projects(root, max_depth=max_depth):
        project_dirs.add(os.path.normcase(proj["project_dir"]))
        found.append({
            "kind": "project",
            "label": project_label(proj),
            "path": proj["prj"],
            "detail": project_detail(proj),
            "usable": proj["icdb_exists"],
            "why": "" if proj["icdb_exists"]
                   else "iCDB 目录不存在: %s" % proj["icdb_dir"],
        })

    root = os.path.abspath(root)
    base = root.rstrip("\\/").count(os.sep)
    for dirpath, dirnames, _files in os.walk(root):
        if dirpath.rstrip("\\/").count(os.sep) - base >= max_depth:
            dirnames[:] = []
            continue
        if "work" not in dirnames:
            continue
        if not os.path.isfile(os.path.join(dirpath, "work", "database.sym")):
            continue
        dirnames[:] = [d for d in dirnames if d != "work"]
        parent = os.path.dirname(dirpath)
        name = os.path.basename(dirpath.rstrip("\\/"))
        if os.path.normcase(parent) in project_dirs:
            name = "%s / %s" % (os.path.basename(parent), name)
        found.append({
            "kind": "export", "label": name, "path": dirpath,
            "detail": "已有导出数据（离线直读，不碰 EE）",
            "usable": True, "why": "",
        })
    return found


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
