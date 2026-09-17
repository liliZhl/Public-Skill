# eda-host-control

Let an AI agent drive a **Windows host over SSH** — run commands and PowerShell,
upload and download files, read directories and logs, inspect console sessions.

Built for the case where the agent has no TTY and therefore cannot answer an
`ssh` password prompt. It ships with its own setup wizard, so it can be pointed
at any Windows SSH host, not just the one it was originally written for.

## Why not just use `ssh`?

Because on domain-joined Windows hosts, and from an agent, the obvious approaches
break:

- **Public keys can authenticate and still fail.** sshd accepts the key, then
  dies building the session token:
  `unable to get security token for user CORP\alice` →
  `fatal: fork of unprivileged child failed`. Password auth takes the `LogonUser`
  path and works. See `references/windows-ssh-notes.md` §1.
- **`ssh.exe` cannot be fed a password non-interactively.** No `--password`, no
  usable `SSH_ASKPASS`, `sshpass` does not exist on Windows, and PowerShell 5.1
  cannot pass a literal empty argument to a native binary (which is also what
  breaks scripted `ssh-keygen`).
- **Agent shells mangle output.** Non-ASCII text piped through PowerShell comes
  back as mojibake, and stdout is frequently swallowed entirely. This tool always
  writes a full report to a file, and offers `--json`.

## Requirements

- Python 3.8+ on the machine running the agent
- `paramiko` (`ssh_setup.py` installs it if missing)
- Network access to the target's TCP 22, with `sshd` running and an inbound
  firewall rule allowing it

## Contents

```
eda-host-control/
├── SKILL.md                          instructions the agent reads
├── README.md                         this file
├── scripts/
│   ├── ssh_ctl.py                    the tool
│   └── ssh_setup.py                  setup / verification wizard
└── references/
    └── windows-ssh-notes.md          field notes: gotchas, triage, offline sshd install
```

## Quick start

```bash
# 1. configure (prompts for whatever you omit)
python scripts/ssh_setup.py --host <HOST_IP> --user 'CORP\alice'

# 2. use it — no arguments needed afterwards
python scripts/ssh_ctl.py probe
python scripts/ssh_ctl.py ps "Get-Service sshd | Select Name,Status"
python scripts/ssh_ctl.py info
```

`ssh_setup.py` verifies three things in order and tells you which one failed:
the local client stack, the connection, and what the remote session is actually
allowed to do. That third one matters — whether an SSH session is elevated
varies per host, and it decides whether `Restart-Service`, HKLM writes and
`netsh` will work.

### Everyday commands

```bash
ssh_ctl.py exec "<cmd>"                # raw shell (usually cmd.exe)
ssh_ctl.py ps   "<powershell>"         # inline PowerShell
ssh_ctl.py psf  ./task.ps1             # run a LOCAL .ps1 ON the host  <- prefer this
ssh_ctl.py put  ./a.exe D:/app/a.exe
ssh_ctl.py get  D:/logs/a.log ./a.log
ssh_ctl.py cat  D:/app/config.ini
ssh_ctl.py ls   D:/app
ssh_ctl.py rm   D:/app/junk.txt
ssh_ctl.py mkdir D:/app/new
ssh_ctl.py probe                       # capabilities of the session
ssh_ctl.py info                        # host, OS, disks, shares
ssh_ctl.py sessions                    # console/RDP state
```

Global flags: `--json`, `--quiet`, `--timeout SEC`, `--config PATH`,
`--host/--port/--user/--password`.

### Where results go

Every run writes its complete report to a log file — default
`<tempdir>/ssh_ctl_last.txt`. **Read that file rather than trusting stdout**, and
use `--json` when you need to parse it. This convention exists because agent
shells routinely return nothing at all.

## Installing into an agent

The skill is a plain folder with a `SKILL.md`, which is the open Agent Skills
format. Copy the folder into whichever location your tool scans:

| Tool | User-level | Project-level |
|---|---|---|
| **WorkBuddy** | `~/.workbuddy/skills/` | `.workbuddy/skills/` |
| **Claude Code** | `~/.claude/skills/` | `.claude/skills/` |
| **Cursor** | `~/.cursor/skills/` or `~/.agents/skills/` | `.cursor/skills/` or `.agents/skills/` |
| **Codex** | `~/.codex/skills/` | `.codex/skills/` |
| **Any Agent Skills host** | `~/.agents/skills/` | `.agents/skills/` |

Example:

```bash
cp -r eda-host-control ~/.claude/skills/
# or, to keep one copy and share it:
ln -s ~/.cursor/skills/eda-host-control ~/.claude/skills/eda-host-control
```

Cursor additionally reads `.claude/skills/` and `.codex/skills/`, so a single
copy under one of those can serve several tools.

Skills are discovered at session start — restart the agent, or let it rescan,
before expecting the new skill to be picked up. Invoke explicitly with
`/eda-host-control` if auto-discovery does not trigger.

## Configuration

Settings resolve with later sources winning, key by key:

1. built-in defaults (`port` 22)
2. first readable file from `$SSH_CTL_CONFIG`,
   `~/.workbuddy/secrets/eda-host.json`, `~/.config/ssh-ctl/config.json`,
   `~/.ssh-ctl.json`, `./ssh-ctl.json`
3. environment: `SSH_CTL_HOST` / `_PORT` / `_USER` / `_PASSWORD` / `_LOG`
   (legacy `EDA_SSH_*` names are also honoured)
4. flags: `--config`, `--host`, `--port`, `--user`, `--password`

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

Aliases accepted for reading: `ssh_user`, `user`, `pass`.

## Security notes

- **Credentials are a readable secret.** Keep exactly one config file, restrict
  it to its owner (`icacls <file> /inheritance:r /grant "<user>:F" /grant
  "SYSTEM:F"` on Windows, `chmod 600` elsewhere), and prefer environment
  variables or `--password-stdin` over an inline `--password`, which lands in
  shell history and process listings. `ssh_setup.py` tightens the ACL on Windows
  automatically.
- **Do not store the config in a git repository.**
- **Host keys are auto-accepted** (`AutoAddPolicy`), which is a deliberate
  trade-off for a trusted LAN and gives no protection against a first-connect
  MITM. For stricter environments, pre-populate `known_hosts` with
  `ssh-keyscan` and switch the policy to `RejectPolicy`.
- **Never leave a public key on the host.** Because of the token bug above, the
  client will then prefer the key, the session will die, and it will *not* fall
  back to password — locking everyone out over SSH. See
  `references/windows-ssh-notes.md` §1–2.
- Rotate any credential that has been pasted into a chat, ticket or log.

## Known limits

- **SSH is logon type 3** — it creates no desktop session, so GUI applications
  and anything bound to an interactive session (machine-locked licence checkout,
  for example) will not behave as they do at the physical console. Use a
  console-mirroring tool such as VNC for those. The two coexist; SSH does not
  evict the console session.
- **Elevation varies by host.** Run `probe` before assuming admin rights.
- **Deletes on UNC paths can be blocked client-side**
  (`SAFE_DELETE_FAIL_CLOSED`). Delete via `ssh_ctl.py rm` so it runs on the host.
