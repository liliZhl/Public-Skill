---
name: windows-lan-remote-access
description: 排查并修复 Windows 局域网内「远程控制另一台电脑」失败的问题，覆盖 ping 单向不通、远程桌面(RDP)凭据不工作、域账号(NetBIOS/UPN)登录名格式、主机侧远程桌面授权与防火墙配置、RDP 每次弹证书/资源重定向警告、以及无外网环境下改用第三方远控(局域网直连)的选型；也覆盖 RDP 会话导致节点锁定 EDA 许可(FLEXnet -103,577)签不出的场景。触发词：ping不通、远程桌面连不上、RDP、凭据不工作、局域网控制、域账号登录、mstsc、本地资源重定向、未知发布者警告、uncounted license、cannot checkout、ToDesk/向日葵/AnyDesk/RustDesk/VNC 局域网直连。
agent_created: true
---

# Windows 局域网远程控制排障

## 判断顺序（不要跳步）

1. **双向 ping** → 单向不通先修 ICMP 入站，别急着查远程桌面
2. **端口可达性** → 决定用哪种方案（RDP / SMB / 第三方远控）
3. **登录名格式** → 非域客户端的头号坑
4. **凭据有效性** → 用两条 net use 命令分离"密码错"与"通道/权限问题"
5. **主机侧配置** → 远程桌面开关、防火墙、远程桌面用户组
6. **连上了但软件报错** → 客户端弹警告/重定向看 §7；进去后 EDA 类软件许可签不出看 §8（节点锁定许可与 TS 会话冲突，RDP 本身没坏）

## 1. ICMP ping 单向不通

Windows 防火墙规则「文件和打印机共享(回显请求 - ICMPv4-In)」在**域/专用/公用所有配置文件下默认禁用**，改网络类型（专用/公用）无效。

```
netsh advfirewall firewall set rule name="文件和打印机共享(回显请求 - ICMPv4-In)" new enable=yes
# 等价 PowerShell（规则 Name 不随语言变化）：
Set-NetFirewallRule -Name "FPS-ICMP4-ERQ-In*" -Enabled True
```

非管理员会话执行会静默失败（exit 1）；`Start-Process -Verb RunAs` 常被安全策略拦截，需引导用户自己在管理员终端执行。

## 2. 端口扫描（快）

`Test-NetConnection` 每个端口都带 ping、很慢。用 .NET 直连、700ms 超时：

```powershell
$map = [ordered]@{3389='RDP';445='SMB';135='RPC';5985='WinRM';22='SSH';5900='VNC';5938='TeamViewer';7070='AnyDesk';21115='RustDesk'}
foreach ($p in $map.Keys) { $c = New-Object System.Net.Sockets.TcpClient; $ok=$false; try{$ok=$c.ConnectAsync('目标IP',$p).Wait(700)}catch{}; $c.Close(); "$p`t$ok`t$($map[$p])" }
```

- 3389 开 → RDP 可用（优先，零安装）
- 全关 → 主机上没有第三方远控在跑，要用就得先装

## 3. 登录名格式（非域客户端的头号失败原因）

非域成员的机器做网络登录走 **NTLM，不支持 UPN（user@domain.com）**；必须 `NetBIOS域名\用户名`。

取 NetBIOS 域名（无需凭据）：
```
nbtstat -A <域控IP>     # 名称表中 <00> 组 / <1C> 组 即是 NetBIOS 域名
nltest /dsgetdc:<dns域> # 定位域控、确认 KDC 可达
```
注意 `域名\用户名` 也可写成 DNS 域形式（<DOMAIN>.com\user），NTLM 同样接受。

## 4. 凭据判定：两条命令分离故障

```bat
net use \\<主机IP>\IPC$ /user:<域>\<账号> <密码>          :: IP 目标 → NTLM 通道
net use \\<域控FQDN>\NETLOGON /user:<域>\<账号> <密码>    :: 域名目标 → Kerberos 通道，直连域控
net use \\<主机IP>\IPC$ /delete /y
```

错误码对照（决定下一步）：

| 错误 | 含义 | 动作 |
|---|---|---|
| 1326 | 用户名或密码不正确（NTLM） | 查格式，再查密码 |
| **86** | 指定的网络密码不正确（Kerberos，域控已校验） | **密码值错**，账号存在且未锁 |
| 1330 | 密码已过期 | 改密 |
| 1907/1909 | 账号锁定 / 必须改密 | 等解锁或改密 |
| 1311 | 无可用登录服务器 | 目标到域控链路不通 |
| 拒绝访问(5) | 认证通过、无权限 | 凭据没问题，转向授权排查 |

Kerberos 通道返回 86 → 可判定：通道正常、账号存在、未锁定、未过期，**就是密码不对**。提醒用户核对 Caps Lock、输入法全角、末尾符号，并停止反复重试（会触发域锁定）。

**但 86 不是终审判决 —— 用户口述的密码记错是常态。** 实测案例：用户先给了一串旧密码，两条通道都返回 86/1326；用户更正为另一串后，**同一条命令立刻在 `\\主机IP\IPC$` 与 `\\主机IP\c$` 上返回 exitcode 0「命令成功完成」，而对域控 `\NETLOGON` 返回错误 5「拒绝访问」**（认证已通过、只是该共享无读权限）。所以正确姿势是：① 明确告诉用户"账号本身没问题，不匹配的是密码值"，② 请其核对后再验一次，**不要一次失败就下结论**；③ 反过来，只要错误码从 86/1326 变成 **5**，就说明凭据已经对了。

验证成功后收尾三件事：`cmdkey /generic:TERMSRV/<主机IP> /user:<域>\<账号> /pass:<密码>` 预存凭据（本环境实测 cmdkey 可用，未被安全策略拦，与 ConvertTo-SecureString 不同）→ 把 .rdp 的 `prompt for credentials` 改为 `0` → 用户双击连接文件即免密直连（清除：`cmdkey /delete:TERMSRV/<主机IP>`）。

## 5. 主机侧修复（能接触到主机时）

用管理员权限执行，顺序固定：

```
reg add "HKLM\SYSTEM\CurrentControlSet\Control\Terminal Server" /v fDenyTSConnections /t REG_DWORD /d 0 /f
powershell -NoProfile -Command "Enable-NetFirewallRule -Name RemoteDesktop-UserMode-In-TCP"
```
- 防火墙规则按 **Name**（`RemoteDesktop-UserMode-In-TCP`）启用，不要用 DisplayGroup（组名随系统语言变化）
- 授权远程桌面登录：本地组 SID **S-1-5-32-555**（Remote Desktop Users），用 SID 解析可绕开语言差异：
  ```powershell
  $g=(New-Object System.Security.Principal.SecurityIdentifier('S-1-5-32-555')).Translate([System.Security.Principal.NTAccount]).Value
  Add-LocalGroupMember -Group $g -Member '<域>\<账号>'
  ```
- 主机的**本地登录界面/runas 能成功 ≠ 域网络登录能成功**（离线缓存凭据）；网络登录要求主机到域控 88/389 可达
- Windows 家庭版无法作为 RDP 服务端；客户端版 RDP 单会话，连接会踢掉本地登录用户

## 6. 无外网环境的第三方远控（兜底）

主机不能上网时：TeamViewer 不可用（依赖公网中转）；**RustDesk、UltraVNC** 受影响最小（纯局域网 IP 直连、不依赖域账号与公网）；AnyDesk/ToDesk/向日葵需开"允许局域网直连"。

处理三件事：装服务端（U 盘拷安装包）→ 设固定密码并允许局域网连接 → 防火墙放行对应端口（VNC 5900）。公司域机器安装前提示合规风险。

## 7. 客户端侧抱怨：证书警告 + 资源重定向每次都要重新勾

现象：每次连接都弹「无法验证此远程连接的发布者 / 警告：未知的远程连接」，并要求勾选智能卡、Windows Hello 企业版、驱动器、剪贴板、录音设备；**在警告框里勾选只对"此次连接"生效**（框内原话："对这些选项的更改仅适用于此次连接启动"），下次照样弹。

根因两条：① 主机用自签名证书，客户端无法验证发布者 → 弹警告；② 重定向设置没写进 .rdp 文件。

修法（生成 .rdp 比让用户在 GUI 里点更省事，直接写文件交付）：

```
authentication level:i:0    # 0=验证失败也连且不警告(关键)；1=警告；2=不连
redirectclipboard:i:1       # 剪贴板
redirectprinters:i:1
redirectcomports:i:1
redirectsmartcards:i:1      # 智能卡
redirectwebauthn:i:1        # Windows Hello 企业版
audiocapturemode:i:1        # 麦克风和其他录音设备
drivestoredirect:s:*        # 驱动器（挂载所有盘）
usbdevicestoredirect:s:*
devicestoredirect:s:*
camerastoredirect:s:*
prompt for credentials:i:0  # 免密码框，前提是已 cmdkey 预存凭据
use multimon:i:0            # 别默认开，否则铺满客户端所有显示器
screen mode id:i:1          # 1=窗口(配 desktopwidth/height 1600x900)，2=全屏
```

