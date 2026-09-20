# -*- coding: utf-8 -*-
"""
probe_com.py -- DxDesigner (ViewDraw) COM automation probe.  READ-ONLY.

Python equivalent of probe_readonly.vbs, plus one thing VBS cannot easily do:
when the server is created through the type library (EnsureDispatch), we can
dump the RUNTIME member table (_prop_map_get_ / _method_map_), which proves
what this specific 7.9.5 build actually exposes -- not what a document claims.

Nothing is modified. No project is opened. Every probe is individually guarded.
"""
import os
import sys
import time

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "probe_com_out.txt")

_buf = []


def W(s=""):
    s = str(s)
    _buf.append(s)
    print(s)


def flush():
    try:
        with open(OUT, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(_buf) + "\n")
        print("\n[written] %s" % OUT)
    except Exception as e:
        print("[!] could not write log: %s" % e)


W("=" * 62)
W(" DxDesigner / ViewDraw COM probe   (READ-ONLY)")
W("=" * 62)
W("time       : %s" % time.strftime("%Y-%m-%d %H:%M:%S"))
W("python     : %s" % sys.version.split()[0])
W("exe        : %s" % sys.executable)
W("SDD_HOME   : %s" % os.environ.get("SDD_HOME", "(unset)"))
W("SDD_VERSION: %s" % os.environ.get("SDD_VERSION", "(unset)"))
W("")

try:
    import pythoncom
    import win32com.client as wc
    from win32com.client import gencache
except Exception as e:
    W("[FATAL] pywin32 not importable: %s" % e)
    flush()
    sys.exit(3)

pythoncom.CoInitialize()

app = None
attached = False
created = False
bound = False          # True when we have a typelib-bound (early-bound) object

# ----------------------------------------------------------------------
W("--- step 1: obtain the automation server ---")
try:
    app = wc.GetActiveObject("ViewDraw.Application")
    attached = True
    W("  [ ok ] attached to a RUNNING instance")
except Exception as e:
    W("  [info] no running instance (%s)" % e)

if app is None:
    try:
        app = gencache.EnsureDispatch("ViewDraw.Application")
        created = True
        bound = True
        W("  [ ok ] created NEW instance via EnsureDispatch (typelib bound)")
    except Exception as e1:
        W("  [warn] EnsureDispatch failed: %s" % e1)
        try:
            app = wc.Dispatch("ViewDraw.Application")
            created = True
            W("  [ ok ] created NEW instance via late-bound Dispatch")
        except Exception as e2:
            W("  [FAIL] Dispatch failed: %s" % e2)
            W("")
            W("  -> COM server not usable. Stop here.")
            flush()
            sys.exit(1)

if attached:
    # an attached object is late-bound by default; try to upgrade it
    try:
        app = gencache.EnsureDispatch(app)
        bound = True
    except Exception:
        pass

W("")

# ----------------------------------------------------------------------
W("--- step 2: application-level properties ---")


def probe(label, fn):
    try:
        v = fn()
        W("  [ ok ] %-34s = %s" % (label, v))
        return v
    except Exception as e:
        W("  [FAIL] %-34s -> %s" % (label, e))
        return None


probe("Application.Version", lambda: app.Version)
probe("Application.Name", lambda: app.Name)
probe("Application.FullName", lambda: app.FullName)
probe("Application.Visible", lambda: app.Visible)
probe("Application.SilentMode", lambda: app.SilentMode)
probe("Application.Caption", lambda: app.Caption)
W("")

