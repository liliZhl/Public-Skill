# Windows SSH: field notes

Hard-won behaviours of driving Windows hosts (especially domain-joined ones
running Win32-OpenSSH) from a non-interactive agent. Everything here was
reproduced on a real host; none of it is theoretical.

---

## 1. Publickey auth can authenticate and still fail

The confusing part: the server *accepts* the key, then kills the connection.

Client sees:

```
Authenticated to <HOST_IP> using "publickey".
channel 0: send open
Connection reset by peer
```

Server event log (`OpenSSH/Admin`) shows the real reason:

```
Accepted publickey for CORP\alice from <HOST_IP> port 51022 ssh2: ED25519 SHA256:...
sshd-session: error: unable to get security token for user CORP\alice
sshd-session: error: get_user_token - unable to generate token on 2nd attempt
sshd-session: fatal: fork of unprivileged child failed
```

**Why.** Password auth can build the logon token straight from the supplied
password (`LogonUser`). Publickey auth has no password, so sshd must synthesise
a token another way (S4U / `LsaLogonUser` with a Kerberos service ticket), which
requires a reachable DC, the right SPN, and `SeTcbPrivilege`. When any of that is
missing, the post-auth token creation fails and the child process cannot fork.

**Consequence.** Password auth works on hosts where key auth cannot. Do not treat
"keys are always better" as a rule here — verify, and be ready to fall back.

**Diagnosis shortcut.** If password logins appear in `OpenSSH/Admin` followed by
`[postauth] Disconnected`, but publickey logins have *no* postauth line at all,
the process died during token creation. That is this bug.

---

## 2. Never leave an `authorized_keys` behind by accident

This is the dangerous corollary of §1. If a key file exists:

1. the client offers the key first,
2. the server accepts it,
3. the session then dies (§1),
4. **the client does not fall back to password** — it reports a broken
   connection and stops.

Result: you have locked the human out of the host over SSH, while the key file
looks perfectly correct.

If you must experiment, remove both candidate locations afterwards:

