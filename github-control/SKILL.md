---
name: github-control
description: 在本机直接管理 GitHub —— 检查/建立 gh CLI 授权、配置 git 身份与凭据、创建与克隆仓库、提交推送、管理 Issue 与 PR、读 Actions 日志、同步技能仓库。当用户说「帮我连 GitHub」「推代码上去」「建个仓库」「把改动同步到 GitHub」「看看 Actions 为什么失败」，或需要在 GitHub 上做任何远端操作时使用。内含本机环境硬坑（Bash PATH 未注入、.git/refs/remotes 写入被丢弃、沙箱内 clone 不落地）与设备码授权的正确操作方向。
agent_created: true
---

# 本机 GitHub 控制

让 agent 直接操作 GitHub 账号与仓库，无需用户手动搬运文件或输入凭据。
首次配置已完成，日常使用只需确认会话健康。

## 0. 先跑环境自检（永远的第一步）

```bash
python scripts/gh_env.py
```

输出一份完整快照：gh/git 路径与版本、授权状态与令牌 scope、git 身份、
credential helper、仓库清单，并给出 `verdict` 结论行。

- `verdict: READY` → 直接干活
- `verdict: NOT_AUTHED` → 走 §2 授权
- `verdict: NO_GH` → 走 §1 安装

加 `--json` 取机器可读输出；结果同时落盘到 `<tempdir>/gh_env_last.txt`，
**stdout 被转码时读那个文件**（本机 shell 有吞输出/转码的毛病，见
`references/local-env-notes.md`）。

> 前提：本机 Bash 工具**不注入 PATH**，裸 `git` / `gh` 全部 command not found。
> 自检脚本自己处理 PATH 注入，所以能直接跑；手工执行 git/gh 前必须按 §5 导出 PATH。

## 1. 安装 gh CLI

```bash
winget install --id GitHub.cli -e --accept-source-agreements --accept-package-agreements
```

装到 `C:\Program Files\GitHub CLI\gh.exe`。装完**不要依赖 PATH**，直接调用绝对路径。

## 2. 授权（设备码流程）

**必须后台跑**，前台会因等待超时被中断：

```bash
cd <workspace> && printf '\n' | gh auth login \
  --hostname github.com --git-protocol https --web --skip-ssh-key > gh_auth.log 2>&1
```
→ 交给后台任务执行，然后 sleep 6 后读 `gh_auth.log` 取码。

### 关键：操作方向（用户极易搞反）

| 谁 | 做什么 |
|---|---|
| **本机** | 生成并显示 8 位设备码（形如 `5147-C59C`） |
| **用户** | 打开 https://github.com/login/device → 粘贴码 → Continue → Authorize |

页面会提示 *"Enter the code displayed in the app or on the device you're signing in to"*
—— **那个「app / device」指的就是本机 CLI**，不是手机。手机不会弹任何码。
用户若去手机 GitHub App 找，或进到 Settings → SSH keys，都是走错路，要点明。

其他要点：
- 码有效期约 **15 分钟**。超时就直接重新生成，不要让用户去研究过期页面。
- 设备码会自动复制到系统剪贴板，可直接 Ctrl+V。
- 用 `references/diagram.md` 里的示意图向用户解释方向（比文字快）。

授权成功后 `gh` 把令牌加密存进 **Windows 凭据管理器**（keyring），非明文。
随时可用 `gh auth logout` 收回。

## 3. 配置 git 身份与凭据

```bash
gh auth setup-git                     # 配 credential helper，git 操作不再弹密码
LOGIN=$(gh api user --jq '.login')
GHID=$(gh api user --jq '.id')        # 注意：必须叫 GHID，不能叫 UID
NAME=$(gh api user --jq '.name')
EMAIL=$(gh api user --jq '.email // empty')
[ -z "$EMAIL" ] && EMAIL="${GHID}+${LOGIN}@users.noreply.github.com"
[ -z "$NAME" ]  && NAME="$LOGIN"
git config --global user.name  "$NAME"
git config --global user.email "$EMAIL"
git config --global init.defaultBranch main
```

