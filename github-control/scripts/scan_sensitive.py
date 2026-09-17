#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""公开仓库前的敏感信息体检：逐条列出命中位置，供人工判定是否可公开。

**必须人工过一遍输出**，不能只看「有没有命中」——有些命中是误报
（例如显卡型号、`es.cursor = sel` 里的 cursor），有些漏报要靠眼睛补。

用法：
    python scan_sensitive.py ~/repos/Meeting
    python scan_sensitive.py ~/repos/Meeting --history    # 连 git 历史一起扫

检查项：
    个人绝对路径 / 内网 IP / 邮箱 / 疑似 API Key / 手机号 /
    AI 工具目录 / 公司名 / 域账号样式 / 令牌密码字面量

设计要点：
  · **零硬编码**：不写任何本机真实值，只放通用形状的模式。
  · `git ls-files` 必须带 `-c core.quotepath=false`，否则中文文件名会被
    八进制转义，脚本按转义名去找文件必然全部落空 —— 实测会让整个
    `docs/` 目录静默漏检，比误报危险得多。
  · 二进制文件按前 8KB 是否含 NUL 判定并跳过。
  · 退出码：0 无命中，1 有命中（便于接进发布前脚本）。

配合 `scrub_git_history.py` 使用：本工具定位问题，那个工具清除历史。
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

# 通用形状的模式（不含任何真实环境值）
PATTERNS = [
    ("Windows 个人路径", re.compile(r"[A-Za-z]:\\Users\\[A-Za-z0-9_.\-]+")),
    ("Unix 家目录路径", re.compile(r"/(?:home|Users)/[A-Za-z0-9_.\-]+")),
    ("内网 IP", re.compile(
        r"\b(?:192\.168|10|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?:\.\d{1,3})?\b")),
    ("邮箱地址", re.compile(r"[\w.+\-]+@[\w\-]+\.[A-Za-z]{2,}")),
    ("疑似 API Key", re.compile(r"\bsk-[A-Za-z0-9_\-]{10,}")),
    ("疑似手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("AI 工具目录", re.compile(r"\.(?:qclaw|claude|cursor|codebuddy)\b")),
    ("公司名", re.compile(r"小湃|创维|Skyworth")),
    ("域账号样式", re.compile(r"(?i)\b[a-z]{3}\d{4}\b|\b<DOMAIN>\b|\b<ACCOUNT>\b")),
    ("令牌/密码字面量", re.compile(
        r"(?i)(?:password|passwd|token|secret|api[_-]?key)\s*[:=]\s*[\"'][^\"'\s]{6,}[\"']")),
]


def git(repo, *args):
    r = subprocess.run(["git", "-C", str(repo), "-c", "core.quotepath=false"] + list(args),
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.stdout or ""


def iter_blobs(repo, history):
    """产出 (标签, 文本)。history=True 时逐提交读，标签形如 <sha:8>:<文件>。"""
    if history:
        revs = [r for r in git(repo, "rev-list", "--all").split() if r]
        for rev in revs:
            names = [f for f in git(repo, "ls-tree", "-r", "--name-only", rev).splitlines() if f.strip()]
            for rel in names:
                raw = subprocess.run(
                    ["git", "-C", str(repo), "-c", "core.quotepath=false",
                     "show", "%s:%s" % (rev, rel)],
                    capture_output=True, encoding="utf-8", errors="replace").stdout or ""
                yield ("%s:%s" % (rev[:8], rel)), raw
        return
    for rel in [f for f in git(repo, "ls-files").splitlines() if f.strip()]:
        p = Path(repo) / rel
        if not p.is_file():
            continue
        try:
            raw = p.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:
            continue
        try:
            yield rel, raw.decode("utf-8")
        except UnicodeDecodeError:
            continue


def main():
    ap = argparse.ArgumentParser(description="公开仓库前的敏感信息体检")
    ap.add_argument("repo", help="仓库路径")
    ap.add_argument("--history", action="store_true", help="连 git 历史一起扫")
    args = ap.parse_args()

    repo = Path(args.repo).expanduser().resolve()
    if not (repo / ".git").is_dir():
        print("不是 git 仓库: %s" % repo)
        return 2

    mode = "全部历史" if args.history else "当前工作区"
    print("仓库: %s\n范围: %s\n" % (repo, mode))

    total = 0
    for label, rx in PATTERNS:
        seen = {}
        n = 0
        for tag, txt in iter_blobs(repo, args.history):
            for i, line in enumerate(txt.splitlines(), 1):
                for m in rx.finditer(line):
                    n += 1
                    seen.setdefault(tag, set()).add((i, m.group(0)))
        if not n:
            continue
        print("=" * 70)
        print("【%s】命中 %d 处" % (label, n))
        print("=" * 70)
        for tag in sorted(seen):
            print("  %s" % tag)
            for ln, val in sorted(seen[tag]):
                print("    第%-4d行  %s" % (ln, val))
        print()
        total += n

    if total:
        print("合计命中: %d 处 —— 逐条人工判定是否可公开" % total)
        print("需要用历史改写清除时: scrub_git_history.py --repo <路径>")
    else:
        print("无命中。")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
