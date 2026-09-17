#!/usr/bin/env python3
"""ssh_ctl - drive a Windows host over SSH from a non-interactive agent.

WHY THIS EXISTS
    A coding agent usually has no TTY, so it cannot answer an ssh(1) password
    prompt. paramiko speaks the SSH protocol directly, which turns the password
    into an ordinary function argument. That is the whole trick.

WHY PASSWORD AND NOT KEYS
    Some Windows hosts (domain-joined, sshd built by Win32-OpenSSH) accept a
    public key and then die while building the logon token:

        Accepted publickey for user CORP\\alice ...
        error: unable to get security token for user CORP\\alice
        error: get_user_token - unable to generate token on 2nd attempt
        fatal: fork of unprivileged child failed

    Password auth takes the LogonUser path instead of S4U and succeeds.
    This tool therefore defaults to password auth and deliberately does NOT
    fall back to keys (allow_agent=False, look_for_keys=False).

USAGE
    ssh_ctl.py exec  "<cmd>"                run through the remote shell (cmd.exe)
    ssh_ctl.py ps    "<powershell>"         run inline PowerShell
    ssh_ctl.py psf   <local.ps1>            run a LOCAL .ps1 ON the host
    ssh_ctl.py put   <local> <remote>       upload (sftp)
    ssh_ctl.py get   <remote> <local>       download (sftp)
    ssh_ctl.py cat   <remote>               print a remote text file
    ssh_ctl.py ls    [<remote_dir>]         list a directory
    ssh_ctl.py rm    <remote>               delete a file
    ssh_ctl.py rmdir <remote_dir>           delete an empty directory
    ssh_ctl.py mkdir <remote_dir>           create a directory
    ssh_ctl.py probe                        capability probe of the session
    ssh_ctl.py info                         host + OS + disks summary
    ssh_ctl.py sessions                     interactive sessions / console state

GLOBAL FLAGS (any position)
    --config PATH     explicit config file
    --host/--port/--user/--password   per-call override
    --json            emit a single JSON object on stdout instead of a report
    --quiet           do not echo the report to stdout (still written to log)
    --timeout SEC     command timeout, default 300

CONFIG RESOLUTION (later sources win, key by key)
    1. built-in defaults
    2. first readable file from:  $SSH_CTL_CONFIG,
       ~/.workbuddy/secrets/eda-host.json, ~/.config/ssh-ctl/config.json,
       ~/.ssh-ctl.json, ./ssh-ctl.json
    3. environment:  SSH_CTL_HOST / _PORT / _USER / _PASSWORD / _LOG
                     (legacy aliases: EDA_SSH_HOST / _PORT / _USER / _PASSWORD)
    4. command-line flags

CONFIG FILE SHAPE
    {
      "host":       "<HOST_IP>",          // required - IP or DNS name
      "port":       22,
      "username":   "CORP\\alice",       // alias accepted: ssh_user
      "password":   "...",               // alias accepted: pass
      "hostname":   "PC-01",             // optional, display only
      "log":        "/tmp/ssh_ctl_last.txt",   // optional
      "remote_shell": "cmd"              // optional: cmd | powershell
    }

REMOTE PATHS
    D:\\x, D:/x and /D:/x are all accepted and normalised.

IMPORTANT - WHERE TO READ THE RESULT
    The full text report is ALWAYS written to the log file (default
    <tempdir>/ssh_ctl_last.txt). Read that file: agent shells frequently
    swallow stdout or transcode non-ASCII output. With --json the structured
    result goes to stdout AND to a sibling file <log basename>.json, whose path
    is reported as "json_file" - read that if stdout looks mangled.

IMPORTANT - ELEVATION IS NOT GUARANTEED
    Whether the session is elevated depends on how the host's sshd creates the
    token. Probe it with `ssh_ctl.py probe` before assuming Restart-Service,
    HKLM writes or netsh will work.

Depends on: paramiko  (pip install paramiko)
"""

import argparse
import base64
import json
import os
import sys
import tempfile

DEFAULT_LOG_NAME = "ssh_ctl_last.txt"

SEARCH_PATHS = [
    os.path.join("~", ".workbuddy", "secrets", "eda-host.json"),
    os.path.join("~", ".config", "ssh-ctl", "config.json"),
    os.path.join("~", ".ssh-ctl.json"),
    os.path.join(".", "ssh-ctl.json"),
]