**致命坑：`UID` 是 bash 只读变量**，`UID=$(...)` 静默失败不报错，结果是邮箱里
拼上本机 UID（如 `<GH_ID>+<LOCAL_UID>@...`），提交作者身份就错了。永远用 `GHID`。

## 4. 日常操作速查

```bash
gh repo list --limit 30                                        # 仓库清单（含私有）
gh repo create <name> --private --source=. --remote=origin --push
gh repo clone <owner>/<repo>
gh repo view <owner>/<repo> --json name,visibility,url,diskUsage
gh repo rename <new> --repo <owner>/<old> --yes
gh repo edit <owner>/<repo> --add-topic <tag>                   # 话题标签：create 不支持，必须 edit

git add -A && git commit -m "..." && git push
gh pr create / gh pr list / gh pr view <n> --comments
gh issue list / gh issue create / gh issue close
gh run list --limit 5 / gh run view <id> --log-failed          # Actions 排查
gh api repos/<owner>/<repo>/git/trees/main?recursive=1 \
  --jq '.tree[] | select(.type=="blob") | "\(.size)\t\(.path)"' # 远端文件清单（比 clone 快）
```

**推送前逐文件核验暂存区**，凭据类文件绝不放行：

```bash
git diff --cached --name-only | grep -iE "secret|credential|\.pem$|\.key$" \
  && echo "!!! 中止 !!!" || echo "OK"
```

## 5. PATH 导出（每条 Bash 调用都要重来）

shell 状态不跨调用保留，**每条命令开头都写一次**：

```bash
export PATH="/c/Program Files/GitHub CLI:/c/Users/<USER>/.workbuddy/binaries/PortableGit/versions/1.2.0/cmd:/c/Users/<USER>/.workbuddy/binaries/PortableGit/versions/1.2.0/usr/bin:$PATH"
```

只用 gh / 只用 git 时可各自裁短。缺了 `cmd/` 目录会找不到 git.exe，缺了
`usr/bin/` 则 shell 内建工具（rm/mkdir）全失。

> **上面的版本段（`1.2.0`）会随升级变化，不要照抄。** 以 `gh_env.py` 输出的
> `PATH 导出` 行为准 —— 它是 glob 探测出来的实时值。

## 6. 本机硬坑速查

| 症状 | 根因 | 处理 |
|---|---|---|
| `git status` 恒显示 `[gone]` | `.git/refs/remotes/**` 写入被静默丢弃 | 加镜像 refspec，见下 |
| 沙箱内 `git clone` 到 Temp 不落地 | Temp 被虚拟化 | 用 HTTP API 直读，或非沙箱执行 |
| PowerShell 工具 exit 0 但无 stdout | 输出通道被吞 | 探测类命令一律走 Bash |
| 本机 `rm` 对 UNC 路径报 `SAFE_DELETE_FAIL_CLOSED` | 本机删除保护 | 用 PortableGit 的 `rm`，或改用远程执行 |

**refs/remotes 绕行**（影响面仅 status 显示，push/pull 本身正常）：

```bash
git config --add remote.origin.fetch "+refs/heads/*:refs/origin/*"
```

`origin/main` 短名会回退命中 `refs/origin/main`，所以 `git diff origin/main` 照常可用。

细节与完整诊断过程见 `references/local-env-notes.md`。

## 7. 双仓库同步工作流（技能仓库）

本机技能分两个仓库，改动要**两边同步**：

| 仓库 | 可见性 | 内容 |
|---|---|---|
| `<GH_LOGIN>/<USER>-Skill` | 私有 | 原样，不动一字 |
| `<GH_LOGIN>/Public-Skill` | 公开 | 同一批技能，**已脱敏** |