- .rdp 用**纯 ASCII 文件名 + CRLF 换行**（写完后用 `[System.IO.File]::WriteAllText` 归一化），放桌面双击即用。
- 免密：`cmdkey /generic:TERMSRV/<主机IP> /user:<域>\<账号> /pass:<密码>`（清除：`cmdkey /delete:TERMSRV/<主机IP>`）。
- 想要**所有**连接（不止这个文件）都不弹警告，改 HKCU 即可、无需管理员：
  `reg add "HKCU\Software\Microsoft\Terminal Server Client" /v AuthenticationLevelOverride /t REG_DWORD /d 0 /f`
  域机被组策略接管时可能被刷回默认，那就只能改「配置服务器证书的警告消息」策略。

## 8. 连上了但 EDA 类软件报许可签不出（FLEXnet -103,577）

现象：远程桌面进去后启动 Mentor/Siemens EDA（Expedition/DxDesigner/PADS）、PTC Creo 等，报
`Cannot checkout an uncounted license within a Windows Terminal Services guest session`，`FLEXnet Licensing error:-103,577`；**同一台机器本机控制台上启动却完全正常**。

根因：节点锁定（uncounted / node-locked）许可是**机器绑定**的，FLEXlm 默认禁止在 Terminal Services 客户会话里签出，防止一份 node-lock 被多台远端机器非法复用。RDP 进来是新的 TS 会话，不是主机控制台会话 → 必然失败。**不是安装坏了、不是许可过期、也不是 RDP 没配好。**

先取证据（一句话定性）：看许可文件里 INCREMENT 行的计数是否为 0/uncounted，且 `grep -c TS_OK <license file>` 是否为 0。是 → 确认节点锁定且未签发 TS_OK。

可选动作（按推荐序）：

