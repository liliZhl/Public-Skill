---
name: ee-icdb-export
description: Export schematic/BOM/netlist data out of a Mentor/Siemens Expedition EE design (DxDesigner → Expedition flow, iCDB databases) without opening the GUI, without COM and without a 32-bit interpreter, by driving Mentor's own command-line exporter (icdb2csv). Yields part number, reference designator, Value, Value1, part name, cell/package, sheet page and pin-level netlist. Use when asked to "从 EE 取 BOM", "不打开 EE 读工程数据", "外部工具读原理图数据库", "把工程的 BOM 导出来", "从工程直接导 BOM", "read an Expedition project outside the GUI", "extract BOM from a .prj / iCDB", "why does icdb2csv report no properties found in prp file", or when another tool (Python, Excel, a web app) needs design data that would otherwise require a human clicking a menu inside EE. Also covers ECO revision comparison between two versions of the same design, at both the component layer (keyed on reference designator) and the netlist layer (pin moves, net add/remove/rename, composition changes), writing a self-contained HTML report and a multi-sheet Excel workbook (CSV on request). Use for "比较两个版本的 BOM", "ECO 变更核对", "改版前后差异", "这版改了什么", "对比两个版本的网络", "网络连接有没有变", "哪些引脚改接了", "compare two revisions of a BOM", "compare netlists between revisions", "what changed between two versions of the design", "give me an HTML diff report". Also documents the traps of the plain-text Netlist.aug fallback.
agent_created: true
---

# Reading Expedition EE data from outside the GUI

Mentor ships a **standalone command-line exporter** with EE. It needs no GUI,
no COM automation and no 32-bit interpreter, and any agent or script can drive
it:

```
<SDD_HOME>\iCDB\win32\bin\icdb2csv.exe
```

It dumps the design's iCDB into tab-delimited tables, from which a complete BOM
can be built — part number, reference designator, Value, Value1, part name,
cell (package), sheet page, plus pin-level connectivity.

## The whole trick, in one line

```bash
icdb2csv.exe -icdb=<design>\database -project=<absolute path>\<x>.prj \
             -output=<out dir> -offline -properties
```

**`-properties` is mandatory.** Mentor documents it as *"created additional
prop_ID on demand"*. Without it the tool exits with
`ERROR: no properties found in prp file!` and writes nothing — and that message
sends you hunting for a missing property file in the `.prj`. There is no
property file to find. Add the flag. See `references/troubleshooting.md` §1.

## Interaction contract — read this before running anything

The failure mode to avoid is **asking the user for a path they should not have
to know**. The project is discoverable; find it yourself.

1. **Scan before you ask.** Never open with "where is your project?". Silently
   try the working directory, its parent, any directory the user already named,
   and any path in the config file. Use `--pick <dir>` for a machine-readable
   candidate list, `--find <dir> --numbered` for a human-readable one.
2. **Offer candidates, never guess.** More than one project → numbered list and
   let the user choose. Exactly one → name it and proceed. None → *now* ask for
   a directory, offering the ones you tried.
3. **Confirm before the run.** The export rewrites `icdb.dat` and the database
   is single-writer. State the project, the output directory and the "close EE
   first" requirement, then get an explicit yes — even if the user already
   said "导出".