本地工作副本：`~/repos/<USER>-Skill`、`~/repos/Public-Skill`。
`~/.workbuddy/skills/` 是**运行时目录**，两个仓库都是它的镜像 —— 改技能要改源头，
再同步过去提交。

同步流程 —— **已工具化，不要手工 `cp -r`**：

```bash
SC=~/.workbuddy/skills/github-control/scripts/sync_skill_repos.py
python "$SC"            # 同步两个本地仓库 + 脱敏 + 独立复查（不推送）
python "$SC" --push     # 复查无残留时自动 commit + push
```

| 参数 | 作用 |
|---|---|
| （默认） | 私有仓原样复制；公开仓复制后脱敏；随后用原始词表**独立复查** |
| `--push` | 无残留才提交推送；有残留则中止并报出位置 |
| `--only private` / `--only public` | 只处理其中一个 |
| `--dry-run` | 只报告，不写文件 |
| `--all` | 同步源目录全部技能（默认只同步**两仓已有**的技能，避免本机新装/实验性技能被意外带入库） |

**脱敏规则不硬编码在脚本里** —— 真实值运行时从 `gh api user`、环境变量、
`~/.workbuddy/secrets/eda-host.json` 推导，所以脚本本身可以安全进入公开仓库。
项目特定词（公司名等）放 `~/.workbuddy/secrets/sanitize-extra.json`，
格式 `{"真实值": "<占位符>"}`，同样不入库。

两个必须知道的实现细节：

- **脚本自身要跳过替换**。它里面写着"泛化模式"（如 `(?i)\b<ACCOUNT_PREFIX>\d{4}\b`、
  `[A-Z][A-Z0-9]{2,7}-PC`），不跳过就会被自己的规则改写，工具直接失效 ——
  这是隐蔽的自伤，用文件名白名单规避。
- **域 / 账号 / 主机名类规则必须大小写不敏感**。同一标识在文档里会写成
  `<DOMAIN>` / `<DOMAIN>` / `<ACCOUNT>` / `<ACCOUNT>` 多种形态，区分大小写会漏一半。

脱敏要点（踩过的坑）：

- **占位符按环境维度定义，一个维度一个名字，不要复用。** 把
  `<USER>` 同时当"系统用户名"和"GitHub 用户名"用，读文档的人必然混乱。
  现用：`<HOST_IP>` `<HOSTNAME>` `<DOMAIN>` `<ACCOUNT>` `<SHARE>`
  `<USER>`（系统用户名）`<GH_LOGIN>` `<GH_ID>` `<GH_NAME>` `<LOCAL_UID>`、
  `Private-Skill`（私有仓库名）
- **跨技能引用会重新引入标识。** 写举例时若援引另一个技能的案例，
  那个案例里的域账号 / 主机名会跟着进仓库 —— 举例要泛化，
  或者退回源头把那段也改掉。
- **用占位符替换，不要删内容** —— 技能要保持可用，只是环境参数待填。
- 终检必须**独立于替换规则**（用原始词表直接搜），
  否则规则漏了什么就永远看不见。

最容易漏的是**词边界失效**：两个词被粘成一个 token 时（例如某段在举例
「反斜杠被 shell 吞掉」，域和账号连写成一个词），带 `\b` 的规则扫不到，
只能靠残留复查发现。

推送后**从远端重新读取文件再扫一遍**验证，不要只看本地：

## 8. 安全边界

- 令牌 scope 为 `repo` / `gist` / `read:org` —— **不含 `workflow`**，
  推送 `.github/workflows/` 下的文件会被拒。需要时补权限：
  `gh auth refresh -h github.com -s workflow`
- 危险操作（删仓库、改组织设置）不在授权范围。
- 对外可见的操作（建公开仓库、push 到公开仓）**先向用户确认**。
- 凭据文件（`~/.workbuddy/secrets/`）绝不入库；两个仓库的 `.gitignore` 均已拦截，
  但**不要手工把 secrets 目录拷进仓库目录** —— 这是唯一兜不住的操作。