ALIASES = {
    "ssh_user": "username",
    "user": "username",
    "pass": "password",
    "passwd": "password",
    "pwd": "password",
    "ip": "host",
    "address": "host",
}

buffer = []
# Filled in by main(); consumed by report()/finish().
CTX = {"json": False, "result": {}}


# --------------------------------------------------------------------------- #
# output
# --------------------------------------------------------------------------- #
def out(line=""):
    buffer.append("" if line is None else str(line))


def log_path(cfg):
    p = cfg.get("log")
    if p:
        return os.path.expanduser(os.path.expandvars(str(p)))
    return os.path.join(tempfile.gettempdir(), DEFAULT_LOG_NAME)


def flush_to_disk(path):
    try:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join(buffer) + "\n")
    except Exception:  # noqa: BLE001
        pass


def finish(code, cfg=None):
    """Write the report to disk, print it (unless --quiet), exit."""
    path = log_path(cfg or {})
    flush_to_disk(path)
    if CTX["json"]:
        CTX["result"].setdefault("ok", code == 0)
        CTX["result"].setdefault("exit_code", code)
        CTX["result"]["log_file"] = path
        blob = json.dumps(CTX["result"], ensure_ascii=False, indent=2)
        # Also drop the JSON next to the text log: a mangling shell can transcode
        # stdout, but a file written here is always intact UTF-8.
        json_path = os.path.splitext(path)[0] + ".json"
        try:
            with open(json_path, "w", encoding="utf-8") as fh:
                fh.write(blob + "\n")
            CTX["result"]["json_file"] = json_path
            blob = json.dumps(CTX["result"], ensure_ascii=False, indent=2)
        except Exception:  # noqa: BLE001
            pass
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        print(blob)
    elif not CTX.get("quiet"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
        print("\n".join(buffer))
    sys.exit(code)


def report(rc, o, e):
    """Record one command result into both the text report and the JSON result."""
    out("rc = %s" % rc)
    out("--- stdout ---")
    out((o or "").rstrip() or "(empty)")
    if (e or "").strip():
        out("--- stderr ---")
        out(e.rstrip())
    CTX["result"].setdefault("commands", []).append(
        {"rc": rc, "stdout": (o or "").strip(), "stderr": (e or "").strip()}
    )


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _canon(d):
    if not d:
        return {}
    out_d = {}
    for k, v in d.items():
        out_d[ALIASES.get(k, k)] = v
    return out_d


def find_default_config():
    env = os.environ.get("SSH_CTL_CONFIG")
    if env:
        p = os.path.expanduser(env)
        if os.path.isfile(p):
            return p
    for cand in SEARCH_PATHS:
        p = os.path.abspath(os.path.expanduser(cand))
        if os.path.isfile(p):
            return p
    return None


def build_config(args):
    cfg = {"port": 22, "timeout": 300}

    default_file = find_default_config()
    if default_file:
        cfg.update(_canon(_read_json(default_file)))
        cfg["_config_file"] = default_file

    if args.config:
        explicit = _canon(_read_json(os.path.expanduser(args.config)))
        if not explicit:
            return None, "cannot read config file: %s" % args.config
        cfg.update(explicit)
        cfg["_config_file"] = args.config

    for env_key, field in (
        ("SSH_CTL_HOST", "host"),
        ("SSH_CTL_PORT", "port"),
        ("SSH_CTL_USER", "username"),
        ("SSH_CTL_PASSWORD", "password"),
        ("SSH_CTL_LOG", "log"),
        ("EDA_SSH_HOST", "host"),
        ("EDA_SSH_PORT", "port"),
        ("EDA_SSH_USER", "username"),
        ("EDA_SSH_PASSWORD", "password"),
    ):
        val = os.environ.get(env_key)
        if val:
            cfg[field] = val.strip()

    for field in ("host", "port", "username", "password"):
        val = getattr(args, field, None)
        if val:
            cfg[field] = val

    if cfg.get("port"):
        try:
            cfg["port"] = int(cfg["port"])
        except Exception:  # noqa: BLE001
            return None, "port must be an integer, got %r" % cfg["port"]

    missing = [k for k in ("host", "username", "password") if not cfg.get(k)]
    if missing:
        return None, (
            "missing connection setting(s): %s\n"
            "  Fix with any of:\n"
            "    export SSH_CTL_HOST=... SSH_CTL_USER=... SSH_CTL_PASSWORD=...\n"
            "    ssh_ctl.py <action> --host H --user U --password P ...\n"
            "    write a config file and pass --config PATH\n"
            "  Or run:  python ssh_setup.py --host H --user U"
            % ", ".join(missing)
        )
    return cfg, None


# --------------------------------------------------------------------------- #
# ssh plumbing
# --------------------------------------------------------------------------- #
def decode(raw):
    if raw is None:
        return ""
    for enc in ("utf-8", "gbk", "cp936", "latin-1"):
        try:
            return raw.decode(enc)
        except Exception:  # noqa: BLE001
            continue
    return raw.decode("utf-8", "replace")


def connect(cfg):
    try:
        import paramiko
    except ImportError:
        raise RuntimeError(
            "paramiko is not installed. Run:  python -m pip install paramiko"
        )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    t = int(cfg.get("timeout", 25))
    client.connect(
        cfg["host"],
        port=int(cfg.get("port", 22)),
        username=cfg["username"],
        password=cfg["password"],
        timeout=t,
        banner_timeout=t,
        auth_timeout=t,
        # Password is the supported path; never silently try keys.
        allow_agent=False,
        look_for_keys=False,
    )
    return client


def run(client, command, timeout=300):
    _stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
    o = stdout.read()
    e = stderr.read()
    rc = stdout.channel.recv_exit_status()
    return rc, decode(o), decode(e)


PS_PRELUDE = (
    "$ProgressPreference='SilentlyContinue'\n"
    "$ErrorActionPreference='Continue'\n"
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8\n"
    "$OutputEncoding=[System.Text.Encoding]::UTF8\n"
)


def run_ps(client, script, timeout=300):
    """Base64 -EncodedCommand dodges every quoting layer between here and the host."""
    blob = PS_PRELUDE + script
    encoded = base64.b64encode(blob.encode("utf-16-le")).decode("ascii")
    return run(client, "powershell -NoProfile -NonInteractive -EncodedCommand " + encoded, timeout)


def parse_remote_path(p):
    """Accept D:\\x, D:/x or /D:/x and normalise for paramiko."""
    if len(p) > 2 and p[0] == "/" and p[2] == ":":
        return p
    return p.replace("\\", "/")


def sftp(client):
    return client.open_sftp()


def is_dir(attr):
    import stat as _stat

    return bool(attr.st_mode and _stat.S_ISDIR(attr.st_mode))


# --------------------------------------------------------------------------- #
# actions
# --------------------------------------------------------------------------- #
def do_probe(client, cfg, timeout):
    """Report what an agent actually needs to know before automating this host."""
    out("-- identity --")
    report(*run(client, "hostname & whoami & echo USERDOMAIN=%USERDOMAIN%", timeout))
    out("-- elevation --")
    report(*run_ps(
        client,
        "$id=[Security.Principal.WindowsIdentity]::GetCurrent()\n"
        "'user      : ' + $id.Name\n"
        "'is_admin  : ' + (New-Object Security.Principal.WindowsPrincipal($id))."
        "IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)\n"
        # The integrity SID must come from whoami /groups: WindowsIdentity.Groups
        # omits it, and the surrounding label text is localised, so match the SID.
        "$m=[regex]::Match(((whoami /groups) -join \"`n\"), 'S-1-16-(\\d+)')\n"
        "if ($m.Success) {\n"
        "  'integrity : ' + $m.Value\n"
        "  switch ($m.Groups[1].Value) {\n"
        "    '12288' { 'verdict   : ELEVATED  (admin writes allowed)' }\n"
        "    '8192'  { 'verdict   : FILTERED  (admin writes denied - use a console session)' }\n"
        "    '4096'  { 'verdict   : MEDIUM    (not elevated)' }\n"
        "    default { 'verdict   : integrity level ' + $m.Groups[1].Value }\n"
        "  }\n"
        "} else { 'integrity : (not reported)' }\n",
        timeout,
    ))
    out("-- default shell --")
    report(*run(client, "echo default_comspec=%COMSPEC%", timeout))
    out("-- sftp reachable --")
    try:
        sf = sftp(client)
        cwd = sf.normalize(".")
        sf.close()
        out("[PASS] sftp ok, home = %s" % cwd)
        CTX["result"]["sftp"] = True
    except Exception as ex:  # noqa: BLE001
        out("[FAIL] sftp: %r" % ex)
        CTX["result"]["sftp"] = False
    out("-- elevated-only smoke test (HKLM read + service query) --")
    report(*run_ps(
        client,
        "try { $null = Get-Item 'HKLM:\\SOFTWARE'; 'HKLM read  : OK' } catch { 'HKLM read  : DENIED' }\n"
        "try { $null = Get-Service -Name 'sshd' -ErrorAction Stop; 'service q  : OK' }"
        " catch { 'service q  : DENIED' }\n",
        timeout,
    ))


def do_cat(client, path, timeout):
    sf = sftp(client)
    try:
        with sf.open(path, "rb") as fh:
            data = fh.read()
        out("path: %s (%d bytes)" % (path, len(data)))
        out("--- content ---")
        out(decode(data).rstrip())
        CTX["result"]["content"] = decode(data)
    finally:
        sf.close()


def do_info(client, cfg, timeout):
    out("-- via shell --")
    report(*run(client, "hostname & whoami & ver", timeout))
    out("-- via powershell --")
    report(*run_ps(
        client,
        "Get-CimInstance Win32_OperatingSystem | "
        "Select-Object Caption,Version,BuildNumber | Format-List\n"
        "'--- disks ---'\n"
        "Get-PSDrive -PSProvider FileSystem | "
        "Select-Object Name,@{n='FreeGB';e={[math]::Round($_.Free/1GB,1)}},"
        "@{n='UsedGB';e={[math]::Round($_.Used/1GB,1)}} | Format-Table -AutoSize\n"
        "'--- admin shares ---'\n"
        "Get-SmbShare | Select-Object Name,Path | Format-Table -AutoSize\n",
        timeout,
    ))


def do_sessions(client, cfg, timeout):
    out("-- query session --")
    report(*run(client, "query session", timeout))
    out("-- query user --")
    report(*run(client, "query user", timeout))
    out("-- vnc-ish processes --")
    report(*run_ps(
        client,
        "Get-Process | Where-Object { $_.ProcessName -match 'vnc|nx|teamviewer|anydesk' } | "
        "Select-Object Id,ProcessName | Format-Table -AutoSize\n",
        timeout,
    ))


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv):
    parser = argparse.ArgumentParser(
        prog="ssh_ctl.py",
        description="Drive a Windows host over SSH (password auth) from a non-interactive agent.",
        add_help=True,
    )
    parser.add_argument("action", nargs="?", help="exec|ps|psf|put|get|cat|ls|rm|rmdir|mkdir|probe|info|sessions")
    parser.add_argument("args", nargs="*", help="action arguments")
    parser.add_argument("--config", help="explicit config file path")
    parser.add_argument("--host")
    parser.add_argument("--port")
    parser.add_argument("--user")
    parser.add_argument("--password")
    parser.add_argument("--json", action="store_true", help="emit one JSON object on stdout")
    parser.add_argument("--quiet", action="store_true", help="suppress the stdout report")
    parser.add_argument("--timeout", type=int, help="command timeout in seconds (default 300)")

    try:
        args = parser.parse_args(argv[1:])
    except SystemExit:
        raise

    CTX["json"] = bool(args.json)
    CTX["quiet"] = bool(args.quiet)

    if not args.action:
        out(__doc__)
        return finish(2)

    cfg, err = build_config(args)
    if err:
        out("[FAIL] %s" % err)
        return finish(3)

    timeout = int(args.timeout or cfg.get("timeout") or 300)
    action = args.action.lower()
    a = list(args.args)

    out("== ssh_ctl: %s ==" % action)
    out("target : %s:%s as %s%s" % (
        cfg["host"], cfg.get("port", 22), cfg["username"],
        ("  (config: %s)" % cfg["_config_file"]) if cfg.get("_config_file") else "",
    ))
    if cfg.get("hostname"):
        out("machine: %s" % cfg["hostname"])
    out("")

    CTX["result"].update({
        "action": action,
        "host": cfg["host"],
        "port": int(cfg.get("port", 22)),
        "username": cfg["username"],
        "config_file": cfg.get("_config_file"),
    })

    try:
        client = connect(cfg)
    except Exception as ex:  # noqa: BLE001
        out("[FAIL] connect: %r" % ex)
        out("")
        out("Checklist: host up? port %s reachable? credentials correct? "
            "sshd running on the host?" % cfg.get("port", 22))
        CTX["result"]["error"] = "connect: %r" % ex
        return finish(4, cfg)

    code = 0
    try:
        if action in ("exec", "run", "cmd"):
            if not a:
                out('usage: exec "<command>"')
                code = 2
            else:
                report(*run(client, " ".join(a), timeout))

        elif action in ("ps", "pwsh", "powershell"):
            if not a:
                out('usage: ps "<powershell script>"')
                code = 2
            else:
                report(*run_ps(client, " ".join(a), timeout))

        elif action in ("psf", "psfile", "script"):
            if not a:
                out("usage: psf <local_powershell_file>")
                code = 2
            else:
                local = a[0]
                with open(local, "r", encoding="utf-8-sig") as fh:
                    script = fh.read()
                out("script : %s (%d bytes)" % (local, len(script)))
                out("")
                report(*run_ps(client, script, timeout))

        elif action == "put":
            if len(a) < 2:
                out("usage: put <local_path> <remote_path>")
                code = 2
            else:
                local, remote = a[0], parse_remote_path(a[1])
                sf = sftp(client)
                try:
                    sf.put(local, remote)
                    st = sf.stat(remote)
                    out("[PASS] uploaded %s -> %s (%s bytes)" % (local, remote, st.st_size))
                    CTX["result"]["bytes"] = st.st_size
                finally:
                    sf.close()

        elif action == "get":
            if len(a) < 2:
                out("usage: get <remote_path> <local_path>")
                code = 2
            else:
                remote, local = parse_remote_path(a[0]), a[1]
                sf = sftp(client)
                try:
                    sf.get(remote, local)
                    size = os.path.getsize(local)
                    out("[PASS] downloaded %s -> %s (%s bytes)" % (remote, local, size))
                    CTX["result"]["bytes"] = size
                finally:
                    sf.close()

        elif action == "cat":
            if not a:
                out("usage: cat <remote_path>")
                code = 2
            else:
                do_cat(client, parse_remote_path(a[0]), timeout)

        elif action in ("ls", "dir"):
            target = parse_remote_path(a[0]) if a else "C:/"
            sf = sftp(client)
            try:
                out("path: %s" % target)
                rows = []
                for attr in sorted(sf.listdir_attr(target), key=lambda x: x.filename.lower()):
                    kind = "d" if is_dir(attr) else "-"
                    out("%s %12s  %s" % (kind, attr.st_size, attr.filename))
                    rows.append({"name": attr.filename, "dir": kind == "d", "size": attr.st_size})
                CTX["result"]["entries"] = rows
            finally:
                sf.close()

        elif action in ("rm", "del", "remove"):
            if not a:
                out("usage: rm <remote_path>")
                code = 2
            else:
                remote = parse_remote_path(a[0])
                sf = sftp(client)
                try:
                    sf.remove(remote)
                    out("[PASS] removed %s" % remote)
                    CTX["result"]["removed"] = remote
                finally:
                    sf.close()

        elif action in ("rmdir", "rd"):
            if not a:
                out("usage: rmdir <remote_dir>")
                code = 2
            else:
                remote = parse_remote_path(a[0])
                sf = sftp(client)
                try:
                    sf.rmdir(remote)
                    out("[PASS] removed dir %s" % remote)
                    CTX["result"]["removed"] = remote
                finally:
                    sf.close()

        elif action in ("mkdir", "md"):
            if not a:
                out("usage: mkdir <remote_dir>")
                code = 2
            else:
                remote = parse_remote_path(a[0])
                sf = sftp(client)
                try:
                    sf.mkdir(remote)
                    out("[PASS] created %s" % remote)
                    CTX["result"]["created"] = remote
                finally:
                    sf.close()

        elif action == "probe":
            do_probe(client, cfg, timeout)

        elif action == "info":
            do_info(client, cfg, timeout)

        elif action in ("sessions", "sess"):
            do_sessions(client, cfg, timeout)

        else:
            out("unknown action: %s" % action)
            out("")
            out(__doc__)
            code = 2

    except Exception as ex:  # noqa: BLE001
        out("[FAIL] %r" % ex)
        CTX["result"]["error"] = repr(ex)
        code = 5
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    return finish(code, cfg)


if __name__ == "__main__":
    c = 1
    try:
        c = main(sys.argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001
        out("FATAL: %r" % exc)
        flush_to_disk(log_path({}))
        print("\n".join(buffer))
        c = 1
    sys.exit(c)
