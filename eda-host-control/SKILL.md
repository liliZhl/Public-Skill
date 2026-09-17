---
name: eda-host-control
description: Drive a Windows host over SSH from a non-interactive agent — run commands and PowerShell, upload/download files, read directories and logs, inspect sessions. Use when asked to "run something on the remote/EDA workstation", "copy this over to the host", "check the host's status", "deploy a tool there", or when a user complains about having to copy files to a machine by hand. Uses password auth via paramiko because publickey auth fails at the token stage on domain-joined Windows hosts. Ships with a setup/verification wizard so it can be pointed at any Windows SSH host, not just the one it was built for.
agent_created: true
---

# Windows host control over SSH

Give an agent real hands on a Windows machine: execute commands, run PowerShell,
transfer files, read state. Designed for hosts where the agent has no TTY and
therefore cannot answer an interactive password prompt.

Two properties matter more than the feature list:

- **Password auth is the supported path.** On domain-joined Windows hosts, public
  key auth can authenticate and *still* die while creating the session token. See
  `references/windows-ssh-notes.md` §1 before trying keys.
- **Read the log file, not stdout.** Agent shells swallow or transcode output.
  Every invocation writes its full report to a file; read that.

## Workflow

### 0. Confirm it is configured

```bash
python scripts/ssh_ctl.py info --quiet
# then read the log file it printed, default <tempdir>/ssh_ctl_last.txt
```

If that fails with "missing connection setting(s)", configure it — see
"First-time setup" below. Do not proceed to guess at host settings.

### 1. Probe before automating

```bash
python scripts/ssh_ctl.py probe
```

Read the `verdict` line. It tells you whether the session is **ELEVATED**
(`Restart-Service`, HKLM writes, `netsh`, `icacls` on system paths all work) or
**FILTERED** (they will all be denied; those operations must happen in an
interactive console session instead). Do not assume — it varies per host.

### 2. Do the work

```bash
python scripts/ssh_ctl.py ps   "Get-Service sshd | Select Name,Status"   # inline PowerShell
python scripts/ssh_ctl.py psf  /local/path/task.ps1                      # run a local .ps1 ON the host
python scripts/ssh_ctl.py exec "hostname & whoami"                        # raw shell (cmd.exe)
python scripts/ssh_ctl.py put  ./build/tool.exe D:/app/tool/tool.exe      # upload
python scripts/ssh_ctl.py get  D:/logs/app.log ./app.log                  # download
python scripts/ssh_ctl.py cat  D:/app/config.ini                          # read a text file
python scripts/ssh_ctl.py ls   D:/app                                     # list a directory
python scripts/ssh_ctl.py info                                            # host, OS, disks, shares
python scripts/ssh_ctl.py sessions                                        # console/RDP state, remote-access processes
```

Add `--json` for machine-readable output, `--quiet` to suppress the stdout copy,
`--timeout SEC` to change the command timeout (default 300).

### 3. Prefer `psf` for anything non-trivial

Quoting crosses shell → python → paramiko → `cmd.exe` → `powershell.exe`. Write
the logic to a local `.ps1`, ship it with `psf`, and none of those layers can
corrupt it. Use inline `ps` only for one-liners.

## First-time setup (any Windows host)

`scripts/ssh_setup.py` checks the local stack, installs paramiko if missing,
tests the connection, probes capabilities, and optionally saves a config file.

```bash
# interactive: prompts for anything omitted
python scripts/ssh_setup.py --host <HOST_IP> --user 'CORP\alice'

# non-interactive (keeps the password out of shell history)
python scripts/ssh_setup.py --host <HOST_IP> --user 'CORP\alice' \
    --password-stdin --write ~/.config/ssh-ctl/config.json

# re-verify the existing config without changing anything
python scripts/ssh_setup.py --check
```

Exit codes: `0` ready, `1` local stack problem, `2` cannot connect, `3` connected
but the session cannot do what you need.

## Configuration

Resolution order, later sources winning key by key:

1. built-in defaults (`port` 22)
2. the first readable file from `$SSH_CTL_CONFIG`,
   `~/.workbuddy/secrets/eda-host.json`, `~/.config/ssh-ctl/config.json`,
   `~/.ssh-ctl.json`, `./ssh-ctl.json`
3. environment: `SSH_CTL_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_LOG`
   (legacy aliases `EDA_SSH_*` are also honoured)
