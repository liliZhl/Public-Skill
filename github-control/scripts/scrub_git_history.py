#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""泄露补救：把环境标识符从 **git 历史** 中抹除。

场景：某个提交已经把真实环境标识（主机名 / 域 / 账号 / 内网 IP / 本机路径）
推到了**公开仓库**。仅靠"再提交一版干净的"是不够的 —— 旧提交对象依然能按
SHA 取到内容。本工具用 `git filter-branch` 逐提交重写工作树，把这些标识
从全部历史中替换成占位符。

用法：
    python scrub_git_history.py --repo ~/repos/Public-Skill
    python scrub_git_history.py --repo ~/repos/Public-Skill --push
    python scrub_git_history.py --repo ~/repos/Public-Skill --branch main --push

选项：
    --repo PATH    目标仓库工作副本（必填）
    --push         重写并校验通过后，强制推送该分支覆盖远端
    --branch NAME  指定分支（默认取当前分支）
    --keep-temp    保留临时脱敏器与规则文件，便于排查
    --no-prune     不清理陈旧 ref（默认清理）

设计要点：
  · **脚本内不出现任何环境标识** —— 替换规则直接复用同目录
    `sync_skill_repos.py` 的 `build_rules()`，真实值运行时推导。
  · 逐提交重写采用**字节级读写**，保留换行符与 BOM，不产生整文件 diff。
  · 重写后必须清掉**陈旧 ref**（`refs/original/*`，以及镜像 refspec 产生的
    `refs/origin/*` 等）。只删 reflog 和 gc 不够 —— 只要还有 ref 指向旧提交，
    旧内容就依然可达。这是最容易漏的一步。
  · 重写后做**独立复查**：对全部新提交反扫原始词表与通用模式，命中即报。

重要限制 —— 本工具**无法**清除已经上传到 GitHub 的旧对象。
GitHub 在收到 force push 后不会立即回收，旧提交仍可通过其 SHA 访问一段时间。
做到零残留只有两条路：
    (a) 删除该仓库并重建；
    (b) 请 GitHub Support 清除缓存视图。
本工具只负责让"分支所指的历史"变干净。

依赖：git（含 filter-branch）、Python 3.9+，与 sync_skill_repos.py 同目录。
"""

import argparse
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SYNC_TOOL = HERE / "sync_skill_repos.py"


# ---------------------------------------------------------------------------
# 规则来源：复用同步器的运行时推导，保持单一事实来源
# ---------------------------------------------------------------------------

def load_sync_module():
    if not SYNC_TOOL.is_file():
        print("找不到 %s（本工具复用它的规则构建逻辑）" % SYNC_TOOL)
        return None
    spec = importlib.util.spec_from_file_location("_sync_skill_repos", SYNC_TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def git(repo, *args, check=False, capture=True):
    r = subprocess.run(["git", "-C", str(repo)] + list(args),
                       capture_output=capture, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError("git %s 失败: %s"
                           % (" ".join(args), (r.stderr or "").strip()))
    return r


def collect_refs(repo):
    r = git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    refs = {}
    for ln in (r.stdout or "").splitlines():
        parts = ln.split()
        if len(parts) == 2:
            refs[parts[0]] = parts[1]
    return refs


def is_ancestor(repo, sha, head):
    return git(repo, "merge-base", "--is-ancestor", sha, head).returncode == 0


# ---------------------------------------------------------------------------
# 临时脱敏器（生成在仓库之外，不会被纳入版本控制）
# ---------------------------------------------------------------------------

TREE_SCRUBBER = r'''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""由 scrub_git_history.py 生成的一次性工作树脱敏器，不单独使用。"""

import json
import os
import re
import sys
from pathlib import Path

TEXT_EXT = {
    ".md", ".txt", ".py", ".json", ".ini", ".cfg", ".conf", ".yml", ".yaml",
    ".ps1", ".sh", ".bat", ".cmd", ".toml", ".env", ".xml", ".html", ".css",
    ".js", ".ts", ".tsx", ".c", ".h", ".cpp", ".rs", ".go", ".java",
}


