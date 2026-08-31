#!/usr/bin/env bash
set -euo pipefail

NAS_IP="${1:-${NAS_IP:-}}"
if [[ -z "$NAS_IP" ]]; then
  echo "usage: NAS_IP=<nas-ip> $0  或  $0 <nas-ip>" >&2
  exit 2
fi
APP_NAME="${APP_NAME:-小米智能存储}"
IFACE="${IFACE:-}"
ROOT_DIR="${ROOT_DIR:-$HOME/issuebase/mi-nas/captures/app-watch-$(date +%Y%m%d-%H%M%S)}"
APP_SUPPORT="$HOME/Library/Application Support/$APP_NAME"
APP_BUNDLE="/Applications/$APP_NAME.app"

mkdir -p "$ROOT_DIR"

if [[ -z "$IFACE" ]]; then
  IFACE="$(route -n get "$NAS_IP" 2>/dev/null | awk '/interface:/{print $2; exit}')"
fi
if [[ -z "$IFACE" ]]; then
  IFACE="$(ifconfig | awk '/^[a-z0-9]+: flags=/{gsub(":","",$1); iface=$1} /inet / && $2 !~ /^127\./{print iface; exit}')"
fi
IFACE="${IFACE:-en0}"

PCAP="$ROOT_DIR/nas-redacted.pcap"
TCPDUMP_LOG="$ROOT_DIR/tcpdump.log"
CONN_LOG="$ROOT_DIR/connections.log"
PROC_LOG="$ROOT_DIR/processes.log"
FILES_LOG="$ROOT_DIR/file-changes.log"
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

echo "[+] output: $ROOT_DIR"
echo "[+] tcpdump needs sudo. 输入密码后，退出/重新打开小米智能存储；完成后 Ctrl-C 停止。"

cleanup() {
  echo
  echo "[+] stopping..."
  [[ -n "${TCPDUMP_PID:-}" ]] && kill "$TCPDUMP_PID" 2>/dev/null || true
  [[ -n "${LOOP_PID:-}" ]] && kill "$LOOP_PID" 2>/dev/null || true
  wait 2>/dev/null || true
  {
    echo "finished: $(date)"
    echo
    echo "Recent cert files:"
    find "$APP_BUNDLE" "$APP_SUPPORT" -maxdepth 8 \( -iname '*_cert.pem' -o -iname '*_private_key.pem' -o -iname '*_csr.pem' -o -iname 'ca_chain.pem' \) -print 2>/dev/null | sort
    echo
    echo "Recent WebDAV-ish strings from app data:"
    find "$APP_SUPPORT" -maxdepth 6 -type f -print0 2>/dev/null | xargs -0 strings -a 2>/dev/null | rg -i 'webdav|baseurl|username|password|shareDeviceToken|deviceCurrentInfo|pool0|nas\.' | head -200 || true
  } >> "$SUMMARY"
  echo "[+] saved: $ROOT_DIR"
}
trap cleanup INT TERM EXIT

sudo tcpdump -i "$IFACE" -n -s 0 -w "$PCAP" "host $NAS_IP and (tcp port 443 or tcp port 5000 or tcp port 22 or tcp port 8086 or tcp port 36673 or portrange 30000-65535)" >"$TCPDUMP_LOG" 2>&1 &
TCPDUMP_PID=$!

echo "[+] tcpdump pid: $TCPDUMP_PID"

(
  while true; do
    {
      echo "===== $(date) ====="
      echo "--- ps ---"
      ps axww -o pid,ppid,user,command | rg -i "$APP_NAME|sso_login|MiNas|SmartStorage|小米智能存储" || true
      echo "--- lsof NAS connections ---"
      lsof -nP -iTCP | rg "$NAS_IP|$APP_NAME|sso_login|小米智能存储" || true
      echo "--- cert/app recent files ---"
      find "$APP_BUNDLE" "$APP_SUPPORT" -maxdepth 8 \( -iname '*_cert.pem' -o -iname '*_private_key.pem' -o -iname '*_csr.pem' -o -iname 'ca_chain.pem' -o -iname '*.ldb' -o -iname '*.log' \) -print 2>/dev/null | while read -r f; do stat -f '%Sm %z %N' -t '%F %T' "$f" 2>/dev/null; done | sort | tail -80 || true
      echo
    } >> "$CONN_LOG" 2>&1
    sleep 1
  done
) &
LOOP_PID=$!

echo "[+] monitor pid: $LOOP_PID"
echo "[+] watching. Ctrl-C to stop."

while true; do sleep 3600; done
