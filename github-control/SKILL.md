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
| `<GH_LOGIN>/Private-Skill` | 私有 | 原样，不动一字 |
| `<GH_LOGIN>/Public-Skill` | 公开 | 同一批技能，**已脱敏** |

本地工作副本：`~/repos/Private-Skill`、`~/repos/Public-Skill`。
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

- **脚本自身要跳过替换**。它里面写着"泛化模式"（如 `(?i)\b[a-z]{3}\d{4}\b`、
  `[A-Z][A-Z0-9]{2,7}-PC`），不跳过就会被自己的规则改写，工具直接失效 ——
  这是隐蔽的自伤，用**文件名白名单**规避（`SELF_SCRIPTS`，多脚本要一起登记）。
  泛化模式本身也要写成"通用形状"而非本公司实例，否则等于换个地方泄露命名规则。
- **域 / 账号 / 主机名类规则必须大小写不敏感**。同一标识在文档里会写成
  全大写 / 全小写 / 首字母大写等多种形态，区分大小写会漏一半。

脱敏要点（踩过的坑）：

- **占位符按环境维度定义，一个维度一个名字，不要复用。** 把
  `<USER>` 同时当"系统用户名"和"GitHub 用户名"用，读文档的人必然混乱。
  现用：`<HOST_IP>` `<HOSTNAME>` `<DOMAIN>` `<ACCOUNT>` `<SHARE>`
  `<USER>`（系统用户名）`<GH_LOGIN>` `<GH_ID>` `<GH_NAME>` `<LOCAL_UID>`、
  `Private-Skill`（私有仓库名）
- **跨技能引用会重新引入标识。** 写举例时若援引另一个技能的案例，
  那个案例里的域账号 / 主机名会跟着进仓库 —— 举例要泛化，
  或者退回源头把那段也改掉。
- **新增「实测记录」类文档是最大的漏点。** 具体值词表只列了你**已经知道**的标识；
  一份新的实测文档会顺带带出**从没登记过**的同类标识（元件位号、网络名、物料号）。
  实测：某技能的参考文档里有 15 个位号与 4 个网络名，词表一条都没覆盖，
  已在公开仓存活了一个版本周期才被发现。
  → 每次推送前，对**新增或改动的文档**跑一次**形状侦察**：按该领域的标识形状扫一遍
  公开副本（位号多为「1–2 个大写字母 + 2–4 位数字」，网络名多为下划线分隔的全大写串，
  物料号是带连字符的长字母数字串），逐条人工判定，该登记的补进 `sanitize-extra.json`。
  **形状只描述形状**——不要写出能反推公司命名规则的正则（连中缀字母这种细节都算线索），
  文档里的举例同样要泛指化。
- **只登记「完整值」不够，还要登记它的「独立子串」。** 词表里写了完整编号
  （形如 `ACME-PN-000123`），文档另一处只出现了它的中段 `000123`，
  完整值那条规则替换不掉中段——实测踩过：完整料号被换成占位符，同一料号在另一份
  文档里以中段形式出现，原样进了公开仓。**文档里的举例本身也要检查形状**：
  举例若照着真实编号的形态写（位数、分段、连字符位置都对得上），等于换个地方
  公布了编号规则——实测就是在这条上二度踩坑。
- **用泛化占位符会削掉可读性，改用「可读泛指」更划算。** 同一批位号/网络名映射到
  `<U1>` `<D1>` `<NET_EN>` 这类泛指形态，公开版读起来仍然通顺；映射成
  `<REFDES>` `<NET>` 会把实测记录变成一串占位符，读者反而看不出在讲什么。
  泛指形态本身也不带命名规则，可安全留在公开仓。
- **脱敏后的公开副本必须还能跑。** 替换是纯文本的，一旦落进运行时会用到的字符串
  字面量就会改行为。最快的判据：用**同一组输入**分别跑私有副本与公开副本，
  两者产出必须**逐字节相同**；分叉就说明替换碰到了逻辑。实测用这一条把
  「替换是否只落在注释与文档」一次跑清。（替换若误伤运行时代码，公开副本多半直接
  报错或产出不同的哈希，两个信号都会出现。）
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

