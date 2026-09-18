#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""The self-contained HTML diff report.

The document itself is assets/diff_report.tpl.html; this module fills the
{{PLACEHOLDER}} holes and builds the tables that go in them.
"""

from __future__ import print_function

import collections
import io
import os
import sys

from ee_diff import DIFF_KINDS, KEY_KINDS, NET_STATUSES, _KIND_CLASS, _key_items, _lead, _norm_bom, _norm_nets, _norm_pins, _sheet_histogram

def _esc(text):
    """Minimal HTML escaping.

    The report must open from a file:// path on an isolated intranet machine,
    so it carries its own CSS and script -- no CDN, no template engine, same
    stdlib-only rule as the rest of this tool.
    """
    return (str("" if text is None else text)
            .replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


_TPL_NAME = "diff_report.tpl.html"


def template_path(name=_TPL_NAME):
    """Absolute path of a report template shipped alongside this script."""
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, os.pardir, "assets", name))


def load_template(path=None):
    """Read the report template.

    The template is a whole HTML document with {{PLACEHOLDER}} holes. It lives
    outside this script so the report's markup, CSS and script can be edited
    with an ordinary editor and diffed sanely, instead of hiding inside a
    Python string literal.
    """
    path = path or template_path()
    if not os.path.isfile(path):
        sys.exit("缺少报告模板: %s\n"
                 "  assets/diff_report.tpl.html 必须与 scripts/ 一起分发。\n"
                 "  只拷了 scripts/ 的话，请把 assets/ 也带上。" % path)
    with io.open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    # 模板可能在 Windows 编辑器里被存成 CRLF；归一化，保证输出稳定。
    return text.replace("\r\n", "\n").replace("\r", "\n")


def fill_template(text, **holes):
    """Fill {{NAME}} holes in a template.

    Deliberately not %-formatting nor str.format: the document is mostly CSS,
    and CSS is full of bare percent signs.
    """
    for key, value in holes.items():
        text = text.replace("{{%s}}" % key, value)
    return text


def _badge(kind):
    """The kind as a coloured chip, so a row's nature reads before its text."""
    return '<span class="badge %s">%s</span>' % (_KIND_CLASS.get(kind, ""),
                                                 _esc(kind))


def _code(text):
    return "<code>%s</code>" % _esc(text)


def _fields_html(fields, mono=False):
    """Changed fields as ``旧值 → 新值``, one line each.

    Only fields that actually moved get here, so no cell ever shows the same
    value twice -- that repetition down six of the old eleven columns is what
    forced horizontal scrolling and made the table unreadable.
    """
    if not fields:
        return '<span class="dim">—</span>'
    parts = []
    for label, old, new in fields:
        if label in ("元件参数",):
            label = ""                     # the badge already says added/removed
        lab = '<span class="flab">%s</span>' % _esc(label) if label else ""
        old_h = _code(old) if mono else _esc(old)
        new_h = _code(new) if mono else _esc(new)
        if old == "(无)":
            parts.append('<div class="fld">%s<span class="addcol">+ %s</span>'
                         "</div>" % (lab, new_h))
        elif new == "(无)":
            parts.append('<div class="fld">%s<span class="delcol">− %s</span>'
                         "</div>" % (lab, old_h))
        else:
            parts.append('<div class="fld">%s<span class="old">%s</span>'
                         '<span class="arw">→</span>'
                         '<span class="new">%s</span></div>'
                         % (lab, old_h, new_h))
    return "".join(parts)


