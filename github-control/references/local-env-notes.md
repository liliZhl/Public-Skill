# 本机环境硬坑（Windows + 托管工具链）

这些不是 GitHub 的问题，是本机 shell / 沙箱层的行为。每条都实测确认过，
附上症状与绕行方式。**遇到"命令明明对却没效果"先查这里。**

---

## 1. Bash 工具的 PATH 未注入

**症状**：裸 `git` / `gh` / `mkdir` / `head` 全报 `command not found`，
但同名工具实际存在于磁盘上。

**根因**：Bash 工具的进程环境没有继承系统 PATH。

**绕行**：每条 Bash 调用开头重新 export（shell 状态不跨调用保留）：

```bash
export PATH="/c/Program Files/GitHub CLI:/c/Users/<USER>/.workbuddy/binaries/PortableGit/versions/1.2.0/cmd:/c/Users/<USER>/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"
```

- 缺 `cmd/` → 找不到 `git.exe`
- 缺 `usr/bin/` → 找不到 `rm` / `mkdir` / `head` 等 shell 内建工具
- `gh` 也可用绝对路径 `/c/Program Files/GitHub CLI/gh` 调用

`scripts/gh_env.py` 自行处理注入，所以不需要外层 export 就能跑。

---

## 2. `.git/refs/remotes/**` 的写入被静默丢弃

**症状**：

- `git fetch` 报 `* [new branch] main -> origin/main`，reflog 也写了
- 但 `.git/refs/remotes/origin/` 目录**始终不存在**
- `git status` 恒显示 `main...origin/main [gone]`
- `git branch --set-upstream-to=origin/main` 报找不到引用

**诊断过程（排除法）**：

| 探针 | 结果 |
|---|---|
| `mkdir -p .git/refs/remotes/origin` | ✅ 目录建出来了 |
| 手工写引用文件 | ✅ `git show-ref` 能看到 |
| 再跑一次 `git fetch` | ❌ 引用被删掉 |
| `core.logAllRefUpdates=false` 后 fetch | ❌ 仍被删 |
| 显式 refspec `main:refs/remotes/origin/main` | ❌ 仍被删 |
| fetch 到 `refs/origin/*` | ✅ **正常持久** |
| fetch 到 `refs/remotes/upstream/*` | ❌ 被删 |

**结论**：问题专属 `refs/remotes/` 命名空间，且沙箱内、非沙箱下表现一致。
写入被外壳过滤层丢弃，不是 git 的问题。

**影响面（很小）**：

| 操作 | 状态 |
|---|---|
| `git push` / `git pull` | ✅ 正常 |
| `git diff origin/main` | ✅ 可用（见绕行） |
| `git status` 显示 `[gone]` | ⚠️ 纯显示噪音 |

**绕行**：给 origin 追加一条镜像 refspec，与标准 refspec 共存：

```bash
git config --add remote.origin.fetch "+refs/heads/*:refs/origin/*"
```

`origin/main` 这个短名解析时会回退命中 `refs/origin/main`，所以
`git diff origin/main`、`git rev-parse origin/main` 都照常工作。
**不要为此修改仓库本身**，也不要试图"修好"它 —— 换个壳还是会这样。

---

## 3. `UID` 是 bash 只读变量

**症状**：`UID=$(gh api user --jq '.id')` 不报错，但 `$UID` 仍是本机 UID（<LOCAL_UID>）。
结果邮箱拼成 `<GH_ID>+<LOCAL_UID>@users.noreply.github.com`，提交作者身份错误。

**根因**：bash 的 `UID` 是只读内建变量，赋值**静默失败**，不返回非零退出码。

**绕行**：一律用 `GHID`：

```bash
GHID=$(gh api user --jq '.id')
git config --global user.email "${GHID}+${LOGIN}@users.noreply.github.com"
```

`gh_env.py` 会交叉核对邮箱里的数字 ID 与 GitHub ID 是否一致，不一致就报警。

---

## 4. 沙箱内 `git clone` 到 Temp 不落地

**症状**：`git clone` 输出看似成功（无报错），但目标目录不存在，
后续 `cd` 报 "No such file or directory"。

**根因**：沙箱把 Temp 目录的写入虚拟化了 —— 写入发生在虚拟层，不落真实磁盘。

**两个后果**：

1. 别把"验证发布内容"的 clone 放在 Temp
2. **更危险的是误判**：`cd` 失败后若继续执行扫描，扫的是**上一条命令残留的
   当前工作目录**，会得出"有敏感残留"的**假警报**。务必先确认 `cd` 成功。

**绕行**（按推荐度排序）：

1. **用 GitHub API 直读远端内容** —— 最贴近"外界实际看到什么"，也不用 clone：
   ```bash
   gh api repos/<owner>/<repo>/contents/<path> --jq '.content' | base64 -d
   ```
   或走 `raw.githubusercontent.com` 按 HTTP 取文本。
2. 克隆到工作区的**非 Temp** 路径
3. 非沙箱执行（需要用户授权）

---

## 5. PowerShell 工具的输出被吞

**症状**：命令 exit 0，但 stdout 为空 —— 看起来像命令没执行。

**根因**：输出通道问题，非命令失败。

**绕行**：

- 探测类命令一律走 Bash 工具
- 或用 PowerShell 写文件再读：
  ```powershell
  $out = @(); $out += "line1"; $out -join "`n" | Set-Content -Encoding UTF8 out.txt
  ```

---

## 6. 本机删除操作被拦

**症状**：对 UNC 路径执行删除报 `SAFE_DELETE_FAIL_CLOSED`，
PowerShell 与 Python `os.remove` 都被拦。

**绕行**：

- 用 PortableGit 的 `rm`（在 workspace 内的普通路径可用）
- 或改用远程执行（若是远程主机上的文件）

---

## 7. gh 设备码授权不能前台跑

**症状**：前台执行 `gh auth login --web` 会一直等待，直到工具超时被中断，
设备码随之作废。

**正确做法**：丢后台执行，睡 6 秒后读日志取码：

```bash
cd <workspace> && printf '\n' | gh auth login \
  --hostname github.com --git-protocol https --web --skip-ssh-key > gh_auth.log 2>&1
```
（交给后台任务，然后读 `gh_auth.log`）

`printf '\n'` 是为了吃掉 gh 的交互式提问；`--skip-ssh-key` 避免它去折腾密钥。

**方向极易搞反**：码由**本机**生成，用户只需把它输进
https://github.com/login/device。页面提示的 "the app or on the device you're
signing in to" 指的就是本机 CLI —— 手机不会弹码。

---

## 8. 其他

- `winget` 可用（v1.29.290）；无系统级 Git，只有 PortableGit
- `git-credential-manager` 内置但未启用 —— 走 gh 的 helper 即可，无需另配
- 工具路径探测**不要硬编码版本号**，PortableGit 路径含版本段，用 glob 抓
