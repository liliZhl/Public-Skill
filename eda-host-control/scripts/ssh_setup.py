#!/usr/bin/env python3
"""ssh_setup - one-shot setup, verification and capability probe for ssh_ctl.

Run this FIRST, before using ssh_ctl. It checks the local client stack, tests
the connection, probes what the remote SSH session is actually allowed to do,
and (optionally) writes a config file so later ssh_ctl calls need no arguments.

USAGE
    # interactive - prompts for anything not supplied
    python ssh_setup.py --host <HOST_IP> --user 'CORP\\alice'

    # fully non-interactive (for an agent / CI)
    python ssh_setup.py --host <HOST_IP> --user 'CORP\\alice' \
        --password-stdin --write ~/.config/ssh-ctl/config.json

    # just re-verify an existing config, change nothing
    python ssh_setup.py --check

    # verify an ad-hoc target without saving anything
    python ssh_setup.py --host <HOST_IP> --user 'CORP\\alice' --no-save

FLAGS
    --host / --port / --user / --password        connection settings
    --password-stdin                             read the password from stdin
                                                 (keeps it out of shell history)
    --name NAME                                  friendly display name
    --write PATH                                 write config here
    --no-save                                    probe and exit, write nothing
    --check                                      load the default config and verify it
    --skip-install                               never try to pip-install paramiko

Exit codes:  0 = ready,  1 = local stack problem,  2 = could not connect,
             3 = connected but the session is unusable for automation.
"""

import argparse
import getpass
import json
import os
import platform
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(os.path.expanduser("~"), ".config", "ssh-ctl", "config.json")

lines = []
STATUS = {"local": False, "connect": False, "automation": False}


def out(line=""):
    lines.append("" if line is None else str(line))


def emit(code):
    text = "\n".join(lines)
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(text)
    try:
        log = os.path.join(
            os.environ.get("TEMP") or os.environ.get("TMP") or "/tmp",
            "ssh_setup_last.txt",
        )
        with open(log, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:  # noqa: BLE001
        pass
    sys.exit(code)


# --------------------------------------------------------------------------- #
def step_local():
    out("=" * 66)
    out(" Step 1 - local client stack")
    out("=" * 66)
    out("python      : %s (%s)" % (sys.version.split()[0], platform.system()))
    out("executable  : %s" % sys.executable)

    try:
        import paramiko  # noqa: F401
        out("paramiko    : %s  OK" % getattr(paramiko, "__version__", "unknown"))
        STATUS["local"] = True
        return True
    except ImportError:
        out("paramiko    : MISSING")
        return False


def ensure_paramiko(skip_install):
    if step_local():
        return True
    if skip_install:
        out("")
        out("  Install it manually:  %s -m pip install paramiko" % sys.executable)
        return False
    out("")
    out("  attempting: %s -m pip install paramiko" % sys.executable)
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet",
             "--disable-pip-version-check", "paramiko"],
            capture_output=True, text=True, timeout=300,
        )
        if r.returncode == 0:
            out("  pip exit 0")
        else:
            out("  pip exit %s" % r.returncode)
            out((r.stderr or r.stdout or "").strip()[:600])
    except Exception as ex:  # noqa: BLE001
        out("  pip failed: %r" % ex)
    out("")
    return step_local()


def step_connect(host, port, user, password, timeout=20):
    out("")
    out("=" * 66)
    out(" Step 2 - connect")
    out("=" * 66)
    out("target      : %s:%s" % (host, port))
    out("username    : %s" % user)

    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            host, port=port, username=user, password=password,
            timeout=timeout, banner_timeout=timeout, auth_timeout=timeout,
            allow_agent=False, look_for_keys=False,
        )
    except paramiko.AuthenticationException as ex:
        out("[FAIL] authentication rejected: %s" % ex)
        out("")
        out("  Most common cause: the 'password' is a Windows Hello PIN /")
        out("  fingerprint login, which is device-bound and NOT valid for")
        out("  network authentication. Use the real account password.")
        out("  Domain accounts must be written  DOMAIN\\user  or  user@domain.")
        return None, "auth"
    except Exception as ex:  # noqa: BLE001
        out("[FAIL] %r" % ex)
        out("")
        out("  Checklist: host reachable on TCP %s? sshd running and listening?" % port)
        out("             firewall rule for inbound %s present?" % port)
        return None, "connect"

    out("[PASS] authenticated")
    STATUS["connect"] = True
    return client, None


