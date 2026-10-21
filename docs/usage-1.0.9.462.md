# xiaomi-nas-root.py — 小米智能存储一键 Root（固件 1.0.9.x）

参照 [kid0114/xiaomi-nas-ssh-root-tool](https://github.com/kid0114/xiaomi-nas-ssh-root-tool) 的使用形态重构的**新攻击链一键脚本**（原漏洞已在 1.0.9.462 被修补）。攻击链各环节经真机逐一验证；脚本本身做过函数级真机回归，端到端完整链未在裸机整体重放（验证机已处于 root 态），详见 README 的验证声明。

## 前置条件

1. 本机已安装「小米智能存储」桌面端并**登录过目标 NAS**（客户端证书由 App 生成，脚本自动查找）
2. Python 3.9+（**仅标准库**：触发器机器码为固定字节、已内置，无需 pip/Conda/Homebrew）。
   先跑本地预检（不联系 NAS）：`python3 scripts/xiaomi-nas-root.py --preflight`

## 用法

```sh
python3 scripts/xiaomi-nas-root.py --preflight        # 本机前置检查，不联系 NAS
python3 scripts/xiaomi-nas-root.py <NAS_IP>
# 可选参数:
#   --uid <米家数字UID>         指定米家数字 UID（默认从证书解析）
#   --cert-dir <路径>          指定证书目录（默认自动搜索 minasCert）
#   --shell-port 2323          root shell 端口
```

## 敏感信息处理

- **证书目录**：默认搜索 `~/AppData/Local/minasCert` 等常见路径，找不到时 `--cert-dir` 手动指定
- **WebDAV 密码**：脚本经 mTLS 调 `get_pool_info` 现场获取，**不落盘不显示**
- **SSH 密钥**：`~/.ssh/id_ed25519_minas` 不存在则现场生成，私钥只写本机

## 攻击链（3 步，约 2 分钟）

| 步骤 | 动作 | 原理 |
|---|---|---|
| ① | mTLS 证书 → get_pool_info | 拿 WebDAV 凭据（端口 5000） |
| ② | MKCOL 换行目录 + add_samba_account | `smb_mgr.sh` uci heredoc 注入 → `force user=root` 的 `/etc` SMB 共享 |
| ③ | ld.so.preload + 触发 .so | 内置固定 aarch64 机器码的 .so，cron 每分钟 root 任务触发 → socat root shell + dropbear |

## 产物

- **root shell**：`nc <IP> 2323`（或 `wsl bash -c 'nc <IP> 2323'`）
- **SSH 22**：`ssh -i ~/.ssh/id_ed25519_minas root@<IP>`（公钥注入；注意本固件 dropbear 有 exec bug——认证可用但命令无输出）

**不做持久化**：本脚本只获取本机会话——无挂载钩子、无开机自启。设备重启后 **socat root shell 与 dropbear 服务消失**，需要时重新运行即可（全程约 2 分钟）；**有意保留**：两处 SSH 公钥（`/etc/dropbear/authorized_keys`、`/home/rootx/.ssh/authorized_keys`）与 passwd 中 root home 修改——密钥认证重启后仍可用，如需彻底清除请删除这两个文件并把 home 改回 `/root`。是否及如何做持久化由使用者自行决定，不属于本脚本范围。

## 清理行为

成功与失败路径都会自动执行清理（经 root shell）：解除 preload 布防、移除注入共享与临时目录、清除注入的 samba 系统账号行。root shell 不可达时自动降级为两种方式：把完整清理命令写入本地 `cleanup-<IP>.sh`（可经 SMB root 共享上传执行），并打印手动提示。

## 注意事项

- 仅用于**自有设备**
- 步骤②会临时创建带注入的 samba 共享（`u<uid>_2`），脚本自动清理（见上节"清理行为"）
- 已知坑：`add_samba_account` 可能返回 2502（restart 失败）——uci 已写入，脚本自动走 `set_samba_account` 修复路径，属正常现象
- SMB 写入在 macOS 用系统自带 `mount_smbfs`；无需 cifs-utils/Conda/Homebrew

## 已知坑

1. socat 会话内 systemctl restart 会 D-Bus 死锁——用 cron 一次性任务执行 systemctl 类操作
2. 若 2323 未开但 `/etc/.rootpwn.log` 有 `uid=0` 记录，说明 cron 已触发、仅设备缺 socat；
   公钥已注入，可走 SSH 认证通道，不要重复布防 preload
