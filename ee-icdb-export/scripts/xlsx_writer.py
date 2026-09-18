# -*- coding: utf-8 -*-
"""Write .xlsx workbooks using nothing but the standard library.

Why not openpyxl: this skill is meant to be copied onto a production machine
that may have no pip and no network access, so the whole tool has to run on a
bare CPython. An .xlsx is just a zip of XML parts, and the subset Excel and
WPS both need to open a workbook is small and stable -- so it is written here
directly rather than pulling in a dependency.

Usage::

    from xlsx_writer import Sheet, write_workbook

    sh = Sheet("概览", widths=[22, 14, 40])
    sh.row([("ECO 差异报告", "title")])
    sh.row([])
    sh.row([("元件级差异", "h2")])
    sh.row(["类型", "数量"], style="head")
    sh.row([("更换料号", "b"), (2, "num")])
    write_workbook("out.xlsx", [sh])

Styles are tokens, combined with commas: ``"num,zebra"``. Unknown tokens are
ignored rather than raising, so a typo degrades to plain text instead of
producing a corrupt file.
"""

import io
import os
import re
import zipfile
import datetime

# --------------------------------------------------------------------------
# style vocabulary
# --------------------------------------------------------------------------

#: font id -> XML fragment. Fixed table; cell formats below reference these.
_FONTS = [
    '<font><sz val="11"/><color rgb="FF222222"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="14"/><color rgb="FF1F3B57"/><name val="Microsoft YaHei"/></font>',
    '<font><sz val="11"/><color rgb="FFC0392B"/><name val="Microsoft YaHei"/></font>',
    '<font><sz val="11"/><color rgb="FF1E8449"/><name val="Microsoft YaHei"/></font>',
    '<font><sz val="11"/><color rgb="FF8A9199"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="11"/><color rgb="FF222222"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="11"/><color rgb="FFC0392B"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="11"/><color rgb="FF1E8449"/><name val="Microsoft YaHei"/></font>',
    '<font><b/><sz val="12"/><color rgb="FF1F3B57"/><name val="Microsoft YaHei"/></font>',
]

#: fill id -> XML fragment. 0 and 1 are mandatory placeholders in the schema.
_FILLS = [
    '<fill><patternFill patternType="none"/></fill>',
    '<fill><patternFill patternType="gray125"/></fill>',
    '<fill><patternFill patternType="solid">'
    '<fgColor rgb="FF2F4F6F"/><bgColor indexed="64"/></patternFill></fill>',
    '<fill><patternFill patternType="solid">'
    '<fgColor rgb="FFF6F8FA"/><bgColor indexed="64"/></patternFill></fill>',
    '<fill><patternFill patternType="solid">'
    '<fgColor rgb="FFFFF7E6"/><bgColor indexed="64"/></patternFill></fill>',
    '<fill><patternFill patternType="solid">'
    '<fgColor rgb="FFEAF1F8"/><bgColor indexed="64"/></patternFill></fill>',
]

_BORDER_PLAIN = ("<border><left/><right/><top/><bottom/><diagonal/></border>")
_BORDER_THIN = (
    '<border><left style="thin"><color rgb="FFD0D7DE"/></left>'
    '<right style="thin"><color rgb="FFD0D7DE"/></right>'
    '<top style="thin"><color rgb="FFD0D7DE"/></top>'
    '<bottom style="thin"><color rgb="FFD0D7DE"/></bottom><diagonal/></border>')

#: token -> the properties it sets. Later tokens in a list win.
_TOKENS = {
    "title":   {"font": 2},
    "h2":      {"font": 9, "fill": 5},
    "head":    {"font": 1, "fill": 2, "border": 1, "align": "center",
                "wrap": True},
    "b":       {"font": 6},
    "k":       {"font": 6, "fill": 3},
    "num":     {"align": "right"},
    "red":     {"font": 3},
    "green":   {"font": 4},
    "grey":    {"font": 5},
    "red-b":   {"font": 7},
    "green-b": {"font": 8},
    "zebra":   {"fill": 3},
    "hot":     {"fill": 4},
    "wrap":    {"wrap": True},
}