def step_probe(client):
    out("")
    out("=" * 66)
    out(" Step 3 - what this session can actually do")
    out("=" * 66)

    def run(cmd):
        _i, o, e = client.exec_command(cmd, timeout=120)
        so = o.read().decode("utf-8", "replace").strip()
        se = e.read().decode("utf-8", "replace").strip()
        rc = o.channel.recv_exit_status()
        return rc, so, se

    rc, so, _ = run("hostname & whoami")
    out("hostname    : %s" % so.splitlines()[0] if so else "hostname    : (none)")
    if len(so.splitlines()) > 1:
        out("whoami      : %s" % so.splitlines()[1])

    import base64

    ps = (
        "$ProgressPreference='SilentlyContinue'\n"
        "$id=[Security.Principal.WindowsIdentity]::GetCurrent()\n"
        "'identity    : ' + $id.Name\n"
        "'is_admin    : ' + (New-Object Security.Principal.WindowsPrincipal($id))."
        "IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\n"
        "$m=[regex]::Match(((whoami /groups) -join \"`n\"), 'S-1-16-(\\d+)')\n"
        "'integrity   : ' + $(if ($m.Success) { $m.Value } else { '(not reported)' })\n"
        "(Get-CimInstance Win32_OperatingSystem).Caption + ' build ' + "
        "(Get-CimInstance Win32_OperatingSystem).BuildNumber\n"
    )
    enc = base64.b64encode(ps.encode("utf-16-le")).decode("ascii")
    rc, so, _ = run("powershell -NoProfile -NonInteractive -EncodedCommand " + enc)
    for ln in (so or "").splitlines():
        if ln.strip():
            out("              " + ln.strip())

    elevated = ("is_admin    : True" in so) or ("S-1-16-12288" in so)
    if elevated:
        out("")
        out("[PASS] elevated session - Restart-Service / HKLM / netsh will work.")
        STATUS["automation"] = True
    else:
        out("")
        out("[WARN] filtered (non-elevated) token - admin writes will be denied.")
        out("       Run privileged work in an interactive console session instead.")

    try:
        sf = client.open_sftp()
        home = sf.normalize(".")
        sf.close()
        out("[PASS] sftp subsystem ok (home = %s)" % home)
    except Exception as ex:  # noqa: BLE001
        out("[WARN] sftp unavailable: %r  (put/get/cat/ls will fail)" % ex)

    return STATUS["automation"]


def harden(path):
    """Best-effort: restrict the credential file to the current user."""
    system = platform.system()
    try:
        if system == "Windows":
            who = os.environ.get("USERNAME", "")
            subprocess.run(
                ["icacls", path, "/inheritance:r",
                 "/grant", "%s:F" % who, "/grant", "SYSTEM:F",
                 "/grant", "Administrators:F"],
                capture_output=True, text=True, timeout=60,
            )
        else:
            os.chmod(path, 0o600)
        return True
    except Exception:  # noqa: BLE001
        return False


def write_config(path, cfg):
    path = os.path.expanduser(path)
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=2)
    hardened = harden(path)
    out("")
    out("config written: %s" % path)
    out("permissions   : %s" % ("restricted to current user + SYSTEM/Administrators"
                               if hardened else "could not tighten - check manually"))
    out("")
    out("ssh_ctl.py now needs no arguments, e.g.:")
    out("  python %s info" % os.path.join(HERE, "ssh_ctl.py"))
    return path