4. **Close the loop.** Report the component/part counts, point at the CSVs and
   the `work\` directory, and offer two or three concrete next steps. Do not
   end on "done".
5. **Abort cleanly.** If the user cancels at any prompt, stop and say so. Never
   fall back to exporting a project you picked on their behalf.
6. **For a comparison, present the checkboxes; do not pre-decide.** Which
   layers matter and which formats are wanted depends on the ECO under review,
   so a comparison is a *multi-select*, not a default. Ask with your own
   multi-select UI, offering exactly these five (get them from
   `--diff-options`, which emits this list as JSON):

| id | label | detail |
|---|---|---|
| `bom` | 元件级 BOM 对比 | 换料 / 改值 / 新增 / 删除，以位号为主键 |
| `net` | 网络级对比 | 引脚改接 / 网络增删 / 构成变化，看连接有没有动 |
| `html` | 网页报告 | 单文件自包含，浏览器直接打开，按钮切页，可打印 |
| `excel` | Excel 报表 | 一个文件多张工作表，每类差异一张；双击即开，可筛选可透视 |
| `csv` | CSV 长表 | 单张纯文本表，供脚本或程序读取；给人看请用 Excel 报表 |

Map the answer onto the flags: `bom`+`net` → `--scope all`, only `bom` →
`--scope bom`, only `net` → `--scope net`; add `--no-html` / `--no-excel` for
anything not ticked, and `--csv` only when CSV is ticked. Default when the user
has no preference: everything but CSV. Never silently drop the netlist layer:
on the design measured below it was the half that carried the actual intent.

### Implementing the choice

| Host capability | How to run it |
|---|---|
| Native picker / question UI | `--pick <dir>` → map `choices[].label` to the options → pass the chosen `prj` to `--prj`. For a comparison: `--diff-options <dir>` → `candidates[]` for the two sides, `options[]` for the checkbox |
| Terminal only | `--interactive` — asks which task first, then either export or the comparison wizard (which renders a real checkbox: arrows move, space toggles, Enter confirms) |
| Terminal, comparison only | `--diff` with no paths, optionally `--diff --find-root <dir>` |
| Fully scripted batch | `--prj ... --bom`, or `--diff <A> <B> --scope net --no-html`, no interaction at all |

The comparison wizard also works non-interactively: when stdin is not a
terminal it prints the same checkbox list and accepts comma-separated numbers,
so one code path serves a pipe and a human.

`--pick` emits JSON with only *usable* projects (a `.prj` whose `iCDBDir`
exists) in `choices[]`, each carrying `index`, `label`, `prj`, `project_dir`,
`design`, `root_block`, `snapshot`, `icdb_dir`. Unusable ones are listed
separately under `unusable[]` so you can explain why they were skipped instead
of silently hiding them.

## Workflow

### 0. Confirm the environment (once per machine)

```bash
python scripts/icdb_export.py --doctor
```

Resolves `SDD_HOME` and checks the exporter is present. If it fails, the tool
is not installed or lives somewhere unusual — pass `--sdd <path>`. `SDD_HOME`
is the directory containing both `iCDB\` and `standard\`.

### 1. Locate the project

```bash
python scripts/icdb_export.py --find "<directory>" --numbered   # numbered list
python scripts/icdb_export.py --pick "<directory>"              # JSON for a picker
```

Prints every `.prj` found, with root block, front-end snapshot and whether the
iCDB directory is present. A project is usable only if its `iCDBDir` (declared
in the `.prj`, usually `database`) actually exists.

### 1b. Or let the user drive it

```bash
python scripts/icdb_export.py --interactive            # scan cwd, then a menu
python scripts/icdb_export.py --interactive --find-root "E:\designs"
```

Step one is a task choice: **export one revision** or **compare two**.
Choosing "compare" hands off to the comparison wizard, which picks both sides
(from projects *and* existing exports), then shows the checkbox of layers and
formats. `--diff` with no paths goes straight to that wizard.

### 2. Export

```bash
python scripts/icdb_export.py --prj "<project>\<design>.prj" --bom   # tables + BOM CSVs
python scripts/icdb_export.py --prj "<project>\<design>.prj" --csv-only   # tables only
```

`--prj` accepts the `.prj` file **or** the directory containing it. Default
output: `<project dir>\icdb_export\`. Use `--out` to change it.

### 3. Read the result

Tables land in `<out>\work\`. The schema is in `references/icdb-data-format.md`.
The three that matter most:

| Table | Meaning |
|---|---|
| `database.sym` | one row per component: RefDes, Part Name, Part Number, Sheet |
| `database.spr` | per-component properties, keyed by **property number** |
| `database.prt` | the part catalogue |

Property numbers are **design-specific** — always resolve them through
`database.prm`, never hard-code `12 = Value`. The bundled script does this.

## Comparing two revisions (ECO diff)

`--diff` answers "what changed between these two versions" — the ECO review
question. Both sides accept either an already-exported directory or a project
path:

```bash
# preferred: two existing exports, fully offline — EE is never touched
python scripts/icdb_export.py --diff "<A>/icdb_export" "<B>/icdb_export"