_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_TOKEN_SPLIT = re.compile(r"[,\s]+")


def _esc(text):
    """Escape for XML text and strip characters XML 1.0 forbids outright."""
    s = _ILLEGAL.sub("", text if isinstance(text, str) else "%s" % text)
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _esc_attr(text):
    return _esc(text).replace('"', "&quot;")


def safe_sheet_name(name, used):
    """Excel rejects ``[]:*?/\\`` in sheet names and caps them at 31 chars."""
    clean = re.sub(r"[\[\]:*?/\\]", "_", (name or "").strip()) or "Sheet"
    clean = clean[:31]
    base, n = clean, 2
    while clean.lower() in used:
        suffix = "(%d)" % n
        clean = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(clean.lower())
    return clean


def col_letter(index):
    """1 -> A, 27 -> AA."""
    letters = ""
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class _Formats(object):
    """Hands out ``cellXfs`` indexes, creating each combination once.

    Excel indexes formats; emitting one <xf> per cell would bloat the file
    and is unnecessary since the same handful of combinations repeats.
    """

    def __init__(self):
        self._index = {}
        self._xml = []

    def id_for(self, tokens):
        props = {"font": 0, "fill": 0, "border": 0, "align": "", "wrap": False}
        for token in _TOKEN_SPLIT.split(tokens or ""):
            if token in _TOKENS:
                props.update(_TOKENS[token])
        if props["wrap"] and not props["align"]:
            props["align"] = "left"
        key = (props["font"], props["fill"], props["border"],
               props["align"], props["wrap"])
        if key in self._index:
            return self._index[key]

        bits = ['numFmtId="0"', 'fontId="%d"' % props["font"],
                'fillId="%d"' % props["fill"],
                'borderId="%d"' % props["border"], 'xfId="0"']
        if props["font"] or props["fill"] or props["border"]:
            bits.append('applyFont="1"' if props["font"] else 'applyFont="0"')
            bits.append('applyFill="1"' if props["fill"] else 'applyFill="0"')
            bits.append('applyBorder="1"' if props["border"]
                        else 'applyBorder="0"')
        if props["align"] or props["wrap"]:
            bits.append('applyAlignment="1"')
            al = '<alignment vertical="%s"' % (
                "top" if props["wrap"] else "center")
            if props["align"]:
                al += ' horizontal="%s"' % props["align"]
            if props["wrap"]:
                al += ' wrapText="1"'
            al += "/>"
            body = "<xf %s>%s</xf>" % (" ".join(bits), al)
        else:
            body = "<xf %s/>" % " ".join(bits)

        idx = len(self._xml)
        self._index[key] = idx
        self._xml.append(body)
        return idx

    def xml(self):
        return ("<cellXfs count=\"%d\">%s</cellXfs>"
                % (len(self._xml), "".join(self._xml)))