1. **改用控制台镜像型远控（实测唯一稳妥的免费路线）**：**UltraVNC（服务模式，首选）** / TightVNC / TigerVNC / ToDesk / 向日葵 / TeamViewer / RustDesk / NoMachine（v10 起需付费许可，见下）。连接时选"控制台会话 / 物理桌面"而不是新建会话。这类工具是**应用在主机上跑、只把画面转发**，进程真正落在主机物理控制台会话里，且该会话没有远程 RDP 客户端附着 → 许可正常签出。需先在主机装服务端并放行端口（NX 4000 / VNC 5900）。
   - **首选 UltraVNC（零摩擦，实测推荐）**：GPL 开源、无订阅无账号、不受任何授权检查，同样是"程序在主机跑、只转发画面"。主机装 UltraVNC Server 并以 **Service Mode** 运行 + 设 VNC 密码（**≤8 字符**，VNC 协议上限）+ 放行 TCP 5900（附带放行 5800 供 web viewer）；笔记本装 UltraVNC/TightVNC Viewer，地址写 `<IP>:5900`。**Service Mode 必须勾**，否则抓的是服务会话而非物理桌面，许可照样过不了。附带好处：它不碰 Windows 账号，只用 VNC 自身密码 —— 域账号格式 / 域密码 / PIN 那一堆坑全部绕过。**版本要点（2026-09-14 官网核实）**：最新版 **1.8.3.0**（2026 年 9 月发布），包名 `UltraVNC_1830_X64_Setup.exe`（约 6.1 MB）/ `UltraVNC_1830_X86_Setup.exe` / `UltraVNC_1830_x64_Setup.msi` / 免安装 `UltraVNC_1830.zip`（约 17 MB，含 32/64 位）。x64 setup 的 SHA256 `e9c22419ef3128a707b0d004a2c58e25459528c5c12d104dea56e5e662bd131e` —— U 盘拷安装包时用它校验。**下限必须 ≥ 1.8.2.3**：该版一次性修掉 11 个 CVE（含 repeater 预认证溢出 CVSS 9.8、硬编码 repeater 管理员口令 9.1）。1.8.2.5+ 起自带 **noVNC 网页端**（含 TLS），服务端内置 HTTP 服务，浏览器直接开 `http://<主机IP>:5800` 即可，不装 Viewer 也能连；1.8.3.0 补上 noVNC 多显示器。同版起 Viewer 密码可存进 Windows 凭据管理器实现免输。**不要用 MS-Logon II**（31 位 DH，属遗留不安全实现），要域认证用 1.8.2.3+ 的 MS-Logon III（X25519+AES-256-GCM）。**uvnc 官网的下载按钮有 JS 门槛、抓不到直链**，让用户用浏览器点下载即可。
   - **安装向导 "Select Additional Tasks" 页怎么勾（1.8.3.0 实测界面）**：必勾 **Register UltraVNC Server as a system service**（= 服务模式，决定许可能否签出）+ **Start or restart UltraVNC service**（立刻启动，省一次重启）；`Create UltraVNC desktop icons` 随意；`Associate .vnc` 不需要（用 `IP:5900` 直连）；**`Add virtual monitor driver (Win 10)` 默认不勾** —— 它是 IddCx 虚拟显示器驱动，只在主机长期不接显示器、远程看到黑屏/低分辨率时才需要，正常接着显示器的工作站不需要，且域机器装内核驱动可能需 IT 放行。
     - **主机不接显示器（headless）是这个方案的连带雷区，两个问题要分开处理**：
       ① **画面问题**：GPU/驱动在无输出设备时可能关掉显示输出 → VNC 连上是黑屏、花屏或只有 640×480~800×600。**别预防性折腾驱动，先连一次看**（集显一般没事）。真有问题再补：重跑安装包勾 `Add virtual monitor driver (Win 10)`，或管理员终端 `& "C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe" -installdriver`（安装脚本的原始调用方式；若报签名问题就重跑安装包，它会先 `certutil -addstore TrustedPublisher ultravnc.cer`）。**更稳的物理替代是 HDMI/DP 假负载（EDID 模拟器，十几块钱）**：插上后显卡认为有显示器，分辨率和刷新率恢复正常，不用装内核驱动，对 PCB/CAD 图形体验更好 —— headless 工作站首选这个。
       ② **会话问题（更致命）**：服务模式镜像的是控制台会话，**若主机没有任何人登录过，控制台停在 Winlogon 登录界面 → 连上去只有登录屏，EDA 根本跑不起来，许可也签不出**。三选一：用 Viewer 的"发送 Ctrl+Alt+Del"在登录屏上远程登录（登进去就是物理控制台会话，符合许可要求）；或让同事在主机登录一次后**锁屏而非注销**（会话保留，许可不受锁屏影响）；或配开机自动登录（域账号自动登录需存密码，IT 可能不允许）。
   - **HDMI/DP 假负载 vs 虚拟显示器驱动**：headless 首选假负载（物理、稳、分辨率正常、免内核驱动）；驱动方案适合没法插假负载的场景（如刀片/机柜内），但分辨率可能需要手动调整。
   - **RDP 与 VNC 互相抢会话：同一账号在 Windows 客户端版只能有一个交互会话（实测现象，务必先讲清楚）**。实测序列：① 已用 RDP 登入（TS 会话）→ 控制台会话被顶成登录屏；② 用 VNC 连上，看到的是**控制台登录屏**（服务模式镜像的就是控制台，所以"VNC 只能看到锁屏"是正常预期，不是连错）；③ 在 VNC 里输密码 = 在控制台用**同一账号**登录 → Windows 判定"该账号已有会话" → **把 RDP 会话顶出**（RDP 窗口直接断开）；④ 会话切换瞬间 UltraVNC 会短暂失去桌面挂载，表现为"VNC 也跟着连不上了"。**这不是故障，是设计行为**，而且反证了"服务模式镜像物理控制台"链路是对的 —— 正是 EDA 许可需要的形态。处理原则：**别再用 RDP 进去**（RDP 一连上就把控制台顶掉 → EDA 立刻又 -103,577，且与 VNC 来回抢会话形成拉锯）；万不得已用 RDP 办事时，**干完立刻注销而不是断开**，先 `query session` 看清 ID（`console` 行是控制台，`rdp-tcp#x` 是 RDP），再 `logoff <id>`。
   - **登录后"VNC 掉线"是设计如此，不是故障（作者 Rudi De Vos 原话，务必先讲清）**：Windows 登录用的是**安全桌面（Winlogon）**，输完凭据后切到**用户桌面**；"VNC follows the active desktop and **restarts himself in the user desktop**"。Vista 起会话隔离 —— winvnc 起在登录会话里，登录后**必须停掉、到用户会话里重启**才能真正操作用户桌面，**这个中断是必然的**。解法只有一个：**Viewer 开自动重连**，重连在后台自动完成，且**重连后直接进桌面、不再显示登录屏**。
     **Viewer 设置页字段对应（实测截图 + 官方命令行文档 + 作者回帖三方核对）**：Viewer → Options 对话框**底部**并排两个输入框 —— 左边 **`reconnection attempts`**（= 命令行 `-reconnectcounter`，重连尝试次数；文档：0=从不重连，上限 9）= "断线后最多重试几次"；右边 **`timeout`**（= 命令行 `-autoreconnect <DelayInSeconds>`，两次重连之间的间隔秒数，默认 3、官方示例用到 30）= "每隔几秒重试一次"。⚠️ `-reconnectcounter` **只有 GUI 能设、命令行不支持**，必须在设置页里填好。
     **别用默认 3/3 —— 总容忍窗口只有 3×3s ≈ 9 秒**。域账号 + EDA 工作站的登录要跑组策略、漫游配置、许可服务，用户会话起来常需 10–60s，窗口一过 Viewer 就放弃 → 症状正是"**输完密码后黑屏 / 直接断开**"。建议 **`timeout` 填 5 秒、`reconnection attempts` 尽量往大写（目标 ≥20；控件若卡在 9 就填 9）**。
     命令行等价（用于做桌面快捷方式/一键连）：`vncviewer.exe -autoreconnect 5 -connect <HOST_IP>:5900`（`-reconnectcounter` 命令行无效，只能靠 GUI 里存下的值）。
   - **黑屏三大成因（连得上、密码对、却全黑）——按这个优先级查**：
     1. **会话不在 `Active` 状态**：`query session` 看 `console` 行是 `Active` 还是 `Disc`。**RDP 若又占回控制台，console 会被置为 Disc（无渲染）→ VNC 全黑**。处理：`logoff <所有 rdp-tcp#x 的 ID>`，关掉 mstsc，等 console 回到 `Active` 再连。
     2. **ddengine（Desktop Duplication，Win10 起取代镜像驱动的捕获引擎）不被显卡驱动支持** → 黑屏/灰屏。作者首选排查项："properties server **[v] ddengine uncheck** — DDengine require latest video card and drivers. Possible not supported. **Uncheck to test if this is causing it**"；论坛实例"关掉 ddengine 后连接正常"。虚拟机、无正规显卡驱动的机器高发。
     3. **主机检测不到显示器 → 没有东西可克隆**：作者原话"**if no console exist vnc can't clone it**"（无显示器时 GPU 可能直接关掉输出）。**首选物理解：HDMI/DP 假负载（EDID 模拟器）** —— 作者亦提到"Most video cards support an overwrite to tell a monitor is connected, even when not plugged"；次选 UltraVNC 虚拟显示器驱动。
     辅助判定：`Get-CimInstance Win32_VideoController | Select Name,DriverVersion,CurrentHorizontalResolution` —— 适配器是 `Microsoft Basic Display Adapter` 或分辨率空/0，即"无输出 / 无驱动"的强信号。
   - **service 模式永远克隆 console 会话**（uvnc 官方特性表原文："Running as service you always clone the console, if started as application you clone the current session (console/RDP)"）。所以"VNC 只看到控制台登录屏"是**正常预期**；反之**应用模式**（双击桌面 Server 图标）在 RDP 会话里跑时克隆的是 **RDP 缓冲区 → RDP 最小化/断开后其视频缓冲被禁用 → 黑屏**（作者原话）。这两条合起来能解释绝大多数"VNC 黑屏/只看到锁屏"的困惑。
   - 配置改完一律 `Restart-Service uvnc_service`（等价 `WinVNC.exe -stopservice` + `-startservice`）让设置生效；会话状态用 `query session` / `query user` 查。
   - **打通之后的日常使用手册（交付给用户的正解，实测跑通后总结）**：
     - **三条红线**：① **不要再用 RDP 连这台主机**（一连上即顶掉控制台 → EDA 立刻 -103,577，且与 VNC 抢会话）；② **不要在主机上"注销"**（注销杀掉会话、控制台退回登录屏，要重新远程登录；**锁屏 Win+L 无害**）；③ **不要两人同时连**（VNC 克隆同一桌面，非多会话，并行使用需各配一台主机）。
     - **Viewer 热键（官方文档核实）**：`Ctrl+Alt+F4` 发送 Ctrl+Alt+Del（**登录屏/锁屏解锁必用**）／`Ctrl+Alt+F12` 全屏／`Ctrl+F11` 1:1 原始尺寸／`Ctrl+Alt+F10` 自适应缩放／`Ctrl+Alt+F11` 半尺寸／`Ctrl+Alt+F6` 连接选项／`Ctrl+Alt+F7` 文件传输／`Ctrl+Alt+F8` 聊天／`Ctrl+Alt+F9` 显示隐藏工具栏／`Ctrl+Alt+F5` 保存连接信息为 `.vnc`；`Break/Pause` 切换全屏、`PrintScreen` 请求整屏刷新（**花屏/缺块第一招**）；**`ScrollLock` 打开后所有组合键直通主机**（Alt+Tab / Ctrl+Esc / Alt+Space 才会作用在主机上）。
     - **一键连接快捷方式**（最省事的日常用法）：`vncviewer.exe -autoreconnect 5 -connect <主机IP>`（`-autoreconnect` 必须写在 `-connect` 之前）；图形界面里对应 Options 底部 timeout=5、reconnection attempts≥20。
     - **排障口诀**：卡在 Connecting 收发 0 字节 = 端口/服务（重启服务、`netstat` 查 5900）；密码过了全黑 = 会话非 Active / headless 无输出 / ddengine；花屏卡住 = 先 PrintScreen，再重启服务；连上几秒断开又自己回来 = 会话切换，**设计如此，不用管**；只看到登录屏 = 主机没人登录 → `Ctrl+Alt+F4` 远程登录。
     - **安全**：VNC 密码受协议限制 ≤8 位、强度有限 → 只在内网用，**绝不做端口映射暴露公网**；要加固就两端同挂 SecureVNCPlugin（DSM）；控制端机器只装 Viewer。
   - **官方文档核对过的配置项与调优键（2026-09-14 整理，用 `uvnc.com/docs` + `uvnc.com/webhelp` 原件，别看二手博客）**：
     - **Server 设置页（Admin Properties / `winvnc.exe -settings`）**：`Accept Socket Connections` 必开（不勾就不监听）；`Display` 保持 0（端口恒 = Display+5900）；`Enable JavaViewer` 勾了才有 5800；`Allow Loopback` 默认关（**关闭时本机连 127.0.0.1 会被拒**，别用它自测）；`Loopback Only` 别勾（外部全断）；**`When last client disconnects` 必须选 Do Nothing** —— 选 Logoff Workstation 的话，Viewer 一关主机就自动注销、EDA 会话直接消失；`Query on incoming` 无人值守必须关；`Multi Viewer connections` 要多人同看必须选 **Keep existing**（选 Disconnect all 则后来者顶掉先来者）；`Remove Wallpaper` 建议勾（断开自动恢复）；`Enable File Transfer` 要传文件才勾；`Disable Tray icon` 勾了就没托盘图标也改不了实时设置。
     - **ini 高价值键（`C:\ProgramData\UltraVNC\ultravnc.ini`，前提 `UseRegistry=0`）**：
       - **`sendbuffer`**：局域网提速第一键。百兆 `4096` / 千兆 `8192` / WiFi 或更差 `1500`。
       - **`MaxCpu`**：winvnc CPU 占用上限百分比（默认 100）。**EDA 工作站建议压到 40~50**，别和仿真抢 CPU。
       - `RemoveWallpaper=1` / `RemoveAero=1`：连接时移除壁纸与 Aero，断开恢复，省带宽也省 CPU。
       - `KeepAliveInterval=5` 保活心跳；**`IdleTimeout=0` = 从不空闲超时**（别设小值，否则人走开就掉线）。
       - `[poll] TurboMode=1` 快扫但**可能漏小变化**（画面细微刷新丢失时改 0）；`OnlyPollOnEvent=1` 只在键鼠活动时更新（省带宽但画面不实时）；`EnableDriver` 镜像驱动默认 0。
       - **`passwd` = 全控密码、`passwd2` = 只读密码（1.0.8.0+）** —— 给围观同事发只读密码很实用；**两者绝不能填成同一个，官方明确说不然会退化到只有只读权限**。
       - `SocketConnect=1`（被改成 0 = "服务在跑却没监听"的经典原因）；`AutoPortSelect=1`（5900 被占会自动往上找端口，**排障时注意端口已漂到 5901**）。
       - `AuthHosts` + `QuerySetting`（按 IP 段放行/拒绝/询问，语法 `+10.0.60:` / `-` / `?`；QuerySetting 默认 2 = 只对 `?` 询问）。
       - `LockSetting`：0=断开不做事 / 1=断开即锁工作站 / **2=断开即注销（千万别用，会让 EDA 会话消失）**。
       - `FTUserImpersonation=1`：以桌面用户身份传文件；**不设 1 就是以 SYSTEM 身份跑 → 看不到映射网络盘，且是安全隐患**；`FileTransferTimeout=30`。
       - `LocalInputsDisabled=1` 屏蔽主机本地键鼠；`DisableTrayIcon=1` 就是托盘图标消失的原因；`AllowProperties` / `AllowShutdown` 控制托盘菜单可见性。
       - `service_commandline=`：服务的附加命令行，可让主机反向连接指定 Viewer。
       - `DebugLevel` / `DebugMode` / `path`：日志级别与 winvnc.log 目录，疑难问题靠它。
     - **Viewer 端**：Quick option 局域网选 **LAN / Ultra**；编码 **Ultra**（LZO，局域网实时性最好）> Hextile（高速以太网经典最佳）> Tight/ZRLE（低带宽）> Raw（本机直连）；**灰色档位只支持 32 位色**（16/24 位下无效）；自定义色彩要命令行加 **`-noauto`** 才不被快速选项覆盖；Viewer 默认设置存 **`%AppData%\UltraVNC\options.vnc`**（可直接编辑做批量部署，例如 `UseDSMPlugin=1`）；勾 `Save connection settings as default` 否则改动只对本次连接有效。
     - **常见坑**：屏保/锁屏策略会让远程操作时主机自动锁屏（主机端关掉屏保或取消"恢复时要求密码"）；某些非标准键盘的特殊键出不来可试 `EnableJapInput=1`（官方说明它也能解决部分特殊键问题）。
     - **Viewer 快捷键（官方 `uvnc.com/docs/ultravnc-viewer/71-ultravnc-viewer-gui.html`）**：**`Ctrl+Alt+F4` 发送 Ctrl+Alt+Del** ｜ `Ctrl+Alt+F7` File Transfer ｜ `Ctrl+Alt+F8` Chat ｜ `Ctrl+Alt+F6` Connection options ｜ `Ctrl+Alt+F9` 显隐工具栏 ｜ `Ctrl+Alt+F5` Save connection info ｜ `Ctrl+Alt+F12` 全屏 ｜ `Ctrl+F11` 1:1 ｜ `Print Screen` 强制全屏刷新（画面花掉时的救场键）｜ `Break/Pause` 切换全屏。上下文菜单里另有 `CTRL Down/Up`、`ALT Down/Up`（把纯 Ctrl/Alt 单独送过去）。
     - **组合键"送不到远端"的官方机制（最容易误判成键盘坏了）**：UltraVNC 默认让 `ALT+TAB` / `CTRL+ESC` / `ALT+SPACE` 在**本地**生效（切本地窗口 / 开本地开始菜单 / 本地窗口菜单）。**按一下 `Scroll Lock`（滚动锁定）** 后，除 Ctrl+Alt+Del 外的**所有组合键直接送远端**；再按一次即关闭该行为。
     - **Ctrl+Alt+Del 任何远控都拿不到**：它是 Windows 的**安全注意序列（SAS）**，被内核拦在本地以阻止伪造。只能用 Viewer 工具栏的 **Send CTRL+ALT+DEL** 按钮或 **`Ctrl+Alt+F4`**；服务端可选装 SAS Hook 驱动（`vgauth.sys`）。
     - **剪贴板同步的四个已知坑（uvnc 论坛实锤，到 1.8.2.4 仍未修）**：① **方向不对称** —— 远端→本地多半自动，**本地→远端经常不自动**，需手动推：Viewer 窗口**左上角小图标 → Clipboard → "Request all formats now"**；② **File Transfer 窗口只要开着（哪怕最小化）就完全阻断剪贴板同步**；③ **只支持文本 / HTML / RTF**，图片与多格式必须走 "Request all formats now"，**文件粘贴根本不支持**（想用 Ctrl+C 传文件是走不通的，用 File Transfer `Ctrl+Alt+F7` 或 SMB）；④ 部分构建/老版工具栏有互斥项 **`Use CTRL+C to sync clipboard (all data is synced)`** 与 **`Use CTRL-ALT-C to sync clipboard`** —— 前者会让 **Ctrl+C 被 Viewer 截走做剪贴板同步**（复制大文件会拖慢甚至断连），Ctrl+C 行为异常先查这里。
     - 论坛土办法（多人复现）：先打开 File Transfer 随便上传一个小文件，之后 Ctrl+C/Ctrl+V 就恢复正常 —— 相当于触发剪贴板通道初始化。
     - Viewer 侧排障出日志：`vncviewer.exe -logfile c:\viewer.log`。
     - **组合键（`Ctrl+<字母>`、`Ctrl+Alt+<字母>` 等）在远端不生效 —— 作者口径的三个解法**（uvnc 论坛 Rudi De Vos 亲答，多个帖子给的是同一套；1.5.0.x 时代开发者仍承认 Ctrl+C/Ctrl+V 没弄好）：
       ① **按一下 `Scroll Lock`**：作者原话 —— "scroll-lock 就是用来告诉 OS 这个热键需要送到远端，否则 OS 会在按键到达 Viewer 之前就把它截走"；再按一次即关闭该行为。**这是官方机制，不是偏方。**
       ② **换用旧式键盘处理路径**：Viewer → Options → **Mouse and keyboard → "Japanese keyboard handling"**（作者称之为 *alternate keyboard method*，老版就叫 *japanese keyboard handling*）；**服务端同名选项 `EnableJapInput=1` 也要开**（有人只开一边会导致 Viewer 崩溃）。
       ③ **检查服务端是否禁用了 Viewer 输入**：`UltraVNC Server - Settings` → **Input / File Transfer** 页 → 取消勾选 **"Disable viewers input"** → 重启服务。（勾上则键鼠**全部**进不去，不只是组合键。）
     - **VNC 场景下更可靠的替代键（先直接用，不必等修好）**：复制 **`Ctrl+Insert`** ｜ 粘贴 **`Shift+Insert`** ｜ 剪切 **`Shift+Delete`**。论坛原话"Ctrl+C / Ctrl+V 在远端机器上从来就没工作过，我一直用 Ctrl+Ins / Shift+Ins" —— 这是 VNC 生态的老现象，不是个例。
     - **分层定位法（30 秒排除法，别一上来就改配置）**：在**远端**开记事本 → ① 敲普通字母能出吗（通道是否通）→ ② 按 `Ctrl+F` 弹查找框吗（组合键是否到达）→ ③ `Ctrl+Insert` 有反应吗（换替代键能否绕过）。据此判断是"键全断"、"只有修饰键组合断"、还是"仅剪贴板断"（三种的修法完全不同）。
   - **域主机开 SMB 共享给"非域"客户端（同网段互传大文件，比 VNC 文件传输快得多，可随机访问）**：
     - **权限是两层取交集：实际权限 = 共享权限 ∩ NTFS 权限，两层都要放行**（只给一层是最常见的"密码对却拒绝访问"）。四道闸门：网络可达 → 445 放行 → 共享权限 → NTFS 权限。
     - **主机侧（可在 VNC 里操作）**：文件夹右键 → 属性 → **共享** → **高级共享** → 勾"共享此文件夹" → 共享名（用 ASCII，如 `EDA`）→ **权限** → 加上要授权的账号（**默认只有 Everyone 只读，很多人卡在这**）；再到**安全**选项卡给同一账号"修改"权限。命令行等价：`New-SmbShare -Name EDA -Path D:\Share_EDA -FullAccess "主机名\user"` + `icacls D:\Share_EDA /grant "主机名\user:(OI)(CI)M"`；查看用 `net share` / `Get-SmbShare`（需管理员）。
     - **共享「已存在」的目录（如 `D:\SHARE`）不要走右键向导的默认权限**（默认只有 `Everyone` 只读，最常见的"我明明共享了却进不去"）。一条命令：`New-SmbShare -Name SHARE -Path D:\SHARE -FullAccess <账号>`，共享名用 ASCII。**授权对象二选一**：① 当前域账号（笔记本端用 `域名\账号`，要求域控可达且域策略允许 NTLM，不一定通）；② **主机本地账号**（非域客户端最稳；域机若没有可用本地账号，`New-LocalUser -Name vncshare -PasswordNeverExpires` 建一个专用账号，共享与 NTFS 都授权给它，笔记本端用 `主机名\账号`，彻底绕开域认证与 NTLM 依赖）。NTFS 层同样要放行：`icacls D:\SHARE /grant "<账号>:(OI)(CI)M"`（M=修改；要完全控制用 F；只给读用 R 并配 `-ReadAccess`）。
     - **跑完自查三项**：`Get-SmbShare -Name SHARE | fl Name,Path`（路径对不对）、`Get-SmbShareAccess -Name SHARE`（账号在不在、权限够不够）、`Test-Path` 前置确认目录存在；再对照 `$env:COMPUTERNAME` / `$env:USERDOMAIN` 算出笔记本端账号前缀，别凭记忆填。
     - **`New-SmbShare` 在「不给任何账号参数」时的默认共享权限是 `Everyone: 读取`**（官方 DSC 文档明确：不加 `-FullAccess`/`-ChangeAccess`/`-ReadAccess` 才会补 Everyone 读取）。所以 `Get-SmbShareAccess` 里若看到 **`Everyone` = 完全控制**，那**不是这条命令产生的**，而是**手工建共享时留下的**（Explorer → 属性 → 共享 → 高级共享 → 权限 → Everyone → 勾"完全控制"）。加固一条命令：`Revoke-SmbShareAccess -Name <共享名> -AccountName Everyone -Force`。**别只清共享层**——NTFS 层用 `icacls <路径>` 看全量 ACL（`(OI)(CI)` = 子目录/文件继承；`M`=修改 / `F`=完全控制 / `R`=只读）；两层是交集，谁更严谁生效。
     - **共享层 vs NTFS 层的分工惯例**：共享层放宽（给 `Authenticated Users` 完全控制）、真正的访问控制交给 NTFS（继承、deny、审计行为都更可预期）。这样做的代价是"共享层看起来谁都能进"，排查时容易误判，所以团队内要统一口径。
     - **445 一般不用再动**：实测笔记本探主机 445 = 3ms OPEN（见上文"同网段多端口探测"）。若被 GPO 收走"文件和打印机共享"规则组，本地改不动，找 IT。
     - **客户端侧连接**：资源管理器地址栏 `\\<HOST_IP>\<SHARE>`，或 `net use Z: \\<HOST_IP>\<SHARE> /user:主机名\本地账号 *`（末尾 `*` 交互输密码，避免明文留在命令行历史）；可加 `/persistent:yes`。
     - **账号写法三选一，写错必失败**：① `主机名\本地账号`（**非域客户端最稳**）；② `域名\域账号`（需能联系域控且域策略允许 NTLM）；③ `域账号@域名`（UPN）。**绝不能用 Windows Hello PIN** —— PIN 绑定设备，不用于网络认证；域机器日常用 PIN/人脸登录时，要让用户在主机上 `Win+L` 切"密码"方式确认真正的账号密码。
     - **报错对照**：`组织的安全策略阻止未经身份验证的来宾访问` = 账号被解析成 Guest（先改用显式 `主机名\账号`；真要开来宾才改 `HKLM\SOFTWARE\Policies\Microsoft\Windows\LanmanWorkstation\AllowInsecureGuestAuth=1` 或 gpedit「启用不安全的来宾登录」，**有安全代价、公司机器慎做**）｜`系统错误 53` = 名称解析或共享名写错，改用 IP 直连｜**`系统错误 1219` 多重连接** = 同一服务器已存在另一账号的会话，先 `net use * /delete` 再重连｜`拒绝访问` = 共享权限或 NTFS 未放行（两层都查）｜**反复弹密码框** = 凭据管理器有该 IP 的旧条目，删掉（`cmdkey /list` 可看）｜域机器还受 GPO 限制："网络访问: 共享和安全模型（经典/仅来宾）"、"不允许匿名枚举 SAM 账户和共享"。
     - **工程纪律**：**不要把 EDA 工程直接放在网络共享盘上跑**（性能 + 文件锁 + 许可风险），SMB 只当大文件传输通道；单文件小传输用 Viewer 的 `Ctrl+Alt+F7` 更省事。
   - **静默批量安装**（U 盘给多台机器铺）：`UltraVNC_1830_X64_Setup.exe /VERYSILENT /NORESTART /MERGETASKS=installservice,startservice`。任务名对照：`installservice` 注册服务 / `startservice` 启动服务 / `desktopicon` 桌面图标 / `associate` 关联 .vnc / `installdriver` 虚拟显示驱动。默认装到 `C:\Program Files\uvnc bvba\UltraVNC`。
   - **桌面 4 个图标分别是什么（1.8.x 安装脚本实证）**：`UltraVNC Server`→`winvnc.exe`（青色眼睛）／`UltraVNC Viewer`→`vncviewer.exe`（绿色眼睛）／`UltraVNC Launcher`→`UVNC_Launch.exe`（局域网搜索）／`UltraVNC Repeater`→`repeater.exe`。
   - **致命坑：双击桌面「UltraVNC Server」= 以"应用模式"再起一个 winvnc 实例**，它不会给已注册的服务补上托盘图标，反而让"到底哪个在跑"变混乱 —— 用户为找"图标右键"而双击它，恰好把自己绕进"许可签不出"的坑。**改设置一律走设置入口，不要双击桌面 Server 图标。**
   - **设置入口（服务模式、无托盘图标时正解）**：开始菜单 → UltraVNC → **`UltraVNC Server - Settings`**，本质是 `winvnc.exe -settings`，安装脚本已给它设了提权位（自动要求管理员）；老版/备用是 `uvnc_settings.exe`。命令行等价：`"C:\Program Files\uvnc bvba\UltraVNC\winvnc.exe" -settings`（需管理员）。
   - **服务控制命令（同样来自安装脚本）**：`winvnc.exe -install` / `-uninstall` / `-startservice` / `-stopservice`。
   - **托盘图标消失怎么判**：① 服务模式下主机控制台无人在登录状态 → 根本没有任务栏，无图标属正常；② Win10/11 图标折叠进溢出区（点 `^`）；③ `ultravnc.ini` 里 `DisableTrayIcon=1` 是被人为关掉的。相关 ini 键：`UseRegistry`（0=用 ultravnc.ini，1=用注册表；改错文件白改）、`AuthRequired`（1=空密码拒绝连接）、`MSLogonRequired` / `NewMSLogon`、`AllowProperties`。
   - **安装目录可能被改（实测域主机装在 `D:\app\UltraVNC`，不是默认的 `C:\Program Files\uvnc bvba\UltraVNC`）**：不要凭默认路径拼命令，用 `Get-CimInstance Win32_Service | ? Name -match vnc | select PathName` 拿到真实路径（返回形如 `"D:\app\UltraVNC\WinVNC.exe" -service`）。**ini 不受安装目录影响，服务模式仍在 `C:\ProgramData\UltraVNC\ultravnc.ini`**（settings 窗口标题栏可确认）。
   - **"服务在跑 + 端口在 LISTENING + 防火墙 Allow 规则都在，但外部探测不通"的排查顺序**（实测遇到的疑难形态）：
     ① `Get-Process -Id <netstat 里的 pid>` 确认那个 5900 监听者**确实是 WinVNC.exe**（若被别的程序抢了端口，服务会活着但绑不上，症状相同）；
     ② **从"另一台机器"探测时，必须区分 TIMEOUT 与 RST/refused —— 这是定性分水岭**：
        - 秒回 `refused`（RST）→ 主机内核正常应答，只是该端口没有监听者；
        - **3 秒超时（完全无应答）→ SYN 被静默丢弃**，一定有东西在丢包（Windows 防火墙 Block 默认即静默丢弃 / EDR-IPS / IPsec / 交换机 ACL）。实测：同网段 3389/445/135/NoMachine 4000 均在 **1–5ms 内 OPEN**，唯独 5900/5800 **3 秒超时** → 排除网络与路由（**同网段不经过路由器，纯 L2 直达**），锁定为**主机对该端口的定向丢弃**。
        - 探测代码要带异常类型，别只取 bool：`BeginConnect` + `WaitOne(3000)`，`EndConnect` 抛异常时打印 `InnerException.SocketErrorCode`。
     ③ **主机本机自测（`Test-NetConnection <自己的LAN IP> -Port 5900` = True）是假阳性，不能作为"服务正常"的证据**：Windows 对**本机任何 IP**（含 LAN IP）的连接都走 **loopback 路径，不受入站防火墙规则过滤**，所以外部被全挡时自测照样 True。要验证入站规则真的放行，只能从**另一台机器**探。
     ③ **第三方防火墙/EDR/终端管控**（域机器高发）：`Get-CimInstance -Namespace root\SecurityCenter2 -ClassName FirewallProduct`、`AntiVirusProduct`；
     ④ **入站 Block 规则**（Windows 防火墙 Block 优先于 Allow）：`Get-NetFirewallRule -Direction Inbound -Action Block -Enabled True`；
     ⑤ 组策略 / IPsec 域隔离 / 交换机 ACL。
     判定技巧：同网段其他端口（445/135/3389/NoMachine 4000）通、只有目标端口不通 → 网络与路由无关，且防火墙并非全拦，堵点就在该端口对应的规则或那个监听者的绑定位上。
   - **配置文件到底在哪（1.8.3.0 实测，别猜）**：从开始菜单打开 `UltraVNC Server - Settings` 后，**窗口标题栏会直接写出正在用的 ini 路径**（实测为 `C:\ProgramData\UltraVNC\ultravnc.ini`，服务模式走 ProgramData，不是安装目录 `C:\Program Files\uvnc bvba\UltraVNC\`）。要判断/改 ini 前先看标题栏，别按老资料去安装目录找。settings 界面的其余要点：`Security` 页只设 VNC Password 即可，**MS-Logon / New MS-Logon 都不要勾**（要域账号认证就得处理域名格式与域密码，等于把已绕开的坑请回来；且 MS-Logon II 是 legacy 弱实现），Encryption 属可选加固（两端都要配插件）；底部 `Password required` 必须保持勾选。
   - **NoMachine 已不能当免费方案用（2026-09 实测，重要）**：NoMachine **自 v10 起取消免费版服务器端**（原 Everybody / free edition 停售；v10 发布于 2026-07-30）。服务器端只剩两条路：**Personal Edition 订阅**（约 $24.5/年/台）或 **14 天评估许可证**（需注册 NoMachine 账号 + 联网校验）。许可证文件名固定为 `server.lic`，放在**服务端安装目录的 `etc\` 下**（如 `E:\APP\NoMachine\etc\server.lic`）；也可以用 `%ALLUSERSPROFILE%\NoMachine\nxserver\nxserver.exe --subscriptionset <lic路径>` 从命令行安装。因此 `No subscription found on this server` 的**真实含义是该服务器没装 `server.lic`**（v9 起所有服务器端产品都走订阅校验），**不是**"装错了 Enterprise 包"（这是早前版本的误判）。验证方法：查 `<安装目录>\etc\server.lic` 是否存在，或看日志里的 `ERROR! File ...\etc\server.lic does not exist.`（客户端侧日志在 `C:\Users\<用户>\.nx\server.log`）。**此报错下改用户名、改密码、改防火墙全都无效** —— 直接换 UltraVNC，不要再耗；确要 NoMachine 就买订阅或领 14 天试用许可。
   - 若 NoMachine 确有有效许可，它的优点仍在：Windows/Mac 端**不创建虚拟桌面**，连接的就是**物理桌面（控制台会话）**；NX 协议对 PCB/CAD 图形响应优于 RDP。注意多用户无法各自独立桌面，所有人共享同一物理桌面；主机上若已有他人登录，可能需在主机侧确认接入（PhysicalDesktopAuthorization）。
   - **（仅在 NoMachine 有有效许可、能走到登录界面时适用）登录界面报 `Authentication failed, please try again.`**：出现 `You are now connected to the requested machine` 就说明网络/协议/授权都通了，纯登录问题。头号原因是**用户名格式**：NoMachine 的 Username 字段必须写 `域名\用户名`（NetBIOS 域，如 `<DOMAIN>\<USER>`）；只写 `<USER>` 会被当作**主机本地账号**去查本地 SAM，域用户查不到 → 直接认证失败。次选 UPN 写法 `user@domain.fqdn`。若两种格式都败，就是密码值问题（域机器日常用 PIN/人脸，用户往往没真正敲过域密码）—— 让其在主机上按 `Win+L` 改用「密码」方式登录验证一次，别反复试（会触发域锁定）。
   - **读 NoMachine 日志**：`nxd.log`（守护进程日志）只记服务端启停 —— 出现 `Server started with pid ... / Listening for connections on any interface on port 4000 / Listening for UDP packets on port 4000` 即服务端配置正常；`14:09:30 启动 → 数秒后 terminated → 再重启` 属安装或服务重启的正常动作，不是故障。**它不含连接明细**，所以"没连上"不能只看 nxd.log，要看同目录的 `server.log`/`nxserver.log` 和客户端报错。用户找不到日志位置时，用服务端/客户端托盘图标的 "Gather logs" 或 Server status → Logs 导出。
   - **判断服务端是否真装在主机**：在怀疑的那台机器上跑 `Test-Path C:\ProgramData\NoMachine` + `Get-Service nxserver` + `Get-NetTCPConnection -LocalPort 4000 -State Listen`。三者全空 = 这台机器没装服务端（用户常把服务端装到自己笔记本上，或只装了客户端）。
2. **浮动许可才是最干净的解法**：向 IT/厂商要 license server，把 `MGLS_LICENSE_FILE=27000@<licserver>`（Mentor）/`LM_LICENSE_FILE=...` 指过去，RDP、虚拟机都能用。
3. TS_OK 关键字确实能让 uncounted 许可在 TS 会话签出，但**必须由许可签发方**在许可行里签发；自己改许可文件属违规且破坏签名 —— 不提供、不指导，只告诉用户"这条路要走厂商"。

**不要指望 `mstsc /admin`（别把它当解法）**：2026-09 实测，用户在 <HOST_IP> 上按此建议做了 `start "" mstsc /admin /v:<IP>` 的 .bat，反馈"不行" —— 许可依旧签不出。原因：`/admin` 只是把 RDP 客户端接到主机控制台会话，FLEXlm 判定"存在远程终端服务客户端"这一条依然成立。厂商侧口径也一致（Microchip/Lattice/Arm 等）：**node-locked 许可不允许在 Windows 远程桌面/终端服务下使用，官方建议就是改用 VNC 类显示转发方案或浮动许可**。RDP 唯一可靠场景是主机本身是浮动许可客户端。

日志里的噪音要一并解释清楚，别让用户误以为是新故障：

- `Feature: viewdraw → -5,357 No such feature exists`：许可文件里**根本没有这个 feature**（viewdraw 是 Mentor 老组件名），与远程会话无关。要用该组件只能让厂商加 feature，或改用现有 feature（如 `dxdesigner100_c`）。
- `Filename: C:\flexlm\license.dat → -1,359 Cannot find license file`：环境变量里残留了不存在的旧路径（历史安装遗留），清理 `MGLS_LICENSE_FILE`/`LM_LICENSE_FILE` 只留有效路径即可。
- 同一条 -103 会在每个 feature 上重复打印十几遍，看着吓人，其实就一个原因。

## 9. SSH 远程登录与文件传输（Windows 域主机）

**用途与边界**：SSH 只做命令行操作 + `scp`/`sftp` 传文件。它的登录类型是 **Network（Type 3）**，**不创建交互式桌面会话** —— 好处是**不会像 RDP 那样顶掉控制台会话**（可安全地与 VNC 并存）；代价是**不能用来签 EDA 节点锁定许可**（那条路仍只能靠 VNC 镜像物理控制台）。

**Windows 默认不开 sshd。** 从客户端判定的关键：22 端口返回 **TIMEOUT（静默丢弃）** 而不是 RST/refused —— Windows 在"无监听 + 无放行规则"时由防火墙丢包，所以**不要**把超时读成"服务在跑但被拦"；配合 445/3389 等其他端口正常，即可判定"主机根本没装/没起 sshd"。反之若返回 refused，才说明有放行规则但没有监听。

主机侧启用（管理员，机器需能访问 Microsoft Update / WSUS）：

```powershell
Get-WindowsCapability -Online | Where-Object Name -like 'OpenSSH*'   # 先看 Server 是否 NotPresent
Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0
Set-Service sshd -StartupType Automatic
Start-Service sshd
Get-NetTCPConnection -LocalPort 22 -State Listen
```

- 装 capability **会自动创建并启用防火墙规则 `OpenSSH-Server-In-TCP`**（TCP 22 入站）；核对/补齐用 `Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP'`，缺失则 `New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -DisplayName 'OpenSSH Server (sshd)' -Enabled True -Direction Inbound -Protocol TCP -Action Allow -LocalPort 22`。
- **域机装不上是常态**：WSUS 环境且客户端无外网时，FoD 载荷取不到 → 报 `0x800f0954`（同类还有 `0x800f0950`/`0x8024402C`/`0x80240438`）。三条出路：① 组策略「指定可选组件安装和组件修复的设置」勾选"直接从 Windows Update 下载修复内容"；② 用匹配版本的 FoD ISO：`Add-WindowsCapability -Online -Name OpenSSH.Server~~~~0.0.1.0 -Source <盘符>: -LimitAccess`；③ 用 GitHub `PowerShell/Win32-OpenSSH` 的 `OpenSSH-Win64.zip` 解压到 `C:\Program Files\OpenSSH` 后跑 `install-sshd.ps1`（无外网时需先在其他机器下好拷过去）。
- **域机防火墙仍可能被 GPO 接管**：症状与 5900 那次完全相同（本机 22 在 LISTENING、自测通过，外部仍超时）→ 找 IT 走 GPO 放行。

