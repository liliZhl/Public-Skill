---
name: ee-icdb-export
description: Export schematic/BOM data out of a Mentor/Siemens Expedition EE design (DxDesigner → Expedition flow, iCDB databases) without opening the GUI, without COM and without a 32-bit interpreter, by driving Mentor's own command-line exporter (icdb2csv). Yields part number, reference designator, Value, Value1, part name, cell/package, sheet page and pin-level netlist. Use when asked to "从 EE 取 BOM", "不打开 EE 读工程数据", "外部工具读原理图数据库", "把工程的 BOM 导出来", "从工程直接导 BOM", "read an Expedition project outside the GUI", "extract BOM from a .prj / iCDB", "why does icdb2csv report no properties found in prp file", or when another tool (Python, Excel, a web app) needs design data that would otherwise require a human clicking a menu inside EE. Also documents the traps of the plain-text Netlist.aug fallback.
agent_created: true
---

# Reading Expedition EE data from outside the GUI

Mentor ships a **standalone command-line exporter** with EE. It needs no GUI,
no COM automation and no 32-bit interpreter, and it can be driven by any agent
or script:

```
<SDD_HOME>\iCDB\win32\bin\icdb2csv.exe
```

It dumps the design's iCDB into a set of tab-delimited tables, from which a
complete BOM can be built — part number, reference designator, Value, Value1,
part name, cell (package), sheet page, plus pin-level connectivity.

## The whole trick, in one line

```bash
icdb2csv.exe -icdb=<design>\database -project=<absolute path>\<x>.prj \
             -output=<out dir> -offline -properties
```

**`-properties` is mandatory.** Mentor documents it as *"created additional
prop_ID on demand"*. Without it the tool exits with
`ERROR: no properties found in prp file!` and writes nothing — and that
message misleads you into hunting for a missing property file in the `.prj`.
There is no property file to find. Add the flag. See
`references/troubleshooting.md` §1.

## Interaction contract — read this before running anything

The failure mode to avoid is **asking the user for a path they should not
have to know**. The project is discoverable; find it yourself.

**Rule 1 — Scan before you ask.** Do not open with "where is your project?".
Silently try, in order: the working directory, its parent, any directory the
user has already named in the conversation, and any path recorded in the
config file. Use `--pick <dir>` for a machine-readable candidate list, or
`--find <dir> --numbered` for a human-readable one.

**Rule 2 — Offer candidates, never guess.** More than one project → show a
numbered list and let the user choose. Exactly one → name it and proceed.
None → *now* ask for a directory, and offer the ones you tried.

**Rule 3 — Confirm before the run.** The export rewrites `icdb.dat` and the
database is single-writer. State the project, the output directory and the
"close EE first" requirement, then get an explicit yes. Do not skip this
because the user already said "导出".

**Rule 4 — Close the loop.** After success, report the component/part counts
and point at the CSVs and the `work\` directory. Offer two or three concrete
next steps; do not end on "done".

**Rule 5 — Abort cleanly.** If the user cancels at any prompt, stop and say
so. Never fall back to exporting a project you picked on their behalf.

### Implementing the choice

| Host capability | How to run it |
|---|---|
| Native picker / question UI | `--pick <dir>` → map `choices[].label` to the options → pass the chosen `prj` to `--prj` |
| Terminal only | `--interactive` — implements project → content → output dir → confirm → export |
| Fully scripted batch | `--prj ... --bom`, no interaction at all |

`--pick` emits JSON with only *usable* projects (a `.prj` whose `iCDBDir`
exists) in `choices[]`, each carrying `index`, `label`, `prj`,
`project_dir`, `design`, `root_block`, `snapshot`, `icdb_dir`. Unusable ones
are listed separately under `unusable[]` so you can explain why they were
skipped instead of silently hiding them.

## Workflow

### 0. Confirm the environment (once per machine)

```bash
python scripts/icdb_export.py --doctor
```

It resolves `SDD_HOME` and checks that the exporter is present. If it fails,
the tool is not installed or lives somewhere unusual — pass `--sdd <path>`.
`SDD_HOME` is the directory that contains both `iCDB\` and `standard\`.

### 1. Locate the project

```bash
python scripts/icdb_export.py --find "<directory>" --numbered   # numbered list
python scripts/icdb_export.py --pick "<directory>"              # JSON for a picker
```

Prints every `.prj` it finds, with the root block, the front-end snapshot and
whether the iCDB directory is present. A project is usable only if its
`iCDBDir` (declared in the `.prj`, usually `database`) actually exists.

### 1b. Or let the user drive it

```bash
python scripts/icdb_export.py --interactive            # scan cwd, then a menu
python scripts/icdb_export.py --interactive --find-root "E:\designs"
```

Four steps — project, content, output directory, confirmation — with the
`icdb.dat` warning shown before the run and next steps shown after it.
Falls back to asking for a directory only when the scan comes up empty.

### 2. Export

```bash
# raw iCDB tables + BOM CSVs
python scripts/icdb_export.py --prj "<project>\<design>.prj" --bom