class Sheet(object):
    """One worksheet. Cells are added row by row."""

    def __init__(self, name, widths=None, freeze=0, autofilter=False):
        self.name = name
        self.widths = list(widths or [])
        #: how many leading rows stay put when scrolling
        self.freeze = freeze
        #: put a filter dropdown on the first row
        self.autofilter = autofilter
        self.rows = []

    # -- building --------------------------------------------------------

    def row(self, cells=(), style=""):
        """Add a row. A cell is a value, or ``(value, style)``.

        ``int``/``float`` become real numbers so Excel can sum and sort them;
        strings stay text so part numbers keep leading zeros and never turn
        into scientific notation.
        """
        self.rows.append([_normalise(c, style) for c in cells])
        return self

    def rows_from(self, iterable, style=""):
        for cells in iterable:
            self.row(cells, style)
        return self

    def blank(self, count=1):
        for _ in range(count):
            self.rows.append([])
        return self

    def widths_for(self):
        """Fixed widths if given, else estimated from the content."""
        if self.widths:
            return self.widths
        widths = []
        for row in self.rows:
            for i, (value, _style) in enumerate(row):
                while len(widths) <= i:
                    widths.append(0)
                widths[i] = max(widths[i], _text_width(value))
        return [min(58, max(9, w + 2)) for w in widths]

    # -- serialising -----------------------------------------------------

    def xml(self, formats):
        buf = io.StringIO()
        buf.write('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>')
        buf.write('<worksheet xmlns="%s">' % _NS_MAIN)
        buf.write('<sheetViews><sheetView workbookViewId="0">')
        if self.freeze:
            buf.write('<pane ySplit="%d" topLeftCell="A%d" activePane='
                      '"bottomLeft" state="frozen"/>'
                      '<selection pane="bottomLeft" activeCell="A%d" '
                      'sqref="A%d"/>'
                      % (self.freeze, self.freeze + 1,
                         self.freeze + 1, self.freeze + 1))
        buf.write("</sheetView></sheetViews>")
        buf.write('<sheetFormatPr defaultRowHeight="16"/>')

        widths = self.widths_for()
        if widths:
            buf.write("<cols>")
            for i, w in enumerate(widths, 1):
                buf.write('<col min="%d" max="%d" width="%s" customWidth="1"/>'
                          % (i, i, _num(w)))
            buf.write("</cols>")

        used = set()
        buf.write("<sheetData>")
        for r, row in enumerate(self.rows, 1):
            if not row:
                continue
            buf.write('<row r="%d">' % r)
            for c, (value, style) in enumerate(row, 1):
                if value is None or value == "":
                    if not style:
                        continue
                    # A styled empty cell still needs to exist for a fill or
                    # a border to show up.
                    buf.write('<c r="%s%d" s="%d"/>'
                              % (col_letter(c), r, formats.id_for(style)))
                    continue
                ref = "%s%d" % (col_letter(c), r)
                if isinstance(value, bool):
                    value = "TRUE" if value else "FALSE"
                if isinstance(value, (int, float)):
                    buf.write('<c r="%s" s="%d"><v>%s</v></c>'
                              % (ref, formats.id_for(style), _num(value)))
                else:
                    buf.write('<c r="%s" s="%d" t="inlineStr">'
                              '<is><t xml:space="preserve">%s</t></is></c>'
                              % (ref, formats.id_for(style), _esc(value)))
            buf.write("</row>")
            used.add(r)
        buf.write("</sheetData>")

        if self.autofilter and used:
            last = max(used)
            span = max((len(r) for r in self.rows), default=1)
            if span:
                buf.write('<autoFilter ref="A1:%s%d"/>'
                          % (col_letter(span), last))
        buf.write("</worksheet>")
        return buf.getvalue()


def _normalise(cell, default_style):
    if isinstance(cell, tuple):
        return (cell[0], cell[1])
    return (cell, default_style)


def _num(value):
    """Numbers go into XML without a trailing ``.0``."""
    if isinstance(value, float) and value == int(value):
        return "%d" % int(value)
    return "%s" % value


def _text_width(value):
    """Rough width in Excel character units; CJK glyphs count double."""
    if value is None:
        return 0
    text = "%s" % value
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in text)


# --------------------------------------------------------------------------
# package parts
# --------------------------------------------------------------------------

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"


def _content_types(sheet_count):
    parts = [
        '<Default Extension="rels" ContentType="application/vnd.'
        'openxmlformats-package.relationships+xml"/>',
        '<Default Extension="xml" ContentType="application/xml"/>',
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.spreadsheetml.styles+xml"/>',
        '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
        'openxmlformats-package.core-properties+xml"/>',
        '<Override PartName="/docProps/app.xml" ContentType="application/vnd.'
        'openxmlformats-officedocument.extended-properties+xml"/>',
    ]
    for i in range(1, sheet_count + 1):
        parts.append('<Override PartName="/xl/worksheets/sheet%d.xml" '
                     'ContentType="application/vnd.openxmlformats-'
                     'officedocument.spreadsheetml.worksheet+xml"/>' % i)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/'
            'content-types">%s</Types>' % "".join(parts))