def _table(tbl_id, heads, rows, kinds=(), note="", num_cols=()):
    """One report table: optional filter row, body, optional footnote."""
    usable = [k for k in kinds if any(r["k"] == k for r in rows)]
    p = ['<div class="panel">']
    if len(usable) > 1:
        cnt = collections.Counter(r["k"] for r in rows)
        p.append('<div class="filters">')
        p.append('<button data-k="*" class="on" onclick="filt(\'%s\',\'*\')">'
                 "全部 %d</button>" % (tbl_id, len(rows)))
        for k in usable:
            p.append('<button data-k="%s" onclick="filt(\'%s\',\'%s\')">'
                     "%s %d</button>"
                     % (_esc(k), tbl_id, _esc(k), _esc(k), cnt.get(k, 0)))
        p.append('<span class="fcnt" data-cnt="%s"></span>' % tbl_id)
        p.append("</div>")
    p.append('<div class="scroll"><table id="%s"><thead><tr>' % tbl_id)
    for h in heads:
        p.append("<th>%s</th>" % _esc(h))
    p.append("</tr></thead><tbody>")
    if not rows:
        p.append('<tr class="empty-row"><td colspan="%d">无差异</td></tr>'
                 % len(heads))
    for r in rows:
        p.append('<tr data-k="%s" class="%s">' % (_esc(r["k"]), r["cls"]))
        for i, cell in enumerate(r["cells"]):
            p.append("<td%s>%s</td>"
                     % (' class="num"' if i in num_cols else "", cell))
        p.append("</tr>")
    p.append("</tbody></table></div>")
    if note:
        p.append('<div class="tnote">%s</div>' % note)
    p.append("</div>")
    return "".join(p)


def _bom_rows(result):
    """Component changes, one row per reference designator."""
    rows = []
    for d in _norm_bom(result):
        kind = d["kind"]
        rows.append({
            "k": kind, "cls": _KIND_CLASS.get(kind, ""),
            "cells": [_badge(kind), _code(d["refdes"]),
                      _fields_html(d["changes"]),
                      _esc(d["sheet"]) or '<span class="dim">—</span>'],
        })
    return rows


def _pin_rows(net_result):
    """One row per pin. Grouped rows cannot be read across, and their count
    stops matching the total printed above them."""
    rows = []
    for r in _norm_pins(net_result):
        rows.append({
            "k": r["kind"], "cls": _KIND_CLASS.get(r["kind"], ""),
            "cells": [_badge(r["kind"]), _code(r["refdes"]), _esc(r["pin"]),
                      _fields_html([("", r["a_net"], r["b_net"])], mono=True)],
        })
    return rows


def _net_rows(net_result):
    """Network-view rows. 引脚数 only appears when it actually moved."""
    rows = []
    for r in _norm_nets(net_result):
        rows.append({
            "k": r["status"], "cls": _KIND_CLASS.get(r["status"], ""),
            "cells": [_badge(r["status"]), _code(r["name"]),
                      _fields_html(r["fields"], mono=True)],
        })
    return rows


def _part_rows(part_rows):
    rows = []
    for r in part_rows:
        if r["delta"] > 0:
            chg = '<span class="addcol">+%d</span>' % r["delta"]
        elif r["delta"] < 0:
            chg = '<span class="delcol">%d</span>' % r["delta"]
        else:
            chg = "0"
        refs = []
        if r["added"]:
            refs.append('<span class="addcol">+ %s</span>' % _esc(r["added"]))
        if r["removed"]:
            refs.append('<span class="delcol">− %s</span>' % _esc(r["removed"]))
        rows.append({
            "k": r["status"], "cls": _KIND_CLASS.get(r["status"], ""),
            "cells": [_badge(r["status"]), _code(r["pn"]),
                      "%d → %d" % (r["a_qty"], r["b_qty"]), chg,
                      " ".join(refs) or '<span class="dim">—</span>'],
        })
    return rows


def _key_row_cells(kind, obj_cell, fields, where):
    return {"k": kind, "cls": _KIND_CLASS.get(kind, ""),
            "cells": [_badge(kind), obj_cell, _fields_html(fields, mono=True),
                      where]}


def _key_rows(result, net_result):
    """HTML rows for the two kinds that always need a human."""
    rows = []
    for it in _key_items(result, net_result):
        if it["pin"]:
            obj = ('%s <span class="dim">.</span>%s'
                   % (_code(it["refdes"]), _esc(it["pin"])))
        else:
            obj = _code(it["refdes"])
        rows.append(_key_row_cells(
            it["kind"], obj, it["changes"],
            _esc(it["where"]) or '<span class="dim">—</span>'))
    return rows


