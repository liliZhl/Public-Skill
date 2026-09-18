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
    python icdb_export.py --diff "<A>/icdb_export" "<B>/icdb_export"   # ECO diff

Guided use
----------
Agents should not guess which project to export. ``--find`` / ``--pick``
enumerate the candidates so the agent can ask, and ``--interactive`` is a
terminal wizard for a human driving the tool directly. See SKILL.md
"Guided use" for the protocol.
"""

from __future__ import print_function

import argparse
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# 同目录的兄弟模块：入口先把 scripts/ 放进 sys.path，再 import。
from choice_ui import CANCEL, DIFF_OPTIONS, _ask, _ask_choice, _ask_multi, auto_scan_roots, diff_option_payload, options_to_flags, scan_diff_candidates
from ee_diff import DIFF_KINDS, diff_boms, diff_nets, infer_label, is_export_dir, load_diff_side, part_summary
from ee_env import EXPORTER_REL, build_env, load_config, looks_like_sdd, out, project_detail, project_label, resolve_sdd_home, scan_projects
from ee_export import run_export
from report_csv import write_diff_csv
from report_html import build_diff_html, write_diff_html
from report_xlsx import HAVE_XLSX, write_diff_excel


def render_diff(result, part_rows, meta_a, meta_b, out_dir, net_result=None,
                scope="all", written=()):
    """Console summary.

    The two layers are printed as separate blocks with their own subtotals.
    They are not summed into one number: component changes and connectivity
    changes are different questions, and collapsing them would hide which
    layer a reviewer still has to look at.
    """
    stats = result["stats"]
    total = sum(stats[name] for name, _ in DIFF_KINDS)

    out("")
    out("  旧版 %s : %d 元件 / %d 料号"
        % (meta_a["label"], meta_a["count"], meta_a["parts"]))
    out("  新版 %s : %d 元件 / %d 料号"
        % (meta_b["label"], meta_b["count"], meta_b["parts"]))

    if scope in ("bom", "all"):
        out("")
        for name, shown in DIFF_KINDS:
            out("  %s %6d" % (shown, stats[name]))
        out("  " + "-" * 13)
        out("  %s %6d" % ("差异合计", total))
        out("  %s %6d" % ("完全一致", stats["unchanged"]))
        if result["no_refdes"]:
            out("  %s %6d" % ("无法比对", result["no_refdes"]))
        if part_rows:
            out("  受影响料号 %d 个（料号视角）" % len(part_rows))
        else:
            out("  物料层面无变化")

    if net_result is not None and scope in ("net", "all"):
        ns = net_result["stats"]
        out("")
        out("  网络层: %d -> %d 个网络 / %d -> %d 个连接点"
            % (net_result["net_count_a"], net_result["net_count_b"],
               net_result["pin_count_a"], net_result["pin_count_b"]))
        out("")
        out("  %s %6d" % ("引脚改接", ns["pin_moved"]))
        out("  " + "-" * 13)
        out("  %s %6d" % ("连接变化合计", ns["pin_moved"]))
        out("  %s %6d" % ("新增接点", ns["conn_new"]))
        out("  %s %6d" % ("断开接点", ns["conn_lost"]))
        out("  %s %6d" % ("网络构成变化", ns["net_mod"]))
        out("  %s %6d" % ("网络改名", ns["net_ren"]))
        out("  %s %6d" % ("新增网络", ns["net_add"]))
        out("  %s %6d" % ("删除网络", ns["net_del"]))
        if ns["drift"]:
            out("  %s %6d" % ("命名漂移", ns["drift"]))
            out("    （自动命名网重编号，连接未变，不计入差异）")

    hot = []
    if scope in ("bom", "all") and stats["swapped"]:
        hot.append("更换料号 %d 处" % stats["swapped"])
    if (net_result is not None and scope in ("net", "all")
            and net_result["stats"]["pin_moved"]):
        hot.append("引脚改接 %d 处" % net_result["stats"]["pin_moved"])
    if hot:
        out("")
        out("  需人工确认: %s（报告首页已单独列出）" % "、".join(hot))

    if written:
        out("")
        out("  报告:")
        for path in written:
            out("    %s" % path)


def cmd_diff(paths, sdd, out_dir=None, labels=None, no_retry=False,
             scope="all", want_html=True, want_excel=True, want_csv=False):
    """Compare two revisions of the same design and write an ECO report."""
    path_a, path_b = paths
    work_root = os.path.abspath(out_dir or os.path.join(os.getcwd(), "icdb_diff"))

    out("ECO 差异比对")
    out("")
    out("  旧版输入: %s" % path_a)
    out("  新版输入: %s" % path_b)

    items_a, meta_a = load_diff_side(path_a, sdd, work_root, no_retry=no_retry)
    items_b, meta_b = load_diff_side(path_b, sdd, work_root, no_retry=no_retry)

    if labels:
        meta_a["label"], meta_b["label"] = labels[0], labels[1]

    if not items_a and not items_b:
        sys.exit("两侧都没有读到元件，无法比对（确认输入是有效的导出目录或工程）")

    result = diff_boms(items_a, items_b)
    part_rows = part_summary(items_a, items_b)

    net_result = None
    if scope in ("net", "all"):
        if meta_a["nets"] or meta_b["nets"]:
            net_result = diff_nets(meta_a["nets"], meta_b["nets"])
        else:
            out("")
            out("  [跳过网络比对] 两侧都没有 database.ppn。")
            out("      旧版导出含 ppn: %s / 新版: %s"
                % ("是" if meta_a["has_ppn"] else "否",
                   "是" if meta_b["has_ppn"] else "否"))
            out("      需重新导出该工程（icdb2csv 会一并产生 ppn）。")
            if scope == "net":
                scope = "all"          # nothing to compare; fall back
                net_result = diff_nets({}, {})

    written = []
    if want_excel:
        if HAVE_XLSX:
            written.append(write_diff_excel(result, part_rows, net_result,
                                            work_root, meta_a, meta_b,
                                            scope=scope))
        else:
            # xlsx_writer.py was not copied next to this script. Say so and
            # fall back to CSV rather than losing the report at the last step.
            out("")
            out("  [跳过 Excel] scripts/xlsx_writer.py 不在旁边，改出 CSV。")
            want_csv = True
    if want_csv:
        written.append(write_diff_csv(result, part_rows, net_result, work_root,
                                      meta_a, meta_b, scope=scope))
    if want_html:
        written.append(write_diff_html(
            build_diff_html(result, part_rows, net_result, meta_a, meta_b,
                            scope=scope), work_root))

    render_diff(result, part_rows, meta_a, meta_b, work_root,
                net_result=net_result, scope=scope, written=written)
    if not written:
        out("")
        out("  （按参数要求，本次未生成任何报告文件）")
    return 0


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


def cmd_interactive(sdd, find_root=None):
    """Top-level wizard: ask which task, then hand off to it."""
    out("")
    out("EE 取数 / 比对向导")
    out("=" * 62)
    out("")
    task = _ask_choice("要做什么:", [
        ("导出工程数据（单版）", "export"),
        ("比对两个版本（ECO 差异，可选网络层）", "diff"),
    ], default=1)
    if task is CANCEL:
        out("  已取消。")
        return 1
    if task == "diff":
        return cmd_diff_interactive(sdd, find_root=find_root)
    return _wizard_export(sdd, find_root=find_root)


def _wizard_export(sdd, find_root=None):
    """Menu-driven export: pick project -> pick output -> confirm -> export."""
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


def cmd_diff_interactive(sdd, find_root=None):
    """Guided ECO comparison.

    Both sides may be a project or an existing export, the layers and report
    formats are chosen with a checkbox rather than assumed, and the confirm
    step states plainly whether anything is about to touch a project
    database -- the one side effect worth knowing about before pressing go.
    """
    out("")
    out("两版差异比对向导（ECO）")
    out("=" * 62)
    out("")
    out("  两侧都可以选「EE 工程」（先导出，会写 icdb.dat）")
    out("  或「已有导出目录」（离线直读，完全不碰工程）。")

    candidates = []
    for cand in auto_scan_roots(find_root):
        found = scan_diff_candidates(cand)
        out("  扫描 %s -> %d 个候选" % (cand, len(found)))
        if found:
            candidates = found
            break

    while not candidates:
        typed = _ask("  没找到候选。请输入目录（回车=取消）", None)
        if typed is CANCEL or not typed:
            out("  已取消。")
            return 1
        if not os.path.isdir(typed):
            out("  目录不存在: %s" % typed)
            continue
        candidates = scan_diff_candidates(typed)
        if not candidates:
            out("  该目录下没有 EE 工程，也没有导出目录。")

    usable = [c for c in candidates if c["usable"]]
    for c in candidates:
        if not c["usable"]:
            out("  [x] 跳过 %s —— %s" % (c["label"], c["why"]))
    if not usable:
        out("  没有可用候选。")
        return 1

    manual = {"kind": "manual", "label": "手动输入路径…", "path": "",
              "detail": "粘贴工程目录 / .prj / 导出目录", "usable": True,
              "why": ""}
    state = {"next": 1}

    def choose(step, name):
        menu = usable + [manual]
        out("")
        out("[%s] 选择%s版本" % (step, name))
        picked = _ask_choice(
            "选择%s:" % name,
            [("%s   (%s)" % (c["label"], c["detail"]), c) for c in menu],
            default=state["next"])
        if picked is CANCEL:
            return None
        if picked["kind"] == "manual":
            typed = _ask("  粘贴路径")
            if typed is CANCEL or not typed:
                return None
            path = os.path.abspath(os.path.expandvars(os.path.expanduser(typed)))
            if not os.path.exists(path):
                out("  路径不存在: %s" % path)
                return None
            picked = {"kind": "auto", "label": infer_label(path), "path": path,
                      "detail": "手动指定", "usable": True, "why": ""}
        try:
            state["next"] = min(usable.index(picked) + 1, len(menu))
        except ValueError:
            state["next"] = 1
        return picked

    side_a = choose("1/4", "旧版")
    if side_a is None:
        out("  已取消。")
        return 1
    side_b = choose("2/4", "新版")
    if side_b is None:
        out("  已取消。")
        return 1
    if side_a["path"] == side_b["path"]:
        out("")
        out("  两侧路径相同，比对结果必然为零差异。已取消。")
        return 1

    # ---- 3/4 比对内容与输出（复选框） ----------------------------------
    out("")
    out("[3/4] 比对内容与输出")
    picked = _ask_multi("要做什么（空格勾选）:", DIFF_OPTIONS)
    if picked is CANCEL:
        out("  已取消。")
        return 1
    if not picked:
        out("  什么都没勾选，已取消。")
        return 1
    scope, want_html, want_excel, want_csv = options_to_flags(picked)
    if not (want_html or want_excel or want_csv):
        out("  没勾选任何报告格式，只输出终端摘要。")

    will_export = [c for c in (side_a, side_b) if c["kind"] == "project"]
    if will_export and not sdd:
        out("")
        out("  [x] 选中了工程路径，需要先导出，但没找到 EE 的 SDD_HOME。")
        out("      用 --sdd 指定，或改选已有导出目录。")
        return 1

    # ---- 4/4 输出目录与确认 ---------------------------------------------
    out("")
    out("[4/4] 输出目录与确认")
    default_out = os.path.join(os.getcwd(), "icdb_diff")
    out("  默认: %s" % default_out)
    typed = _ask("  直接回车用默认，或输入其它目录")
    if typed is CANCEL:
        out("  已取消。")
        return 1
    out_dir = (os.path.abspath(os.path.expandvars(os.path.expanduser(typed)))
               if typed else default_out)

    out("")
    out("  旧版    : %s" % side_a["path"])
    out("            -> %s" % side_a["label"])
    out("  新版    : %s" % side_b["path"])
    out("            -> %s" % side_b["label"])
    out("  比对内容: %s" % ("元件级 + 网络级" if scope == "all"
                             else ("仅元件级" if scope == "bom" else "仅网络级")))
    out("  报告格式: %s" % ("、".join(
        [x for x, on in (("网页", want_html), ("Excel", want_excel),
                         ("CSV", want_csv)) if on]) or "不生成文件"))
    out("  输出目录: %s" % out_dir)
    out("")
    if will_export:
        out("  注意: 以下路径是 EE 工程，会先导出，改写其 icdb.dat")
        out("        （会话簿记，数据不受影响）。请先关闭 Expedition EE。")
        for c in will_export:
            out("          %s" % c["path"])
    else:
        out("  两侧都是已有导出目录 —— 离线直读，不会改动任何工程文件。")
    ans = _ask("  开始比对？(Y/n)", "Y")
    if ans is CANCEL or str(ans).strip().lower() not in ("", "y", "yes"):
        out("  已取消。")
        return 1

    out("")
    try:
        rc = cmd_diff([side_a["path"], side_b["path"]], sdd, out_dir=out_dir,
                      labels=[side_a["label"], side_b["label"]], scope=scope,
                      want_html=want_html, want_excel=want_excel,
                      want_csv=want_csv)
    except SystemExit as exc:
        return exc.code if isinstance(exc.code, int) else 1

    if rc == 0:
        out("")
        out("下一步可选：")
        if want_excel and HAVE_XLSX:
            out("  · Excel 打开报表        %s"
                % os.path.join(out_dir, "diff_report.xlsx"))
            out("    （工作表：概览 / 元件级差异 / 引脚改接 / 网络变化 /"
                " 料号视角）")
        if want_html:
            out("  · 浏览器打开网页报告    %s"
                % os.path.join(out_dir, "diff_report.html"))
        if want_csv:
            out("  · 脚本读取 CSV          %s"
                % os.path.join(out_dir, "diff_report.csv"))
    return rc


def cmd_diff_options(root=None):
    """Emit the diff checkbox list (and any candidates) as JSON.

    The counterpart of ``--pick`` for comparisons: a host AI that wants to
    put real checkboxes in front of the user reads this instead of guessing
    which layers and formats are on offer.
    """
    import json
    payload = diff_option_payload()
    if root:
        payload["scanned"] = os.path.abspath(root)
        cands = scan_diff_candidates(root)
        payload["candidates"] = [
            {"kind": c["kind"], "label": c["label"], "path": c["path"],
             "detail": c["detail"], "usable": c["usable"], "why": c["why"]}
            for c in cands]
        payload["candidate_count"] = len(cands)
    out(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


def main():
    ap = argparse.ArgumentParser(
        description="Export Expedition EE data via the official iCDB CLI.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="例:\n"
               "  %(prog)s --prj \"E:/proj/board.prj\" --bom\n"
               "  %(prog)s --doctor\n"
               "  %(prog)s --find \"E:/designs\" --numbered\n"
               "  %(prog)s --pick \"E:/designs\"          # 候选工程 JSON\n"
               "  %(prog)s --interactive                # 向导模式\n"
               "  %(prog)s --diff \"<A>/icdb_export\" \"<B>/icdb_export\"   # ECO 差异\n"
               "  %(prog)s --diff                      # 比对向导（复选框）\n"
               "  %(prog)s --diff \"<A>\" \"<B>\" --scope net --no-html\n"
               "  %(prog)s --diff-options \"E:/designs\"  # 选项 + 候选 JSON\n")
    ap.add_argument("--prj", help="工程 .prj，或包含它的目录")
    ap.add_argument("--icdb", help="iCDB 目录（默认取 .prj 的 iCDBDir）")
    ap.add_argument("--out", help="输出目录（导出：默认 <prj 同级>/icdb_export；"
                                  "--diff：报告目录，默认 ./icdb_diff）")
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
    ap.add_argument("--diff", nargs="*", metavar=("旧版", "新版"),
                    help="比对同一工程的两个版本（接受导出目录或工程路径）；"
                         "不带路径参数则进入比对向导")
    ap.add_argument("--label", nargs=2, metavar=("旧版", "新版"),
                    help="配合 --diff：给两端命名，用于报告")
    ap.add_argument("--scope", choices=("all", "bom", "net"), default="all",
                    help="配合 --diff：比对哪一层，默认 all（两层都做）")
    ap.add_argument("--no-html", action="store_true",
                    help="配合 --diff：不生成网页报告")
    ap.add_argument("--no-excel", action="store_true",
                    help="配合 --diff：不生成 Excel 报表")
    ap.add_argument("--csv", action="store_true",
                    help="配合 --diff：额外出单张 CSV 长表"
                         "（供脚本读取；给人看用 Excel 报表）")
    ap.add_argument("--diff-options", nargs="?", const="", metavar="DIR",
                    help="输出比对选项清单（JSON，供宿主 AI 弹复选框）；"
                         "给一个目录则同时列出候选")
    args = ap.parse_args()

    if args.diff_options is not None:
        sys.exit(cmd_diff_options(args.diff_options or None))

    diff_paths = args.diff or []
    if len(diff_paths) == 1:
        ap.error("--diff 需要两个路径，或一个都不给（进入比对向导）")

    offline_diff = len(diff_paths) == 2 and all(is_export_dir(p)
                                                for p in diff_paths)
    cfg = load_config()
    if offline_diff:
        # A purely offline comparison never needs EE, so skip the (possibly
        # slow) SDD_HOME discovery.
        sdd, tried = None, []
    else:
        sdd, tried = resolve_sdd_home(args.sdd, cfg)

    if args.doctor:
        sys.exit(cmd_doctor(sdd, tried, cfg))
    if args.find:
        sys.exit(cmd_find(args.find, numbered=args.numbered, as_json=args.json))
    if args.pick:
        sys.exit(cmd_pick(args.pick))
    if args.interactive:
        sys.exit(cmd_interactive(sdd, find_root=args.find_root))
    if args.diff is not None and len(diff_paths) < 2:
        sys.exit(cmd_diff_interactive(sdd, find_root=args.find_root))
    if len(diff_paths) == 2:
        sys.exit(cmd_diff(diff_paths, sdd, out_dir=args.out,
                          labels=args.label, no_retry=args.no_retry,
                          scope=args.scope, want_html=not args.no_html,
                          want_excel=not args.no_excel,
                          want_csv=args.csv))
    if not args.prj:
        ap.error("需要 --prj，或改用 --doctor / --find / --pick / "
                 "--interactive / --diff / --diff-options")
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