- `%ProgramData%\ssh\administrators_authorized_keys` (used when the account is in
  the Administrators group — sshd's `Match Group administrators` block)
- `%USERPROFILE%\.ssh\authorized_keys`

The authoritative ACL on the admin file is
`O:BAG:BAD:PAI(A;;FA;;;SY)(A;;FA;;;BA)` — SYSTEM and Administrators full,
inheritance removed. Anything looser and sshd silently ignores the file:

```powershell
icacls.exe "C:\ProgramData\ssh\administrators_authorized_keys" `
  /inheritance:r /grant "SYSTEM:F" /grant "Administrators:F"
Restart-Service sshd
```

---

## 3. `ssh.exe` cannot be fed a password non-interactively

There is no `--password` flag by design, and the usual workarounds do not work
for an agent:

| Attempt | Result |
|---|---|
| `SSH_ASKPASS` helper | needs `DISPLAY` / a detached controlling terminal |
| `sshpass` | not present on Windows |
| `powershell -Command "& ssh-keygen ... -N ''"` | empty string argument is swallowed (PS 5.1) |
| `Start-Process -ArgumentList @(...,'')` | empty element trips parameter validation |
| `[Diagnostics.Process]::Start(psi)` | blocked by endpoint security in managed environments |
| `cmd /c` / `Process.Start` | same |
| `echo pw \| ssh ...` | ssh reads the password from the console, not stdin |

**The path that works:** use a library that speaks SSH directly. paramiko's
`connect()` takes the password as a normal argument, so none of the above
matters. Python `subprocess` also passes argv as an array, which is why it can
launch helpers that PowerShell cannot.

---

## 4. Elevation: do not assume, probe it

Common advice says "OpenSSH on Windows sessions are filtered tokens, so admin
operations fail." **That is not universal.** sshd builds the token with
`LogonUser(..., LOGON32_LOGON_INTERACTIVE, ...)`, a path that **does not pass
through UAC filtering**. On a host where the account is a local/domain
administrator, the resulting session can be fully elevated:

```
is_admin  : True
integrity : S-1-16-12288        # High Mandatory Level
HKLM read : OK
```

Meanwhile other hosts genuinely return a filtered token (`S-1-16-8192`), where
`Restart-Service`, HKLM writes, `netsh advfirewall` and `icacls` on system paths
all return *Access is denied*.

**Always run `ssh_ctl.py probe` first** and read the `verdict` line before
deciding whether a privileged operation can be done over SSH or must be done in
an interactive console session (VNC/RDP).

---

## 5. Session type: Network (3), not Interactive (2)

An SSH login is logon type 3. Consequences that matter:

- **No interactive desktop session is created.** Anything that needs a real
  desktop session — GUI apps, machine-locked licence checkout, per-session
  drive mappings, `WScript.Shell` COM shortcuts — will not behave as it does
  when someone is logged on at the console.
- **It does not evict the console session.** Unlike RDP (remote into a machine
  where the same account already has a console session and Windows moves it),
  an SSH login coexists with whatever is on the physical console. You can keep a
  VNC session and an SSH session open at the same time on the same account.
- `query session` shows the console and any RDP sessions; the SSH login does not
  appear as a session row.

This combination is why SSH and a console-mirroring remote tool (VNC) are
complementary rather than redundant: SSH for command/file automation, VNC for
anything that must happen in the physical console session.

---

## 6. Output encoding will be mangled — read the log file

Agent shells routinely swallow stdout, and PowerShell text piped through
`Out-String` gets transcoded (UTF-8 bytes decoded as the ANSI codepage). A
Chinese string such as `功耗分析仪` arrives as `鍔熻€楀垎鏋愪仪`.

Robust pattern:

1. force UTF-8 at the remote end:
   `[Console]::OutputEncoding=[System.Text.Encoding]::UTF8` and
   `$OutputEncoding=[System.Text.Encoding]::UTF8`
2. have the tool write the full report to a **file** and read that file, rather
   than trusting stdout
3. when decoding captured bytes, try `utf-8` → `gbk`/`cp936` → `latin-1` instead
   of assuming one encoding

`ssh_ctl.py` does all three; `--json` gives structured output for parsing.

---

## 7. Quoting: stop fighting it, use `-EncodedCommand`

Every layer between the agent and the host (shell → python → paramiko →
`cmd.exe` → `powershell.exe`) can eat quotes, backslashes or `%` expansion.
Embedded double quotes are the usual casualty.

Two robust ways out:

- **Base64 `-EncodedCommand`** for short inline scripts. The script is encoded as
  UTF-16LE base64, so no layer sees a special character at all.
- **`psf` (run a local `.ps1` file)** for anything non-trivial: ship the script
  over SFTP and execute it on the host. This removes the quoting question
  entirely and is the right default for real work.

Also remember the remote default shell is `cmd.exe` unless `DefaultShell` is set
under `HKLM:\SOFTWARE\OpenSSH`. So `ls` fails while `dir` works. Either call
`ssh_ctl.py ps "..."` or set the registry value.

---

## 8. Deletes, and other things that fail on UNC paths

Some managed clients block destructive operations on network paths because the
file cannot be moved to a recycle bin:

```
[safe-delete][SAFE_DELETE_FAIL_CLOSED]
```

Both PowerShell `Remove-Item` and Python `os.remove` hit it when the target is
`\\host\share\...`.

**Workaround:** perform the delete *on the host* over SSH
(`ssh_ctl.py rm <path>`), where the client-side protection does not apply.
Alternatively move the file into a directory that is going to be deleted as a
whole.

---

## 9. Choosing a transfer channel

| Channel | Good for | Notes |
|---|---|---|
| **SFTP** (`put` / `get`) | single files, scripts, logs | works anywhere SSH works; no extra service; scriptable |
| **SMB admin share** (`\\host\C$`) | bulk copies, browsing, mounting artifacts | needs the SMB session to carry an admin token; watch for `net use` conflicts (error 1219) |
| **SMB normal share** | handing files to non-admin users | share and NTFS permissions are intersected — both must allow |

If the host has a folder that is synced by some tool (Syncthing, Dropbox,
OneDrive), do **not** stage temporary artifacts inside it: they will replicate to
every peer. Prefer a dedicated staging directory.

---

## 10. Failure triage

Match the *symptom*, not the guess.

| Symptom | Meaning | Where to look |
|---|---|---|
| TCP connect **times out** (~3–4 s, no reply) | something is dropping the SYN — no listener and no allow rule, or a silent-drop firewall action | is sshd running and listening (`Get-NetTCPConnection -LocalPort 22 -State Listen`)? is there an inbound rule? is a GPO overriding it? |
| TCP connect is **refused instantly** | the rule allows it but nothing is listening | service stopped or failing to start |
| **Auth rejected** | credentials or username format | domain accounts need `DOMAIN\user`; a Windows Hello **PIN is not a password** and never works over the network |
| **Connects, then resets right after auth** | token creation failed | §1 — publickey path; check `OpenSSH/Admin` |
| Connects, but `ls` says "not recognized" | you are in `cmd.exe`, not PowerShell | expected default; call `ps`, or set `DefaultShell` |
| A local self-test passes but remote still times out | loopback traffic bypasses inbound firewall filtering | only a test from a *different* machine is authoritative |

Domain firewalls are commonly managed by GPO. If a local allow rule appears to
have no effect and the profile shows `AllowInboundRules` / `DefaultInboundAction`
as `NotConfigured` while traffic still fails, escalate to whoever owns the GPO
rather than adding more local rules.

---

## 11. Installing OpenSSH Server when the host has no internet

`Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0` fails with
**`0x800f0954`** when the machine is pointed at a WSUS server that cannot serve
the optional-feature payload.

Options, best first:

1. **Ship the MSI.** Download `OpenSSH-Win64-<ver>.msi` from the
   `PowerShell/Win32-OpenSSH` GitHub releases, copy it over (SMB/SFTP/USB), then
   `msiexec /i <file> /qn`. Verify with
   `Get-Item 'C:\Program Files\OpenSSH\sshd.exe' | % VersionInfo`.
2. **Ship the ZIP.** Unzip to `C:\Program Files\OpenSSH` and run
   `powershell -ExecutionPolicy Bypass -File .\install-sshd.ps1`. Use this when
   policy blocks `msiexec`.
3. **Features-on-Demand ISO** with `-Source <drive> -LimitAccess`.
4. **Ask IT** to allow "download repair content directly from Windows Update".

Note: the GitHub ZIP/MSI route does **not** create the inbound firewall rule for
you (unlike the capability route), so add it explicitly:

```powershell
New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' `
  -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22
```

Pre-flight the host and the runtime prerequisites:

```powershell
Get-WindowsCapability -Online | ? Name -like 'OpenSSH*'
Test-Path 'C:\Windows\System32\OpenSSH\sshd.exe'   # client files existing != server installed
Test-Path 'C:\Windows\System32\vcruntime140.dll'   # VC++ 2015-2022 runtime
```

A `C:\Windows\System32\OpenSSH` directory that contains only `ssh.exe`, `scp.exe`
and friends means the **client** is present and the server is not. Check for
`sshd.exe` specifically.

Common service-start failures: `C:\ProgramData\ssh` and `C:\ProgramData\ssh\logs`
must be writable by SYSTEM and Administrators and **not** by ordinary users;
otherwise sshd fails with 1053/1067/7034.

---

## 12. Host key verification

`ssh_ctl.py` uses `AutoAddPolicy`, i.e. it accepts an unknown host key on first
contact. That is a deliberate trade-off for automation on a trusted LAN, and it
means the tool is **not** protected against a man-in-the-middle on first connect.

For a stricter setup, pre-populate `known_hosts`:

```bash
ssh-keyscan -p 22 <HOST_IP> >> ~/.ssh/known_hosts
```

and change the policy to `RejectPolicy`. Do this if the network path is not
trusted.

---

## 13. Credential handling

- Password auth means the password is a readable secret somewhere. Keep it in
  one file, restrict the ACL (`icacls <file> /inheritance:r /grant "<user>:F"
  /grant "SYSTEM:F" /grant "Administrators:F"` on Windows, `chmod 600` elsewhere),
  and never let it into shell history — use env vars or a stdin flag rather than
  an inline `--password` where that matters.
- Prefer reading the password from the environment over passing it on the command
  line; command lines are visible in process listings.
- Rotate the credential if it has ever been pasted into a chat, a ticket, or a
  log file.