### 10.8 私有仓转公开：先跑体检

把私有仓改成公开**不可逆**，动手前先跑一次扫描：

```bash
SC=~/.workbuddy/skills/github-control/scripts/scan_sensitive.py
python "$SC" ~/repos/Meeting              # 扫当前工作区
python "$SC" ~/repos/Meeting --history    # 连 53 个提交的历史一起扫
```

**输出必须逐条人工判定，不能只看"有没有命中"** —— 实测的经验是：

工具是**两层检查**，两层都要看：

1. **通用形状模式**（脚本体里，零环境标识）——抓个人路径、内网 IP、凭据字面量。
2. **运行时派生词表**（从同目录 `sync_skill_repos.py` 的 `build_rules()` 取，
   覆盖域 / 账号 / 主机名 / GH 身份）——**这层经常抓到纯模式扫描漏掉的东西**。
   实测：单靠模式扫描报了 13 处，加上这层又抓出 1 处。
   用 `--no-secrets` 可关掉。

- **误报很常见**，必须调出原文看：显卡型号 `<ACCOUNT>` 会命中"域账号样式"、
  `es.cursor = sel` 里的 `cursor` 会命中"AI 工具目录"、
  README 里 `git clone https://github.com/<登录名>/...` 会命中"派生词表"——
  但登录名随仓库 URL 本来就是公开的，属误报。
- **通用模式层本身也不能带公司命名线索。** 把"真实域名 / 账号字母前缀"直接写进
  检测模式或写进文档举例，等于换个地方公布命名规则 —— 本层只放**形状**
  （`[a-z]{3}\d{4}`），具体值一律走运行时派生词表。
  这条踩过两次，都是"工具自己成了泄露源"：第一次是脚本里的硬编码值绕过了
  自身脱敏；第二次是在**文档里引用具体前缀**——域名被替换规则吃掉了，
  但孤立的字母前缀没有规则能匹配，就这么漏进了公开仓。
  对策：脱敏工具除了整账号，还要单独拦**账号的字母前缀**（运行时从账号值切出来，
  仍然零硬编码）。
- **措辞类风险要靠眼睛**：`docs/` 里的「交付物 / 交接文档 / 开发机 / 目标机」这类措辞，
  会暴露这是内部工作交付物；`.qclaw` / `workbuddy shim` 之类
  会暴露项目是用 AI 工具链做的。这类"是否可公开"只有用户能定。

处理方式二选一，都要**连历史一起**（只在最新提交上改没用，旧提交仍可取到）：

| 情况 | 做法 |
|---|---|
| 内容有公开价值（技术文档等） | 脱敏后保留：用 `scrub_git_history.py` 把标识替换为占位符 |
| 内容是内部记录，公开无意义 | 从仓库移除（含历史），并同步修 README 里指向它的链接与目录树 |

移除此类目录时要留意 **README 往往已经链接了它们**，删完得回头改 README，
否则公开后一片死链。

**转公开的完整顺序（少任何一步都会留痕）：**

1. 体检：`scan_sensitive.py`（先工作区，再 `--history`）
2. 改内容：路径 / 工具名换占位符；内部措辞中性化（含**文件名本身**，以及 README 里指向它们的链接）
3. 补 LICENSE，并核对第三方组件许可（PyQt6 的 GPL/商业双授权是常见坑）
4. 提交
5. 历史改写：`scrub_git_history.py` —— **第 2 步改了内容就必须做**，否则老提交仍可取到原文
6. 强制推送
7. 切可见性：`gh repo edit <owner>/<repo> --visibility public --accept-visibility-change-consequences`
8. **全新克隆复核**，并用体检工具再扫一次（本地干净 ≠ 远端干净）

第 7 步只需 `repo` 权限；走到删库重建才需要 `delete_repo`。


