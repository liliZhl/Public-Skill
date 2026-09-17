#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""技能双仓同步器 —— 私有仓原样、公开仓脱敏。

用法：
    python sync_skill_repos.py                 # 同步到两个本地仓库 + 校验，不提交
    python sync_skill_repos.py --push          # 校验通过后自动 commit + push
    python sync_skill_repos.py --only private  # 只同步私有仓
    python sync_skill_repos.py --dry-run       # 只报告将做什么，不写文件
    python sync_skill_repos.py --all           # 连同源目录新增的技能一起同步

设计要点：
  · **脚本内不出现任何环境标识** —— 仓库位置自动发现，真实值在运行时从
    gh CLI、环境变量、本机 secrets 文件推导。因此本脚本可安全进入公开仓库。
  · 脱敏采用**占位符替换**而非删除，技能文档替换后仍然可用。
  · 替换之后做**独立复查**：用原始词表反扫公开副本，命中即失败。
    复查词表独立于替换规则构建，否则规则漏了什么就永远看不见。

仓库定位（零硬编码）：
  扫描 ~/repos/ 下所有"含技能目录的 git 仓库"，再用 gh 查可见性区分
  公开 / 私有。可用环境变量 SKILL_REPO_PRIVATE / SKILL_REPO_PUBLIC 覆盖。

依赖：gh CLI 已授权、git、Python 3.9+。
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
REPOS_ROOT = HOME / "repos"

EDA_SECRETS = HOME / ".workbuddy" / "secrets" / "eda-host.json"
EXTRA_SECRETS = HOME / ".workbuddy" / "secrets" / "sanitize-extra.json"

# 需要跳过替换的脚本文件名白名单（含自身）。
# 它们不含环境标识，但描述若干“泛化模式”（形如 (?i)\b<三字母>\d{4}\b），
# 不跳过的话这些模式字符串会被自己的规则改写，工具直接失效。
# 新增同类脚本时**必须登记到这里**，否则会被自伤。
SELF_SCRIPTS = {
    os.path.basename(__file__),
    "sync_skill_repos.py",
    "scrub_git_history.py",
    "scan_sensitive.py",
}


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


def repo_visibility(name):
    """查仓库可见性（public / private / internal），查不到返回空串。"""
    owner = gh_field(".login")
    if not owner:
        return ""
    return _run(["gh", "api", "repos/%s/%s" % (owner, name), "--jq", ".visibility"])


def local_uid():
    """本机 shell 视角的 UID（Windows 的 Git Bash 下是一个六位数字）。"""
    out = _run(["id", "-u"])
    return out if out.isdigit() else ""


def load_json(p):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return {}


def is_skill_repo(d):
    """该目录是否为"技能仓库"：git 仓库且含带 SKILL.md 的子目录。"""
    if not (Path(d) / ".git").is_dir():
        return False
    try:
        return any((p / "SKILL.md").is_file() for p in Path(d).iterdir() if p.is_dir())
    except OSError:
        return False


def discover_repos():
    """发现 (私有仓, 公开仓) 路径。环境变量优先，其次自动扫描。"""
    env_priv = os.environ.get("SKILL_REPO_PRIVATE")
    env_pub = os.environ.get("SKILL_REPO_PUBLIC")
    if env_priv and env_pub:
        return Path(env_priv), Path(env_pub), "环境变量"

    priv = pub = None
    if REPOS_ROOT.is_dir():
        for d in sorted(REPOS_ROOT.iterdir()):
            if not is_skill_repo(d):
                continue
            vis = repo_visibility(d.name)
            if vis == "public" and pub is None:
                pub = d
            elif vis == "private" and priv is None:
                priv = d
    return priv, pub, "自动扫描 ~/repos/"


def discover_skills(repo_priv, repo_pub, include_all=False):
    """确定要同步的技能列表。

    默认只同步**仓库中已有**的技能 —— 避免本机新增的（第三方或实验性）
    技能被意外带进版本库。要纳入，先手工复制进仓库，或加 --all。
    """
    if not include_all:
        names = set()
        for repo in (repo_priv, repo_pub):
            if repo and Path(repo).is_dir():
                names |= {p.name for p in Path(repo).iterdir()
                          if p.is_dir() and (p / "SKILL.md").is_file()}
        if names:
            return sorted(names), "仓库已有技能"

    names = {p.name for p in SKILLS_SRC.iterdir()
             if p.is_dir() and (p / "SKILL.md").is_file()}
    return sorted(names), "源目录全部"


# ---------------------------------------------------------------------------
# 规则构建（真实值全部运行时推导）
# ---------------------------------------------------------------------------

