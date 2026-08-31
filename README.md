# Xiaomi NAS SSH/root recovery toolkit

Owner-only toolkit/runbook for enabling SSH on a Xiaomi NAS using the device owner's desktop-app certificate/WebDAV workflow.

## Safety and scope

- Use only on a NAS you own or are explicitly authorized to administer.
- Do not publish or paste device IPs, WebDAV passwords, app account credentials, tokens, cert private keys, or SSH private keys.
- Scripts require the current owner's NAS IP at runtime; no IP is hard-coded.

## Scripts

```bash
# Watch app/runtime activity while the owner logs into Xiaomi Smart Storage app
NAS_IP='<your-nas-ip>' ./scripts/watch-xiaomi-nas-app.sh

# Run step-by-step SSH enablement; prints OK/WARN/FAIL after each stage
NAS_IP='<your-nas-ip>' ./scripts/enable-xiaomi-nas-ssh.sh
```

The runner stores sensitive runtime state locally:

- WebDAV credentials: `/tmp/.wdav_creds` mode 600
- SSH private key: `/tmp/nas-root-key`, then `~/.ssh/id_ed25519_xiaomi_nas`
- SSH config alias: `ssh xiaomi-nas` / `ssh minas`

## Pi skill

See `skill/SKILL.md` for the agent workflow notes.