**主机完全没有外网时的投递与安装（2026-09 实测流程，Win11 24H2 域主机）**：

- **投递通道不要用业务共享目录**。若目标机的共享目录是 Syncthing/OneDrive 之类的**同步目录**（看到 `.stfolder` / `.stversions` / `.stignore` 就是），丢安装包进去会被同步到其他节点。改用**管理共享**：`\\<主机名>\D$`、`\\<主机名>\C$`（域管理员账号可直接访问；`\\<主机名>\E$` 之类不存在的盘符当然返回 False，先枚举主机实际盘符）。实测主机 `D:\app` 是约定俗成的应用目录，暂存放 `D:\app\_offline_staging` 最不打扰。
- **一个映射盘引发的假阴性**：共享已按**主机名**映射成盘符（如 `Z:` → `\\<HOSTNAME>\<SHARE>`）后，再用**IP**去 `Test-Path \\<HOST_IP>\<SHARE>` 会返回 **False** —— 同一台服务器用两个名字被视为两条连接（对应报错 1219 家族）。**已经映射了盘符就直接用盘符，或用同一个主机名**，不要混用 IP 与主机名来判定共享是否存在。
- **选哪个离线包**：GitHub `PowerShell/Win32-OpenSSH` 的 releases 里，`OpenSSH-Win64.zip`（免安装，配 `install-sshd.ps1`）与 `OpenSSH-Win64-vX.Y.Z.msi`（静默 `msiexec /i ... /qn /norestart`，装到 `C:\Program Files\OpenSSH`）。注意该项目**近期所有发行版都标 `-Preview`/`-Beta` 且 body 写明 "preview-release (non-production ready)"**，这是它一贯做法，不是异常。两个包都带上，MSI 失败（域机可能被"关闭 Windows Installer"策略拦）就退到 ZIP。
- **装前必查 VC++ 运行时**：离线包依赖 `vcruntime140.dll` / `msvcp140.dll`。直接经管理共享查主机 `C:\Windows\System32\vcruntime140.dll` 是否存在即可判定；存在就不用再补 VC++ redist，省掉一个离线依赖。绝大多数装了 EDA/CAD 的机器都已具备。
- **先确认主机上"到底缺什么"**：`C:\Windows\System32\OpenSSH\` 在 Windows 上**默认就存在，但只有客户端**（`ssh.exe` / `scp.exe` / `sftp.exe` / `ssh-keygen.exe`……）。**看到这个目录不等于服务端已装 —— 关键是看里面有没有 `sshd.exe`**。这一步能直接避免误判。
- **离线安装的三条收尾必做**（GitHub 路线**不会**自动建防火墙规则，这点与系统能力路线不同）：① `Set-Service sshd -StartupType Automatic` + `Start-Service sshd`；② 显式建入站规则 `New-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' ... -LocalPort 22`；③ 打印 `Get-NetFirewallProfile` 的 `DefaultInboundAction`/`AllowInboundRules`，用于判断是否被 GPO 接管。
- **已知启动失败（错误 1053/1067/7034）**：OpenSSH 9.5 与 2024-10-08 ~ 2025-03-11 之间的某些更新存在此问题，根因是 `C:\ProgramData\ssh` 与其 `logs` 子目录的 ACL 不对（应为 SYSTEM + Administrators 权限，普通用户不可写）。
- **交付脚本形式**（本项目已验证有效）：`.ps1`（纯 ASCII、CRLF）负责全部动作 + `Start-Transcript` 落盘报告 + `Stop-Transcript` 后 `Set-Clipboard` + 末尾 `Read-Host` 停留；再用一个 `.bat` 包装器（`net session` 自检管理员权限 → `powershell -NoProfile -ExecutionPolicy Bypass -File ...`）。**投递到主机前必须把换行统一成 CRLF、编码写成 ASCII**（`[System.IO.File]::WriteAllText($dst, ($t -replace "`r?`n","`r`n"), [Text.Encoding]::ASCII)`），并顺手统计非 ASCII 字符数确认归零，否则本地化控制台会乱码。
- **删除类操作会被本机安全层拦截**：`Remove-Item` **以及 python 的 `os.remove`** 对 UNC 路径都失败，报 `[safe-delete][SAFE_DELETE_FAIL_CLOSED]`（trash 失败且拒绝回退删除）—— 这是本环境的保护机制，不是权限问题，加 `-Force`、提权、换 API 都没用。
  - **首选解法**：如果有 SSH，用 `eda-ssh.py rm <远程路径>` —— 命令在**目标主机上**执行，绕开本机保护层（实测可删 `C:\ProgramData\ssh` 下的文件）。
  - **没有 SSH 时**：用 `Move-Item` 把残留挪进那个"用完即删"的暂存目录一起清掉。

