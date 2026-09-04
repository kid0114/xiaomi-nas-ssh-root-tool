#!/usr/bin/env bash
set -euo pipefail

# Install the validated reboot-persistence hook after root SSH is available.
# Usage:
#   ./scripts/install-ssh-persistence.sh
#   ./scripts/install-ssh-persistence.sh root@<nas-ip>
#   HOST=root@<nas-ip> ./scripts/install-ssh-persistence.sh

TARGET="${1:-${HOST:-xiaomi-nas}}"
SSH_BIN="${SSH_BIN:-ssh}"

printf '%s\n' '[1/2] Installing the pool-mounted SSH persistence hook'
"$SSH_BIN" \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  "$TARGET" 'sh -s' <<'REMOTE_SCRIPT'
set -eu

[ "$(id -u)" -eq 0 ] || {
  echo 'FAIL: remote SSH user is not root' >&2
  exit 1
}

HOOK_DIR=/etc/syshotplug/pool
HOOK="$HOOK_DIR/98.ssh-persistence"
UPPER_HOOK=/data/etc/upper/syshotplug/pool/98.ssh-persistence
TMP_HOOK="$HOOK.tmp.$$"

cleanup() {
  rm -f "$TMP_HOOK"
}
trap cleanup EXIT HUP INT TERM

install -d -m 755 "$HOOK_DIR"
cat > "$TMP_HOOK" <<'HOOK_SCRIPT'
#!/bin/sh

[ "${ACTION:-}" = "mounted" ] || exit 0

if systemctl start dropbear.socket; then
    logger -t ssh.persistence "dropbear.socket started after pool mount"
else
    logger -t ssh.persistence "failed to start dropbear.socket"
    exit 1
fi
HOOK_SCRIPT

chmod 755 "$TMP_HOOK"
mv -f "$TMP_HOOK" "$HOOK"
trap - EXIT HUP INT TERM

test -x "$HOOK"
test -x "$UPPER_HOOK"
cmp -s "$HOOK" "$UPPER_HOOK"

# Keep SSH available for the current boot as well.
systemctl start dropbear.socket
systemctl is-active --quiet dropbear.socket

echo 'PERSIST_HOOK_OK'
REMOTE_SCRIPT

printf '%s\n' '[2/2] Verifying the installed hook and current SSH service'
VERIFY_OUTPUT=$("$SSH_BIN" \
  -o BatchMode=yes \
  -o ConnectTimeout=8 \
  "$TARGET" \
  'test -x /etc/syshotplug/pool/98.ssh-persistence && test -x /data/etc/upper/syshotplug/pool/98.ssh-persistence && systemctl is-active --quiet dropbear.socket && echo PERSISTENCE_READY')

printf '%s\n' "$VERIFY_OUTPUT"
grep -q '^PERSISTENCE_READY$' <<<"$VERIFY_OUTPUT" || {
  echo 'FAIL: persistence verification did not complete' >&2
  exit 1
}

printf '%s\n' 'OK: SSH persistence hook installed; a whole-device reboot is still required for end-to-end verification.'