## 11. 敏感内容已经推到公开仓：历史改写补救

触发场景：某个提交已经把真实环境标识（主机名 / 域 / 账号 / 内网 IP / 本机路径）
推到了**公开仓库**。可能是手工推了原始文件，也可能是**脱敏工具自己漏掉的**
（工具有"跳过自身"逻辑，写在它自己文件里的硬编码值会绕过脱敏直接进公开仓）。

**先记住一条：再补一个"干净提交"没用。** 旧提交对象依然能按 SHA 取到完整内容。
唯一出路是重写历史，工具已备好：

```bash
SC=~/.workbuddy/skills/github-control/scripts/scrub_git_history.py
python "$SC" --repo ~/repos/Public-Skill              # 重写 + 清理 + 独立复查
python "$SC" --repo ~/repos/Public-Skill --push       # 复查无残留才强推
```

它复用 `sync_skill_repos.py` 的 `build_rules()` 取规则（单一事实来源，脚本内无环境标识），
逐提交重写工作树，替换为占位符，再清扫陈旧 ref、回收对象、反扫全部新提交。

**项目仓要单独用。** 技能仓那套规则会连 GitHub 登录名一起换掉，而项目仓 README 里的
`git clone https://github.com/<登录名>/<仓库>.git` 是**必须保留**的。此时改用定向替换：

```bash
python "$SC" --repo ~/repos/Meeting --no-secrets-rules \
  --replace "<本机用户名>==><用户名>" \
  --replace "<外部工具目录名>==><工具目录>"
```

`--replace OLD==>NEW` 可重复，按**字面量、大小写敏感**处理；
`--no-secrets-rules` 关掉本机 secrets 派生规则。两条配套经验：

- 只改最新提交没有用，老提交仍能取到 —— **改了文件内容/文件名之后必须再跑一次历史改写**。
- 复查的**大小写语义必须与替换一致**。字面量替换是大小写敏感的，复查若统一加 `-i`，
  就会把 `.workbuddy/` 这类大小写不同的正常内容误报成残留（实测踩过，44 处"残留"全是误报）。
  工具现已按规则分别处理：派生词表用 `-i`，定向替换值用大小写敏感。

### 11.1 最大的坑：只删 reflog 和 gc 是不够的

`git filter-branch` 会把旧提交挂在 `refs/original/*` 下；而如果本地配过
**镜像 refspec**（`+refs/heads/*:refs/origin/*`，本机为绕开 `refs/remotes` 写入丢失
问题加过），`refs/origin/*` 仍指向旧提交。

> 实测：重写后 `git rev-list --all` 扫描显示"干净"，但 `git cat-file -e <旧sha>` 依然成功 ——
> 因为旧提交还挂在 `refs/origin/main` 上。删掉该 ref、expire reflog、`gc --prune=now` 之后才真正消失。

工具的判据很硬：**历史整体重写后，任何不是新 HEAD 祖先的 ref 都指向旧内容，一律删除。**
删完必须用 `git cat-file -e <旧sha>` 逐个验证"已清除"，不要凭 `rev-list --all` 下结论。

### 11.2 filter-branch 的 `--tree-filter` 参数顺序

传给临时脱敏器的位置参数必须与脚本 `argv` 对齐：

```
"<python>" "<临时脚本>" . "<规则json>"
```

写成 `"<python>" "<临时脚本>" "<规则json>" .` 会让脚本把规则文件当工作树根、
把 `.` 当规则文件读，报 `PermissionError: [Errno 13] Permission denied: '.'`。

### 11.3 GitHub 侧：force push 清不掉旧对象

**这是本工具的硬边界，必须主动告知用户。** GitHub 收到 force push 后不会立即回收，
旧提交仍可通过 SHA 访问：

```bash
gh api repos/<owner>/<repo>/commits/<旧sha> --jq .sha     # 实测仍返回 sha
gh api "repos/<owner>/<repo>/contents/<路径>?ref=<旧sha>" # 内容照样取得到
```