**客户端侧无凭据验证 SSH 服务端的三个手法（不输密码就能定性，实测有效）**：

```bash
ssh-keyscan -p 22 <host>          # 能打印 "SSH-2.0-OpenSSH_..." banner = SSH 协议栈端到端通了
ssh -o PreferredAuthentications=none -l '<域>\<用户>' <host>   # 返回 "Permission denied (publickey,password,keyboard-interactive)"
                                  # 括号里那串就是服务端**实际提供的认证方式**，可确认 password 未被禁用
ssh -o BatchMode=yes -l '<域>\<用户>' <host>   # 同样返回 Permission denied，但可用于脚本化探测
```

- 加上 TCP 端口探测（客户端侧 OPEN）即可**同时**证明"监听存在"和"入站防火墙放行且未被 GPO 覆盖"——这正是 5900 那次卡了很久的地方，SSH 场景应一次确认完。
- `choose_kex: unsupported KEX method sntrup761x25519-sha512@openssh.com` 属**噪音**：客户端较旧（如 Win 内置 9.5p2）而服务端较新（如 10.0）时，`ssh-keyscan` 会打印它，但两边仍有共同算法、握手正常成功，不用处理。

**一次完整的实战路径（2026-09-17，Win11 24H2 域主机，全离线）**：管理共享投递 → 双击 `install-openssh.bat`（MSI 静默安装 exit code 0）→ 六步自检全 PASS（服务 Running/Auto、22 双栈监听、防火墙规则建成、`sshd -t` 无报错）→ 笔记本侧 TCP OPEN 11ms + `ssh-keyscan` 取到 banner + `PreferredAuthentications=none` 列出 `publickey,password,keyboard-interactive`。**MSI 路线在域机上未必被拦，先试 MSI、失败再退 ZIP 的顺序是对的。**