def _root_rels():
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="%s">'
            '<Relationship Id="rId1" Type="%s/officeDocument" '
            'Target="xl/workbook.xml"/>'
            '<Relationship Id="rId2" Type="%s/metadata/core-properties" '
            'Target="docProps/core.xml"/>'
            '<Relationship Id="rId3" Type="%s/extended-properties" '
            'Target="docProps/app.xml"/>'
            "</Relationships>"
            % (_NS_PKG_REL, _NS_REL, _NS_PKG_REL, _NS_REL))


def _workbook_xml(sheets):
    entries = []
    for i, sheet in enumerate(sheets, 1):
        entries.append('<sheet name="%s" sheetId="%d" r:id="rId%d"/>'
                       % (_esc_attr(sheet.name), i, i))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<workbook xmlns="%s" xmlns:r="%s"><sheets>%s</sheets>'
            "</workbook>" % (_NS_MAIN, _NS_REL, "".join(entries)))


def _workbook_rels(sheets, style_rel_id):
    parts = []
    for i in range(1, len(sheets) + 1):
        parts.append('<Relationship Id="rId%d" Type="%s/worksheet" '
                     'Target="worksheets/sheet%d.xml"/>' % (i, _NS_REL, i))
    parts.append('<Relationship Id="%s" Type="%s/styles" '
                 'Target="styles.xml"/>' % (style_rel_id, _NS_REL))
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            "<Relationships xmlns=\"%s\">%s</Relationships>"
            % (_NS_PKG_REL, "".join(parts)))


def _styles_xml(formats):
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<styleSheet xmlns="%s">'
            '<fonts count="%d">%s</fonts>'
            '<fills count="%d">%s</fills>'
            '<borders count="2">%s%s</borders>'
            '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" '
            'borderId="0"/></cellStyleXfs>'
            "%s"
            '<cellStyles count="1"><cellStyle name="Normal" xfId="0" '
            'builtinId="0"/></cellStyles>'
            "</styleSheet>"
            % (_NS_MAIN, len(_FONTS), "".join(_FONTS),
               len(_FILLS), "".join(_FILLS),
               _BORDER_PLAIN, _BORDER_THIN, formats.xml()))


def _stamp():
    """Build stamp for docProps/core.xml.

    Honours SOURCE_DATE_EPOCH so the produced file can be byte-for-byte
    reproducible -- which is what a regression check wants. Without it the
    wall clock is used, as before.
    """
    fixed = os.environ.get("SOURCE_DATE_EPOCH")
    if fixed:
        try:
            when = datetime.datetime.fromtimestamp(int(fixed),
                                                   datetime.timezone.utc)
        except (TypeError, ValueError, OSError, OverflowError):
            pass
        else:
            return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _core_xml(title):
    stamp = _stamp()
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/'
            'package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" '
            'xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
            "<dc:title>%s</dc:title>"
            "<dc:creator>ee-icdb-export</dc:creator>"
            '<dcterms:created xsi:type="dcterms:W3CDTF">%s</dcterms:created>'
            "</cp:coreProperties>" % (_esc(title), stamp))


def _app_xml(sheet_names):
    items = "".join("<vt:lpstr>%s</vt:lpstr>" % _esc(n) for n in sheet_names)
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Properties xmlns="http://schemas.openxmlformats.org/'
            'officeDocument/2006/extended-properties" '
            'xmlns:vt="http://schemas.openxmlformats.org/officeDocument/2006/'
            'docPropsVTypes">'
            "<Application>ee-icdb-export</Application>"
            '<HeadingPairs><vt:vector size="2" baseType="variant">'
            "<vt:variant><vt:lpstr>Worksheets</vt:lpstr></vt:variant>"
            '<vt:variant><vt:i4>%d</vt:i4></vt:variant>'
            "</vt:vector></HeadingPairs>"
            '<TitlesOfParts><vt:vector size="%d" baseType="lpstr">%s'
            "</vt:vector></TitlesOfParts>"
            "</Properties>" % (len(sheet_names), len(sheet_names), items))