# ----------------------------------------------------------------------
W("--- step 3: runtime member table (what this build really exposes) ---")
if bound:
    cats = []
    for attr, tag in (("_prop_map_get_", "properties(read)"),
                      ("_prop_map_put_", "properties(write)"),
                      ("_method_map_", "methods")):
        d = getattr(app, attr, None)
        if isinstance(d, dict):
            cats.append((tag, sorted(d.keys())))
    for tag, names in cats:
        W("  %s  (%d)" % (tag, len(names)))
    W("")

    allnames = set()
    for tag, names in cats:
        allnames.update(names)

    KEY = ["addpartinstance", "addnet", "addpinatlocation", "addsymbolinstance",
           "addline", "addarc", "addtext", "addattribute", "addbatchattributes",
           "addlabel", "addcomponent", "addnetlabel", "addbus", "addport",
           "query", "executecommand", "openproject", "closeproject",
           "saveproject", "exportsymbol", "importsymbol", "registerolecommand",
           "geticdbdesignrootblock", "designcomponents", "designpaths",
           "activedocument", "activeview", "commandbars", "symbolpartitions",
           "createblock", "deletecomponent", "deletenet", "refresh", "quit",
           "version", "visible", "silentmode", "setclientadvisor"]
    lowered = {n.lower(): n for n in allnames}
    W("  --- key-member presence check ---")
    hit = miss = 0
    for k in KEY:
        if k in lowered:
            hit += 1
            W("    [HIT ] %s" % lowered[k])
        else:
            miss += 1
            W("    [ -- ] %s" % k)
    W("  => %d hit / %d absent (of %d checked)" % (hit, miss, len(KEY)))

    W("")
    W("  --- members containing Add/New/Create/Delete/Import/Export ---")
    for n in sorted(allnames):
        ln = n.lower()
        if any(ln.startswith(p) or ("add" in ln and ln.startswith("add"))
               for p in ("add", "new", "create", "delete", "import", "export",
                         "remove", "insert", "open", "save", "query")):
            W("    %s" % n)
else:
    W("  [info] server is late-bound; no runtime member table available.")
    W("         probing a fixed name list through IDispatch::GetIDsOfNames")
    W("")
    KEY = ["AddPartInstance", "AddNet", "AddPinAtLocation", "AddSymbolInstance",
           "AddLine", "AddText", "AddAttribute", "AddLabel", "Query",
           "ExecuteCommand", "OpenProject", "ExportSymbol",
           "RegisterOLECommand", "GetiCDBDesignRootBlock", "DesignComponents",
           "DesignPaths", "ActiveDocument", "ActiveView", "CommandBars",
           "Version", "Visible", "Quit"]
    oleobj = getattr(app, "_oleobj_", None)
    hit = miss = 0
    for k in KEY:
        try:
            disp, _ = oleobj.GetIDsOfNames(k)
            hit += 1
            W("    [HIT ] %-28s dispid=%s" % (k, disp))
        except Exception:
            miss += 1
            W("    [ -- ] %s" % k)
    W("  => %d hit / %d absent" % (hit, miss))
W("")

# ----------------------------------------------------------------------
W("--- step 4: collections prove the object model is live ---")
cb_names = []
try:
    bars = app.CommandBars
    n = 0
    for cb in bars:
        n += 1
        try:
            if n <= 30:
                cb_names.append(cb.Name)
        except Exception:
            pass
    W("  [ ok ] CommandBars enumerated, count = %d" % n)
    for nm in cb_names:
        W("      bar: %s" % nm)
except Exception as e:
    W("  [FAIL] enumerate CommandBars -> %s" % e)
W("")

for label, path in (("DesignComponents.Count", lambda: app.DesignComponents.Count),
                    ("DesignPaths.Count", lambda: app.DesignPaths.Count)):
    probe(label, path)
W("")

# ----------------------------------------------------------------------
W("--- step 5: active document / view (empty when no design is open) ---")
doc = view = None
try:
    doc = app.ActiveDocument
    W("  [ ok ] ActiveDocument.Name = %s" % doc.Name)
except Exception as e:
    W("  [info] no ActiveDocument (%s)" % e)
try:
    view = app.ActiveView
    W("  [ ok ] ActiveView.Name = %s" % view.Name)
except Exception as e:
    W("  [info] no ActiveView (%s)" % e)
W("")

# ----------------------------------------------------------------------
W("--- step 6: deferred to phase 2 (needs an OPEN design; NOT touched here) ---")
for s in ("ProjectData.GetiCDBDesignRootBlock",
          "Block.AddPartInstance / AddNet / AddPinAtLocation",
          "Application.OpenProject"):
    W("          %s" % s)
W("")

# ----------------------------------------------------------------------
W("--- step 7: cleanup ---")
if created:
    try:
        app.Quit()
        W("  [ ok ] created instance closed")
    except Exception as e:
        W("  [warn] Quit failed -> %s" % e)
else:
    W("  [info] attached instance left RUNNING (not ours to close)")
W("")
W("=== probe finished ===")

flush()