# or two projects; each is exported first (this writes icdb.dat)
python scripts/icdb_export.py --diff "<v1 project>" "<v2 project>" --label v1 v2
```

A path holding `work/database.sym` is read as an export; a path holding a `.prj`
is exported first. `--label` names the two sides in the report. Reports default
to `./icdb_diff/`.

**Component layer — keyed on the reference designator.** Positions are compared
by refdes, never by part number: a refdes is the physical position on the board,
and *a position reused for a different part* is exactly what an ECO review has
to catch.

| Category | Rule |
|---|---|
| 更换料号 (swapped) | same refdes, different part number |
| 变更参数 (revalued) | same refdes and part number; Value / Value1 / Part Name differ |
| 新增元件 (added) | refdes present only in the newer revision |
| 删除元件 (removed) | refdes present only in the older revision |

Cell Name and Sheet page are deliberately **excluded** from the verdict — why,
and what it changed, is in `references/diff-design.md` §2.

**Netlist layer — keyed on the pin, not the net name.** `--scope net` compares
`work\database.ppn`, one row per pin with the net it sits on. The primary axis
is `(refdes, pin) → net`, because EE auto-generates net names and renumbers
them whenever symbol ordering shifts; a pin-keyed comparison is immune to that
by construction.

| Category | Meaning |
|---|---|
| 引脚改接 (pin moved) | same refdes and pin, different net — **the counted difference** |
| 新增接点 / 断开接点 | a pin existing on one side only — context, one row per pin |
| 网络构成变化 | a net on both sides whose pin set differs |
| 网络改名 | unmatched nets paired by endpoint overlap — a rename, not a delete+add |
| 新增网络 / 删除网络 | no counterpart on the other side |
| 命名漂移 | an auto-named net renumbered with identical connections — **not a change** |

Only 引脚改接 feeds the net-layer total. The other rows are a second view of
the same events, exactly as 受影响料号 is a second view of the component
changes — adding them in would double-count. Rationale, rename-pairing rules
and the real-ECO evidence: `references/diff-design.md` §4 and §6.

### Output — written together by default

Console: both layers with their own subtotals (deliberately not summed into one
number — they answer different questions), plus a `需人工确认` line naming the
categories that need eyes.

| File | Contents |
|---|---|
| `diff_report.html` | the report for reading. One self-contained file — **no CDN, no external reference**, so it opens from a file share on an isolated network. Plain-language conclusion first, then clickable count tiles, then the changes needing review. Button nav between 概览 / 元件变更 / 网络变更 / 料号视角; each table has its own filter row; dark-mode and print stylesheets included |
| `diff_report.xlsx` | the report for working in. Sheets **概览 / 元件级差异 / 引脚改接 / 网络变化 / 料号视角**, header row frozen and filterable, change kinds colour-coded |
| `diff_report.csv` | only with `--csv`: the same findings as one plain-text table, columns `分类 / 差异类型 / 对象 / 明细 / 旧版本 / 新版本 / 说明`, one row per changed field. For scripts; Excel is the better answer for a human |

Expect **different row counts between the HTML and the workbook**: Excel is
field-level (a part changed twice gets two rows, so 变更项 stays filterable),
the page is object-level. The workbook's 元件 / 引脚 / 料号 row counts must match
the CSV's, though — that is a self-check worth running. Details:
`references/diff-design.md` §5.

Drop a format with `--no-html` / `--no-excel`; narrow the analysis with
`--scope bom` or `--scope net`. The CSV is UTF-8-SIG (Excel opens it on
double-click).

## Hard rules — each one learned the hard way

1. **Always pass `-properties`.** Omitting it is the single most common failure.
2. **`-project=` must be an absolute path** unless the working directory is
   exactly the project directory. A bare filename from elsewhere gives
   `ERROR: project file (.prj) access scan fail.`
3. **Never trust the exit code.** `icdb2csv` returns `0` for some hard failures
   (bad `-icdb`, unwritable `-output`). Verify `<out>\work\database.esf` exists.
4. **Always pass `-offline`.** Without it the tool starts a real iCDB server
   session that can keep the database locked; the next export then fails with
   no diagnostic at all. The script retries automatically for this reason.
5. **Do not copy the project and export from the copy.** iCDB records its own
   session state inside `icdb.dat`, so a copy is rejected with *"is
   inconsistent. It has been manually copied while the iCDB Server was
   running."* The repair path (`iCDBProjectBackup.exe`) is **GUI-only**.
   Export in place.
6. **Close EE first.** The database is single-writer.
7. **The export rewrites `icdb.dat`** (session bookkeeping: host, user, access
   stamp). Size stays stable **for unchanged content**, and the exported data is
   byte-for-byte reproducible — but do not be alarmed when the hash changes.
   The script reports both hashes. Note the size *does* change when the design
   itself changed in between (measured 3 688 256 → 3 214 032 bytes across an
   editing session) — that is content, not bookkeeping.

## Files

| Path | Purpose |
|---|---|
| `scripts/icdb_export.py` | CLI entry point: argument parsing, the command dispatchers, the wizards, and the diff report orchestrator. stdlib only |
| `scripts/ee_env.py` | `SDD_HOME` discovery, environment building, project scanning |
| `scripts/ee_export.py` | running `icdb2csv`, reading the tables, writing the BOM CSVs |
| `scripts/ee_diff.py` | the diff engine (component + netlist) **and** the single normalized record set every renderer reads from |
| `scripts/report_html.py` | HTML report: fills the template, builds the tables |
| `scripts/report_xlsx.py` | multi-sheet Excel workbook |
| `scripts/report_csv.py` | flat CSV (one record per changed field) |
| `scripts/choice_ui.py` | terminal prompts, the checkbox reader, candidate scanning |
| `scripts/xlsx_writer.py` | minimal .xlsx writer (zip + XML) with a read-back helper the self-checks use. stdlib only, no openpyxl |
| `assets/diff_report.tpl.html` | the HTML report document itself — markup, CSS and script, with `{{PLACEHOLDER}}` holes. **Edit the report's look here**, not in Python |
| `references/diff-design.md` | why the diff works the way it does: keys, exclusions, rename pairing, report-format trade-offs, measured ECO evidence, self-check recipes |
| `references/icdb-data-format.md` | every table, every column, property-number map, Netlist.aug |
| `references/troubleshooting.md` | verbatim error → cause → fix, write behaviour, alternatives |
| `README.md` | what it is, how to install it into another agent, configuration |

All of `scripts/` and `assets/` must travel together — the modules import each
other, and the report template is read at render time.

## Going deeper

| Question | Read |
|---|---|
| Why is the change keyed this way? Why is Cell Name ignored? | `references/diff-design.md` §1–§4 |
| Why separate worksheets? Why do row counts differ per format? | `references/diff-design.md` §5 |
| What did a real ECO show? How do I self-check a change? | `references/diff-design.md` §6–§7 |
| `icdb2csv` failed / returned 0 / locked the DB | `references/troubleshooting.md` §1, §4, §5 |
| What is *not* possible here (COM, `.cce`, `icdb2bom.exe`) | `references/troubleshooting.md` §7 |
| What is in each exported table? | `references/icdb-data-format.md` §2 |
| `Netlist.aug` fallback and its traps | `references/icdb-data-format.md` §5 |
