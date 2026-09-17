#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能双仓同步器 —— 私有仓原样、公开仓脱敏。

用法：
    python sync_skill_repos.py                 # 同步到两个本地仓库 + 校验，不提交
    python sync_skill_repos.py --push          # 校验通过后自动 commit + push
    python sync_skill_repos.py --only private  # 只同步私有仓
    python sync_skill_repos.py --dry-run       # 只报告将做什么，不写文件

设计要点：
  · **脚本内不硬编码任何敏感值** —— 真实值在运行时从 gh CLI、环境变量、
    本机 secrets 文件推导。因此本脚本可以安全地进入公开仓库。
  · 脱敏采用**占位符替换**而非删除，技能文档替换后仍然可用。
  · 替换之后做**独立复查**：用原始词表反扫公开副本，命中即失败。
    复查词表独立于替换规则构建，否则规则漏了什么就永远看不见。

依赖：gh CLI 已授权（用于取 GitHub 登录名 / ID / 姓名）、git。
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
SKILLS_SRC = HOME / ".workbuddy" / "skills"
REPO_PRIVATE = HOME / "repos" / "Private-Skill"
REPO_PUBLIC = HOME / "repos" / "Public-Skill"

EDA_SECRETS = HOME / ".workbuddy" / "secrets" / "eda-host.json"
EXTRA_SECRETS = HOME / ".workbuddy" / "secrets" / "sanitize-extra.json"

# 本脚本文件名：同步与复查时跳过。
# 它本身零硬编码敏感值，但它描述"泛化模式"（如 (?i)\b<ACCOUNT>\d{4}\b），
# 若不跳过，这些模式字符串会被自己的规则改写成无意义的占位符。
SELF_SCRIPT = "sync_skill_repos.py"


# ---------------------------------------------------------------------------
# 运行时取值
# ---------------------------------------------------------------------------

def _run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=timeout, encoding="utf-8", errors="replace")
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def gh_field(jq):
    """从 gh api user 取字段，失败返回空串。"""
    return _run(["gh", "api", "user", "--jq", jq])


