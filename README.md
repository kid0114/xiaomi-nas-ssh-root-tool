# 小米 NAS（RP05）固件 1.0.9.462 一键 Root — macOS 版

面向固件 1.0.9.462 的小米 NAS 机主：完整的 root SSH 恢复包，包含可直接运行的脚本和
Pi/Grok coding-agent skill。已在自有设备上实测验证（2026-10-20）。

## 本次更新内容

- 新增 `scripts/xiaomi-nas-root.py`：固件 1.0.9.x UCI heredoc 注入一键 root 工具
  （含 macOS 适配：`mount_smbfs`、`etc-<uid>` 共享名回退、`/bin/sh` payload、
  不依赖 `nc`/`timeout`/`bash` 的 socket 清理）
- **零第三方依赖**：触发器 aarch64 机器码为固定字节、已内置到脚本，runner 只用
  Python 标准库——不需要 Conda/Homebrew/pip 包。新增 `--preflight` 本地预检
  （Python 版本、系统工具、触发器自检、证书目录），跑之前不联系 NAS 即可排障
- 重写 `skill/SKILL.md`：从实际验证的 minas-reroot 恢复流程提炼的完整操作指南
  （重启后如何恢复 root、持久化、注意事项、总结）
- 更新 `skill/references/reboot-persistence.md`：双 hook 持久化（97 dropbear-unpatch
  + 98 ssh-persistence）和重启验收流程
- 新增 `skill/references/firmware-1.0.9.462.md`：新固件恢复链的完整参考
- 新增 `docs/usage-1.0.9.462.md`、`docs/technical-review-1.0.9.462.md`：工具使用说明
  和技术复盘

## 分支说明

| 分支 | 适用固件 | 方法 | 状态 |
|---|---|---|---|
| `main` | ≤1.0.8.x | WebDAV 视频路径注入（bash） | 已验证，1.0.9.462 已修补 |
| `persistence-research` | ≤1.0.8.x | 同上 + 重启持久化研究 | 已验证 |
| `python-cross-platform` | ≤1.0.8.x | 同上（Python，Windows 已验证） | 已验证 |
| **`firmware-1.0.9.462-macos`** | **1.0.9.462** | **UCI heredoc 注入（Python，macOS）** | **本分支，实测验证** |

两方法不互通：旧分支脚本在 1.0.9.462 上会失败，本分支脚本也未在旧固件上测试。

## 适用范围

- **固件**：1.0.9.462（其他 1.0.9.x 构建未经测试，先验证再用）
- **操作主机**：仅 macOS（Windows/Linux 未实现）
- **NAS**：RP05，「小米智能存储」桌面端已登录过目标设备
- **网络**：家用 LAN 或机主控制的私有隧道

## 快速开始

```sh
# 1. 本机预检（不联系 NAS；任何 Python 3.9+ 均可，无需 Conda/Homebrew/pip 包）
python3 scripts/xiaomi-nas-root.py --preflight

# 2. 确认 root 确实不可用（返回 0 说明 root 正常，不要重跑）
ssh -o BatchMode=yes <你的SSH别名或root@IP> id -u

# 3. 运行（从仓库根目录）
python3 scripts/xiaomi-nas-root.py <NAS_IP>

# 4. 恢复 SSH 命令执行 + 持久化（详见 skill/references/）
```

## 注意事项（必读）

- 仅用于你自己的设备；不要在未授权设备上运行
- 本 skill 只做获取 root 与安全 SSH，不检查/管理磁盘媒体共享映射设置
- 工具会临时布防 `/etc/ld.so.preload`；成功/失败都会自动清理，异常中断需手动解除
- 重启后 socat 和 dropbear 修复消失，但 SSH 公钥保留；装好双 hook 后重启自动恢复
- 不要尝试 `mitee_tool rpmb set ssh_en`（TEE 挑战门控，必失败）或 sysota 降级（RPMB 门控）
- 输出日志可能含临时 SMB 凭据，分享前务必脱敏

## 文档

| 文件 | 内容 |
|---|---|
| `skill/SKILL.md` | Agent 操作主指南（何时适用 / 步骤 / 重启恢复 / 持久化 / 注意事项 / 总结） |
| `skill/references/firmware-1.0.9.462.md` | 恢复链详细参考（环境准备 / 停止条件 / 各阶段验证 / 清理验收） |
| `skill/references/reboot-persistence.md` | 重启持久化（双 hook / 验收测试 / 回滚） |
| `docs/usage-1.0.9.462.md` | 工具命令行用法 |
| `docs/technical-review-1.0.9.462.md` | 漏洞链技术复盘 |

## 安全

- 不公开：设备 IP、UID、序列号、WebDAV/SMB 密码、token、证书私钥、SSH 私钥
- 本仓库不包含任何真实私钥、密码、token 或抓包文件
- 运行时生成的 `cleanup-<ip>.sh` 和 `pwn_trigger.so` 已在 `.gitignore` 中排除