def write_workbook(path, sheets, title=None):
    """Write ``sheets`` to ``path`` and return the path.

    Raises ``ValueError`` when there is nothing to write -- an empty workbook
    is never what the caller wanted, and Excel dislikes one anyway.
    """
    sheets = list(sheets)
    if not sheets:
        raise ValueError("write_workbook: no sheets")

    used = set()
    for sheet in sheets:
        sheet.name = safe_sheet_name(sheet.name, used)

    formats = _Formats()
    body = [sheet.xml(formats) for sheet in sheets]   # fills the format table
    style_rel_id = "rId%d" % (len(sheets) + 1)

    parent = os.path.dirname(os.path.abspath(path))
    if parent and not os.path.isdir(parent):
        os.makedirs(parent)

    parts = [
        ("[Content_Types].xml", _content_types(len(sheets))),
        ("_rels/.rels", _root_rels()),
        ("docProps/core.xml", _core_xml(title or sheets[0].name)),
        ("docProps/app.xml", _app_xml([s.name for s in sheets])),
        ("xl/workbook.xml", _workbook_xml(sheets)),
        ("xl/_rels/workbook.xml.rels", _workbook_rels(sheets, style_rel_id)),
        ("xl/styles.xml", _styles_xml(formats)),
    ]
    for i, xml in enumerate(body, 1):
        parts.append(("xl/worksheets/sheet%d.xml" % i, xml))

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, text in parts:
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, text.encode("utf-8"))
    return path


# --------------------------------------------------------------------------
# reader -- for self-checks, not for production use
# --------------------------------------------------------------------------

def read_workbook(path):
    """Read back a workbook written by :func:`write_workbook`.

    Enough to assert that what came out matches what went in. Only handles
    the parts this module writes; it is a test aid, not a general reader.
    """
    from xml.etree import ElementTree as ET

    ns = {"m": _NS_MAIN}
    with zipfile.ZipFile(path) as zf:
        names = zf.namelist()
        wb = ET.fromstring(zf.read("xl/workbook.xml"))
        rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_target = {}
    for rel in rels:
        rel_target[rel.get("Id")] = rel.get("Target")

    out = []
    with zipfile.ZipFile(path) as zf:
        for sheet in wb.find("m:sheets", ns):
            rid = sheet.get("{%s}id" % _NS_REL)
            target = rel_target.get(rid)
            xml = ET.fromstring(zf.read("xl/" + target))
            rows = []
            for row in xml.iter("{%s}row" % _NS_MAIN):
                cells = {}
                for c in row:
                    ref = c.get("r")
                    letter = re.match(r"([A-Z]+)", ref or "")
                    if not letter:
                        continue
                    if c.get("t") == "inlineStr":
                        node = c.find("m:is/m:t", ns)
                        value = node.text if node is not None else ""
                    else:
                        node = c.find("m:v", ns)
                        value = node.text if node is not None else ""
                        try:
                            value = float(value) if "." in value else int(value)
                        except (TypeError, ValueError):
                            pass
                    cells[_col_index(letter.group(1))] = value
                width = max(cells) if cells else 0
                rows.append([cells.get(i) for i in range(1, width + 1)])
            out.append((sheet.get("name"), rows))
    return out


def _col_index(letters):
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n


def sheet_names(path):
    with zipfile.ZipFile(path) as zf:
        xml = zf.read("xl/workbook.xml").decode("utf-8")
    return re.findall(r'<sheet name="([^"]*)"', xml)
