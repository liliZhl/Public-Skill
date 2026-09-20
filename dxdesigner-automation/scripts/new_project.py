# -*- coding: utf-8 -*-
"""new_project.py - 用"复制现成工程"的方式造一个新工程（绕开 7.9.5 坏掉的建工程向导）。

为什么需要它：EE 7.9.5 的 Com 接口 `NewProject` 已废弃（LastErrorId 670），
GUI 的新建工程向导也会失败（弹 `Cannot create project`，见
references/new-project-failure.md）。唯一可靠的路是复制一个能打开的工程，
再改名/改配置。

用法：
    # 最小：复制成新目录，工程文件名保持不变（最保险，iCDB 链接不会断）
    python new_project.py --src "<旧工程目录>" --dst "<新工程目录>"

    # 连 .prj 文件名一起改（会验证 iCDB 是否仍能打开——见脚本输出提示）
    python new_project.py --src "<旧>" --dst "<新>" --prj-name MYPROJ

    # 中央库不可达时，把副本的中央库改指向本地库
    python new_project.py --src "<旧>" --dst "<新>" --central-library "<本地.lmc>"

    # 瘦身：跳过备份/日志（默认全量复制，保真优先）
    python new_project.py --src "<旧>" --dst "<新>" -x ProjectBackup -x "Log Files"

注意：
  * `--dst` 已存在且非空时默认拒绝（EE 里"目标文件夹已存在"本身就是失败原因之一），
    要覆盖请显式加 --force。
  * 源工程全程只读，绝不改源。
  * 工程内部的 design 名（如 Board1）、根图纸名（RootSchematicFile）属于 iCDB 内容，
    本脚本**不改**——改它们需要动 iCDB，风险高收益低。
"""
import argparse
import os
import shutil
import sys
import time

DEFAULT_EXCLUDE = ()


def human(n):
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024 or unit == 'GB':
            return '%.1f %s' % (n, unit) if unit != 'B' else '%d B' % n
        n /= 1024.0


def find_prj(src):
    prjs = [f for f in os.listdir(src) if f.lower().endswith('.prj')]
    if not prjs:
        raise SystemExit('源目录里没有 .prj：%s' % src)
    if len(prjs) > 1:
        raise SystemExit('源目录里有多个 .prj，请手动指定：%s' % prjs)
    return prjs[0]


def fix_unc(p):
    """命令行传 UNC 路径时，shell 常把 `\\\\server\\share` 吃掉一层变成 `\\server\\share`，
    写进 .prj 就是个坏路径。这里自动补回来并告知调用者。"""
    if p.startswith('\\\\') or not p.startswith('\\'):
        return p, False
    return '\\' + p, True


def patch_central_library(prj_path, lmc):
    """改 SECTION DesignInfo 里的 KEY CentralLibrary。返回 (旧值, 新值)。"""
    with open(prj_path, 'r', encoding='utf-8', errors='replace') as f:
        lines = f.readlines()
    old, section, hit = None, None, False
    for i, ln in enumerate(lines):
        s = ln.strip()
        if s.upper().startswith('SECTION '):
            section = s.split(None, 1)[1].strip().strip('"')
        elif s.upper().startswith('KEY '):
            parts = s.split(None, 2)
            if len(parts) >= 2 and parts[1].lower() == 'centrallibrary':
                old = parts[2].strip().strip('"') if len(parts) > 2 else ''
                lines[i] = 'KEY CentralLibrary "%s"\n' % lmc
                hit = True
    if hit:
        with open(prj_path, 'w', encoding='utf-8', newline='') as f:
            f.writelines(lines)
    return old, lmc, hit


def main():
    ap = argparse.ArgumentParser(description='Copy an existing EE project into a new one.')
    ap.add_argument('--src', required=True, help='源工程目录（只读，不会被改）')
    ap.add_argument('--dst', required=True, help='新工程目录')
    ap.add_argument('--prj-name', default=None,
                    help='把 .prj 改名成这个（不带扩展名）。不改则保持原文件名，最保险。')
    ap.add_argument('--central-library', default=None,
                    help='把副本的 CentralLibrary 改成这个 .lmc 路径')
    ap.add_argument('-x', '--exclude', action='append', default=[],
                    help='复制时跳过的子目录名（可重复），默认全量复制')
    ap.add_argument('--force', action='store_true', help='目标目录已存在时也继续')
    a = ap.parse_args()

    src, dst = os.path.abspath(a.src), os.path.abspath(a.dst)
    if not os.path.isdir(src):
        raise SystemExit('源工程目录不存在：%s' % src)
    if os.path.abspath(src) == dst:
        raise SystemExit('源和目标是同一个目录')
    if os.path.exists(dst):
        if os.listdir(dst) and not a.force:
            raise SystemExit('目标目录已存在且非空（这正是向导失败的原因之一）：%s\n'
                             '换个名字，或加 --force' % dst)
    else:
        parent = os.path.dirname(dst)
        if not os.path.isdir(parent):
            print('父目录不存在，先创建：%s' % parent)
            os.makedirs(parent)

    prj = find_prj(src)
    print('源工程   : %s' % src)
    print('工程文件 : %s' % prj)
    print('目标     : %s' % dst)
    if a.exclude:
        print('跳过     : %s' % ', '.join(a.exclude))

    skip = set(x.lower() for x in a.exclude)

    def ignore(dirpath, names):
        return [n for n in names if n.lower() in skip]

    t0 = time.time()
    shutil.copytree(src, dst, ignore=ignore if skip else None)
    files = sum(len(fs) for _, _, fs in os.walk(dst))
    size = sum(os.path.getsize(os.path.join(dp, f))
               for dp, _, fs in os.walk(dst) for f in fs)
    print('已复制   : %d 个文件 / %s / %.1f s' % (files, human(size), time.time() - t0))

    new_prj = os.path.join(dst, prj)
    if a.prj_name:
        target = a.prj_name + '.prj'
        os.rename(new_prj, os.path.join(dst, target))
        print('工程文件已改名 : %s -> %s' % (prj, target))
        print('  ⚠ 必须验证：改名后 iCDB 可能对不上，用 probe 打开一次确认')
        new_prj = os.path.join(dst, target)

    if a.central_library:
        lmc, fixed = fix_unc(a.central_library)
        if fixed:
            print('⚠ 中央库路径少了一层反斜杠（被 shell 转义吃掉了），已自动补回：')
            print('    %s   ->   %s' % (a.central_library, lmc))
        old, new, ok = patch_central_library(new_prj, lmc)
        if ok:
            print('中央库已改 : %s -> %s' % (old, new))
        else:
            print('⚠ 没找到 CentralLibrary 键，未改动')

    print()
    print('下一步（验证能打开）：')
    print('  python -c "import sys; sys.path.insert(0, r\'%s\'); import dxd;'
          ' app,c=dxd.connect(); dxd.open_project(app, r\'%s\');'
          ' print(dxd.list_schematics(app)); dxd.close(app, c)"'
          % (os.path.dirname(os.path.abspath(__file__)), new_prj))


if __name__ == '__main__':
    main()