def local_uid():
    """本机 bash 视角的 UID（Windows 下形如 <LOCAL_UID>）。取不到返回空串。"""
    out = _run(["id", "-u"])
    return out if out.isdigit() else ""


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_rules():
    """构建 [(正则, 占位符)] 与复查词表。真实值全部运行时推导。"""
    rules = []
    originals = []          # 复查用原始词表

    def add(value, holder, escape=True, flags=0):
        """登记一条替换规则；value 为空则跳过。"""
        v = (value or "").strip()
        if not v or len(v) < 3:
            return
        pat = re.escape(v) if escape else v
        rules.append((pat, holder, flags))
        originals.append(v)

    gh_login = gh_field(".login")
    gh_id = gh_field(".id")
    gh_name = gh_field(".name")
    local_user = os.environ.get("USERNAME") or os.environ.get("USER") or HOME.name
    uid = local_uid()

    # 1) 私有仓库名（含用户名的形式，必须先于用户名本身替换，否则会被拆碎）
    if gh_login:
        add("%s-Skill" % gh_login, "Private-Skill")

    # 2) GitHub 身份
    add(gh_login, "<GH_LOGIN>")
    add(gh_id, "<GH_ID>")
    add(gh_name, "<GH_NAME>")

    # 3) 本机身份
    add(local_user, "<USER>")
    add(uid, "<LOCAL_UID>")

    # 4) EDA 主机标识（来自本机 secrets，不入库）
    #    必须大小写不敏感：同一标识在文档里会写成 <DOMAIN> / <DOMAIN> / <ACCOUNT> 等多种形态
    sec = load_json(EDA_SECRETS)
    add(sec.get("hostname"), "<HOSTNAME>", flags=re.I)
    add(sec.get("domain"), "<DOMAIN>", flags=re.I)
    ssh_user = (sec.get("ssh_user") or "").strip()
    add(ssh_user, "<ACCOUNT>", flags=re.I)                    # 域\账号 整体（先，更长）
    if "\\" in ssh_user:
        add(ssh_user.split("\\")[-1], "<ACCOUNT>", flags=re.I)  # 单独出现的账号部分
    add(sec.get("host"), "<HOST_IP>")
    for key in ("unc_admin_share", "unc_data_share", "business_share"):
        add(sec.get(key), "<SHARE>")

    # 5) 项目特定词表（可选，放置于 secrets 目录，永不入库）
    for real, holder in (load_json(EXTRA_SECRETS) or {}).items():
        add(real, holder)

    # 6) 泛化规则（兜住具体值之外的同类标识）
    generics = [
        (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "<HOST_IP>", 0),
        (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<HOST_IP>", 0),
        (r"(?i)\b<ACCOUNT>\d{4}\b", "<ACCOUNT>", 0),
        (r"\b[A-Z][A-Z0-9]{2,7}-PC\b", "<HOSTNAME>", 0),
    ]
    for pat, holder, flags in generics:
        rules.append((pat, holder, flags))

    # 复查词表：用**未转义**的原值，独立于上面的替换规则
    leftover = [v for v in originals]
    return rules, leftover


# ---------------------------------------------------------------------------
# 同步与脱敏
# ---------------------------------------------------------------------------

def sanitize_text(text, rules):
    """应用替换，返回 (新文本, 替换处数)。"""
    count = 0
    for pat, holder, flags in rules:
        text, k = re.subn(pat, holder, text, flags=flags)
        count += k
    return text, count


def iter_files(root):
    return sorted(p for p in Path(root).rglob("*") if p.is_file())


def copy_skills(dest, skills):
    """把技能目录复制到目标仓库（先移除同名旧目录）。"""
    for name in skills:
        src = SKILLS_SRC / name
        if not src.is_dir():
            print("    [跳过] 源不存在: %s" % name)
            continue
        dst = Path(dest) / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        print("    复制 %s" % name)


def discover_skills(include_all=False):
    """确定要同步的技能列表。

    默认只同步**两个仓库中已有**的技能 —— 避免本机新增的技能（可能是第三方或
    实验性的）被意外带进版本库。要纳入新技能，先手工复制进仓库，或加 --all。
    """
    if include_all:
        names = {p.name for p in SKILLS_SRC.iterdir()
                 if p.is_dir() and (p / "SKILL.md").is_file()}
        return sorted(names), "源目录全部（--all）"

    names = set()
    for repo in (REPO_PRIVATE, REPO_PUBLIC):
        rp = Path(repo)
        if rp.is_dir():
            names |= {p.name for p in rp.iterdir()
                      if p.is_dir() and (p / "SKILL.md").is_file()}
    if names:
        return sorted(names), "两仓已有技能"

    # 两仓都为空（首次初始化）：退回源目录全部
    return discover_skills(include_all=True)[0], "源目录全部（仓库为空，首次初始化）"


def main():
    ap = argparse.ArgumentParser(description="技能双仓同步（私有原样 / 公开脱敏）")
    ap.add_argument("--push", action="store_true", help="校验通过后自动提交并推送")
    ap.add_argument("--only", choices=["private", "public"], help="只同步其中一个仓库")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--all", action="store_true",
                    help="同步源目录中全部技能（默认只同步两仓已有的技能）")
    args = ap.parse_args()

    if not SKILLS_SRC.is_dir():
        print("技能源目录不存在: %s" % SKILLS_SRC)
        return 2

    rules, originals = build_rules()
    skills, skill_src = discover_skills(include_all=args.all)

    print("=" * 64)
    print("技能源目录 : %s" % SKILLS_SRC)
    print("技能数量   : %d  (%s)" % (len(skills), ", ".join(skills)))
    print("选取依据   : %s" % skill_src)
    print("脱敏规则   : %d 条（真实值运行时推导，脚本内无硬编码）" % len(rules))
    print("复查词表   : %d 项" % len(originals))
    if not gh_field(".login"):
        print("  ⚠ 未能从 gh 取到登录名 —— GitHub 相关规则缺失，请先 gh auth login")
    if not rules:
        print("  ⚠ 规则为空，脱敏无意义，已中止")
        return 2
    print("=" * 64)

    failed = False

    # ---------- 私有仓：原样 ----------
    if args.only in (None, "private"):
        print("\n[1] 私有仓 %s" % REPO_PRIVATE)
        if not REPO_PRIVATE.is_dir():
            print("    不存在，跳过")
        elif args.dry_run:
            print("    (dry-run) 将原样复制 %d 个技能" % len(skills))
        else:
            copy_skills(REPO_PRIVATE, skills)
            print("    完成（内容与源逐字节一致）")

    # ---------- 公开仓：脱敏 ----------
    if args.only in (None, "public"):
        print("\n[2] 公开仓 %s" % REPO_PUBLIC)
        if not REPO_PUBLIC.is_dir():
            print("    不存在，跳过")
            return 1
        if args.dry_run:
            print("    (dry-run) 将复制并脱敏 %d 个技能" % len(skills))
        else:
            copy_skills(REPO_PUBLIC, skills)

            total_hits = 0
            for name in skills:
                d = REPO_PUBLIC / name
                if not d.is_dir():
                    continue
                replaced = 0
                for p in iter_files(d):
                    # 脱敏工具自身不含硬编码敏感值，跳过以免它描述的"泛化模式"
                    # 被自己的规则改写（例如 (?i)\b<ACCOUNT>\d{4}\b 会匹配模式字符串本身）
                    if p.name == SELF_SCRIPT:
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue          # 二进制文件不处理
                    new, k = sanitize_text(txt, rules)
                    if k:
                        p.write_text(new, encoding="utf-8", newline="")
                        replaced += k
                print("    %s: 已脱敏（%d 处替换）" % (name, replaced))
            print("\n[3] 独立复查（用原始词表反扫公开副本）")
            for name in skills:
                d = REPO_PUBLIC / name
                for p in iter_files(d):
                    if p.name == SELF_SCRIPT:
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for v in originals:
                        # 大小写不敏感：原文可能写成与 secrets 不同的形态
                        if v and re.search(re.escape(v), txt, re.I):
                            print("    [残留] %r 在 %s" % (v, p.relative_to(REPO_PUBLIC)))
                            total_hits += 1
            # 通用模式复查
            generic_checks = [
                ("内网 IP", r"192\.168\.\d+\.\d+|\b10\.\d+\.\d+\.\d+\b"),
                ("域账号", r"(?i)\b<ACCOUNT>\d{4}\b"),
                ("主机名", r"\b[A-Z][A-Z0-9]{2,7}-PC\b"),
            ]
            for name in skills:
                d = REPO_PUBLIC / name
                for p in iter_files(d):
                    if p.name == SELF_SCRIPT:
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for label, pat in generic_checks:
                        for m in set(re.findall(pat, txt)):
                            print("    [残留] %s: %r 在 %s"
                                  % (label, m, p.relative_to(REPO_PUBLIC)))
                            total_hits += 1

            if total_hits:
                print("    ✗ 共 %d 处残留，**不要推送**" % total_hits)
                failed = True
            else:
                print("    ✓ 无残留，公开副本干净")

            # 凭据类文件兜底检查
            bad = [p for p in iter_files(REPO_PUBLIC)
                   if re.search(r"(secret|credential|\.pem$|\.key$|eda-host\.json)",
                                str(p), re.I)]
            if bad:
                print("    ✗ 发现凭据类文件：%s" % bad)
                failed = True

    # ---------- 提交推送 ----------
    if args.push and not args.dry_run:
        if failed:
            print("\n[4] 校验未通过，已跳过推送")
            return 1
        print("\n[4] 提交并推送")
        for repo in (REPO_PRIVATE, REPO_PUBLIC):
            if not repo.is_dir():
                continue
            subprocess.run(["git", "-C", str(repo), "add", "-A"])
            if not subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                                  capture_output=True, text=True).stdout.strip():
                print("    %s: 无改动" % repo.name)
                continue
            subprocess.run(["git", "-C", str(repo), "commit", "-q",
                            "-m", "sync: 同步技能（私有原样 / 公开脱敏）"])
            r = subprocess.run(["git", "-C", str(repo), "push"],
                               capture_output=True, text=True)
            ok = "OK" if r.returncode == 0 else "FAIL"
            print("    %s: push %s" % (repo.name, ok))
            if r.returncode != 0:
                print("      %s" % (r.stderr or "").strip()[:200])
                failed = True

    print("\n完成。" + ("有未通过项，见上。" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