def _chip(label, value, sub="", pane=None, tbl=None, kind=None, hot=False,
          zero=False):
    """One summary tile. Clickable when it has somewhere to jump to."""
    cls = "chip" + (" hot" if hot else "") + (" zero" if zero else "")
    inner = ('<span class="k">%s</span><span class="v">%s</span>'
             '<span class="d">%s</span>' % (_esc(label), _esc(value), _esc(sub)))
    if not pane:
        # A plain div, not a disabled button: disabled buttons get greyed out
        # by the browser and stop matching the tiles next to them.
        return '<div class="%s">%s</div>' % (cls, inner)
    return ('<button class="%s" onclick="jump(\'%s\',\'%s\',\'%s\')">%s'
            "</button>" % (cls, pane, tbl or "", kind or "*", inner))


def build_diff_html(result, part_rows, net_result, meta_a, meta_b, scope="all"):
    """Render the whole report as one self-contained HTML document."""
    stats = result["stats"]
    total = sum(stats[name] for name, _ in DIFF_KINDS)
    do_bom = scope in ("bom", "all")
    do_net = net_result is not None and scope in ("net", "all")
    ns = net_result["stats"] if do_net else {}

    nav, panes = [], []

    def pane(pid, label, count_html, body):
        nav.append('<button data-p="%s"%s onclick="go(\'%s\')">%s%s</button>'
                   % (pid, "" if nav else ' class="on"', pid, _esc(label),
                      count_html))
        panes.append('<section class="pane%s" id="pane-%s">%s</section>'
                     % ("" if panes else " on", pid, body))

    # ---- overview -------------------------------------------------------
    ov = ['<div class="lead">']
    for line in _lead(meta_a, meta_b, result, net_result, part_rows, scope):
        ov.append("<div>%s</div>" % line)
    ov.append("</div>")

    ov.append('<div class="chips">')
    if do_bom:
        ov.append(_chip("元件总数", "%d → %d" % (meta_a["count"], meta_b["count"]),
                        "位号数量"))
        for name, shown in DIFF_KINDS:
            n = stats[name]
            ov.append(_chip(shown, str(n), "", "bom", "tbl-bom", shown,
                            hot=shown in KEY_KINDS, zero=not n))
    if do_net:
        ov.append(_chip("引脚改接", str(ns["pin_moved"]), "同一脚换网络", "net",
                        "tbl-conn", "引脚改接", hot=bool(ns["pin_moved"])))
        nnet = ns["net_mod"] + ns["net_ren"] + ns["net_add"] + ns["net_del"]
        ov.append(_chip("网络变化", str(nnet), "增删与构成变化", "net",
                        "tbl-net", "*", zero=not nnet))
    if do_bom:
        ov.append(_chip("受影响料号", str(len(part_rows)), "采购视角", "part",
                        "tbl-part", "*", zero=not part_rows))
    ov.append("</div>")

    keys = _key_rows(result, net_result) if (do_bom or do_net) else []
    if keys:
        ov.append("<h3>需人工确认<span class=\"sub\">同位置换料、同一脚改接网络"
                  "</span></h3>")
        ov.append(_table("tbl-key", ["类型", "对象", "变更", "图纸页"], keys))
    hist = _sheet_histogram(result) if do_bom else []
    if hist:
        rows = [{"k": "s", "cls": "",
                 "cells": [_code(sh), str(n)]} for sh, n in hist]
        ov.append("<h3>差异分布<span class=\"sub\">按原理图图纸页，"
                  "便于分派核对</span></h3>")
        ov.append(_table("tbl-sheet", ["图纸页", "元件差异"], rows,
                         num_cols=(1,)))

    ov.append('<div class="src"><div>旧版 <b>%s</b> &nbsp;<code>%s</code></div>'
              "<div>新版 <b>%s</b> &nbsp;<code>%s</code></div>"
              "<div>比对基于 icdb 导出的快照；导出之后工程若又改动，"
              "需重新导出再比。</div></div>"
              % (_esc(meta_a["label"]), _esc(meta_a["work"]),
                 _esc(meta_b["label"]), _esc(meta_b["work"])))
    pane("overview", "概览", "", "".join(ov))

    # ---- component layer ------------------------------------------------
    if do_bom:
        body = ["<h2>元件变更<span class=\"sub\">以位号为主键，一行一个位号"
                "</span></h2>",
                '<p class="desc">位号是板上的物理位置，同一位置换了料正是 ECO '
                "要抓的事。表中只列真正变了的字段，未变化的不占列。</p>"]
        body.append(_table(
            "tbl-bom", ["类型", "位号", "变更", "图纸页"], _bom_rows(result),
            kinds=[s for _n, s in DIFF_KINDS],
            note="共 %d 处差异，%d 个位号未变。" % (total, stats["unchanged"])))
        pane("bom", "元件变更", '<span class="n">%d</span>' % total,
             "".join(body))

    # ---- net layer ------------------------------------------------------
    if do_net:
        pins = _pin_rows(net_result)
        moved = [r for r in pins if r["k"] == "引脚改接"]
        rest = [r for r in pins if r["k"] != "引脚改接"]
        body = ["<h2>网络变更<span class=\"sub\">连到了一起的东西变了没有"
                "</span></h2>",
                '<p class="desc">以 (位号, 引脚) 为主轴：网络自动重编号不会移动'
                "任何引脚，所以这里天生没有命名噪声。BOM 只会说多了几个元件，"
                "接在哪里要看这一页。</p>"]
        if moved:
            body.append("<h3>引脚改接<span class=\"sub\">同一引脚接到了别的网络"
                        "</span></h3>")
            body.append(_table("tbl-conn", ["类型", "位号", "引脚", "改接"],
                               moved))
        net_rows = _net_rows(net_result)
        if net_rows:
            body.append("<h3>网络增减与构成变化<span class=\"sub\">另一个视角，"
                        "与上表是同一批事件</span></h3>")
            body.append(_table(
                "tbl-net", ["状态", "网络", "变化"], net_rows,
                kinds=list(NET_STATUSES),
                note="这一节与引脚改接看的是同一批事件，已分开计数，"
                     "不可相加。"))
        if rest:
            body.append("<h3>接点增减<span class=\"sub\">新增元件的落点、"
                        "既有元件断开的脚</span></h3>")
            body.append(_table("tbl-pins", ["类型", "位号", "引脚", "网络"],
                               rest))
        cnt_html = ('<span class="n">%d 改接 · %d 网络</span>'
                    % (ns["pin_moved"], len(net_rows)))
        pane("net", "网络变更", cnt_html, "".join(body))

    # ---- part layer -----------------------------------------------------
    if do_bom and part_rows:
        pane("part", "料号视角",
             '<span class="n">%d</span>' % len(part_rows),
             "<h2>料号视角<span class=\"sub\">采购关心的用量变化</span></h2>"
             '<p class="desc">与元件变更是同一批变化的另一个视角，'
             "不要与上表相加。</p>"
             + _table("tbl-part", ["状态", "料号", "用量", "增减", "位号变化"],
                      _part_rows(part_rows),
                      kinds=["仅新版有", "仅旧版有", "用量变化"],
                      num_cols=(2, 3)))

    return fill_template(
        load_template(),
        LABEL_A=_esc(meta_a["label"]),
        LABEL_B=_esc(meta_b["label"]),
        NAV="".join(nav),
        PANES="".join(panes))


def write_diff_html(html_text, out_dir):
    """Write the HTML report and return its path."""
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    path = os.path.join(out_dir, "diff_report.html")
    with io.open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(html_text)
    return path