def main():
    root = Path(sys.argv[1])
    rules = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    skip = set(rules[0].pop("_skip", [])) if rules else set()
    compiled = []
    for r in rules:
        r.pop("_skip", None)
        compiled.append((re.compile(r["pattern"], r["flags"]), r["holder"]))

    changed = []
    for dirpath, dirnames, filenames in os.walk(root):
        for d in list(dirnames):
            if d in (".git", "__pycache__"):
                dirnames.remove(d)
        for fn in filenames:
            if fn in skip:
                continue
            p = Path(dirpath) / fn
            if p.suffix.lower() not in TEXT_EXT and p.name != ".gitignore":
                continue
            try:
                raw = p.read_bytes()
            except OSError:
                continue
            if b"\x00" in raw[:8192]:
                continue
            try:
                txt = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue
            new = txt
            for pat, holder in compiled:
                new = pat.sub(holder, new)
            if new != txt:
                p.write_bytes(new.encode("utf-8"))
                changed.append(str(p.relative_to(root)))
    if changed:
        print("scrub: " + ", ".join(sorted(changed)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def export_rules(rules, skip_names, dest):
    """把替换规则导出为 JSON，供临时脱敏器读取。"""
    out = []
    for pat, holder, flags in rules:
        out.append({"pattern": pat, "holder": holder,
                    "flags": int(re.I) if (flags & re.I) else 0})
    if out:
        out[0]["_skip"] = sorted(skip_names)
    dest.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return len(out)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="把环境标识从 git 历史中抹除")
    ap.add_argument("--repo", required=True, help="目标仓库工作副本")
    ap.add_argument("--push", action="store_true", help="重写校验后强制推送覆盖远端")
    ap.add_argument("--branch", help="分支名（默认当前分支）")
    ap.add_argument("--keep-temp", action="store_true", help="保留临时文件")
    ap.add_argument("--no-prune", action="store_true", help="不清理陈旧 ref")
    ap.add_argument("--no-secrets-rules", action="store_true",
                    help="不用本机 secrets 派生规则（只做 --replace 的定向替换）")
    ap.add_argument("--replace", action="append", metavar="OLD==>NEW",
                    help="定向字面量替换，可重复。用于仓库本身的公开名要保留、"
                         "只需清掉个别值的场景")
    ap.add_argument("--no-skip-scripts", action="store_true",
                    help="连 SELF_SCRIPTS 白名单里的脚本一起脱敏。默认跳过它们"
                         "是为了防止工具自身的'泛化模式'被规则改写；但当真实指纹"
                         "就写在脚本里时（如命名规则正则），必须打开本开关，"
                         "否则脚本会整文件被跳过、指纹留在历史里")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").is_dir():
        print("不是 git 仓库: %s" % repo)
        return 2

    mod = load_sync_module()
    if mod is None:
        return 2

    branch = args.branch
    if not branch:
        branch = (git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout or "").strip() or "main"

    repo_priv = repo_pub = None
    try:
        repo_priv, repo_pub, _ = mod.discover_repos()
    except Exception:
        pass

    rules, originals = [], []
    lit_originals = []          # --replace 的值：大小写敏感，复查时不能用 -i
    if not args.no_secrets_rules:
        rules, originals = mod.build_rules(repo_priv, repo_pub)

    # 定向替换：某些仓库（如项目仓）本身的名字/地址就是公开的，不能套用
    # 技能仓那套"GH 登录名也换掉"的规则，只需清掉个别值。
    for spec in (args.replace or []):
        if "==>" not in spec:
            print("--replace 需要 OLD==>NEW 形式，收到: %s" % spec)
            return 2
        old, new = spec.split("==>", 1)
        if not old:
            print("--replace 左侧不能为空")
            return 2
        rules.append((re.escape(old), new, 0))
        lit_originals.append(old)

    if not rules:
        print("规则为空，脱敏无意义，已中止"
              "（先确认 gh 已授权 / secrets 就位，或用 --replace 给定向规则）")
        return 2

    print("=" * 66)
    print("目标仓库 : %s" % repo)
    print("分支     : %s" % branch)
    print("替换规则 : %d 条（真实值运行时推导，脚本内无环境标识）" % len(rules))
    print("复查词表 : %d 项" % len(originals))
    print("=" * 66)

    before = collect_refs(repo)
    head_before = (git(repo, "rev-parse", "HEAD").stdout or "").strip()
    n_commits = (git(repo, "rev-list", "--count", "--all").stdout or "0").strip()
    print("重写前 HEAD: %s   提交数: %s   ref 数: %d"
          % (head_before[:12], n_commits, len(before)))

    tmp = Path(tempfile.mkdtemp(prefix="scrub_"))
    scrub_py = tmp / "_tree_scrub.py"
    rules_json = tmp / "rules.json"
    scrub_py.write_text(TREE_SCRUBBER, encoding="utf-8")
    skip_names = set(getattr(mod, "SELF_SCRIPTS", {Path(SYNC_TOOL).name}))
    if args.no_skip_scripts:
        skip_names = set()
        print("注意：已关闭脚本白名单跳过 —— 白名单内的脚本也会被脱敏")
    n = export_rules(rules, skip_names, rules_json)
    print("临时脱敏器: %s（导出 %d 条规则）" % (tmp, n))

    # 注意参数顺序：filter-branch 的 tree-filter 里第一个位置参数是工作树根，
    # 必须与临时脚本的 argv[1]=root / argv[2]=rules 对齐。
    tree_filter = '"%s" "%s" . "%s"' % (sys.executable, scrub_py, rules_json)

    # ---- 逐提交重写 ----
    print("\n[1] git filter-branch 逐提交重写…")
    env = dict(os.environ)
    env["FILTER_BRANCH_SQUELCH_WARNING"] = "1"
    r = subprocess.run(
        ["git", "-C", str(repo), "filter-branch", "--force",
         "--tree-filter", tree_filter, "--tag-name-filter", "cat", "--", "--all"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    lines = [ln for ln in (r.stderr or "").splitlines() if ln.strip()]
    for ln in lines[-5:]:
        print("    " + ln[:150])
    if r.returncode != 0:
        print("    ! filter-branch 返回 %d，重写未完成，中止（未做任何后续操作）"
              % r.returncode)
        if not args.keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)
        return 3

    head_after = (git(repo, "rev-parse", "HEAD").stdout or "").strip()
    print("    重写后 HEAD: %s%s"
          % (head_after[:12], "（未变化）" if head_after == head_before else ""))

    # ---- 清理陈旧 ref ----
    if not args.no_prune:
        print("\n[2] 清理陈旧 ref（旧提交仍可达的根源）")
        removed = []
        for ref, sha in sorted(collect_refs(repo).items()):
            if ref.startswith("refs/original/") or ref.startswith("refs/origin/"):
                git(repo, "update-ref", "-d", ref)
                removed.append(ref)
            elif sha != head_after and not is_ancestor(repo, sha, head_after):
                git(repo, "update-ref", "-d", ref)
                removed.append(ref + "  [指向旧提交]")
        for x in removed:
            print("    删除 %s" % x)
        if not removed:
            print("    无陈旧 ref")
        git(repo, "reflog", "expire", "--expire=now", "--all")
        git(repo, "gc", "--prune=now", "--quiet")
        print("    reflog 已过期，对象已回收")

    # ---- 独立复查 ----
    print("\n[3] 独立复查（全部新提交）")
    revs = (git(repo, "rev-list", "--all").stdout or "").split()

    def do_scan(label, pat, case_insensitive):
        """复查语义必须与替换语义一致：字面量替换是大小写敏感的，
        若统一加 -i，会把 `.workbuddy/` 这类大小写不同的正常内容误报成残留。"""
        if not pat or not revs:
            return 0
        flags = ["grep", "-nE"] if not case_insensitive else ["grep", "-niE"]
        g = git(repo, *flags, pat, *revs)
        n = 0
        for ln in (g.stdout or "").splitlines():
            print("    [%s] %s" % (label, ln[:160]))
            n += 1
        return n

    hits = 0
    hits += do_scan("secrets 派生词表",
                    "|".join(re.escape(v) for v in originals if v), True)
    hits += do_scan("定向替换值",
                    "|".join(re.escape(v) for v in lit_originals if v), False)
    hits += do_scan("内网 IP", r"192\.168\.\d+\.\d+|\b10\.\d+\.\d+\.\d+\b", False)
    print("    无残留" if not hits else "    !! 发现 %d 处残留 —— 不要推送，先补规则" % hits)

    # ---- 推送 ----
    if args.push:
        print("\n[4] 强制推送")
        if hits:
            print("    复查有残留，已跳过推送")
        else:
            p = git(repo, "push", "--force", "origin", branch)
            out = ((p.stdout or "") + (p.stderr or "")).strip()
            print("    " + (out.replace("\n", "\n    ") if out else "(无输出)"))

    if not args.keep_temp:
        shutil.rmtree(tmp, ignore_errors=True)
    else:
        print("\n临时文件保留在: %s" % tmp)

    print("\n" + "=" * 66)
    print("注意：GitHub 收到 force push 后不会立即回收旧对象，旧提交仍可能")
    print("按其 SHA 访问一段时间。要零残留，只有删库重建或请 GitHub Support")
    print("清除缓存视图。本工具只保证分支所指的历史是干净的。")
    print("=" * 66)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