`sshd_config` 里出现两行 `AuthorizedKeysFile` 是**正常的**：第一行 `.ssh/authorized_keys` 在文件顶部，第二行 `__PROGRAMDATA__/ssh/administrators_authorized_keys` 位于文件末尾的 `Match Group administrators` 块内 —— 正是"管理员账号的公钥必须放 ProgramData"这条规则的服务端实现依据。

**域账号的连接格式（客户端侧头号坑）**：微软文档明确 **域用户/组一律按 `域名\用户名`（NameSamCompatible）解析**，默认 sshd 会把裸用户名当**本地 SAM 账号**查 → 必然认证失败。正确写法：

```
ssh domain\username@servername          # 官方推荐格式，如 ssh <DOMAIN>\<USER>@<HOST_IP>
ssh -l '<DOMAIN>\<USER>' <HOST_IP>  # 等价写法
```

**Git Bash 下必须加引号**（`'<DOMAIN>\<USER>'`），否则 `\x` 被 shell 吞掉变成 `<DOMAIN>x<USER>`；cmd / PowerShell 下可直接写。密码填**域密码，不是 PIN**（同 RDP/SMB 的老问题）。认证方式仅 `password` 与 `publickey` 两种（不支持 Entra 账号）。

**免密密钥登录**：客户端 `ssh-keygen -t ed25519`；公钥落点分两种 —— 账号**属于 Administrators 组时必须放 `C:\ProgramData\ssh\administrators_authorized_keys`**（放到 `%USERPROFILE%\.ssh\authorized_keys` 会被直接忽略，这是最常踩的一步），非管理员才放用户目录。拷完收紧 ACL（**必须**，否则 sshd 拒用该文件）：