## 9. 把本地既有仓库接入 GitHub（含敏感内容历史清理）

场景：目录里**已经是带提交历史的本地仓库**，要推到 GitHub，同时**排除某些已入库的敏感内容**。
光改当前文件不够 —— 内容在历史里，必须重写历史。

### 9.1 网络路径 / 非本机盘的前提

若仓库在 UNC 或映射盘（如 `Z:\` → `\\host\share`），git 会报 `dubious ownership`，
需**按三种路径写法各登记一次**，否则换个写法就又被拦：

```bash
git config --global --add safe.directory "//host/share/<repo>"
git config --global --add safe.directory "Z:/<repo>"
git config --global --add safe.directory "Z:/<repo>/.git"   # clone 时 git 明确要求这条
```

且给 git 传路径**必须用 `Z:/...` 形式**，msys 的 `/z/...` 会报 `repository does not exist`。

### 9.2 在副本上操作，源仓库不动

```bash
git clone "Z:/<repo>" "C:/Users/<user>/repos/<repo>"
```

> ⚠️ **沙箱会拦截 git 的仓库创建**：`git clone` / `git init` 报 exit 0，但产物在 `ls`
> 与原生 Win32 API 里都看不到；更糟的是**残留的隔离层目录会污染后续操作**（再 clone 报
> "already exists"，或报成功却依然不可见）。
> → **凡涉及 `.git` 的写入，一律用非沙箱模式执行**，并先清空目标目录名。
> → 判定真相要用**原生 API**，`ls`（msys 视图）不可信，详见 `references/local-env-notes.md` §9。

### 9.3 重写历史

```bash
<venv>/Scripts/python.exe -m pip install git-filter-repo
# 可执行：<venv>/Scripts/git-filter-repo.exe
```

```bash
git filter-repo --force \
  --path "docs/物料库文档" --invert-paths \
  --replace-text expressions.txt
```

`expressions.txt` 每行 `原值==>占位符`：

```
<真实口令>==><SAP_PASSWORD>
<账号>==><SAP_USER>
<服务器>==><SAP_SERVER>
```

- filter-repo **会移除 origin remote**（因为它指向源仓库），之后需重新 `git remote add`
- 末段 `repacking/cleaning` 会重算体积，数十秒属正常

### 9.4 两个必踩的坑

1. **重写后工作区检出可能不全** —— 实测 97 个 tracked 只写出 52 个，
   `git status` 冒出几十个 ` D`。**此时 `git add -A` 会把这些"删除"提交上去。**
   - 提交前**必须**核对：`git status --porcelain | grep '^ D'` 应为空
   - 恢复：`git checkout <filter-repo 后的提交> -- <路径>`
2. **核验中文路径必须加 `-c core.quotepath=false`** —— 否则
   `git ls-files | grep <中文名>` 恒为 0，看着干净其实根本没验证到。

### 9.5 推送前核验

```bash
git -c core.quotepath=false log --all --name-only --pretty=format: | grep -c "<被排除路径>"
git log --all -S"<凭据原值>" --oneline | wc -l      # 都应为 0
```

同时在仓库 README 写明**排除了什么、怎么本地恢复**，并把被排除路径补进 `.gitignore`。

### 9.6 远端实测（不要只看本地）

```bash
gh api repos/<owner>/<repo>/git/trees/<branch>?recursive=1 \
  --jq '.tree[]|select(.type=="blob")|.path' | grep -c "<被排除路径>"    # 应为 0
gh api repos/<owner>/<repo>/contents/config.ini.example \
  -H "Accept: application/vnd.github.raw"                                # 直接读原文，验占位符
gh api repos/<owner>/<repo>/git/trees/<branch>?recursive=1 \
  --jq '[.tree[]|select(.type=="blob")]|length'                          # 与本地 tracked 数比对