def build_rules(repo_priv=None, repo_pub=None):
    """返回 (替换规则, 复查词表)。

    规则 = [(正则, 占位符, flags)]；复查词表 = 原始真实值列表，
    独立于替换规则，用于替换后的残留检测。
    """
    rules = []
    originals = []

    def add(value, holder, flags=0, escape=True):
        v = (value or "").strip()
        if not v or len(v) < 3:
            return
        rules.append((re.escape(v) if escape else v, holder, flags))
        originals.append(v)

    gh_login = gh_field(".login")
    gh_id = gh_field(".id")
    gh_name = gh_field(".name")
    local_user = os.environ.get("USERNAME") or os.environ.get("USER") or HOME.name

    # 1) 私有仓库名（必须在"用户名"规则之前处理，否则会被拆碎）
    if repo_priv:
        add(Path(repo_priv).name, "Private-Skill")

    # 2) GitHub 身份
    add(gh_login, "<GH_LOGIN>")
    add(gh_id, "<GH_ID>")
    add(gh_name, "<GH_NAME>")

    # 3) 本机身份
    add(local_user, "<USER>")
    add(local_uid(), "<LOCAL_UID>")

    # 4) 远端主机标识（来自本机 secrets，永不入库）
    #    必须大小写不敏感：同一标识在文档里会写成全大写 / 全小写 / 首字母大写
    #    等多种形态，区分大小写会漏掉一半。
    sec = load_json(EDA_SECRETS)
    add(sec.get("hostname"), "<HOSTNAME>", flags=re.I)
    add(sec.get("domain"), "<DOMAIN>", flags=re.I)
    ssh_user = (sec.get("ssh_user") or "").strip()
    add(ssh_user, "<ACCOUNT>", flags=re.I)                       # 域\账号 整体（更长，先换）
    if "\\" in ssh_user:
        add(ssh_user.split("\\")[-1], "<ACCOUNT>", flags=re.I)   # 单独出现的账号部分

    # 账号的**字母前缀**单独出现时也要拦。整账号规则管不到它：
    # 文档里若写「账号形如 <前缀>0422」，域名那段被别的规则吃掉后，
    # 孤立的字母前缀没有任何规则匹配，会直接漏进公开仓库（实测踩过）。
    # 前缀从账号值切出来，仍然零硬编码。
    acct = ssh_user.split("\\")[-1] if ssh_user else ""
    m_acct = re.match(r"^([A-Za-z]{2,6})\d{3,6}$", acct)
    if m_acct:
        add(m_acct.group(1), "<ACCOUNT>", flags=re.I)
    add(sec.get("host"), "<HOST_IP>")
    for key in ("unc_admin_share", "unc_data_share", "business_share"):
        add(sec.get(key), "<SHARE>")

    # 5) 项目特定词（公司名等），放 secrets 目录，格式 {"真实值": "<占位符>"}
    for real, holder in (load_json(EXTRA_SECRETS) or {}).items():
        add(real, holder)

    # 6) 泛化规则：兜住具体值之外的同类标识
    for pat, holder in [
        (r"\b192\.168\.\d{1,3}\.\d{1,3}\b", "<HOST_IP>"),
        (r"\b10\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", "<HOST_IP>"),
        (r"(?i)\b[a-z]{3}\d{4}\b", "<ACCOUNT>"),
        (r"\b[A-Z][A-Z0-9]{2,7}-PC\b", "<HOSTNAME>"),
    ]:
        rules.append((pat, holder, 0))

    return rules, originals


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


def copy_skills(src_root, dest, skills):
    """把技能目录复制到目标仓库（先移除同名旧目录），跳过缓存目录。"""
    for name in skills:
        src = Path(src_root) / name
        if not src.is_dir():
            print("    [跳过] 源不存在: %s" % name)
            continue
        dst = Path(dest) / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", "*.pyo"))
        print("    复制 %s" % name)


