# Reboot persistence on Xiaomi NAS

Read this reference when SSH must survive an ordinary whole-device reboot, port 22 closes
after reboot, or a custom service stored in `/etc/systemd/system` is enabled but never starts.

## Validated boot behavior

On the validated firmware, `dropbear.socket` is enabled in the vendor image and initially
listens during boot. Later, `minas.boot_check.service` runs `/lib/minas/boot_check.sh`.
Its `ssh_check()` keeps SSH only when factory/develop mode applies or
`mitee_tool rpmb get ssh_en` returns exactly `true`; otherwise it explicitly stops the
socket and logs `ssh server stop`.

Do not use a new `/etc/systemd/system` unit or a drop-in on `minas.boot_check.service` as the
primary workaround. `/etc` is an overlay whose upper directory is under `/data`. systemd
loads the vendor unit before that overlay is mounted and does not automatically reload it
when the persisted drop-in becomes visible. A file can survive reboot while being absent
from systemd's in-memory boot transaction.

`mitee_tool rpmb set <key> <value>` is an interactive challenge-response operation. A plain
`mitee_tool rpmb set ssh_en true` prints a Base64 challenge, reads another line from stdin,
then fails verification when no valid response is supplied. Check its exit status and
read back the key; never append an unconditional success marker after it.

## Validated persistence hook

Install an executable late hook at:

```text
/etc/syshotplug/pool/98.ssh-persistence
```

with:

```sh
#!/bin/sh

[ "mounted" = "$ACTION" ] || exit 0
systemctl start dropbear.socket
logger -t ssh.persistence "dropbear.socket started after pool mount"
```

Set mode `0755` and verify the same file is visible at
`/data/etc/upper/syshotplug/pool/98.ssh-persistence`. The pool syshotplug manager enumerates
this directory dynamically after the `/etc` overlay is available. Naming it `98.*` makes it
run after the vendor boot check and the earlier pool initialization hooks.

This hook depends on the pool-mounted event. If the data pool cannot mount, it will not
recover SSH; use the still-available certified WebDAV injection path for diagnosis when
ports 443/5000 are available.

## Reboot acceptance test

Do not reboot without the user's explicit instruction. Before reboot, capture the current
boot ID and verify the key, root shell, and hook in `/data/etc/upper`. After the user reboots:

1. Wait for a different `/proc/sys/kernel/random/boot_id`.
2. Allow at least the normal pool initialization window; the validated device reopened SSH
   roughly 34 seconds after kernel boot.
3. Verify old-key root login and `systemctl is-active dropbear.socket`.
4. Check the current boot journal for this ordering:
   - initial `Listening on dropbear.socket`;
   - vendor `Closed dropbear.socket` / `ssh server stop`;
   - later `ssh.persistence: dropbear.socket started after pool mount`;
   - successful execution of `98.ssh-persistence` by the syshotplug manager.
5. Also verify `/data`, `/etc`, `/data/docker_data`, and `/nas/pool0` mounts when the user is
   investigating apparently missing Docker state.

Ordinary reboot persistence is established only when the new boot ID, old-key login, and
journal ordering all agree. This does not establish OTA or factory-reset persistence.

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
