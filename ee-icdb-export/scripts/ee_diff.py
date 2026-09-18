#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ECO diff: comparing two revisions of one design.

Also the single normalized record set that the HTML, Excel and CSV
renderers all read from -- ordering, type labels and the "which fields are
worth showing" rules live here once, not once per renderer.
"""

from __future__ import print_function

import collections
import os
import re
import sys

from ee_env import find_prj, out
from ee_export import gather_components, load_tab, run_export

#: Diff categories in report order: (internal name, label). Every label is
#: four Chinese characters so the console summary lines up in a column.
DIFF_KINDS = (
    ("swapped", "更换料号"),
    ("revalued", "变更参数"),
    ("added", "新增元件"),
    ("removed", "删除元件"),
)


#: Attributes compared when the part number is unchanged.
#:
#: ``cell`` is deliberately absent. In the designs examined so far Cell Name
#: and Part Name carry the same value, and Cell Name is frequently filled in
#: on one revision only -- comparing it produced a run of "(空) -> XXX" rows
#: that were attribute back-fill, not electrical change. A genuine package
#: change always moves Part Name or the part number, so nothing is lost here;
#: the old/new cell values are still printed in the detail report.
ATTR_FIELDS = (
    ("value", "Value"),
    ("value1", "Value1"),
    ("part_name", "器件名"),
)


#: The single report file: one row per changed field, whichever layer the
#: change came from.
#:
#: Four separate files (component detail / material summary / connections /
#: nets) used to be written. Each had its own header, its own notion of "what
#: counts", and a reviewer had to join them by eye and work out which header
#: applied to which block. 分类 does that job inside Excel, with no joining.
DIFF_REPORT_COLS = ("分类", "差异类型", "对象", "明细", "旧版本", "新版本", "说明")


def _refdes_key(refdes):
    """Index key for a reference designator.

    Case- and whitespace-tolerant, otherwise verbatim. EE spells multi-unit
    parts as U1A/U1B and two revisions occasionally differ in that spelling;
    normalising it away would hide a real change, so it is left alone.
    """
    return (refdes or "").strip().rstrip(",").upper()


def _safe_name(name):
    """A filesystem-safe fragment for the scratch export directories."""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name or "side").strip("_") or "side"


def _row_sig(item):
    """Comparable signature of one symbol, over the same fields as ATTR_FIELDS."""
    return tuple((item.get(f) or "").strip()
                 for f in ("part_number", "value", "value1", "part_name"))


def _join_set(values):
    """Render a set of attribute values for the report.

    Separator is " | ", not "/": part values themselves contain slashes
    ("200K/1%", "4.7UF/6.3V"), which would make the output ambiguous.

    Blank values are shown as "(空)" rather than dropped -- dropping them
    makes {"", "U1"} and {"U1"} render identically and silently hides the
    difference.
    """
    items = sorted(values)
    if not items:
        return "(无)"
    return " | ".join(v if v else "(空)" for v in items)


def is_export_dir(path):
    """True when the path already holds exported iCDB tables."""
    if not path:
        return False
    for probe in (os.path.join(path, "work"), path):
        if os.path.isfile(os.path.join(probe, "database.sym")):
            return True
    return False


def infer_label(path):
    """A readable name for one side, used in the report."""
    p = os.path.abspath(str(path).rstrip("\\/"))
    name = os.path.basename(p)
    if name.lower() in ("icdb_export", "icdb_diff"):
        name = os.path.basename(os.path.dirname(p)) or name
    return name


def locate_export(path):
    """Classify an input path.

    Returns ("export", work_dir) when the path already holds exported tables,
    or ("project", prj_path) when it is an EE project still to be exported.
    """
    if not os.path.exists(path):
        sys.exit("路径不存在: %s" % path)
    if is_export_dir(path):
        for probe in (os.path.join(path, "work"), path):
            if os.path.isfile(os.path.join(probe, "database.sym")):
                return "export", probe
    try:
        return "project", find_prj(path)
    except SystemExit:
        sys.exit("无法识别该路径: %s\n"
                 "  既不是导出目录（缺 work/database.sym），"
                 "也不是 EE 工程（找不到 .prj）" % path)


def load_diff_side(path, sdd, work_root, no_retry=False):
    """Load one side of the comparison, as (items, meta).

    An existing export is read in place, so nothing touches EE. A project
    path is exported first, into a scratch directory under the report folder.
    """
    kind, target = locate_export(path)
    label = infer_label(path)

    if kind == "export":
        work = target
        exported = False
    else:
        if not sdd:
            sys.exit("该输入是 EE 工程，需要先导出，但未找到 EE 的 SDD_HOME:\n"
                     "  %s\n"
                     "  请用 --sdd 指定，或改为传入已导出的 icdb_export 目录。"
                     % target)
        scratch = os.path.join(work_root, "_export_" + _safe_name(label))
        out("  [导出] %s" % target)
        out("         -> %s" % scratch)
        run_export(sdd, target, out_dir=scratch, no_retry=no_retry)
        work = os.path.join(scratch, "work")
        if not os.path.isfile(os.path.join(work, "database.sym")):
            sys.exit("导出未产生可用数据表: %s" % work)
        exported = True

    items, _ids = gather_components(work)
    parts = set((it["part_number"] or "").strip() for it in items
                if (it["part_number"] or "").strip())
    meta = {"label": label, "work": work, "project": target if exported else "",
            "exported": exported, "count": len(items), "parts": len(parts),
            "nets": load_nets(work),
            "has_ppn": os.path.isfile(os.path.join(work, "database.ppn"))}
    return items, meta


#: Attributes shown when describing a part that appeared or vanished.
_PART_FIELDS = (("part_number", "料号"), ("value", "Value"),
                ("part_name", "器件名"), ("cell", "封装"))


def _part_desc(item):
    """料号 / Value / 器件名 / 封装 of one symbol, as one readable line."""
    if not item:
        return "(无)"
    bits = []
    for field, shown in _PART_FIELDS:
        val = (item.get(field) or "").strip()
        if val:
            bits.append("%s %s" % (shown, val))
    return " ；".join(bits) if bits else "(无参数)"


def _split_part_desc(text):
    """Inverse of :func:`_part_desc`, for the workbook.

    A wholly added or removed component carries its whole parameter list in
    one joined string, because the HTML page shows it on one line. Excel wants
    a row per field, so that string is taken apart again here. Labels come
    from ``_PART_FIELDS`` rather than being guessed, and anything that does
    not parse stays attached to the previous field -- a value that happens to
    contain the separator must not silently become a new row.
    """
    known = set(shown for _field, shown in _PART_FIELDS)
    out = []
    for chunk in (text or "").split(" ；"):
        chunk = chunk.strip()
        if not chunk:
            continue
        label, _, value = chunk.partition(" ")
        if label in known and value:
            out.append((label, value))
        elif out:
            out[-1] = (out[-1][0], "%s ；%s" % (out[-1][1], chunk))
        else:
            out.append(("", chunk))
    return out


def _mk_detail(kind, refdes, old, new, note, changes=()):
    """One row of the detail report.

    ``changes`` is ``[(字段名, 旧值, 新值), ...]`` -- what actually moved.
    Reports read the structure rather than the ``note`` string: a rendered
    "Value: 200K -> 210K" line already carries the field name and both values,
    so parsing it back out would be guesswork. ``note`` now explains what the
    category means (why the row is here), which the values cannot say.
    """
    def g(item, field):
        return (item or {}).get(field, "") or ""
    return {
        "kind": kind,
        "refdes": refdes or "",
        "changes": list(changes),
        "a_pn": g(old, "part_number"), "b_pn": g(new, "part_number"),
        "a_value": g(old, "value"), "b_value": g(new, "value"),
        "a_cell": g(old, "cell"), "b_cell": g(new, "cell"),
        "a_sheet": g(old, "sheet"), "b_sheet": g(new, "sheet"),
        "note": note,
    }


def diff_boms(items_a, items_b):
    """Compare two BOMs keyed by reference designator.

    The reference designator is the anchor because it is the physical position
    on the board -- a position reused for a different part is exactly what an
    ECO review has to catch. Keying on part number instead would blur "this
    position was swapped" into "this material changed quantity".

    Returns {"stats": {...}, "details": [...], "no_refdes": int}.
    """
    index_a = collections.defaultdict(list)
    index_b = collections.defaultdict(list)
    for it in items_a:
        index_a[_refdes_key(it["refdes"])].append(it)
    for it in items_b:
        index_b[_refdes_key(it["refdes"])].append(it)

    stats = dict((name, 0) for name, _ in DIFF_KINDS)
    stats["unchanged"] = 0
    details = []

    for key in sorted(set(index_a) | set(index_b)):
        if not key:
            continue                       # counted separately
        group_a = index_a.get(key, [])
        group_b = index_b.get(key, [])

        if not group_b:
            for it in group_a:
                details.append(_mk_detail(
                    "removed", it["refdes"], it, None, "该位号仅旧版存在",
                    [("元件参数", _part_desc(it), "(无)")]))
                stats["removed"] += 1
            continue
        if not group_a:
            for it in group_b:
                details.append(_mk_detail(
                    "added", it["refdes"], None, it, "该位号仅新版存在",
                    [("元件参数", "(无)", _part_desc(it))]))
                stats["added"] += 1
            continue

        if len(group_a) == 1 and len(group_b) == 1:
            old, new = group_a[0], group_b[0]
            pn_a = (old["part_number"] or "").strip()
            pn_b = (new["part_number"] or "").strip()
            attrs = []
            for field, shown in ATTR_FIELDS:
                va = (old.get(field) or "").strip()
                vb = (new.get(field) or "").strip()
                if va != vb:
                    attrs.append((shown, va or "(空)", vb or "(空)"))
            if pn_a != pn_b and (pn_a or pn_b):
                details.append(_mk_detail(
                    "swapped", old["refdes"], old, new, "同一位号下料号被替换",
                    [("料号", pn_a or "(空)", pn_b or "(空)")] + attrs))
                stats["swapped"] += 1
                continue
            if attrs:
                details.append(_mk_detail("revalued", old["refdes"], old, new,
                                          "位号数量未变，仅参数变更", attrs))
                stats["revalued"] += 1
            else:
                stats["unchanged"] += 1
            continue

        # A position may hold several symbols (multi-unit parts). Compare as
        # multisets instead of pairing rows, so a changed unit count is
        # reported rather than silently zipped together.
        if sorted(_row_sig(i) for i in group_a) == sorted(_row_sig(i) for i in group_b):
            stats["unchanged"] += len(group_a)
            continue
        pns_a = set((i["part_number"] or "").strip() for i in group_a)
        pns_b = set((i["part_number"] or "").strip() for i in group_b)
        kind = "swapped" if pns_a != pns_b else "revalued"
        bits = []
        if len(group_a) != len(group_b):
            bits.append(("符号数", "%d" % len(group_a), "%d" % len(group_b)))
        for field, shown in (("part_number", "料号"),) + ATTR_FIELDS:
            sa = set((i.get(field) or "").strip() for i in group_a)
            sb = set((i.get(field) or "").strip() for i in group_b)
            if sa != sb:
                bits.append((shown, _join_set(sa), _join_set(sb)))
        details.append(_mk_detail(
            kind, group_a[0]["refdes"], group_a[0], group_b[0],
            "同一位号下有多个符号（多单元器件），符号或参数有变化", bits))
        stats[kind] += 1

    no_refdes = len(index_a.get("", [])) + len(index_b.get("", []))
    return {"stats": stats, "details": details, "no_refdes": no_refdes}


def part_summary(items_a, items_b):
    """Material-level view: which part numbers changed their reference set.

    This is the same information seen from the other side, and it is what
    procurement asks after an ECO. It deliberately does not feed the
    reference-level totals -- that would double-count.
    """
    usage_a = collections.defaultdict(set)
    usage_b = collections.defaultdict(set)
    for it in items_a:
        key, pn = _refdes_key(it["refdes"]), (it["part_number"] or "").strip()
        if pn and key:                     # a blank refdes cannot be counted
            usage_a[pn].add(key)
    for it in items_b:
        key, pn = _refdes_key(it["refdes"]), (it["part_number"] or "").strip()
        if pn and key:
            usage_b[pn].add(key)

    rows = []
    for pn in sorted(set(usage_a) | set(usage_b)):
        sa = usage_a.get(pn, set())
        sb = usage_b.get(pn, set())
        if sa == sb:
            continue
        if not sa:
            status = "仅新版有"
        elif not sb:
            status = "仅旧版有"
        else:
            status = "用量变化"
        rows.append({
            "pn": pn, "status": status,
            "a_qty": len(sa), "b_qty": len(sb), "delta": len(sb) - len(sa),
            "added": " ".join(sorted(sb - sa)),
            "removed": " ".join(sorted(sa - sb)),
        })
    return rows


#: Auto-generated net names look like ``<AUTO_NET>``. They carry no design
#: intent and EE renumbers them whenever the symbol ordering shifts, so a
#: change of number alone is bookkeeping, not an ECO item.
AUTO_NET_RE = re.compile(r"^\$\d+N\d+$")


#: Endpoint-set overlap at or above which two unmatched nets are taken to be
#: the same net renamed / split / merged, rather than one net removed plus
#: one net added. 0.5 is deliberately loose: a split net keeps most of its
#: pins, and reporting it as a rename with a note beats two unrelated rows.
NET_MATCH_MIN = 0.5


#: Net-level statuses in report order. ``命名漂移`` is listed for visibility
#: but never counted -- it is a pure renaming of an auto-generated net.
NET_STATUSES = ("构成变化", "网络改名", "新增网络", "删除网络", "命名漂移")


#: Connection-level statuses. Only ``引脚改接`` counts as a difference; the
#: other two are the landing points of changes reported elsewhere.
CONN_KINDS = ("引脚改接", "断开接点", "新增接点")


def load_nets(work):
    """Read ``database.ppn`` into ``{net_name: {(REFDES, pin), ...}}``.

    ``database.ppn`` is the physical-pin view of the flat netlist: one row
    per pin, carrying the net it sits on. That makes it the right table for
    connectivity comparison -- no symbol round-trip needed, and every pin is
    addressable as (reference designator, pin number).

    Columns are located by header name rather than position; the iCDB table
    layout is not guaranteed to stay put across EE releases.
    """
    rows = load_tab(work, "database.ppn")
    if len(rows) < 2:
        return {}
    head = [c.strip().lower().replace(" ", "_") for c in rows[0]]
    try:
        i_pin = head.index("pin_number")
        i_ref = head.index("reference_designator")
        i_net = head.index("flat_net_name")
    except ValueError:
        return {}
    width = max(i_pin, i_ref, i_net)

    nets = collections.defaultdict(set)
    for row in rows[1:]:
        if len(row) <= width:
            continue
        ref = _refdes_key(row[i_ref])
        pin = row[i_pin].strip()
        net = row[i_net].strip()
        if not ref and not pin:
            continue
        nets[net].add((ref, pin))
    return dict(nets)


def _pin_net_map(nets):
    """Invert to ``{(REFDES, pin): net}``.

    A pin belongs to exactly one flat net. If a malformed table offers two,
    the alphabetically first net wins, so the result is deterministic rather
    than dependent on file order.
    """
    m = {}
    for net in sorted(nets):
        for key in nets[net]:
            m.setdefault(key, net)
    return m


def _fmt_pins(pins, limit=14):
    """Render a set of pins for one report cell, truncated when long."""
    shown = sorted("%s.%s" % (r or "?", p or "?") for r, p in pins)
    if not shown:
        return "(无)"
    if len(shown) > limit:
        return " ".join(shown[:limit]) + "  …(共 %d 个)" % len(shown)
    return " ".join(shown)


def diff_nets(nets_a, nets_b):
    """Compare two flat netlists. Returns a dict of rows and counts.

    Three angles, from one pass:

    * **pin moves** -- a ``(refdes, pin)`` that now sits on a different net.
      This is the noise-free view, and the reason the pin is the primary
      axis: a renumbered auto-net does not move any pin, so churn cannot
      appear here. ``<D1>.1`` leaving ``<NET_CTRL>`` for a brand new net
      is one move -- keyed on nets instead it reads as "net lost a pin" plus
      "a net appeared", which hides the intent.
    * **net composition** -- nets on both sides whose pin sets differ.
    * **net add / remove / rename** -- the remainder, with renames matched by
      endpoint overlap so a renamed net is not one deletion plus one
      addition.

    Only ``pin_moved`` feeds the difference total. Net-level rows are a
    second view of the same events (the same relationship the part view has
    to the reference view), so adding them in would double-count.
    """
    pins_a = _pin_net_map(nets_a)
    pins_b = _pin_net_map(nets_b)

    stats = {"pin_moved": 0, "conn_new": 0, "conn_lost": 0,
             "net_mod": 0, "net_ren": 0, "net_add": 0, "net_del": 0,
             "drift": 0, "same_nets": 0}
    conn_rows, net_rows = [], []

    refdes_a = set(r for r, _ in pins_a)
    refdes_b = set(r for r, _ in pins_b)

    # ---- pin view: the counted differences ------------------------------
    for key in sorted(set(pins_a) & set(pins_b)):
        na, nb = pins_a[key], pins_b[key]
        if na == nb:
            continue
        conn_rows.append({
            "kind": "引脚改接", "refdes": key[0], "pin": key[1],
            "a_net": na or "(空)", "b_net": nb or "(空)",
            "note": "同一位号同一引脚改接到别的网络",
        })
        stats["pin_moved"] += 1

    # Pins present on one side only. A wholly added/removed component is BOM
    # news already; what a reviewer needs is where the new part landed and
    # what an existing part lost, so a lost pin is only reported while the
    # reference designator still exists.
    #
    # One row per pin, not one row per component: a row that carries
    # "<C1>.1 <C1>.2" in one cell and "<AUTO_NET> <AUTO_NET>" in the next cannot
    # be read across, and its count no longer matches the stat above it.
    for key in sorted(set(pins_a) - set(pins_b)):
        if key[0] not in refdes_b:
            continue                       # a removed part is BOM news
        stats["conn_lost"] += 1
        conn_rows.append({
            "kind": "断开接点", "refdes": key[0], "pin": key[1],
            "a_net": pins_a[key] or "(空)", "b_net": "(无)",
            "note": "元件仍在，但该引脚新版不再连接",
        })
    for key in sorted(set(pins_b) - set(pins_a)):
        stats["conn_new"] += 1
        conn_rows.append({
            "kind": "新增接点", "refdes": key[0], "pin": key[1],
            "a_net": "(无)", "b_net": pins_b[key] or "(空)",
            "note": ("新增元件的落点" if key[0] not in refdes_a
                     else "既有元件新增连接"),
        })

    # ---- net view: a second angle on the same changes -------------------
    ka, kb = set(nets_a), set(nets_b)
    common = ka & kb
    for name in sorted(common):
        sa, sb = nets_a[name], nets_b[name]
        if sa == sb:
            stats["same_nets"] += 1
            continue
        stats["net_mod"] += 1
        net_rows.append({
            "name": name, "status": "构成变化",
            "a_pins": len(sa), "b_pins": len(sb),
            "added": _fmt_pins(sb - sa), "removed": _fmt_pins(sa - sb),
            "note": "同名网络，引脚有增删",
        })

    only_a = sorted(ka - kb)
    only_b = sorted(kb - ka)

    def jaccard(x, y):
        inter = len(x & y)
        return inter / float(len(x | y)) if inter else 0.0

    # Exact endpoint-set matches first: that is a rename with no electrical
    # change at all.
    by_set_b = collections.defaultdict(list)
    for name in only_b:
        by_set_b[frozenset(nets_b[name])].append(name)

    pairs, used_b, rest_a = [], set(), []
    for name in only_a:
        cands = [x for x in by_set_b.get(frozenset(nets_a[name]), [])
                 if x not in used_b]
        if cands:
            pairs.append((name, cands[0], True))
            used_b.add(cands[0])
        else:
            rest_a.append(name)
    rest_b = [x for x in only_b if x not in used_b]

    # Then nearest-neighbour overlap, for a split/merge that also renamed.
    for name in list(rest_a):
        best, best_j = None, 0.0
        for cand in rest_b:
            j = jaccard(nets_a[name], nets_b[cand])
            if j > best_j:
                best, best_j = cand, j
        if best is not None and best_j >= NET_MATCH_MIN:
            pairs.append((name, best, False))
            rest_b.remove(best)
            rest_a.remove(name)

    for name_a, name_b, exact in pairs:
        both_auto = bool(AUTO_NET_RE.match(name_a) and AUTO_NET_RE.match(name_b))
        if exact and both_auto:
            stats["drift"] += 1
            status = "命名漂移"
            note = "自动命名网重编号，连接完全相同 —— 非电气变更"
        else:
            stats["net_ren"] += 1
            status = "网络改名"
            note = ("连接完全相同" if exact
                    else "连接有变化（疑似网络拆分/合并）")
        net_rows.append({
            "name": "%s -> %s" % (name_a, name_b), "status": status,
            "a_pins": len(nets_a[name_a]), "b_pins": len(nets_b[name_b]),
            "added": _fmt_pins(nets_b[name_b] - nets_a[name_a]),
            "removed": _fmt_pins(nets_a[name_a] - nets_b[name_b]),
            "note": note,
        })

    for name in rest_a:
        stats["net_del"] += 1
        net_rows.append({
            "name": name, "status": "删除网络",
            "a_pins": len(nets_a[name]), "b_pins": 0,
            "added": "(无)", "removed": _fmt_pins(nets_a[name]),
            "note": "该网络仅旧版存在",
        })
    for name in rest_b:
        stats["net_add"] += 1
        net_rows.append({
            "name": name, "status": "新增网络",
            "a_pins": 0, "b_pins": len(nets_b[name]),
            "added": _fmt_pins(nets_b[name]), "removed": "(无)",
            "note": "该网络仅新版存在",
        })

    order = dict((s, i) for i, s in enumerate(NET_STATUSES))
    net_rows.sort(key=lambda r: (order.get(r["status"], 99), r["name"]))

    return {"pins": conn_rows, "nets": net_rows, "stats": stats,
            "net_count_a": len(ka), "net_count_b": len(kb),
            "pin_count_a": len(pins_a), "pin_count_b": len(pins_b)}


#: Badge and left-edge colour per difference kind.
_KIND_CLASS = {
    "更换料号": "k-swap", "变更参数": "k-mod",
    "新增元件": "k-add", "删除元件": "k-del",
    "引脚改接": "k-pin", "新增接点": "k-add", "断开接点": "k-del",
    "构成变化": "k-mod", "网络改名": "k-ren",
    "新增网络": "k-add", "删除网络": "k-del", "命名漂移": "k-drift",
    "用量变化": "k-mod", "仅新版有": "k-add", "仅旧版有": "k-del",
}


#: Kinds a reviewer has to look at first: a position that was re-partnumbered,
#: or a pin that was re-routed. The rest is an addition, a removal or
#: bookkeeping, and reads fine in bulk.
KEY_KINDS = ("更换料号", "引脚改接")


def _norm_bom(result):
    """Component changes: ordered, type already labelled, fields untouched."""
    order = dict((name, i) for i, (name, _) in enumerate(DIFF_KINDS))
    label_of = dict(DIFF_KINDS)
    return [{"kind": label_of[d["kind"]],
             "refdes": d["refdes"],
             "changes": list(d["changes"]),
             "sheet": (d["b_sheet"] or d["a_sheet"]).strip(),
             "note": d["note"]}
            for d in sorted(result["details"],
                            key=lambda x: (order[x["kind"]], x["refdes"]))]


def _norm_pins(net_result):
    """Pin records: ordered by kind, then by position."""
    order = dict((k, i) for i, k in enumerate(CONN_KINDS))
    return [{"kind": r["kind"], "refdes": r["refdes"], "pin": r["pin"],
             "a_net": r["a_net"], "b_net": r["b_net"], "note": r["note"]}
            for r in sorted(net_result["pins"],
                            key=lambda x: (order.get(x["kind"], 9),
                                           x["refdes"], x["pin"]))]


def _norm_nets(net_result):
    """Net records: ordered as given, fields worth showing already worked out.

    A net that exists on one side only already lists every pin it has, so
    "引脚数 0 → 2" next to that list says the same thing twice; the count is
    therefore dropped unless it really moved.
    """
    out = []
    for r in net_result["nets"]:
        adds = "" if r["added"] in ("(无)", "") else r["added"]
        dels = "" if r["removed"] in ("(无)", "") else r["removed"]
        fields, pin_a, pin_b = [], "", ""
        if r["a_pins"] and r["b_pins"] and r["a_pins"] != r["b_pins"]:
            fields.append(("引脚数", "%d" % r["a_pins"],
                           "%d" % r["b_pins"]))
            pin_a, pin_b = r["a_pins"], r["b_pins"]
        if dels:
            fields.append(("移除引脚", dels, "(无)"))
        if adds:
            fields.append(("新增引脚", "(无)", adds))
        out.append({"name": r["name"], "status": r["status"],
                    "fields": fields, "pin_a": pin_a, "pin_b": pin_b,
                    "adds": adds, "dels": dels, "note": r["note"]})
    return out


def _sheet_histogram(result):
    """Component changes per drawing sheet, commonest first.

    Tells a reviewer which sheets to open, which is the first thing anyone
    asks of a diff.
    """
    cnt = collections.Counter()
    for d in result["details"]:
        sheet = (d["b_sheet"] or d["a_sheet"]).strip()
        if sheet:
            cnt[sheet] += 1
    return cnt.most_common()


def _lead(meta_a, meta_b, result, net_result, part_rows, scope):
    """A sentence a reviewer can act on, before any table."""
    lines = []
    stats = result["stats"]
    if scope in ("bom", "all"):
        total = sum(stats[n] for n, _ in DIFF_KINDS)
        segs = ["%s %d" % (shown, stats[name])
                for name, shown in DIFF_KINDS if stats[name]]
        if total:
            lines.append("元件 <b>%d → %d</b>，共 <b>%d</b> 处差异：%s。"
                         % (meta_a["count"], meta_b["count"], total,
                            "、".join(segs)))
        else:
            lines.append("元件 <b>%d → %d</b>，无差异。"
                         % (meta_a["count"], meta_b["count"]))
        hist = _sheet_histogram(result)
        if hist:
            lines.append("差异集中在 %s。"
                         % "、".join("%s（%d 处）" % (sh, n)
                                     for sh, n in hist[:3]))
    if net_result is not None and scope in ("net", "all"):
        ns = net_result["stats"]
        segs = []
        if ns["pin_moved"]:
            segs.append("引脚改接 %d" % ns["pin_moved"])
        other = ns["net_mod"] + ns["net_ren"] + ns["net_add"] + ns["net_del"]
        if other:
            segs.append("网络增减或构成变化 %d" % other)
        lines.append("连接 <b>%d → %d</b> 个网络，%s。"
                     % (net_result["net_count_a"], net_result["net_count_b"],
                        "、".join(segs) if segs else "无差异"))
    if part_rows:
        lines.append("受影响料号 <b>%d</b> 个。" % len(part_rows))
    return lines


def _key_items(result, net_result):
    """只列需要人工确认的两类 —— 换料与引脚改接。

    Plain data rather than markup, so the HTML page and the workbook show the
    same list from one source instead of two that can drift apart.
    """
    label_of = dict(DIFF_KINDS)
    items = []
    for d in result["details"]:
        kind = label_of[d["kind"]]
        if kind not in KEY_KINDS:
            continue
        items.append({"kind": kind, "refdes": d["refdes"], "pin": "",
                      "obj": d["refdes"], "changes": list(d["changes"]),
                      "where": (d["b_sheet"] or d["a_sheet"]).strip()})
    if net_result is not None:
        for r in net_result["pins"]:
            if r["kind"] in KEY_KINDS:
                items.append({"kind": r["kind"], "refdes": r["refdes"],
                              "pin": r["pin"],
                              "obj": "%s.%s" % (r["refdes"], r["pin"]),
                              "changes": [("", r["a_net"], r["b_net"])],
                              "where": ""})
    return items


def _expand_changes(changes):
    """Field-level rows for the workbook.

    Everything except an added/removed part already arrives field by field.
    Those two kinds arrive as one 元件参数 row, so they are spread out here --
    four short rows read better in a spreadsheet than one long cell, and the
    per-field rows can then be filtered on.
    """
    out = []
    for label, old, new in (changes or [("", "", "")]):
        if label != "元件参数":
            out.append((label, old, new))
            continue
        added = (not old) or old == "(无)"
        fields = _split_part_desc(new if added else old)
        if not fields:
            out.append((label, old, new))
            continue
        for name, value in fields:
            out.append((name or label,
                        "(无)" if added else value,
                        value if added else "(无)"))
    return out or [("", "", "")]
