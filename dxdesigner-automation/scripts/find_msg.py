# -*- coding: utf-8 -*-
"""find_msg.py - locate which program/module owns a GUI error message.

Why: an error dialog tells you nothing about its source. A byte-level scan of the
install trees does - and the *neighbouring* strings in a resource block usually
spell out the failure conditions.

Usage:
    python find_msg.py "Cannot create project" E:\\APP
    python find_msg.py "Cannot create project" E:\\APP -x E:\\APP\\MentorGraphics
    python find_msg.py "Cannot create project" E:\\APP\\MentorGraphics --context 30

Notes / gotchas learned the hard way:
  * ripgrep / grep tools time out on trees this big (70k+ files). Scan in Python.
  * Windows resource strings are usually UTF-16LE - ASCII-only search MISSES them.
    This script searches both encodings.
  * The context dump is the valuable part: sibling strings in the same block are
    the validation branches ("folder already exists", "No name was specified"...).
"""
import argparse
import os
import queue
import re
import sys
import threading

SKIP_EXT = {'.zip', '.7z', '.rar', '.cab', '.msi', '.tmp', '.asar', '.iso', '.gz'}
CHUNK = 8 * 1024 * 1024
OVERLAP = 64


def needles(text):
    b = text.encode('utf-8')
    return [b, text.encode('utf-16-le')]


def walk(roots, exclude):
    out = []
    ex = tuple(os.path.abspath(e).lower() for e in exclude)
    for root in roots:
        for dp, dns, fns in os.walk(root):
            low = os.path.abspath(dp).lower()
            if ex and any(low.startswith(e) or low == e for e in ex):
                dns[:] = []
                continue
            for fn in fns:
                if os.path.splitext(fn)[1].lower() in SKIP_EXT:
                    continue
                out.append(os.path.join(dp, fn))
    return out


def scan(files, ns, max_mb):
    """Return list of files containing any needle."""
    limit = max_mb * 1024 * 1024
    q = queue.Queue()
    for f in files:
        q.put(f)
    hits, done = [], [0]
    lock = threading.Lock()

    def worker():
        while True:
            try:
                p = q.get_nowait()
            except queue.Empty:
                return
            found = False
            try:
                if os.path.getsize(p) <= limit:
                    with open(p, 'rb') as fh:
                        tail = b''
                        while True:
                            buf = fh.read(CHUNK)
                            if not buf:
                                break
                            data = tail + buf
                            if any(n in data for n in ns):
                                found = True
                                break
                            tail = data[-OVERLAP:]
            except Exception:
                pass
            with lock:
                done[0] += 1
                if found:
                    hits.append(p)
                if done[0] % 20000 == 0:
                    sys.stderr.write('  ... %d scanned, %d hits\n' % (done[0], len(hits)))

    ths = [threading.Thread(target=worker, daemon=True) for _ in range(16)]
    [t.start() for t in ths]
    [t.join() for t in ths]
    return hits, done[0]


def strings(path, minlen, encoding):
    with open(path, 'rb') as f:
        data = f.read()
    if encoding == 'ascii':
        pat, enc = rb'[\x20-\x7e]{%d,}' % minlen, 'ascii'
    else:
        pat, enc = rb'(?:[\x20-\x7e]\x00){%d,}' % minlen, 'utf-16-le'
    return [(m.start(), m.group().decode(enc, 'replace')) for m in re.finditer(pat, data)]


def context(path, text, span):
    print('=' * 96)
    print(path)
    print('=' * 96)
    for encoding in ('ascii', 'utf-16le'):
        try:
            ss = strings(path, 4, encoding)
        except Exception as e:
            print('  (unreadable: %s)' % e)
            continue
        idx = [i for i, (_, s) in enumerate(ss) if text.lower() in s.lower()]
        if not idx:
            continue
        print('--- %s : %d hit(s) / %d strings ---' % (encoding, len(idx), len(ss)))
        for i in idx:
            for j in range(max(0, i - span), min(len(ss), i + span + 1)):
                print('%s %s' % ('>>' if j == i else '  ', ss[j][1][:170]))
            print('-' * 88)
        print()


def main():
    ap = argparse.ArgumentParser(description='Find which app owns an error message.')
    ap.add_argument('text', help='message text, e.g. "Cannot create project"')
    ap.add_argument('roots', nargs='+', help='directories to scan')
    ap.add_argument('-x', '--exclude', action='append', default=[], help='subtree to skip')
    ap.add_argument('-c', '--context', type=int, default=0,
                    help='dump N neighbouring strings per hit (>0 enables it)')
    ap.add_argument('-m', '--max-mb', type=int, default=250, help='skip files larger than N MB')
    a = ap.parse_args()

    print('needle : %r  (ASCII + UTF-16LE)' % a.text)
    print('roots  : %s' % a.roots)
    if a.exclude:
        print('exclude: %s' % a.exclude)
    files = walk(a.roots, a.exclude)
    print('files  : %d' % len(files))
    hits, done = scan(files, needles(a.text), a.max_mb)
    print('scanned: %d' % done)
    print('HITS   : %d' % len(hits))
    for h in sorted(hits):
        try:
            sz = os.path.getsize(h)
        except OSError:
            sz = -1
        print('   %10d  %s' % (sz, h))
    if a.context:
        for h in sorted(hits):
            context(h, a.text, a.context)


if __name__ == '__main__':
    main()
