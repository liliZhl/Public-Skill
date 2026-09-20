# -*- coding: utf-8 -*-
"""
dump_typelib.py -- list every interface / method / enum in a DxDesigner typelib.

This is the authoritative way to find out what a given EE build really exposes.
Never trust blog posts or documentation versions; read the typelib on the machine.

usage:
    python dump_typelib.py [typelib] [outfile]

default typelib: %SDD_HOME%\\wv\\win32\\bin\\viewdraw.tlb
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import pythoncom
from pythoncom import LoadTypeLib

KIND = {0: "ENUM", 1: "RECORD", 2: "MODULE", 3: "INTERFACE",
        4: "DISPATCH", 5: "COCLASS", 6: "ALIAS", 7: "UNION"}


def default_tlb():
    sdd = os.environ.get("SDD_HOME", "")
    if sdd:
        p = os.path.join(sdd, "wv", "win32", "bin", "viewdraw.tlb")
        if os.path.exists(p):
            return p
    for drive in ("C", "D", "E", "F"):
        for pat in (r"\%s\MentorGraphics" % drive,):
            pass
    return ""


def main():
    tlb_path = sys.argv[1] if len(sys.argv) > 1 else default_tlb()
    out = sys.argv[2] if len(sys.argv) > 2 else "typelib-api.txt"
    if not tlb_path or not os.path.exists(tlb_path):
        print("typelib not found; pass the path explicitly.")
        print("typical: <SDD_HOME>\\wv\\win32\\bin\\viewdraw.tlb")
        return 2

    lines = []

    def W(s=""):
        s = str(s)
        lines.append(s)
        print(s)

    W("file : %s  (%d bytes)" % (tlb_path, os.path.getsize(tlb_path)))
    tlb = LoadTypeLib(tlb_path)
    W("name : %s" % tlb.GetDocumentation(-1)[0])
    W("attr : %s" % (tlb.GetLibAttr(),))
    n = tlb.GetTypeInfoCount()
    W("typeinfos: %d" % n)
    W("")

    items = []
    for i in range(n):
        try:
            ti = tlb.GetTypeInfo(i)
            ta = ti.GetTypeAttr()
            items.append((ti.GetDocumentation(-1)[0] or "(unnamed)",
                          KIND.get(ta.typekind, "kind%d" % ta.typekind), ti, ta))
        except Exception as e:
            W("[skip %d] %s" % (i, e))

    W("-" * 70)
    W("SUMMARY")
    W("-" * 70)
    by = {}
    for name, kind, ti, ta in items:
        by.setdefault(kind, []).append(name)
    for k in sorted(by):
        W("  %-10s %3d : %s" % (k, len(by[k]), ", ".join(sorted(by[k]))))
    W("")

    W("-" * 70)
    W("DETAIL")
    W("-" * 70)
    add_index = []
    for name, kind, ti, ta in items:
        if kind not in ("INTERFACE", "DISPATCH"):
            continue
        W("")
        W("### %s  [%s]" % (name, kind))
        meths, props = [], []
        for j in range(ta.cFuncs):
            try:
                fd = ti.GetFuncDesc(j)
                nm = ti.GetNames(fd.memid)
                if not nm:
                    continue
                sig = "%s(%s)" % (nm[0], ", ".join(nm[1:]))
                if fd.invkind == 2:
                    sig += "   [PROPERTY-PUT]"
                elif fd.invkind == 4:
                    sig += "   [PROPERTY-PUTREF]"
                meths.append(sig)
                if nm[0].lower().startswith("add"):
                    add_index.append((name, sig))
            except Exception as e:
                meths.append("<func %d: %s>" % (j, e))
        for j in range(ta.cVars):
            try:
                nm = ti.GetNames(ti.GetVarDesc(j).memid)
                if nm:
                    props.append(nm[0])
            except Exception:
                pass
        if props:
            W("    properties: %s" % ", ".join(sorted(set(props))))
        for s in sorted(set(meths)):
            W("      %s" % s)

    W("")
    W("-" * 70)
    W("ENUM VALUES (usable constants)")
    W("-" * 70)
    for name, kind, ti, ta in items:
        if kind != "ENUM":
            continue
        members = []
        for j in range(ta.cVars):
            try:
                vd = ti.GetVarDesc(j)
                nm = ti.GetNames(vd.memid)
                if nm:
                    members.append("%s=%s" % (nm[0], vd.value))
            except Exception:
                pass
        if members:
            W("  %-26s %s" % (name, ", ".join(members)))

    W("")
    W("-" * 70)
    W("INDEX: members starting with 'Add'")
    W("-" * 70)
    for iface, sig in add_index:
        W("  %-24s %s" % (iface, sig))

    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    print("\n[written] %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