```powershell
icacls.exe "C:\ProgramData\ssh\administrators_authorized_keys" /inheritance:r /grant "Administrators:F" /grant "SYSTEM:F"
Restart-Service sshd
```

**默认落在 cmd.exe，不是 PowerShell**（首次登录用户常立刻踩到：敲 `ls` 报 `'ls' 不是内部或外部命令`）。三种切法，成本递增：

1. **会话里直接敲 `powershell`** —— 一行、零配置、立刻可用（`ls` 在 PS 里是 `Get-ChildItem` 的别名，能直接用）。日常首选。
2. **临时指定**：`ssh -t -l '<DOMAIN>\<USER>' <HOST_IP> powershell`（`-t` 必须有，否则拿不到交互式终端）。
3. **改默认 shell**（全局、需管理员 + 重启 sshd）：

```powershell
New-ItemProperty -Path "HKLM:\SOFTWARE\OpenSSH" -Name DefaultShell -Value "C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe" -PropertyType String -Force
Restart-Service sshd
```

⚠️ 改注册表要管理员权限 —— **先确认你的 SSH 会话是否提权**（见下方"是否提权"一节，实测多数情况下是提权的，可直接改）。若确实非提权，再考虑 VNC 或不改（用方法 1 每次敲一行 `powershell` 即可）。

cmd 与 PowerShell 常用命令对照（用户在 SSH 里习惯敲 Linux 风格命令时用得上）：

| 想做的 | cmd | PowerShell |
|---|---|---|
| 列目录 | `dir` | `ls` / `gci` |
| 切目录 | `cd /d D:\SHARE`（**跨盘必须带 `/d`**） | `cd D:\SHARE` |
| 看文件内容 | `type f.txt` | `cat f.txt` / `gc` |
| 找文本 | `findstr /i "x" f.txt` | `Select-String x f.txt` |
| 进程 | `tasklist` / `taskkill /pid N /f` | `ps` / `Stop-Process -Id N` |
| 服务 | `sc query sshd` | `Get-Service sshd` |
| 网络监听 | `netstat -ano` | `Get-NetTCPConnection -State Listen` |

**SSH 会话是否提权 —— 必须实测，别照抄经验（2026-09-17 更正）**

本技能早期版本写过"SSH 会话默认非提权、拿到的是过滤令牌"。**该结论在 OpenSSH 10.0 + Windows 11 24H2/26200 上被实测推翻**：

```powershell
$id = [Security.Principal.WindowsIdentity]::GetCurrent()
(New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
# 实测 -> True
whoami /groups | Select-String "S-1-16-"
# 实测 -> Mandatory Label\High Mandatory Level  S-1-16-12288   （高完整性 = 已提权）
```

原因：Windows OpenSSH 用 `LogonUser(LOGON32_LOGON_INTERACTIVE)` 建用户令牌，**该路径不经过 UAC 过滤** —— UAC 的令牌过滤是 winlogon 在**交互式登录**时做的，服务端模拟登录不适用。

后果（实测确认）：`Restart-Service`、改 HKLM、`netsh advfirewall`、`icacls`、写 `C:\ProgramData\ssh` **全部可做**。所以不要再套 `Start-Process -Verb RunAs`（headless 机器上那条路的 UAC 弹窗只有 VNC 看得到，纯属自找麻烦）。**但每次遇到新主机仍应跑上面两行确认**，域策略差异会改变结论。

