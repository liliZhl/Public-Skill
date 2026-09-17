#!/usr/bin/env python3
"""Deprecated entry point. Use scripts/ssh_ctl.py instead.

Kept so that older notes and habits that call
    python eda-ssh.py <action> ...
keep working. It forwards every argument to scripts/ssh_ctl.py unchanged.
"""

import os
import runpy
import sys

TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts", "ssh_ctl.py")

if not os.path.isfile(TARGET):
    sys.stderr.write("missing %s\n" % TARGET)
    sys.exit(1)

sys.argv[0] = TARGET
runpy.run_path(TARGET, run_name="__main__")
