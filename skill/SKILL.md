---
name: xiaomi-nas-ssh-root
description: >
  Re-root an owner's Xiaomi NAS RP05 on firmware 1.0.9.462 from macOS: run the in-repo
  UCI-heredoc root tool, restore a usable SSH command shell with a compatible Dropbear,
  restart root after reboot, and persist SSH across ordinary reboots. Use when SSH
  authenticates but shows the MINAS banner and ignores commands, root was lost after an
  OTA, or the user asks to re-root a 1.0.9.x NAS.
metadata:
  version: "2.0.0"
---

# Xiaomi NAS root SSH — firmware 1.0.9.462 (macOS)

Ready-to-use package: this skill plus `scripts/xiaomi-nas-root.py`. Verified on an owned
device on 2026-10-20 (firmware 1.0.9.462.F24C26D24-REL, Apple Silicon, system Python
3.9+; the runner uses only the Python standard library). Each chain stage ran
successfully on the real device; a full unattended end-to-end replay from a clean
state has not been performed.

## When this applies

- SSH authenticates but shows the MINAS banner and ignores commands.
- Firmware 1.0.9.462's Dropbear forces `/usr/sbin/mi-shell`; the binary contains a
  hardcoded mi-shell reference, the older firmware's Dropbear does not.
- The older WebDAV video-segment injection (this repo's `main` branch) is patched in
  1.0.9.x — do not retry it there.
- Host: macOS only. Windows/Linux are not implemented in this branch.

## Precautions — read before running

- Own-device use only. Reading this skill never authorizes injection, reboots, or
  NAS changes; get explicit user confirmation for each.
- Never publish device IPs, UIDs, serials, WebDAV/SMB passwords, tokens, client or
  SSH private keys. Runner output and SMB mount URLs can contain live temporary
  credentials — keep logs private and redact before sharing.
- The tool temporarily creates a Samba share for its own root-write channel and
  removes it during cleanup. This skill covers only root acquisition and secure
  SSH; it does not inspect or manage the owner's disk/media share settings.
- The tool arms `/etc/ld.so.preload`. It disarms itself on both success and failure
  paths, but if the run is interrupted abnormally you must disarm it manually through
  whatever root channel exists — an armed preload reloads into every dynamic process.
- Re-running is safe in principle: keys are appended only if absent, and the
  credential pool is refreshed each run. Do not run twice concurrently.
- Do not attempt `mitee_tool rpmb set ssh_en` as an unlock: it is TEE
  challenge-response gated and fails with `rpmb set verify failed`. No vendor-side
  SSH unlock is available.
- Do not attempt a sysota downgrade: blocked by an RPMB `downgrade_en` gate with TEE
  verification.

## Root chain (verified steps)

Source layout: `scripts/xiaomi-nas-root.py` (this repo, adapted from
[lvmaple/minas_plugin](https://github.com/lvmaple/minas_plugin) with macOS fixes:
`mount_smbfs`, share-name fallback `etc-<uid>` after repair, `/bin/sh` payload,
portable socket-based cleanup).

1. **Environment** — any Python 3.9+; no Conda, Homebrew, venv, or pip packages:
   ```sh
   python3 scripts/xiaomi-nas-root.py --preflight
   ```
   `--preflight` checks the local Python, macOS `ssh-keygen`/`mount_smbfs`, the
   embedded trigger binary, and the desktop-app certificate directory. It does not
   contact the NAS. Fix only what it reports.
2. **Pre-check** — confirm root is genuinely gone:
   `ssh -o BatchMode=yes <target> id -u` must NOT succeed. If it returns `0`, stop:
   root already works; do not re-root.
3. **Run** — from the repository root:
   ```sh
   python3 scripts/xiaomi-nas-root.py <NAS_IP>
   ```
   The desktop app must be installed and logged into the target NAS (client certs are
   found automatically under the app bundle). Chain: mTLS cert → `get_pool_info`
   WebDAV creds → `add_samba_account` UCI heredoc injection → `force user=root` `/etc`
   SMB share → `/etc/ld.so.preload` + assembled aarch64 trigger `.so` → cron root job
   → socat root shell on 2323 + SSH pubkey installed + dropbear socket started.
4. **Verify channels** — socat: `nc <NAS_IP> 2323` gives a root shell. SSH key auth
   alone is not enough: on this firmware commands still hit mi-shell until the next
   step. A fresh `uid=0` line in `/etc/.rootpwn.log` proves cron executed; it does
   not prove the socat listener or SSH exec.
5. **Restore SSH exec** — firmware 1.0.9.462's Dropbear is patched; bind-mount a known
   good 1.0.8.425 `dropbearmulti` (owner-supplied from a verified backup; not bundled):
   ```sh
   mount --bind /home/rootx/dropbearmulti.good /usr/sbin/dropbearmulti
   ```
   Then `ssh -o BatchMode=yes <target> id -u` must return `0`. Do not stack a second
   bind over an existing one; inspect the target mount first.
6. **Cleanup** — automatic on success/failure (disarms preload, removes the injected
   share, staging dir, and the injected Samba system account lines; SSH pubkeys and
   the `/home/rootx` home change are intentionally kept). Verify the result through
   real root; a printed `CLEAN_OK` or a generated `cleanup-<ip>.sh` alone proves
   nothing. **Never blindly delete `sambashare.u<uid>_2`** if the owner has since put
   a legitimate share in that slot — inspect name/path first.

## Restarting root after a reboot

The tool deliberately does not persist everything. After a NAS reboot:

- SSH public keys (`/etc/dropbear/authorized_keys`, `/home/rootx/.ssh/authorized_keys`)
  and the root home change survive on the `/data` overlay.
- The socat listener and the Dropbear bind-mount are gone; firmware Dropbear again
  forces mi-shell, so key auth alone yields the banner.
- With the persistence hooks below installed, one observed reboot recovered full
  UID-0 SSH with no manual steps (first connect ~100 s after boot).
- Without hooks, re-run this chain (keys are reused, not regenerated) or repeat only
  the bind-mount step through whatever root channel you re-established.

## Persistence (ordinary reboot)

Two syshotplug pool hooks, `/etc/syshotplug/pool/`, mode 0755 — this mechanism is the
one that demonstrably fires at boot; `/etc/systemd/system/` units do NOT (the `/etc`
overlay mounts after systemd builds its boot transaction):

- `97.dropbear-unpatch` — on `ACTION=mounted`, bind-mounts
  `/home/rootx/dropbearmulti.good` over `/usr/sbin/dropbearmulti` if not already a
  mountpoint. Runs before 98.
- `98.ssh-persistence` — starts `dropbear.socket`. Also installable standalone via
  `scripts/install-ssh-persistence.sh <target>` when Dropbear is unpatched.

Full hook bodies and acceptance test: [references/reboot-persistence.md](references/reboot-persistence.md).
Reboot acceptance requires a changed boot ID, old-key `id -u` → `0`, and the hook log
lines in the current boot journal. This is ordinary-reboot persistence, not an OTA or
factory-reset guarantee — recheck hooks after every OTA.

## If the chain fails at a step

- mTLS handshake fails → check cert validity dates; the runner probes server-name
  role suffixes `.0/.00/.1/.2/.20` automatically.
- MKCOL 409 → path encoding issue; the runner's relative-`etc` trick handles the
  observed case.
- `add_samba_account` returns 2511 (not just 2502) when the share exists — non-fatal
  for the chain.
- socat 2323 never opens → check `/etc/.rootpwn.log` via the root share; `uid=0` there
  means cron ran and the listener failed (e.g. wrong EXEC shell path) — do not blindly
  re-arm a live preload.
- `systemctl restart` inside the socat PTY can D-Bus-stall — use verified SSH or a
  cron one-shot for service operations.
- Root lost again after a later OTA → re-verify firmware, keys, hooks, then re-run
  this chain. Do not attempt sysota downgrade.

## Summary

1. Verify root is really gone (`id -u` must fail).
2. `python3 scripts/xiaomi-nas-root.py --preflight`, then run it with `<NAS_IP>` → socat root shell + SSH keys.
3. Bind-mount good Dropbear → `id -u` returns `0` over SSH.
4. Install hooks 97 + 98 → survives ordinary reboot (verified).
5. Cleanup is automatic; verify through real root. Keys intentionally persist;
   re-running after a reboot restores root without regenerating anything.

Details, sanitization rules, and troubleshooting tables:
[references/firmware-1.0.9.462.md](references/firmware-1.0.9.462.md) ·
[references/reboot-persistence.md](references/reboot-persistence.md) ·
[technical review](../docs/technical-review-1.0.9.462.md) ·
[tool usage](../docs/usage-1.0.9.462.md)
