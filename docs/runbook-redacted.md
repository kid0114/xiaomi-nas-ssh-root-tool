# 小米 NAS WebDAV 注入开启 SSH 跑通

日期：2026-09-01

## 结论

- 通过小米智能存储 app 生成的客户端证书可访问 LuCI 正确接口：`/cgi-bin/luci/filemgr/get_pool_info`。
- 错误路径 `/cgi-bin/luci/admin/filemgr/get_pool_info` 会返回 403。
- WebDAV 凭据已可用于 5000 端口上传。
- WebDAV 路径注入可触发 NAS 侧 root 命令执行。
- 已写入 dropbear `authorized_keys`、修改 root shell、启动 dropbear，最终 root SSH 验证成功。

## 敏感信息处理

- NAS IP、本机 IP、WebDAV 密码、账号密码、证书私钥、SSH 私钥均不写入本文。
- WebDAV 凭据本机保存：`/tmp/.wdav_creds`，权限 600。
- SSH 私钥本机保存：`/tmp/nas-root-key`，权限 600。

## 脚本

- 监控脚本：`~/issuebase/mi-nas/scripts/watch-xiaomi-nas-app.sh`
- 一体化执行脚本：`~/issuebase/mi-nas/scripts/enable-xiaomi-nas-ssh.sh`

运行方式：

```sh
NAS_IP='<NAS_IP>' ~/issuebase/mi-nas/scripts/enable-xiaomi-nas-ssh.sh
```

脚本每个阶段打印 `[step]` 和 `OK/WARN/FAIL`。

## 实跑阶段结果

- 证书目录识别成功。
- 443/5000 可达，22 初始未开放。
- WebDAV PUT 返回成功。
- sleep 注入耗时约 3 秒，说明注入生效。
- root id 回连确认：`uid=0(root)`。
- 公钥与脚本上传成功。
- `KEY_OK` 出现，authorized_keys 写入成功。
- `SHELL_OK` 出现，root shell 修改成功。
- dropbear 启动后 SSH root `id` 返回 root。

## 注意

- `/runtime` 在本次环境中不存在，setshell 脚本已改成先 `mkdir -p /runtime`，失败则退回 `/tmp/passwd.bak-pre-ssh`。
- 后续复用时必须运行时传入当前机主自己的 NAS IP，不要硬编码。