def main():
    ap = argparse.ArgumentParser(description="技能双仓同步（私有原样 / 公开脱敏）")
    ap.add_argument("--push", action="store_true", help="校验通过后自动提交并推送")
    ap.add_argument("--only", choices=["private", "public"], help="只同步其中一个仓库")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写文件")
    ap.add_argument("--all", action="store_true",
                    help="同步源目录全部技能（默认只同步仓库已有的技能）")
    args = ap.parse_args()

    if not SKILLS_SRC.is_dir():
        print("技能源目录不存在: %s" % SKILLS_SRC)
        return 2

    repo_priv, repo_pub, how = discover_repos()
    skills, skill_src = discover_skills(repo_priv, repo_pub, include_all=args.all)
    rules, originals = build_rules(repo_priv, repo_pub)

    print("=" * 66)
    print("技能源目录 : %s" % SKILLS_SRC)
    print("技能数量   : %d  (%s)" % (len(skills), ", ".join(skills)))
    print("选取依据   : %s" % skill_src)
    print("仓库发现   : %s" % how)
    print("  私有仓   : %s" % (repo_priv or "（未找到）"))
    print("  公开仓   : %s" % (repo_pub or "（未找到）"))
    print("脱敏规则   : %d 条（真实值运行时推导，脚本内无环境标识）" % len(rules))
    print("复查词表   : %d 项" % len(originals))
    if not gh_field(".login"):
        print("  ! 未从 gh 取到登录名 —— GitHub 相关规则缺失，请先 gh auth login")
    if not rules:
        print("  ! 规则为空，脱敏无意义，已中止")
        return 2
    print("=" * 66)

    failed = False

    # ---------- 私有仓：原样 ----------
    if args.only in (None, "private"):
        print("\n[1] 私有仓")
        if not repo_priv:
            print("    未找到，跳过")
        elif args.dry_run:
            print("    (dry-run) 将原样复制 %d 个技能" % len(skills))
        else:
            print("    %s" % repo_priv)
            copy_skills(SKILLS_SRC, repo_priv, skills)
            print("    完成（内容与源一致）")

    # ---------- 公开仓：脱敏 ----------
    if args.only in (None, "public"):
        print("\n[2] 公开仓")
        if not repo_pub:
            print("    未找到，公开仓同步中止")
            return 1
        if args.dry_run:
            print("    (dry-run) 将复制并脱敏 %d 个技能" % len(skills))
        else:
            print("    %s" % repo_pub)
            copy_skills(SKILLS_SRC, repo_pub, skills)

            for name in skills:
                d = Path(repo_pub) / name
                if not d.is_dir():
                    continue
                replaced = 0
                for p in iter_files(d):
                    if p.name in SELF_SCRIPTS:
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    new, k = sanitize_text(txt, rules)
                    if k:
                        p.write_text(new, encoding="utf-8", newline="")
                        replaced += k
                print("    %s: 已脱敏（%d 处替换）" % (name, replaced))

            # ---------- 独立复查 ----------
            print("\n[3] 独立复查（原始词表 + 通用模式反扫公开副本）")
            generic_checks = [
                ("内网 IP", r"192\.168\.\d+\.\d+|\b10\.\d+\.\d+\.\d+\b"),
                ("域账号", r"(?i)\b[a-z]{3}\d{4}\b"),
                ("主机名", r"\b[A-Z][A-Z0-9]{2,7}-PC\b"),
            ]
            hits = 0
            for name in skills:
                for p in iter_files(Path(repo_pub) / name):
                    if p.name in SELF_SCRIPTS:
                        continue
                    try:
                        txt = p.read_text(encoding="utf-8")
                    except (UnicodeDecodeError, OSError):
                        continue
                    for v in originals:
                        # 大小写不敏感：原文可能写成与 secrets 不同的形态
                        if v and re.search(re.escape(v), txt, re.I):
                            print("    [残留-具体值] 于 %s"
                                  % p.relative_to(repo_pub))
                            hits += 1
                    for label, pat in generic_checks:
                        for m in set(re.findall(pat, txt)):
                            print("    [残留-%s] %r 于 %s"
                                  % (label, m, p.relative_to(repo_pub)))
                            hits += 1

            if hits:
                print("    × 共 %d 处残留 —— 不要推送，先补规则" % hits)
                failed = True
            else:
                print("    √ 无残留，公开副本干净")

            bad = [p for p in iter_files(repo_pub)
                   if re.search(r"(secret|credential|\.pem$|\.key$|\.pyc$)",
                                str(p), re.I)]
            if bad:
                for p in bad:
                    print("    × 不应入库的文件：%s" % p)
                failed = True

    # ---------- 提交推送 ----------
    if args.push and not args.dry_run:
        if failed:
            print("\n[4] 校验未通过，已跳过推送")
            return 1
        print("\n[4] 提交并推送")
        for repo in (repo_priv, repo_pub):
            if not repo or not Path(repo).is_dir():
                continue
            subprocess.run(["git", "-C", str(repo), "add", "-A"])
            st = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                                capture_output=True, text=True).stdout.strip()
            if not st:
                print("    %s: 无改动" % Path(repo).name)
                continue
            subprocess.run(["git", "-C", str(repo), "commit", "-q",
                            "-m", "sync: 同步技能（私有原样 / 公开脱敏）"])
            r = subprocess.run(["git", "-C", str(repo), "push"],
                               capture_output=True, text=True)
            print("    %s: push %s" % (Path(repo).name,
                                       "OK" if r.returncode == 0 else "FAIL"))
            if r.returncode != 0:
                print("      %s" % (r.stderr or "").strip()[:200])
                failed = True

    print("\n完成。" + ("有未通过项，见上。" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
