# 设备码授权：方向图与常见走错路

用户在这一步搞反方向的概率很高，**别只发链接和码，要同时把方向讲清**。

## 正确流程

```
        本机（agent 侧）                        用户侧（浏览器）
   ─────────────────────────              ──────────────────────────
   1. gh auth login --web
      └→ 生成 8 位设备码
         例如 5147-C59C
      └→ 自动复制到剪贴板
                      │
                      └──────────→        2. 打开
                                             github.com/login/device
                                          3. Ctrl+V 粘贴码
                                          4. Continue
                                          5. Authorize github
                      ←──────────
   6. 后台进程收到令牌
      └→ 加密存入 Windows 凭据管理器（keyring）
      └→ 完成，不再需要用户操作
```

**一句话记忆**：**码从本机出来，往网页里输。** 手机不参与。

## 用户常见的三条走错路

| 走错路 | 用户看到什么 | 怎么纠正 |
|---|---|---|
| 去 GitHub 手机 App 等弹码 | 什么都没弹 | 手机 App 弹码是**另一种**流程（GitHub Mobile 登录确认），与设备码授权无关。改在电脑浏览器打开 `github.com/login/device` |
| 进到 Settings → SSH keys | 一个公钥列表（可能是空的） | 那是给 SSH 密钥用的，不是设备授权页。设备授权页在**设置之外** |
| 以为码要由 GitHub 发给自己 | 找不到码的来源 | 码是**本机 CLI 打印出来的**，页面提示里的 "app or on the device you're signing in to" 指的就是本机 |

## 页面文案的陷阱

设备页会显示：

> Enter the code displayed in the app or on the device you're signing in to.
> Never use a code sent by someone else.

- 第一句的「app / device」= **本机 CLI**，不是手机
- 第二句是防钓鱼的通用警告，**不适用于本人操作自己的码** —— 用户容易因此犹豫

## 操作要点

- 码有效期约 **15 分钟**。过期直接重新生成，不要引导用户去研究过期页面
- 重新生成前先停掉旧的后台进程（旧进程会一直挂着）
- 用户报"好了"之后再验证，别提前假设成功
- **验证方式**：`gh auth status` 返回 0 且能读到 `account <login>`
