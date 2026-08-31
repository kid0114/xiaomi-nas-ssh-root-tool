# 打开小米 NAS SSH/root 权限的 Skill

这是一个用于打开小米 NAS SSH/root 权限的 Pi/Grok coding-agent skill，同时附带可独立运行的脚本和脱敏流程文档。

这个仓库用于整理并自动化“小米 NAS 稳定开启 SSH/root”的本地恢复流程：通过机主已经登录的小米智能存储桌面端生成的客户端证书，获取 WebDAV 凭据，再利用 WebDAV 路径注入通道在 NAS 本机执行必要的 SSH 启用步骤。

## 用途

适用于这些场景：

- 自己的小米 NAS 默认关闭 SSH，需要开启 root SSH 便于维护；
- 已能使用“小米智能存储”桌面 App 正常登录并访问 NAS；
- 需要把手工截图教程整理成可重复执行的脚本；
- 需要监控 App 登录/访问过程，定位证书、WebDAV、动态端口等运行态信息。

本仓库包含：

- `scripts/watch-xiaomi-nas-app.sh`：监控 App 进程、连接、证书/缓存文件变化，并抓取到 NAS 的加密流量元数据；
- `scripts/enable-xiaomi-nas-ssh.sh`：一步步执行 SSH 开启流程，每步打印 `OK/WARN/FAIL`；
- `skill/SKILL.md`：给 Pi/Grok 类 coding agent 使用的操作说明；
- `docs/runbook-redacted.md`：一次已脱敏的跑通记录。

## 安全和授权范围

- 仅用于你拥有或明确被授权管理的 NAS。
- 不要公开或粘贴：设备 IP、WebDAV 密码、App 账号密码、token、证书私钥、SSH 私钥。
- 脚本要求运行时传入当前机主自己的 NAS IP；仓库里不硬编码具体 IP。
- 在监视脚本运行期间登录“小米智能存储”App 时，最好使用机主自己的小米账号/手机号和密码完成授权，不要使用他人账号。
- 本仓库不包含任何真实私钥、密码、token 或抓包文件。

## 流程概览

1. 在本机安装并打开“小米智能存储”App。
2. 如需定位证书/运行态信息，先启动监视脚本；监视期间重新登录 App，并最好使用机主自己的小米账号/手机号和密码完成授权。
3. 监视脚本会记录 App 进程、NAS 连接、证书/缓存变化和加密流量元数据，不保存明文账号密码。
4. 一体化脚本读取 App 生成的客户端证书，通过正确 LuCI API 获取 WebDAV 凭据。
5. 脚本用 WebDAV 验证上传能力，然后通过 WebDAV 路径注入做低风险测试。
6. 确认 NAS 侧命令执行身份为 root 后，上传 SSH 公钥和辅助脚本。
7. 脚本写入 dropbear `authorized_keys`、调整 root shell、启动 dropbear，并验证 root SSH。
8. 最后把 SSH 私钥复制到本机 `~/.ssh/`，写入 `ssh xiaomi-nas` / `ssh minas` 别名。

## 快速使用

```bash
# 1. 可选：监控 App 登录/访问过程，用于定位证书和连接信息
NAS_IP='<your-nas-ip>' ./scripts/watch-xiaomi-nas-app.sh

# 2. 执行 SSH 开启流程
NAS_IP='<your-nas-ip>' ./scripts/enable-xiaomi-nas-ssh.sh
```

执行脚本会依次验证：

1. 证书目录和 curl；
2. NAS 443/5000/22 端口状态；
3. WebDAV 凭据；
4. WebDAV 上传；
5. 本机监听器；
6. WebDAV 路径注入延迟测试；
7. NAS 侧 root 命令执行；
8. SSH key 上传；
9. `authorized_keys` 写入；
10. root shell 修改；
11. dropbear/SSH 启动；
12. root SSH 登录验证；
13. 写入本机 SSH config 别名。

## 本地生成/保存的敏感文件

运行过程中会在本机保存：

- WebDAV 凭据：`/tmp/.wdav_creds`，权限 `600`；
- 临时 SSH 私钥：`/tmp/nas-root-key`；
- 持久 SSH 私钥：`~/.ssh/id_ed25519_xiaomi_nas`；
- SSH config 别名：`ssh xiaomi-nas` 或 `ssh minas`。

请妥善保管这些文件，不要提交到 Git。`.gitignore` 已默认排除常见敏感文件。