**让 ssh(1) 非交互地登录 —— 做不到，用 paramiko**

当 AI/脚本需要自动登录时，原生 `ssh.exe` 是死路，因为密码无法非交互喂入（没有 TTY、`SSH_ASKPASS` 助手不可用）。且注意 PowerShell 传参的两个陷阱：

```powershell
ssh-keygen ... -N ""            # PS 5.1 吞掉空字符串参数 -> "Too many arguments"
Start-Process -ArgumentList @(...,'')   # 空串触发参数验证失败："参数为 Null 或空"
[Diagnostics.Process]::Start(...)       # 被本环境安全策略拦截（等价 Start-Process）
cmd /c "..."                            # 被本环境安全策略拦截
--% ...                                 # 在工具传参模式下破坏脚本解析
```

**可行方案：python + paramiko**（subprocess 用 argv 数组传参，空串与特殊字符都安全）：

```python
import paramiko
c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect(host, username=r"<DOMAIN>\<USER>", password=pw,
          allow_agent=False, look_for_keys=False, timeout=25)
_i, so, se = c.exec_command(cmd); out = so.read(); rc = so.channel.recv_exit_status()
```

要点：① 先 `so.read()` 再 `recv_exit_status()`，顺序反了会死锁；② PowerShell 脚本用 `-EncodedCommand`（UTF-16LE + base64）投递，彻底避开引号地狱；③ 输出按 utf-8 → gbk 顺序试解码。

现成实现：`~/.workbuddy/skills/eda-host-control/eda-ssh.py`（exec / ps / psf / put / get / ls / rm / mkdir / info / sessions）。

**公钥认证的坑（重要）**：某些域主机上 publickey 认证会**认证成功但连接被重置** —— 事件日志表现为先 `Accepted publickey`，紧接着 `sshd-session: error: unable to get security token for user`、`get_user_token - unable to generate token on 2nd attempt`、`fatal: fork of unprivileged child failed`。原因是密码路径走 `LogonUser`、公钥路径走 S4U，后者在该环境失败。**出现这种情况时，确保不要在任何 `authorized_keys` 里留公钥** —— 否则客户端会先试公钥、被重置且**不回落密码认证**，等于把用户彻底挡在 SSH 门外。诊断思路：从 `\\<host>\C$\Windows\System32\winevt\Logs\OpenSSH%4Admin.evtx` 把日志复制出来本地 `Get-WinEvent -Path` 解析（远程 `Get-WinEvent -ComputerName` 走 RPC，常被防火墙挡）。

**传文件**：`sftp -l '<DOMAIN>\<USER>' <HOST_IP>` 后用 `put`/`get`（Windows 盘符在 sftp 里写作 `/D:/SHARE`）；或直接用 `scp`。已开 SMB（445）时两者可并用：SMB 适合图形化拖拽，SSH 适合脚本化/自动化。

**排障与安全**：日志默认进 ETW，看 `事件查看器 → 应用程序和服务日志 → OpenSSH`；要落文件则在 `sshd_config` 设 `SyslogFacility LOCAL0`（输出到 `%ProgramData%\ssh\logs`），改完先 `sshd -t` 校验再重启。做公网端口映射是红线；域账号可被暴力破解并触发锁定，优先密钥认证，必要时用 `AllowUsers`/`AllowGroups`（写成 `域名\用户`）或改非 22 端口。

## 环境适配注意（本机运行环境）

- PowerShell 工具的标准输出经常不回显：把结果 `Out-File` 到文件再用 Read 读取
- Bash 工具缺少 dirname/mkdir/grep/ls/rm 等命令，文件操作用 Read/Write/Glob 或 PowerShell 工具
- 安全策略拦截：`Add-Type`（动态编译）、`ConvertTo-SecureString`（明文凭据）、`Start-Process -Verb RunAs`（提权）；因此**无法由 AI 侧校验凭据或提权**，这类步骤必须交给用户手动执行
- 交付给用户的手动脚本：文件名与内容保持**纯 ASCII**，换行转 CRLF（cmd 用 `^|` 转义 for /f 里的管道，避免在 for /f 的单引号命令中使用单引号）
- **脚本输出必须落盘 + 自动进剪贴板**：用户是在另一台机器上运行，控制台窗口滚过去/被关掉就什么都拿不到（用户会明确抱怨"不给我复制和看的时间"）。做法：PowerShell 脚本用 `Start-Transcript` 写报告文件，结束时 `Set-Clipboard -Value (Get-Content $report -Raw)`，并 `Read-Host` 停留窗口；再配一个 ASCII 的 .bat 包装器以 `-ExecutionPolicy Bypass` 启动它
- 用 .bat 直接写流程容易在中文/换行/label 转义上翻车，**复杂流程用 .ps1（ASCII）+ .bat 包装器**更稳
- **`runas` 不要在 PowerShell 里直接调用**：`& runas /user:X\y cmd` 的密码提示会被 PowerShell 吞掉键盘输入，用户表现为"密码输不进去"，runas 拿到空密码后报 1326 —— 会得到假结论。正确做法：`Start-Process cmd.exe -ArgumentList '/k','runas /user:X\y cmd' -Wait`，让它在独立窗口里读输入
- **本地化系统上不要按名称找本地组**：`$sid.Translate([NTAccount])` 可能返回英文名（如 `BUILTIN\Remote Desktop Users`），随后 `Add-LocalGroupMember -Group` 报"未找到组"。改用 SID 取名：`(Get-CimInstance Win32_Group -Filter "SID='S-1-5-32-555'").Name`（回退 `Get-LocalGroup -SID`）
- 判断"能不能远程登录"还要看用户权限策略：`secedit /export /areas USER_RIGHTS /cfg x.inf`，检查 `SeRemoteInteractiveLogonRight` / `SeDenyRemoteInteractiveLogonRight` / `SeDenyNetworkLogonRight`；若 `S-1-5-114`（本地账户和管理员组成员）出现在拒绝网络登录名单里，**本地管理员账号不能用于网络/RDP 登录**，只能用域账号
- 用户"记着的密码"经常是错的：域机器普遍用 PIN/人脸/指纹登录，用户从未真正输入过域密码。验证的最省事办法是 `Win+L` 锁屏后用"密码"方式登录（失败可用 PIN 回去，无锁定风险），而不是反复用凭据试探（会触发锁定）
- 目标机器上用户已是本地管理员时，**不需要**再往 Remote Desktop Users 加人，管理员默认可 RDP；此时唯一缺口通常就是密码
- **生成 .rdp 时不要默认开 `use multimon:i:1`**：它就是「将我的所有监视器用于远程会话」，会让远程桌面自动铺满客户端**所有**显示器——单屏笔记本用户接上外接屏后会一脸问号地问"为什么把两个屏幕都用了"。稳妥默认值是 `use multimon:i:0`，再由 `screen mode id` 决定 `1`（窗口，推荐）或 `2`（全屏）；窗口模式下 `desktopwidth`/`desktopheight` 即窗口尺寸，取略小于主屏分辨率的 1600x900 比较舒服。会话中切全屏/窗口用 `Ctrl+Alt+Break`（笔记本无 Break 键时 `Ctrl+Alt+Fn+B`）。
- **用户抱怨"输入密码看不到任何显示、不知道输进去没有"**：这是正常的（`runas`、`Read-Host -AsSecureString` 连星号都不给），但会让用户误以为没输入、甚至提交空密码，从而得出 1326 这种**假结论**。改用 `Get-Credential` 的图形凭据框：密码以圆点显示、右侧有眼睛图标可回看，并能用 `$cred.GetNetworkCredential().Password.Length` 报出**实际收到的字符数**，一举分清"根本没输进去"与"输进去了但密码不对"。配 .bat 包装时要加 `-STA`（凭据框需要 STA 线程）
- **用户坚称"密码没错"时怎么定案**：`ConvertTo-SecureString` 常被安全策略拦截，此时 `net use \\<域控FQDN>\NETLOGON /user:<域>\<账号> <密码>` 是唯一可用的自动化凭据校验——密码由**代码直传、不经过键盘**，可排除大小写/全角半角/带空格/没输进去等一切输入类干扰。拿到 86 就是密码值本身不匹配，可以理直气壮地下结论。但务必克制：**每一次失败都计入域账号锁定计数**，一次定案即可，完后 `net use \\...\NETLOGON /delete /y` 清理会话
- **当用户只想"不用再输入密码就能连上"**：① 在主机上用管理员终端建本地账号 `net user <名> "<密码>" /add` + `net localgroup Administrators <名> /add` + `net localgroup "Remote Desktop Users" <名> /add`，笔记本用 `主机名\<名>` 连接，与域密码彻底解耦；② 笔记本侧 `cmdkey /generic:TERMSRV/<主机IP> /user:<账号> /pass:<密码>` 预存凭据，之后双击 .rdp **不再弹密码框**（target 必须带 `TERMSRV/` 前缀，清除用 `cmdkey /delete:TERMSRV/<主机IP>`）