要做到零残留，只有两条路：

1. **删库重建**（推荐）—— 仓库新建不久、无 fork / 无 star 时成本极低：
   ```bash
   gh repo delete <owner>/<repo> --yes
   gh repo create <owner>/<repo> --public --source ~/repos/Public-Skill --push
   ```
2. 请 GitHub Support 清除缓存视图（慢，要发邮件）。

判断依据：`gh api repos/<owner>/<repo> --jq '{forks_count,stargazers_count,created_at}'`。
fork 数为 0 且创建时间很近 → 直接删库重建，别犹豫。

> **撤不掉的部分要如实说**：如果泄露的是口令 / 令牌 / 私钥（不是标识符），
> 无论怎么改写历史都必须**先轮换凭据**，把"撤历史"当额外动作而不是替代方案。

### 11.4 预防：公开仓的每次 push 前都跑独立复查

补救成本远高于预防。`sync_skill_repos.py` 的复查逻辑是**独立于替换规则**的
（用原始词表反扫），这是能发现"工具自己漏了"的唯一手段 —— 别把它当成可跳过的步骤。
新增任何同类脚本，记得登记进 `SELF_SCRIPTS` 白名单（但要留意 11.5 的反作用）。

### 11.5 脚本白名单会挡住定向清理（实测踩过）

`SELF_SCRIPTS` 的初衷是防止工具自身的"泛化模式"被规则改写。但它按**文件名整文件跳过**，
所以当真实指纹就写在脚本里时（典型形态：文档/脚本里引用的**公司命名规则正则**，
`(?i)\b<三字母前缀>\d{4}\b` 这种），这些脚本会被整文件跳过。更隐蔽的是——
复查也跳过同一批文件，于是**报"无残留"，但 `grep` 还能搜到**。

```bash
# 指纹在脚本里 → 必须关掉跳过
python scripts/scrub_git_history.py --repo <路径> --no-skip-scripts
```

判断标准：**指纹落在脚本正文里就打开**；纯粹是担心工具自伤就保持默认。
脱敏后不要只信工具自带的复查，自己再扫一遍：

```bash
cd <repo> && git grep -niE "<指纹形状>" $(git rev-list --all)
```

### 11.6 删库重建的实操顺序（已验证）

```bash
# 1) 先把本地历史彻底改干净，并独立验证
python scripts/scrub_git_history.py --repo ~/repos/Public-Skill --no-skip-scripts
cd ~/repos/Public-Skill && git rev-list --count HEAD
git grep -niE "<指纹>" $(git rev-list --all)      # 必须无输出

# 2) 删（需 delete_repo scope）
gh repo delete <owner>/<repo> --yes
gh api repos/<owner>/<repo> --jq .name            # 应 404

# 3) 建 + 推（分两步，比 create --source --push 稳，origin 已存在时后者易报错）
gh repo create <owner>/<repo> --public --description "<原描述>"
git push -u origin main

# 4) 恢复 topics：gh repo edit <owner>/<repo> --add-topic <t1> --add-topic <t2>

# 5) 全新克隆验证 + 旧 SHA 可达性
gh api repos/<owner>/<repo>/commits/<旧sha> --jq .sha
```

**"清干净"的判定信号是 422** —— `{"message":"No commit found for SHA: ...","status":"422"}`
表示对象真的不存在了。返回 200 就说明还在。

两个操作层面的注意点：

- **`delete_repo` 要单独授权**：`gh auth refresh -h github.com -s delete_repo`。
  该流程**必须用能跨轮存活的后台方式跑** —— 用普通 `&` 起的进程会在轮次结束时被回收，
  GitHub 那边即使授权成功、本机也没来得及保存令牌，设备码白白作废（踩过两次）。
- **`git bundle` 备份在本机不可靠**（实测静默不落盘）。反正技能仓内容可由
  `sync_skill_repos.py` 从源目录一键重建，删库前确认本地工作区 `git status` 干净即可。
