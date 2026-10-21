# RP05 firmware 1.0.9.462 recovery from macOS

## Proven scope, not a one-click guarantee

This reference records the recovery method observed on one owner's RP05. macOS
Python 3.11 with Keystone 0.9.2 was used. Windows/Linux support is not implemented or
accepted for this branch. Root job execution, manual restoration of a usable SSH
shell, and a subsequent ordinary-reboot test were observed. A later clean script
attempt did not establish its socat listener; unattended end-to-end repeatability
must not be claimed.

Sources:

- In-repo tool: `scripts/xiaomi-nas-root.py` (adapted from upstream with macOS fixes
  and portable cleanup)
- [Upstream root tool](https://github.com/lvmaple/minas_plugin/tree/main/xiaomi-nas-root-tool-1.0.9.462)
- [Technical retrospective](../../docs/technical-review-1.0.9.462.md) ·
  [tool usage](../../docs/usage-1.0.9.462.md)

The root runner ships in this repository at `scripts/xiaomi-nas-root.py`; there is no
external dependency to clone. It already carries the macOS adaptations verified during
recovery:

- `/sbin/mount_smbfs`, not Linux `mount -t cifs`, for the temporary SMB mount.
- Both the original generated share name and `etc-<uid>` as candidates: repair may
  rename the share after its path becomes `/etc`.
- `/bin/sh` for the NAS shell. `/bin/bash` was absent on the recovery device.
- Portable socket-based cleanup: no workstation `nc`/`timeout`/`bash` and no NAS-side
  `bash` dependency.

Run it only on macOS; the Linux/Windows mount paths in the file are upstream
compatibility code, not accepted configurations on this branch.

## Read-only preparation

Use the owner-selected reachable NAS address. A private tunnel is suitable if its
route reaches the same device; app login/relay status does not establish SSH reachability.
For the new-firmware chain the workstation needs TCP/443 (mTLS API), TCP/5000
(WebDAV), TCP/445 (SMB), and later TCP/22. Port 2323 is a temporary root-shell port
used by the reference runner, not a secure permanent management service. Do not
expose it to the public Internet or treat a connection to it as authenticated SSH.

The desktop app must have logged into the target NAS. Common macOS certificate path:

```text
/Applications/小米智能存储.app/Contents/Resources/extraResources/cert
```

Select the matching `*_cert.pem`, `*_private_key.pem`, and `ca_chain.pem` for the
current owner. Inspect certificate dates/identity locally without publishing them.
The client certificate is not the app password, a guessed serial-number code, or a
response to the vendor TEE challenge. Do not delete the app's certificate directory
or ask the owner to disclose a private key.

Correct API path: `/cgi-bin/luci/filemgr/get_pool_info`. The Python runner probes
server-name role suffixes rather than assuming the client certificate CN is the
server name. Do not disable certificate verification to bypass a mismatch.

## Dedicated Conda environment

Use a dedicated environment, never `base`, venv, or user-site installs. Reuse the
environment if already present; create it only when missing:

```sh
conda create -n minas-root python=3.11 -y
```

With the repository root as the working directory:

```sh
conda run -n minas-root python -m pip install -r scripts/requirements-1.0.9.462.txt
conda run -n minas-root python -c 'import sys, keystone; print(sys.version.split()[0], keystone.__version__)'
conda run -n minas-root python scripts/xiaomi-nas-root.py --help
```

`--help` is a local CLI check, not permission to start recovery. The script has no
`--dry-run`; do not invent one. Its documented options are `--uid`, `--cert-dir`, and
`--shell-port` in addition to the positional NAS address.

On the tested Apple-silicon installation, the pip package lacked a loadable native
Keystone library. The successful repair used Homebrew's arm64 Keystone dylib inside
the dedicated environment's `site-packages/keystone/`, not in `base`. Only if that
same missing-library error occurs and a matching Homebrew library is available:

```sh
KEYSTONE_PACKAGE="$(conda run -n minas-root python -c 'import importlib.util, pathlib; print(pathlib.Path(importlib.util.find_spec("keystone").origin).parent)')"
cp "$(brew --prefix keystone)/lib/libkeystone.0.dylib" "$KEYSTONE_PACKAGE/libkeystone.dylib"
conda run -n minas-root python -c 'import keystone; print(keystone.__version__)'
```

Do not apply an arm64 dylib fix to an Intel/Rosetta environment. Other macOS
architecture combinations have not been accepted by this recovery record.

## Stop conditions before any mutation

1. Verify root SSH first. If a normal remote `id -u` already returns `0`, do not
   rerun the exploit to refresh aliases, inspect files, or maintain persistence.
2. The reference runner targets account/share ID `2` and UCI sections
   `sambashare.u<uid>_2` / `sambauser.u<uid>_2`. Its repair and cleanup can overwrite
   or delete an existing legitimate share. Establish that this slot is unused and
   preserve the existing SMB configuration through an available authorized channel.
   If that cannot be established, **stop before running the tool**; no automatic
   collision-safe path is claimed here. Never label response `2511` harmless without
   proving which share exists. Response `2502` alone also does not prove ownership.
3. The runner arms `/etc/ld.so.preload`. Establish that it and the fixed payload
   paths are not owned by another integration; preserve any existing content. Have
   a working disarm route before arming. If no such route is available, do not arm it.
4. Inspect the selected runner's cleanup. The reviewed version uses NAS-side `bash`
   in one cleanup path, although the recovered NAS lacked `/bin/bash`; it also uses
   a workstation `timeout` executable, which macOS does not supply by default.
   Correct/verify those execution prerequisites in the chosen runner before use;
   a payload changed to `/bin/sh` does not also repair cleanup.
5. Do not publish unredacted runner output. Its failure paths can include temporary
   SMB credentials; mounting SMB also passes a credential-bearing URL to a local
   process. Keep logs private and redact before sharing.

These are limitations of the external runner, not capabilities silently supplied
by this skill. A request to review or share the skill never authorizes a live rerun.

## Authorized recovery phases

Once all prerequisites and stop conditions have been resolved, run the tool **from the
repository root** (generated cleanup artifacts are relative to the working directory):

```sh
conda run --no-capture-output -n minas-root python scripts/xiaomi-nas-root.py "$NAS_IP" \
  --cert-dir "$CERT_DIR"
```

Observe each phase before proceeding:

1. **Certificate/API**: mTLS `get_pool_info` obtains WebDAV credentials in memory.
2. **Temporary share**: WebDAV `MKCOL` makes a newline-bearing directory; its path
   reaches an unquoted UCI heredoc through `add_samba_account`, yielding a temporary
   `force user=root` SMB share. Repair can change its name to `etc-<uid>`.
3. **Root job**: the tool writes its shell payload and assembled aarch64 shared
   object through that share, then arms the preload trigger. A root cron job invokes
   it. A fresh `/etc/.rootpwn.log` entry containing `uid=0` proves job execution,
   **not** listener startup, successful cleanup, or working SSH commands.
4. **Command access**: prove a root command through the available recovery channel.
   If socat never starts, inspect the job log and dependencies rather than assuming
   the injection did not run. Do not blindly rearm a trigger that may still be active.
5. **SSH repair**: if key authentication only yields MINAS, follow the compatible
   Dropbear procedure below. An open port or successful key authentication is not
   a substitute for remote UID-0 command execution.
6. **Cleanup**: disarm through the verified root channel and verify the result.
   Only then close temporary access; preserve the established SSH identity.

Avoid `systemctl restart` inside the socat PTY: recovery encountered a D-Bus stall
there. Use verified normal SSH or the established root cron execution path for
service operations. Do not force-reboot or physically unplug storage to escape it.

## Restoring a usable Dropbear command shell

On the observed device, swapping firmware 1.0.9.462's Dropbear for a saved compatible
1.0.8.425 `dropbearmulti` restored normal SSH execution. The newer binary contained
`/usr/sbin/mi-shell`; its exact forcing branch was not decompiled. Neither the
upstream “child-fd bug” theory nor compatibility of arbitrary older binaries should
be treated as established.

Prerequisites: an owner-trusted compatible aarch64 binary from a verified backup,
a verified root execution channel, and the existing public key/root shell setup.
The binary is not bundled or downloaded from an arbitrary mirror. If no trusted
compatible binary is available, report that missing prerequisite; do not invent a
fallback or call SSH repaired.

Stage it, with its trusted checksum verified, as:

```text
/home/rootx/dropbearmulti.good
```

Make it root-owned and executable. Do not replace a running mounted source in place.
Through the verified recovery root channel, the observed current-boot repair was:

```sh
mount --bind /home/rootx/dropbearmulti.good /usr/sbin/dropbearmulti
```

Before binding, inspect any existing mount at the target. If it already contains the
same binary, do not stack another bind; if it differs, stop and identify its owner.
The filesystem image remains read-only and unchanged. With the existing Dropbear
socket active, new SSH sessions use the restored binary; do not needlessly restart it.
Ensure the configured root shell actually exists and executes commands (`/bin/sh`
on the recovered unit); changing a passwd shell alone did not fix the patched daemon.

Prove success using the intended key/target from the Mac:

```sh
ssh -o BatchMode=yes -o ConnectTimeout=8 "$SSH_TARGET" 'id -u'
```

Only successful output `0` passes. Then install the binary-restoration hook and the
socket-start hook as described in [reboot-persistence.md](reboot-persistence.md).

## Cleanup acceptance

- Disarm this attempt's preload entry first; do not delete another component's preload
  configuration. Then remove only this attempt's payload/shared object and staging
  directory after establishing their exact ownership.
- Remove only the temporary SMB account/share created by this run. Inspect its actual
  name/path before deleting any UCI section. Never blindly execute a generated cleanup
  script against a slot now holding an ordinary user share.
- Preserve existing SSH keys and unrelated passwd/shadow/SMB entries. Do not use a
  broad substring deletion of account lines as an ownership check.
- A locally generated `cleanup-<address>.sh`, an open listener, or the script's success
  banner does not prove cleanup ran. Check the resulting NAS state through real root.
- Close only the temporary recovery listener after authenticated root SSH has been
  independently verified. Do not leave an unauthenticated root listener as persistence.

## Storage follow-up, only when separately requested

The SSH flow must not rewrite media or USB mappings. The recovery incident also
showed three independent storage issues: the safe-eject overlay had not started,
SMB regeneration restored a stale UCI path, and the Mac retained a stale SMB mount
after actual ejection. They were not evidence that the root chain had failed.

Reusable fixes: restore overlay startup before lifecycle reconciliation; persist
SMB paths in UCI rather than patching rendered `smb.conf` alone; use a fixed,
lifecycle-managed bind selected by serial plus filesystem UUID; reconnect the
specific stale client share. A manual lifecycle call uses `reconcile`, not `pool`
without its required hotplug environment. Run a media scan as the library owner,
not root, when the scanner is user-scoped. No personal disk IDs, share names, or
library IDs from the recovery device are required by this skill.
