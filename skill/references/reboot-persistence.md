# Reboot persistence on Xiaomi NAS

Read this reference when SSH must survive an ordinary whole-device reboot, port 22 closes
after reboot, or a custom service stored in `/etc/systemd/system` is enabled but never starts.

## Validated boot behavior (applies to both old and new firmware)

On the validated firmware, `dropbear.socket` is enabled in the vendor image and initially
listens during boot. Later, `minas.boot_check.service` runs `/lib/minas/boot_check.sh`.
Its `ssh_check()` keeps SSH only when factory/develop mode applies or
`mitee_tool rpmb get ssh_en` returns exactly `true`; otherwise it explicitly stops the
socket and logs `ssh server stop`.

## `/etc` overlay boot-order limitation

Do not rely on a new `/etc/systemd/system` unit or a drop-in on `minas.boot_check.service`
as the primary workaround. `/etc` is an overlay whose upper directory is under `/data`.
systemd loads the vendor unit before that overlay is mounted and does not automatically
reload it when the persisted drop-in becomes visible. A file can survive reboot while being
absent from systemd's in-memory boot transaction. On the 1.0.9.462 recovery, both a systemd
service and a safe-eject overlay unit were enabled but did not fire at boot; the pool-hook
mechanism did fire.

## RPMB limitation

`mitee_tool rpmb set <key> <value>` is an interactive challenge-response operation. A plain
`mitee_tool rpmb set ssh_en true` prints a Base64 challenge, reads another line from stdin,
then fails verification when no valid response is supplied. Check its exit status and
read back the key; never append an unconditional success marker after it. No working
vendor challenge-response credential has been established; do not brute-force it.

## Two persistence hooks (firmware 1.0.9.462)

On firmware 1.0.9.462, the vendor-shipped Dropbear binary forces `/usr/sbin/mi-shell` and
ignores unknown commands. After restoring a compatible Dropbear via bind mount (see the
firmware reference), persistence requires **two** ordered hooks:

### Hook 97: restore compatible Dropbear before SSH startup

```text
/etc/syshotplug/pool/97.dropbear-unpatch
```

```sh
#!/bin/sh
[ "${ACTION:-}" = "mounted" ] || exit 0
GOOD=/home/rootx/dropbearmulti.good
TARGET=/usr/sbin/dropbearmulti
[ -f "$GOOD" ] || { logger -t dropbear.unpatch "compatible binary missing"; exit 1; }
mountpoint -q "$TARGET" && exit 0
mount --bind "$GOOD" "$TARGET" \
  && logger -t dropbear.unpatch "compatible dropbear restored" \
  || logger -t dropbear.unpatch "bind-mount failed"
```

### Hook 98: start the Dropbear socket after pool mount

```text
/etc/syshotplug/pool/98.ssh-persistence
```

```sh
#!/bin/sh
[ "mounted" = "$ACTION" ] || exit 0
systemctl start dropbear.socket
logger -t ssh.persistence "dropbear.socket started after pool mount"
```

Both hooks must be `chmod 755`, and the same files must be visible under
`/data/etc/upper/syshotplug/pool/`. The pool syshotplug manager enumerates this directory
dynamically after the `/etc` overlay is available. Numbering `97.*` and `98.*` places
binary restoration before socket startup, and both after the vendor boot check and earlier
pool initialization hooks.

The compatible binary staged at `/home/rootx/dropbearmulti.good` is owned by the
`/data`-backed overlay and survived the tested reboot. If this file is deleted, moved,
or its checksum changes, hook 97 will log a failure and SSH will not recover. Recheck
these artifacts after any OTA update. On older firmware where Dropbear is unpatched,
hook 97 is unnecessary; only hook 98 is needed and is installed by
`scripts/install-ssh-persistence.sh`.

This hook depends on the pool-mounted event. If the data pool cannot mount, it will not
recover SSH; use the still-available certified WebDAV injection path for diagnosis when
ports 443/5000 are available.

## Reboot acceptance test

Do not reboot without the user's explicit instruction. Before reboot, capture the current
boot ID and verify the key, root shell, compatible binary, and both hooks in
`/data/etc/upper`. After the user reboots:

1. Wait for a different `/proc/sys/kernel/random/boot_id`.
2. Allow at least the normal pool initialization window; the validated device reopened SSH
   roughly 34–100 seconds after kernel boot.
3. Verify old-key root login: `ssh -o BatchMode=yes "$SSH_TARGET" id` must return `uid=0`.
4. Check the current boot journal for this ordering:
   - initial `Listening on dropbear.socket`;
   - vendor `Closed dropbear.socket` / `ssh server stop`;
   - `dropbear.unpatch: compatible dropbear restored`;
   - `ssh.persistence: dropbear.socket started after pool mount`;
   - successful execution of both hooks by the syshotplug manager.
5. Verify the Dropbear binary in memory is the compatible one (e.g. confirm it does not
   contain `/usr/sbin/mi-shell` using a `grep` on its path via `/proc`).

Ordinary reboot persistence is established only when the new boot ID, old-key login,
compatible binary, and journal ordering all agree. This does not establish OTA or
factory-reset persistence.

## Recovery and rollback

If port 22 remains closed but the certificate path still works, inject only the minimum
commands needed to restore the current boot: install the existing public key, ensure the
root shell is usable, and run `systemctl start dropbear.socket`. Then inspect current-boot
logs over SSH before changing the persistence mechanism.

Rollback is limited to removing
`/etc/syshotplug/pool/98.ssh-persistence`. Record the removal and verify its corresponding
upper-layer file is gone. Do not unset or rewrite unrelated RPMB keys.

## Custom services in `/etc/systemd/system`

The same early-cache issue applies to locally created services such as a Docker control
panel. An enabled symlink that appears only after `/etc` overlay mount may be present on disk
while the service has no boot journal and remains inactive. A late hook can run
`systemctl daemon-reload` and explicitly start that known service after its data and unit
files are visible. Keep application-specific recovery in that application's own project;
do not silently add unrelated services to the SSH persistence hook.