4. flags: `--config`, `--host`, `--port`, `--user`, `--password`

Config file shape:

```json
{
  "host": "<HOST_IP>",
  "port": 22,
  "username": "CORP\\alice",
  "password": "...",
  "hostname": "PC-01",
  "log": "/tmp/ssh_ctl_last.txt"
}
```

Keys `ssh_user` / `user` / `pass` are accepted as aliases. Keep the file readable
only by its owner; `ssh_setup.py` tightens the ACL on Windows automatically.

## Where to read results

The tool writes the full report to its log file (default
`<tempdir>/ssh_ctl_last.txt`, override with `log` in the config or `SSH_CTL_LOG`).
**Read that file** — agent shells frequently return empty stdout, and non-ASCII
text piped through PowerShell gets transcoded into mojibake.

With `--json` the structured result goes to stdout **and** to a sibling file
`<log basename>.json`, reported in the output as `json_file`. If stdout looks
mangled, read `json_file` instead — it is always written as clean UTF-8.

## Rules that prevent damage

1. **Never place a public key on the host** to enable passwordless login. The
   client will then prefer the key, the session will die at the token stage, and
   it will **not** fall back to password — locking everyone out over SSH. If you
   experiment with keys, remove both `%ProgramData%\ssh\administrators_authorized_keys`
   and `%USERPROFILE%\.ssh\authorized_keys` afterwards.
2. **Do not assume privilege.** Run `probe` first. High-risk changes (service
   config, firewall, registry) deserve a confirmation with the user, especially
   on a headless host where a network misconfiguration can only be fixed by
   walking to the machine.
3. **Deletes on UNC paths fail** from the client side
   (`SAFE_DELETE_FAIL_CLOSED`). Delete via `ssh_ctl.py rm` so the operation runs
   on the host.
4. **Do not stage temporary files inside a synchronised folder** (Syncthing,
   OneDrive, Dropbox) — they replicate to every peer.
5. **SSH is logon type 3 and creates no desktop session.** It is the right tool
   for commands and files. Anything bound to an interactive desktop session —
   GUI apps, machine-locked licence checkout — must be done in the physical
   console session via VNC/RDP. The two coexist; SSH does not evict the console.

## Common symptoms

| Symptom | Cause | Fix |
|---|---|---|
| `missing connection setting(s)` | not configured | run `ssh_setup.py` |
| Connect times out (~3–4 s) | no listener and no allow rule, or a silent-drop firewall | check sshd running + inbound rule; local self-test is not authoritative |
| Connect refused instantly | rule allows, nothing listening | start the service |
| Auth rejected | username format, or a Windows Hello **PIN** used as the password | domain accounts need `DOMAIN\user`; a PIN is device-bound and never works over the network |
| Connects, resets right after auth | token creation failed | publickey path — see `references/windows-ssh-notes.md` §1 |
| `ls` is not recognized | the default shell is `cmd.exe` | use `ps`, or set `DefaultShell` under `HKLM:\SOFTWARE\OpenSSH` |
| Admin command returns *Access is denied* | FILTERED token | run `probe`; do it in a console session instead |
| Output is mojibake or empty | shell transcoding / swallowed stdout | read the log file, or use `--json` |

## Reference

`references/windows-ssh-notes.md` — the full set of field notes: why publickey
auth breaks and how to confirm it from the event log, elevation semantics,
session-type consequences, encoding handling, quoting strategies, transfer
channel selection, failure triage, and offline OpenSSH Server installation
(including the WSUS `0x800f0954` case). Load it when something behaves
unexpectedly rather than guessing.

## Concrete instance in this workspace

The host this skill was built against, kept here so the working setup is not lost:

| Item | Value |
|---|---|
| Host | `<HOSTNAME>` / `<HOST_IP>`, domain `<DOMAIN>`, account `<USER>` |
| Config file | `~/.workbuddy/secrets/eda-host.json` (found automatically) |
| Interpreter with paramiko | `<VENV_PYTHON>` |
| Log file | `%TEMP%\ssh_ctl_last.txt` |
| Session verdict | ELEVATED (`S-1-16-12288`) as verified 2026-09-17 |
| Notable constraint | Cannot check out node-locked EDA licences over SSH — that needs the physical console via VNC on port 5900 |
| `D:\SHARE` share | Syncthing-synchronised; never stage temporary files there |

The user's standing instruction: control the host directly rather than asking them
to copy files across by hand.
