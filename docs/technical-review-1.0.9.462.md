# 小米 NAS（RP05）固件 1.0.9.462 开启 SSH 与插件运维实录

> 设备：小米智能存储 RP05 · 固件 1.0.9.462.F24C26D24-REL · 全程自有设备真机验证
> 背景：旧工具依赖的 WebDAV 视频分段 shell 注入已在本固件被修补，SSH 与手动插件随固件升级被清空。本文记录从零重建利用链、开启 SSH、重部署插件并处理连带事故的全过程。
> 旧工具参考：kid0114 的 [xiaomi-nas-ssh-root-tool](https://github.com/kid0114/xiaomi-nas-ssh-root-tool)（本链在其链路失效后重建，向先行者致谢）
> 完成日期：2026-10-16

**免责声明**：本文仅描述对自有设备的授权安全研究。请勿对他人设备实施文中任何技术。

---

## 〇、旧漏洞是怎么被堵上的

旧利用链是「桌面端登录证书 → LuCI API 拿 WebDAV 凭据 → WebDAV 视频分段路径注入 root 命令 → 写 dropbear 公钥开 SSH」。新固件的反汇编结果（固件可从官方 OTA 服务器按型号 + 旧版本号换取下载地址，EROFS 格式，用 erofs-utils 可完整解包）显示修补点：

- 404 转码处理脚本改为 `realpath -sm` 路径校验（强制用户目录前缀）+ **argv 传参**（不再拼 shell）；
- 文件操作从 Lua 直调改为 D-Bus 委托编译守护进程，下层统一 `execvp`/`nixio.execp`（argv）；
- 插件本地安装增加 RSA 验签。

一句话：**从 Lua→shell→Python→ELF 全链无 shell 注入**。旧路已死，需要找新的。

## 一、Root + SSH 完整流程

### 攻击链总览

```
mTLS 客户端证书（桌面端 App 生成）
  → ① get_pool_info 获取 WebDAV 凭据
  → ② MKCOL 换行目录名 + add_samba_account 的 uci heredoc 注入
      → force user = root 的 /etc SMB 共享（root 任意文件写）
  → ③ /etc/ld.so.preload + aarch64 触发 .so
      → cron 每分钟的 root 任务必然触发
      → socat root shell + dropbear(22)
```

### 步骤 ① 证书即会话

桌面端「小米智能存储」登录 NAS 后会在本机生成客户端证书（`minasCert` 目录，含私钥与 CA 链）。证书 CN 形如 `nas.<uid>.<did>.<n>`，以 CN 做 SNI 即可完成 mTLS。

固件 Lua 源码里的关键发现：**所有 LuCI 模块都以 `setuser=false` 声明**——证书认证通过后，请求处理器在 **root 上下文**执行。也就是说，这套系统没有独立的"Web 会话"概念，**证书认证本身就是完整的 admin 会话**。

用证书调 `filemgr/get_pool_info` 即返回 WebDAV 账号密码（用户 `u<uid>`），WebDAV 在 5000 端口（mTLS + Basic 双认证），可读写 `/pool0/data/`。

已验证走不通的路（不必再试）：WebDAV 路径穿越（451）、filemgr 的 move/copy/decompress/compress 命令注入与密码注入（全部 argv 传参）、Range 头命令替换（bash 不会对参数展开结果二次扫描）。

### 步骤 ② uci heredoc 注入——root 文件写的核心

整条链的关键一跳。`netshare/add_samba_account` 以 root 运行，其 `share_dir` 参数**只校验目录存在、不校验路径范围**（`check_parms` 只做 `fs.access`）。该值最终进入 `smb_mgr.sh` 的 **uci batch heredoc**：

```
uci -q batch <<-EOF
    set sambashare.$share_name.path="$path"
    ...
EOF
```

heredoc 的引号是跨行扫描的——**目录名里带换行就能注入额外的 uci 命令**。普通手段建不出含换行的目录（Windows API 与 filemgr 的 create_dir 都拒绝换行名），但 **WebDAV MKCOL 接受 URL 编码的 `%0A` 文件名**（底层 cfs 文件系统支持）。最终构造（三次迭代才跑通）：

```
MKCOL /pool0/data/<30个M>%0A"%0A
set sambashare.u<uid>_2.user_force=root
set sambashare.u<uid>_2.create_mask=0644
set sambashare.u<uid>_2.path=etc
set sambashare.u<uid>_2.comment=
```

（首行为实际发送的 URL——目录名中每个换行都编码为 `%0A`；其余行展示解码后的目录名内容，即 uci 实际收到的注入串。）

要点（每条都是试出来的）：

- **首行 30 个相同字符**：共享名取 basename 前 30 字符，必须干净合法；
- **第二行孤立引号**闭合模板的开引号，后续行成为自由文本；
- 注入 `user_force=root`——smb_mgr 会把 `force user = root` 渲染进 smb.conf，SMB 会话即以 root 身份操作文件；
- **账号名必须等于 `u<uid>`**：模板在 `users != force_user` 时会覆写 user_force，同名则跳过，注入才能存活；
- **path 用相对路径 `etc`**：nginx/WebDAV 拒绝 URL 中 `/` 与 `%0A` 相邻（409），相对路径最终落到 `/etc`；
- **`create_mask=0644`**：为下一步满足 Debian cron 对 crontab 的安全权限检查（组可写会被拒绝）；
- 末行 `comment=` 吃掉模板尾引号。

`add_samba_account` 可能返回 2502（restart 失败）——**uci 已落盘，属正常**，用 `set_samba_account` 把路径改回干净的 `/etc` 即可修复（set 不覆写 user_force，权限保留）。

### 步骤 ③ ld.so.preload 触发——从文件写到 root 执行

拿到 `/etc` 的 root 写入后还差"执行"。最可靠的触发器是 `/etc/ld.so.preload`：设备上 cron 每分钟都有 root 任务（监控采集等），任何动态链接进程启动都会加载我们的 .so，构造函数即以 root 执行。

触发 .so 最初用 keystone-engine **现场汇编**：最小 aarch64 ELF（DT_INIT 指向一段裸指令），逻辑是 fork + `execve("/bin/sh", ["/bin/sh", "/etc/.rootpwn.sh"], 0)`，父进程立即返回不干扰宿主。payload 脚本先清掉 preload 自身（防风暴）、装公钥、起服务。**现行脚本已改为内置固定机器码**（该段代码无可变输入，汇编结果恒定），生成的 .so 与现场汇编版逐字节一致，因此运行时零第三方依赖；汇编源码保留在脚本注释中供审计/重新生成。

**说明**：本文描述的获取方式不做重启持久化（无挂载钩子、无开机自启）——设备重启后 **socat 辅助 shell 与 dropbear 服务会消失**，需要时重新执行获取流程。一键脚本在成功与失败路径上都会执行清理（解除 preload 布防、移除注入共享与临时目录、清除注入的 samba 系统账号行）。**有意保留的部分**：SSH 公钥（`/etc/dropbear/authorized_keys` 与 `/home/rootx/.ssh/authorized_keys` 两处）及 passwd 中 root home 指向 `/home/rootx` 的修改——密钥认证通道重启后仍可用；如需彻底清除，删除这两个文件并将 home 改回 `/root`。是否及如何做持久化，属于使用者自己的决定，不在本文范围内。

历史坑（现场汇编时期）：汇编字符串里 `\0` 的转义层数——Python 源码需 `"\\0"` 传给 keystone 才是正确的汇编文本，写成真实 NUL 会报 `KS_ERR_ASM_SYMBOL_MISSING`。内置字节版不再受此影响。

### SSH 的曲折与真相

公钥注入 dropbear 的 authorized_keys。注意：**dropbear 优先读取 root home 下的 `~/.ssh/authorized_keys`，而 root 出厂 home 是只读的 `/root`**——payload 先用 sed 把 passwd 中 root 的 home 改到可写位置 `/home/rootx`（这是链路自身的修改，不是设备固有状态），再在 `/home/rootx/.ssh/` 与 `/etc/dropbear/` 双写公钥。完成后 dropbear.socket 正常监听 22，**密钥认证每次都成功**（日志 `Pubkey auth succeeded`）。但远程命令无输出。

排查过程最初怀疑是账户层的问题，实测排除了这一方向：root 账户完好（shell 正常、`su - root` 可用）。真相是 **dropbear 2022.83 的 child fd 布局 bug**：认证后 exec 的 bash，stdin 接到了 /dev/null、阻塞在 pipe_read（`/proc/PID/syscall` 实证）。SSH 认证通道保留（未来固件修复后命令输出自动恢复），**运维期主力通道改用 socat**：

```
nc <NAS_IP> 2323     # 完整交互式 PTY root shell，绕开 dropbear 的 bug
```

### 运维期通道的坑（都很隐蔽）

| 现象 | 根因 | 解法 |
|---|---|---|
| socat 会话内 `systemctl restart` 挂死 | D-Bus 与 PTY 会话死锁 | 写一次性 cron 任务执行 systemctl 类操作 |
| 手动 `systemctl start xxx-restore` 卡死 | restore 脚本内的 systemctl 与父 job 循环等待 | 直接手动跑 restore.sh，勿经 unit |
| 多个 systemctl job 全部 waiting | 死锁的 restore job 堵住队列 | 杀掉卡死的 restore 进程，队列自清 |

## 二、插件部署：流程变化与运行期防删除（server.list 自愈）

### 部署通道的变化

传统部署链（scp 上传 + SSH 远程执行）因 dropbear 的 exec bug 失效，改为**三段式通道**：

1. **打包上传**：插件源码打 zip，经 WebDAV（二进制安全）上传到 `/pool0/data/deploy_staging/`；
2. **远程执行**：任意 shell/python 脚本经 socat root shell 执行（base64 分块下发，规避 PTY 行长限制）；
3. **安装**：解压后 `sh deploy/deploy.sh <uid>`。

### 两个必踩的坑

**CRLF**：Windows 检出的脚本带 `\r`，目标机上报 `set: 无效选项`。解压后必须对文本统一 `tr -d '\r'`——**但仅限 .sh/.py/.cgi 等文本**。图省事对全目录执行过一次，结果 PNG 里的 0x0D 字节被删，两个插件图标在 App 上显示空白（文件恰好少了 15/14 字节 = 被删的 `\r` 数，对得严丝合缝）。修复：源图重传，全量复扫 30 个二进制 0 损坏。**硬规矩：文本二进制分流，二进制后缀永远不进行字符处理。**

**iptables 不在 PATH**：它在 /sbin，非交互 shell 需 `export PATH=$PATH:/usr/sbin:/sbin`。

### "打开应用市场就清插件"的根因与运行期防删除

部署成功后插件反复消失，最终定位的触发条件是**在 App 打开应用市场**。逆向 plugincenter 二进制找到了判定字符串：

```
plugin %s already taken down and not installed, delete it
```

完整机制：打开应用市场 → `plugincenter update` → 下载云端 server.list → 与本地 `.list` 对账 → **不在 server.list 的插件判"已下架" → 删 .list 条目 + 删整个插件目录**。自研插件不在小米云端目录，每次必删。此前以为 ports 表注册可防——实证推翻，**server.list 对账才是插件存活的唯一真源**（这也正是"固件升级清插件"的同一机制：升级后首次对账同样触发）。

**运行期防删除自愈**（已端到端验证；路径为示意，读者自选 /data 分区下的任意隐蔽位置）：

```
/data/<自选目录>/entries.json      # 各插件的 server.list 条目缓存（从备份 INFO 生成）
/data/<自选目录>/keep-plugins.sh   # root crontab 定时执行（示例 2 分钟）：
                                    #   ① server.list 缺条目 → 注入
                                    #   ② 插件目录被删 → 从 /data 分区备份恢复
                                    #   ③ 服务未运行 → 拉起
```

双防线：注入后 update 对账直接通过（不删）；即使云端重新下载 server.list 覆盖了注入，2 分钟内 cron 自动恢复。极端场景实测：手动换成纯云端列表 + 跑 update（插件全清，完整复现 bug）→ 150 秒内目录、注册表、服务、API 健康检查全部自动恢复。

## 三、插件的重启恢复（restore 机制）

三层保险 + 一层自愈：

| 层 | 位置 | 抗什么 |
|---|---|---|
| ports 表注册 | /etc/config/plugin + /data 可写层副本 | 重启时系统重建插件目录的依据 |
| 源码备份 | `/data/backup/plugins/<插件>/` | 目录被清后的恢复源 |
| restore unit | `xxx-restore.service`（enabled） | **重启**时自动恢复 |
| server.list 自愈 | 条目缓存 + 定时 cron 脚本（读者自建） | **运行期**被对账清除（含打开应用市场、定时任务、固件升级后首次对账） |

效果：重启 → restore unit 拉起全部；运行期被清 → 2 分钟 cron 自愈；固件升级 → /data 分区的备份与自愈脚本全部存活，升级后首次对账被自愈层接住。

## 结语

新固件把旧的"文件名 shell 注入"堵得很彻底——从 Lua 到 shell 到 Python 到 ELF，全链 argv 传参。但**根信任模型没变**：证书认证即 root 上下文；在 `setuser=false` 的模块面里，参数校验最薄的 `add_samba_account` 遇上 heredoc 引号的跨行语义，就重新打开了 root 文件写入。配合 `ld.so.preload` 这个"每分钟必然触发"的执行原语，链路在三步内闭环。

运维侧的教训同样值钱：二进制与文本必须分流处理（部署管道里一次一刀切的 `\r` 清洗删掉了 PNG 内的 0x0D，两个插件图标在 App 上变成空白）；"打开应用市场"这类最普通的用户操作背后是对账式的数据清理；改常驻服务的数据库要读懂它的唯一性约束并记得重启它（重建相册分类时用秒级时间戳做键，同秒连拍照片键冲突，相册守护进程整个加载中止、全部相册消失，改微秒级才恢复）——每一条都是真机上白花花的事故换来的。

**给厂商的修复建议**（按性价比排序）：

1. **收敛 `setuser=false` 的模块面，把"证书认证"与"授权执行"解耦**——这是本文结语点名的根因：客户端证书只应证明身份，不应直接等价于 root 执行上下文；
2. **WebDAV MKCOL 对文件名中的控制字符（换行/引号）规范化或拒绝**——链路第二步的硬依赖，堵住它整条链断在创建注入目录；
3. `share_dir` 增加路径范围校验（限定用户数据目录）；
4. smb_mgr 的 uci 写入改用参数化接口而非字符串拼接；
5. `add_samba_account` 与 `set_samba_account` 的账号名与 force_user 解耦；
6. dropbear 升级修复 child fd bug。

---

*全文基于对自有设备的授权研究整理。利用细节已做脱敏（无具体 IP/账号/密钥信息），脚本与完整档案未随文发布。*
