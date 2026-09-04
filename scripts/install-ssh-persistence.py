#!/usr/bin/env python3
r"""Install the Xiaomi NAS ordinary-reboot SSH hook over an existing root SSH connection.

Windows examples:
  py -3 scripts\install-ssh-persistence.py
  py -3 scripts\install-ssh-persistence.py root@<nas-ip> -i "%USERPROFILE%\.ssh\id_ed25519_xiaomi_nas"

The default SSH target is the ``xiaomi-nas`` alias. This script never reboots the NAS.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Optional


REMOTE_INSTALL_SCRIPT = r"""set -eu

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

systemctl start dropbear.socket
systemctl is-active --quiet dropbear.socket

echo 'PERSIST_HOOK_OK'
"""

REMOTE_VERIFY_COMMAND = (
    "test -x /etc/syshotplug/pool/98.ssh-persistence && "
    "test -x /data/etc/upper/syshotplug/pool/98.ssh-persistence && "
    "systemctl is-active --quiet dropbear.socket && "
    "echo PERSISTENCE_READY"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Install the validated Xiaomi NAS SSH reboot-persistence hook over root SSH."
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=os.environ.get("NAS_SSH_TARGET", "xiaomi-nas"),
        help="Root SSH target or configured alias (default: xiaomi-nas)",
    )
    parser.add_argument(
        "-i",
        "--identity-file",
        type=Path,
        help="SSH private key; omit when the target alias already selects it",
    )
    parser.add_argument(
        "--connect-timeout",
        type=int,
        default=8,
        help="SSH connection timeout in seconds (default: 8)",
    )
    return parser.parse_args()


def run_command(cmd: list[str], *, input_text: Optional[str] = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Run with temporary files so Windows OpenSSH is reliable under remoting."""
    input_path: Optional[Path] = None
    output_path = Path(tempfile.gettempdir()) / f"nas-ssh-persistence-{os.getpid()}.out"
    try:
        if input_text is not None:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", suffix=".sh", delete=False
            ) as input_file:
                input_file.write(input_text)
                input_path = Path(input_file.name)

        stdin_handle = input_path.open("rb") if input_path else subprocess.DEVNULL
        try:
            with output_path.open("wb") as output_file:
                process = subprocess.Popen(cmd, stdin=stdin_handle, stdout=output_file, stderr=subprocess.STDOUT)
                try:
                    return_code = process.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                    output = output_path.read_bytes().decode("utf-8", errors="replace")
                    raise subprocess.TimeoutExpired(cmd, timeout, output=output)
        finally:
            if input_path:
                stdin_handle.close()

        output = output_path.read_bytes().decode("utf-8", errors="replace")
        return subprocess.CompletedProcess(cmd, return_code, output, None)
    finally:
        if input_path:
            input_path.unlink(missing_ok=True)
        output_path.unlink(missing_ok=True)


def ssh_command(ssh: str, args: argparse.Namespace) -> list[str]:
    cmd = [
        ssh,
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={args.connect_timeout}",
        "-o",
        "ConnectionAttempts=1",
        "-o",
        "StrictHostKeyChecking=accept-new",
    ]
    if args.identity_file:
        identity = args.identity_file.expanduser()
        if not identity.is_file():
            raise FileNotFoundError(f"SSH identity file not found: {identity}")
        cmd.extend(["-i", str(identity), "-o", "IdentitiesOnly=yes"])
    cmd.append(args.target)
    return cmd


def print_output(output: str) -> None:
    if output:
        print(output, end="" if output.endswith("\n") else "\n")


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

    args = parse_args()
    ssh = shutil.which(os.environ.get("SSH_BIN", "ssh"))
    if not ssh:
        print("FAIL: ssh executable not found; install or enable OpenSSH Client", file=sys.stderr)
        return 1

    try:
        base = ssh_command(ssh, args)
    except Exception as error:
        print(f"FAIL: {error}", file=sys.stderr)
        return 1

    print("[1/2] Installing the pool-mounted SSH persistence hook")
    try:
        install_result = run_command(base + ["sh -s"], input_text=REMOTE_INSTALL_SCRIPT, timeout=30)
    except subprocess.TimeoutExpired as error:
        print_output(str(error.output or ""))
        print("FAIL: SSH installation timed out", file=sys.stderr)
        return 1
    print_output(install_result.stdout)
    if install_result.returncode != 0 or "PERSIST_HOOK_OK" not in install_result.stdout.splitlines():
        print("FAIL: persistence hook installation failed", file=sys.stderr)
        return 1

    print("[2/2] Verifying the installed hook and current SSH service")
    try:
        verify_result = run_command(base + [REMOTE_VERIFY_COMMAND], timeout=20)
    except subprocess.TimeoutExpired as error:
        print_output(str(error.output or ""))
        print("FAIL: SSH verification timed out", file=sys.stderr)
        return 1
    print_output(verify_result.stdout)
    if verify_result.returncode != 0 or "PERSISTENCE_READY" not in verify_result.stdout.splitlines():
        print("FAIL: persistence verification did not complete", file=sys.stderr)
        return 1

    print(
        "OK: SSH persistence hook installed; a whole-device reboot is still required "
        "for end-to-end verification."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
