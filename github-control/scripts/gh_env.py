#!/usr/bin/env python3
"""gh_env.py - GitHub 环境自检

一次性拿到本机 GitHub 操作的全套前置状态，并给出可直接复制的 PATH 导出语句。
设计目标：agent 在一次调用内回答"现在能不能直接操作 GitHub"。

用法:
    python gh_env.py             # 人类可读报告（同时落盘）
    python gh_env.py --json      # JSON 输出
    python gh_env.py --out FILE  # 指定落盘路径
    python gh_env.py --quiet     # 只落盘，不打印

退出码: 0=READY  1=NO_GH  2=NO_GIT  3=NOT_AUTHED

落盘默认路径: <tempdir>/gh_env_last.txt（--json 时同时写 .json）
stdout 被转码时读落盘文件。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from glob import glob
from pathlib import Path

HOME = Path.home()

# ---------------------------------------------------------------- 路径探测

def _uniquify(items):
    seen, out = set(), []
    for it in items:
        if it and it not in seen:
            seen.add(it)
            out.append(it)
    return out


def gh_candidates() -> list[str]:
    """gh 可执行文件的候选路径，按优先级排列。"""
    cands: list[str] = []
    found = shutil.which("gh")
    if found:
        cands.append(found)
    cands += [
        r"C:\Program Files\GitHub CLI\gh.exe",
        str(HOME / "AppData/Local/GitHubCLI/gh.exe"),
        r"C:\ProgramData\chocolatey\bin\gh.exe",
        "/usr/bin/gh",
        "/usr/local/bin/gh",
    ]
    return _uniquify(cands)


def _normalize_git(p: str) -> str:
    """把 mingw64/bin/git.exe 规范到同级 cmd/git.exe。

    原因：只导出 mingw64/bin 时，PATH 里有 git 却没有 rm/mkdir 等 shell 工具
    （它们在 usr/bin）。cmd/ 才是推荐的入口，且同级 usr/bin 会被一并注入。
    """
    f = Path(p)
    if f.parent.name.lower() == "mingw64":
        alt = f.parent.parent / "cmd" / "git.exe"
        if alt.is_file():
            return str(alt)
    return str(f)


def git_candidates() -> list[str]:
    """git 可执行文件的候选路径。

    托管 PortableGit 的 cmd/git.exe 优先于 PATH 上的任意 git —— 这样注入的
    PATH 同时带上 usr/bin，shell 工具才完整。
    """
    cands: list[str] = []
    # 1. 托管 PortableGit：版本号不固定，glob 抓全部再倒序（新版本优先）
    for pat in (
        str(HOME / ".workbuddy/binaries/PortableGit/versions/*/cmd/git.exe"),
        str(HOME / ".workbuddy/binaries/PortableGit/*/cmd/git.exe"),
    ):
        for p in sorted(glob(pat), reverse=True):
            cands.append(p)
    # 2. PATH 上的 git（可能落在 mingw64/bin，需规范化）
    found = shutil.which("git")
    if found:
        cands.append(_normalize_git(found))
    cands += [
        r"C:\Program Files\Git\cmd\git.exe",
        "/usr/bin/git",
        "/usr/local/bin/git",
    ]
    return _uniquify(cands)


def first_existing(cands: list[str]) -> str | None:
    for c in cands:
        p = Path(c)
        if p.is_file():
            return str(p)
    return None


def bin_dirs(gh: str | None, git: str | None) -> list[str]:
    """收集需要注入 PATH 的目录，顺序：gh 在前，git 的 cmd/ 与 usr/bin/ 都要。"""
    dirs: list[str] = []
    if gh:
        dirs.append(str(Path(gh).parent))
    if git:
        gp = Path(git)
        dirs.append(str(gp.parent))                     # .../cmd
        # PortableGit 布局：cmd/ 与 usr/bin/ 平级
        for sib in ("usr/bin", "mingw64/bin", "bin"):
            d = gp.parent.parent / sib
            if d.is_dir():
                dirs.append(str(d))
    return _uniquify(dirs)


def win_to_posix(p: str) -> str:
    """C:\\Program Files\\X -> /c/Program Files/X（供 Bash 工具的 export 用）。"""
    p = str(p).replace("\\", "/")
    m = re.match(r"^([A-Za-z]):/(.*)$", p)
    return f"/{m.group(1).lower()}/{m.group(2)}" if m else p


# ---------------------------------------------------------------- 执行封装

def run(cmd: list[str], env_path: str | None = None, timeout: int = 25):
    """跑一个命令，返回 (returncode, stdout, stderr)。编码容错。"""
    env = dict(os.environ)
    if env_path:
        env["PATH"] = env_path
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, errors="replace",
            timeout=timeout, env=env, shell=False,
        )
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", f"timeout after {timeout}s"
    except OSError as e:
        return 127, "", f"{type(e).__name__}: {e}"


# ---------------------------------------------------------------- 各项检查

def check_env() -> dict:
    gh = first_existing(gh_candidates())
    git = first_existing(git_candidates())
    dirs = bin_dirs(gh, git)
    path = os.pathsep.join(dirs + [os.environ.get("PATH", "")]) if dirs else None

    info: dict = {
        "gh_path": gh,
        "git_path": git,
        "path_dirs": dirs,
        "path_export_posix": "export PATH=\"" + ":".join(win_to_posix(d) for d in dirs) + ":$PATH\"",
        "path_export_windows": 'set PATH=' + ";".join(dirs) + ";%PATH%",
    }

    if gh:
        _, out, _ = run([gh, "--version"], path)
        info["gh_version"] = out.splitlines()[0] if out else ""
    if git:
        _, out, _ = run([git, "--version"], path)
        info["git_version"] = out.splitlines()[0] if out else ""
    info["path"] = path
    return info


def check_auth(env: dict) -> dict:
    gh = env["gh_path"]
    if not gh:
        return {"authed": False, "raw": "gh 未安装"}
    rc, out, err = run([gh, "auth", "status"], env["path"])
    raw = (out + "\n" + err).strip()
    res = {"authed": rc == 0, "raw": raw}
    m = re.search(r"account\s+(\S+)", raw)
    res["login"] = m.group(1) if m else None
    res["token_store"] = "keyring" if "keyring" in raw else None
    m = re.search(r"Token scopes:\s*(.+)", raw)
    res["scopes"] = [s.strip().strip("'\"") for s in m.group(1).split(",")] if m else []
    res["protocol"] = "https" if "https" in raw else None
    res["has_workflow_scope"] = "workflow" in res["scopes"]
    return res


def check_git_identity(env: dict) -> dict:
    git = env["git_path"]
    if not git:
        return {}
    out: dict = {}
    for key in ("user.name", "user.email", "init.defaultBranch"):
        _, v, _ = run([git, "config", "--global", key], env["path"])
        out[key] = v or None
    _, v, _ = run([git, "config", "--global", "--get-regexp", r"^credential\."], env["path"])
    out["credential_config"] = [ln.strip() for ln in v.splitlines() if ln.strip()]
    out["credential_helper_ok"] = any("gh" in ln and "git-credential" in ln
                                      for ln in out["credential_config"])
    # 邮箱是否误用本机 UID
    email = out.get("user.email") or ""
    m = re.match(r"^(\d+)\+", email)
    if m:
        out["email_numeric_id"] = m.group(1)
    return out


def check_account(env: dict) -> dict:
    gh = env["gh_path"]
    if not gh:
        return {}
    rc, out, _ = run([gh, "api", "user",
                      "--jq", '{login:.login,id:.id,name:.name,public_repos:.public_repos}'], env["path"])
    if rc != 0:
        return {}
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"raw": out}


def check_repos(env: dict, limit: int = 30) -> list[dict]:
    gh = env["gh_path"]
    if not gh:
        return []
    rc, out, _ = run([gh, "repo", "list", "--limit", str(limit),
                      "--json", "name,visibility,url,diskUsage,pushedAt"], env["path"])
    if rc != 0:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


# ---------------------------------------------------------------- 报告

def verdict_of(env: dict, auth: dict) -> tuple[str, str]:
    if not env["gh_path"]:
        return "NO_GH", "gh CLI 未安装 → 走 SKILL.md §1（winget install --id GitHub.cli -e）"
    if not env["git_path"]:
        return "NO_GIT", "git 未找到 → 检查 PortableGit 是否在位"
    if not auth.get("authed"):
        return "NOT_AUTHED", "未授权 → 走 SKILL.md §2 设备码流程"
    return "READY", "环境就绪，可直接操作 GitHub"


def render(env: dict, auth: dict, ident: dict, acct: dict, repos: list[dict]) -> str:
    L: list[str] = []
    v, why = verdict_of(env, auth)

    L.append("=" * 62)
    L.append("  GitHub 环境自检")
    L.append("=" * 62)
    L.append(f"  verdict : {v}")
    L.append(f"  说明    : {why}")
    L.append("")

    L.append("[ 工具链 ]")
    L.append(f"  gh  : {env.get('gh_version') or '未找到'}")
    L.append(f"        {env.get('gh_path') or '-'}")
    L.append(f"  git : {env.get('git_version') or '未找到'}")
    L.append(f"        {env.get('git_path') or '-'}")
    L.append("")

    L.append("[ PATH 导出（每条 Bash 调用都要重来） ]")
    L.append(f"  {env.get('path_export_posix')}")
    L.append("")

    L.append("[ 授权 ]")
    if auth.get("authed"):
        L.append(f"  账号    : {auth.get('login')}")
        L.append(f"  令牌存储: {auth.get('token_store') or '-'}")
        L.append(f"  协议    : {auth.get('protocol') or '-'}")
        L.append(f"  scopes  : {', '.join(auth.get('scopes') or []) or '-'}")
        if not auth.get("has_workflow_scope"):
            L.append("  ⚠ 无 workflow scope：推送 .github/workflows/ 会被拒")
            L.append("    补齐：gh auth refresh -h github.com -s workflow")
    else:
        L.append("  未授权")
        for line in (auth.get("raw") or "").splitlines()[:4]:
            L.append(f"    {line}")
    L.append("")

    L.append("[ git 全局配置 ]")
    L.append(f"  user.name  : {ident.get('user.name') or '未设置'}")
    L.append(f"  user.email : {ident.get('user.email') or '未设置'}")
    L.append(f"  defaultBr  : {ident.get('init.defaultBranch') or '未设置'}")
    L.append(f"  凭据助手   : {'已接 gh' if ident.get('credential_helper_ok') else '未配置'}")
    # 邮箱里的数字 ID 必须等于 GitHub ID；等于本机 UID 说明踩了 UID 只读变量坑
    nid = ident.get("email_numeric_id")
    if nid:
        ghid = str(acct.get("id")) if acct.get("id") else None
        if ghid and nid == ghid:
            L.append(f"  邮箱数字 ID {nid} = GitHub ID ✓")
        elif ghid:
            L.append(f"  ⚠ 邮箱数字 ID {nid} ≠ GitHub ID {ghid} —— 身份配错了！")
            L.append("     多半是用了 UID 变量（bash 只读，赋值静默失败），改用 GHID")
        else:
            L.append(f"  邮箱数字 ID {nid}（无法核对 GitHub ID，账号信息未取到）")
    L.append("")

    if acct:
        L.append("[ GitHub 账号 ]")
        L.append(f"  login={acct.get('login')}  id={acct.get('id')}  "
                 f"name={acct.get('name')}  公开仓库={acct.get('public_repos')}")
        L.append("")

    if repos:
        L.append(f"[ 仓库（{len(repos)}） ]")
        w = max(len(r.get("name", "")) for r in repos)
        for r in repos:
            vis = r.get("visibility", "?")
            kb = r.get("diskUsage") or 0
            size = f"{kb/1024:7.1f} MB" if kb >= 1024 else f"{kb:7d} KB"
            L.append(f"  {r.get('name','').ljust(w)}  [{vis:<6}]  "
                     f"{size}  {r.get('pushedAt','')[:10]}")
        L.append("")

    L.append("[ 已知本机硬坑 ]")
    L.append("  · Bash 工具不注入 PATH —— 每条命令先 export")
    L.append("  · .git/refs/remotes/** 写入被静默丢弃 → git status 恒 [gone]")
    L.append("    绕行：git config --add remote.origin.fetch \"+refs/heads/*:refs/origin/*\"")
    L.append("  · 沙箱内 clone 到 Temp 不落地 → 改用 HTTP API 直读或非沙箱执行")
    L.append("  · PowerShell 工具输出可能被吞 → 探测类命令走 Bash")
    L.append("")
    L.append("=" * 62)
    return "\n".join(L)


# ---------------------------------------------------------------- main

def main() -> int:
    ap = argparse.ArgumentParser(description="GitHub 环境自检")
    ap.add_argument("--json", action="store_true", help="JSON 输出")
    ap.add_argument("--quiet", action="store_true", help="不打印，只落盘")
    ap.add_argument("--out", help="落盘路径（默认 <tempdir>/gh_env_last.txt）")
    ap.add_argument("--limit", type=int, default=30, help="仓库清单条数上限")
    args = ap.parse_args()

    env = check_env()
    auth = check_auth(env)
    ident = check_git_identity(env)
    acct = check_account(env)
    repos = check_repos(env, args.limit)
    v, why = verdict_of(env, auth)

    payload = {
        "verdict": v, "reason": why, "env": {k: val for k, val in env.items() if k != "path"},
        "auth": auth, "git": ident, "account": acct,
        "repos": [{"name": r.get("name"), "visibility": r.get("visibility"),
                   "url": r.get("url"), "diskUsageKB": r.get("diskUsage"),
                   "pushedAt": r.get("pushedAt")} for r in repos],
    }

    out_dir = Path(args.out).parent if args.out else Path(tempfile.gettempdir())
    if args.out:
        out_txt = Path(args.out)
    else:
        out_txt = out_dir / "gh_env_last.txt"
    text = json.dumps(payload, ensure_ascii=False, indent=2) if args.json else render(
        env, auth, ident, acct, repos)

    written = []
    try:
        out_txt.write_text(text, encoding="utf-8")
        written.append(out_txt)
        if args.json:
            jp = out_txt.with_suffix(".json")
            jp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            written.append(jp)
    except OSError as e:
        print(f"[warn] 落盘失败: {e}", file=sys.stderr)

    if not args.quiet:
        print(text)
        if not args.json:
            print(f"  [已落盘] {out_txt}")

    return {"READY": 0, "NO_GH": 1, "NO_GIT": 2, "NOT_AUTHED": 3}[v]


if __name__ == "__main__":
    sys.exit(main())