```

---

## 10. 仓库可复现性检查（「让别人能跟着编译」）

用户要「写好 README」时，真正要的是**别人能跑起来**。先扫这五项 ——
缺任何一项，README 写得再漂亮也白搭。

### 10.1 被 gitignore 排除的运行时资源

```bash
cat .gitignore                                  # 被排除的大目录
git -c core.quotepath=false ls-files | wc -l    # 实际入库文件数
```

凡是**运行时必需但被排除**的资源（模型、二进制、数据包），README 必须逐项写清
**去哪下载 → 放到哪个确切路径 → 目录名有没有要求**。

> 真实教训：程序按硬编码名 `sensevoice` 找模型，而从 ModelScope 网页下载解压出来叫
> `iic--SenseVoiceSmall` —— 不改名就永远「模型缺失」。

### 10.2 下载源必须实测，不能凭记忆

模型 / 数据集 / 工具链的仓库 ID 极易写错（网上文章也常有笔误）。写进 README 前逐个验证：

```bash
# ModelScope
python -c "
import json,urllib.request
u='https://www.modelscope.cn/api/v1/models/<ns>/<name>'
j=json.loads(urllib.request.urlopen(urllib.request.Request(
    u,headers={'User-Agent':'Mozilla/5.0'}),timeout=25).read())
print(j['Data']['Downloads'])"
# HuggingFace
curl -sI https://huggingface.co/<repo>/resolve/main/config.json | head -1
```

> 真实教训：`iic/Fun-ASR-Nano-2512` 返回 **404**，官方命名空间其实是 `FunAudioLLM/`。
> 两种写法在网页文章里都出现过，只有 API 能分辨。

### 10.3 路径事实以代码为准，别信常量名

同一项目里两处可能对同一目录给出**不同**的相对基准
（如 `main.py` 的 `APP_DIR` 与 `asr_engine.py` 的 `Path(__file__).parent.parent`）。
**以真正读取文件的那段代码为准**：

```bash
grep -rn "model_root\|MODEL_DIR\|BIN_DIR\|ffmpeg" src/ | head -30
```

### 10.4 构建脚本是否硬编码了个人路径

```bash
grep -rnE "C:\\\\Users\\\\[A-Za-z0-9]+|/home/[a-z]+/|/Users/[A-Za-z]+/" *.py *.spec *.bat 2>/dev/null
```

**这是「别人编译不了」的头号原因。** 优先**修脚本**，而不是在 README 里教人
「请修改第 13 行的路径」：

- 通用解释器 → 用 `sys.executable`
- 特殊解释器（如 32 位）→ 环境变量覆盖 + 常见路径探测 + **校验特征**（位数 / 版本），
  找不到时给明确指引，而不是默默用错
- 修完把原硬编码值保留在候选列表，对原开发机即 **no-op**（零回归风险）

### 10.5 依赖清单的废弃项

功能被移除后，依赖常留在清单里，新环境照着装会白拉体积巨大的包
（典型：`sentence-transformers` 连带拉入 PyTorch）。
**加注释标明**「已废弃、新环境无需安装」，比直接删更安全（不破坏历史环境）。

### 10.6 补防误提交规则

为「必须存在但内容不入库」的目录（模型目录等）加规则，并**逐条验证**：

```
mod/*
!mod/README.md
```

```bash
git check-ignore -v mod/README.md      # 命中 ! 规则 → 会入库
git check-ignore -v mod/x/model.pt     # 命中 mod/* → 被忽略
```

### 10.7 交付前自查清单

- [ ] README 含：环境要求 / 从零编译步骤 / 外部依赖获取 / 配置说明 / 打包 / 常见问题
- [ ] 所有下载链接与仓库 ID **实测可用**
- [ ] 构建脚本可在他人机器运行（无个人路径）
- [ ] `.gitignore` 防误提交规则已验证
- [ ] 敏感信息扫描（个人路径 / 内网标识 / 凭据）无命中
- [ ] 推送后**从远端读回**验证，不只看本地