# or just the tables
python scripts/icdb_export.py --prj "<project>\<design>.prj" --csv-only
```

`--prj` accepts the `.prj` file **or** the directory containing it.
Default output: `<project dir>\icdb_export\`. Use `--out` to change it.

### 3. Read the result

Tables land in `<out>\work\`. The schema is in
`references/icdb-data-format.md`. The three tables that matter most:

| Table | Meaning |
|---|---|
| `database.sym` | one row per component: RefDes, Part Name, Part Number, Sheet |
| `database.spr` | per-component properties, keyed by **property number** |
| `database.prt` | the part catalogue |

Property numbers are **design-specific** — always resolve them through
`database.prm`, never hard-code `12 = Value`. The bundled script does this.

## Hard rules — each one learned the hard way

1. **Always pass `-properties`.** Omitting it is the single most common failure.
2. **`-project=` must be an absolute path** unless the working directory is
   exactly the project directory. A bare filename from elsewhere gives
   `ERROR: project file (.prj) access scan fail.`
3. **Never trust the exit code.** `icdb2csv` returns `0` for some hard
   failures (bad `-icdb`, unwritable `-output`). Verify by checking that
   `<out>\work\database.esf` exists.
4. **Always pass `-offline`.** Without it the tool starts a real iCDB server
   session that can keep the database locked; the next export then fails with
   no diagnostic at all. The script retries automatically for this reason.
5. **Do not copy the project and export from the copy.** iCDB records its own
   session state inside `icdb.dat`, so a copy is rejected with
   *"is inconsistent. It has been manually copied while the iCDB Server was
   running."* The repair path (`iCDBProjectBackup.exe`) is **GUI-only**.
   Export in place.
6. **Close EE first.** The database is single-writer.
7. **The export rewrites `icdb.dat`** (session bookkeeping: host, user,
   access stamp). The file size stays stable, the exported data is
   byte-for-byte reproducible, and the schematic content is not touched —
   but do not be alarmed when the hash changes. EE's own full backup sits at
   `<design>\database\cdbback\<timestamp>.zip`. The script reports both hashes.

## Fallback: the plain-text netlist

`<design>\PCB\Logic\Netlist.aug` is ASCII and can be parsed with no tooling,
but it only carries **part number ↔ reference designator** — no Value, no
part name, no package. It is also only refreshed when someone runs a netlist
export inside EE, so it can be stale. Use it for cross-checking, not as the
primary source. Traps are documented in `references/icdb-data-format.md` §4.

## What does not work here

- **COM automation** (`MGCPCB.Application`, the AutoActive framework) is a real
  API with a 614-type type library, but its CLSID is registered only in
  `Wow6432Node`, so it requires a **32-bit** process — plus a licence checkout.
  More moving parts than the CLI, for the same data.
- **`.cce` files** are XML but wholesale encrypted (`<CCZEncrypt>`).
- **`icdb.dat`, `*.lyt`, `*.lgc`, `*.pdb`** are proprietary binary. Do not parse.
- **`icdb2bom.exe`** exists, but its default config `cdb2bom.asc` is usually
  absent from a 7.9.x install, so `-cfg` fails. Build the BOM yourself from
  the exported tables (that is what `--bom` does).

## Files

| Path | Purpose |
|---|---|
| `scripts/icdb_export.py` | the tool: env discovery, project scan, interaction, export, retry, BOM. stdlib only |
| `references/icdb-data-format.md` | every table, every column, property-number map, Netlist.aug |
| `references/troubleshooting.md` | verbatim error → cause → fix, write behaviour, alternatives |
| `README.md` | what it is, how to install it into another agent, configuration |