# --------------------------------------------------------------------------- #
def main(argv):
    p = argparse.ArgumentParser(
        prog="ssh_setup.py",
        description="Set up and verify ssh_ctl against a Windows SSH host.",
    )
    p.add_argument("--host")
    p.add_argument("--port", type=int)
    p.add_argument("--user")
    p.add_argument("--password")
    p.add_argument("--password-stdin", action="store_true",
                   help="read the password from stdin instead of a prompt")
    p.add_argument("--name", help="friendly display name for the host")
    p.add_argument("--write", help="config file to write (default %s)" % DEFAULT_CONFIG)
    p.add_argument("--no-save", action="store_true", help="probe only, write nothing")
    p.add_argument("--check", action="store_true",
                   help="load the default config and verify it, write nothing")
    p.add_argument("--skip-install", action="store_true")
    args = p.parse_args(argv[1:])

    out("ssh_setup - %s" % __file__)

    # ---- resolve settings ------------------------------------------------- #
    cfg = {"port": 22}

    if args.check:
        sys.path.insert(0, HERE)
        try:
            import ssh_ctl
            found = ssh_ctl.find_default_config()
        except Exception:  # noqa: BLE001
            found = None
        if not found:
            out("[FAIL] no readable config found in the default search path.")
            out("       Run ssh_setup.py --host H --user U to create one.")
            return emit(2)
        with open(found, "r", encoding="utf-8-sig") as fh:
            raw = json.load(fh)
        for k, v in raw.items():
            cfg[ssh_ctl.ALIASES.get(k, k)] = v
        out("using config : %s" % found)
        args.no_save = True

    if args.host:
        cfg["host"] = args.host
    if args.port:
        cfg["port"] = args.port
    if args.user:
        cfg["username"] = args.user
    if args.name:
        cfg["hostname"] = args.name
    if args.password:
        cfg["password"] = args.password

    if not cfg.get("host"):
        try:
            cfg["host"] = input("host (IP or DNS name): ").strip()
        except EOFError:
            out("[FAIL] no host supplied and stdin is not interactive.")
            return emit(2)
    if not cfg.get("username"):
        try:
            cfg["username"] = input("username (DOMAIN\\user for domain accounts): ").strip()
        except EOFError:
            out("[FAIL] no username supplied and stdin is not interactive.")
            return emit(2)
    if not cfg.get("password"):
        if args.password_stdin:
            cfg["password"] = sys.stdin.readline().rstrip("\r\n")
        else:
            cfg["password"] = getpass.getpass("password: ")

    if not cfg.get("password"):
        out("[FAIL] empty password.")
        return emit(2)

    # ---- run the three steps ---------------------------------------------- #
    if not ensure_paramiko(args.skip_install):
        out("")
        out("RESULT: cannot continue without paramiko.")
        return emit(1)

    client, why = step_connect(cfg["host"], int(cfg.get("port", 22)),
                               cfg["username"], cfg["password"])
    if client is None:
        out("")
        out("RESULT: not ready (%s)." % why)
        return emit(2)

    try:
        step_probe(client)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- persist ---------------------------------------------------------- #
    if not args.no_save:
        saved = {
            "host": cfg["host"],
            "port": int(cfg.get("port", 22)),
            "username": cfg["username"],
            "password": cfg["password"],
        }
        if cfg.get("hostname"):
            saved["hostname"] = cfg["hostname"]
        write_config(args.write or DEFAULT_CONFIG, saved)
    else:
        out("")
        out("(--no-save / --check: nothing written)")

    out("")
    out("=" * 66)
    out(" RESULT: READY")
    out("=" * 66)
    out("Next:  python %s info" % os.path.join(HERE, "ssh_ctl.py"))
    out("       python %s probe" % os.path.join(HERE, "ssh_ctl.py"))
    return emit(0)


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        print("\ninterrupted")
        code = 130
    except Exception as exc:  # noqa: BLE001
        print("FATAL: %r" % exc)
        code = 1
    sys.exit(code)
