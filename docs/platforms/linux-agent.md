# Linux agent

The Rust agent connects outbound to the Guardian server over WebSocket, enforces AppArmor policies, domain blocklists via local DNS, and reports usage/alerts.

## Access approvals (app launch + domains)

When a child profile uses approval modes on the server, the Linux agent consumes the same additive sync fields as the Android agent.

### App launch (`sync_apparmor_policy`)

When `app_launch_mode` is `allowlist` or `blocklist`, the server includes:

```json
{
  "policies": [ ... ],
  "approval_policy": {
    "app_launch_mode": "allowlist",
    "approved_packages": ["/usr/bin/firefox", "$HOME/Games/**"],
    "blocked_packages": ["/usr/bin/steam"]
  }
}
```

On Linux, `approved_packages` and `blocked_packages` hold **executable paths** and **home path patterns** (`$HOME/.../**`), not Android package IDs.

The agent enforces the server-precomputed `blocked_packages` set (minus `approved_packages`) at process exec time via the netlink monitor (SIGKILL). When `approval_policy` is omitted (`open` mode), only static `blocked` rules from `policies` apply (AppArmor profiles + path rules).

On blocked launch under an approval overlay, the agent emits:

- `access_requested` with `target_kind: executable`
- `app_blocked` with `reason: not_approved` (server ingest fallback)

Alerts are rate-limited per target (5-minute cooldown).

### Domain access (`update_domain_policy_manifest`)

Per-UID manifest entries may include:

```json
{
  "linux_username": "child",
  "source_ids": ["1"],
  "domain_access_mode": "approval_on_block",
  "allowed_domains": ["wikipedia.org"]
}
```

Granted domains bypass the local DNS sinkhole. When `domain_access_mode` is `approval_on_block`, a blocked DNS query:

1. Auto-emits a deduped `access_requested` alert (`target_kind: domain`)
2. Shows a desktop notification via `notify-send` (requires a user session with D-Bus notifications)

## Device restrictions (`sync_linux_device_policy`)

Per Linux device mapping, the admin UI can configure polkit-backed system privileges and terminal access control. The server pushes them via `sync_linux_device_policy` (on save, and again when the agent reconnects after a `policy_sync_check` cycle).

Payload shape:

```json
{
  "device_policy": {
    "polkit": {
      "installSoftwareDisabled": false,
      "uninstallSoftwareDisabled": false,
      "mountRemovableMediaDisabled": false,
      "modifyAccountsDisabled": false,
      "systemPowerActionsDisabled": false,
      "pkexecElevationDisabled": false,
      "flatpakInstallDisabled": false,
      "snapInstallDisabled": false
    },
    "connectivity": {
      "bluetoothDisabled": false
    },
    "exec": {
      "terminalAccessDisabled": false
    },
    "supportMessage": "This setting is managed by your parent through TimeKpr."
  }
}
```

### Catalog vs active-session enforcement

The agent keeps two layers of state:

1. **Policy catalog** — one entry per managed Linux username, updated whenever the server sends `sync_linux_device_policy`. Stored in `/var/lib/timekpr-agent/linux-device-policy.json` (fallback `/etc/timekpr-agent/`).
2. **Active-session enforcement** — polkit rules, Bluetooth rfkill, and terminal exec blocking are applied only for the user signed into the **primary desktop session** (logind seat0 active session). When that session changes, the agent reconciles: remove all `50-timekpr-*.rules`, unblock Bluetooth, then apply restrictions for the new active user if they have a catalog entry.

This mirrors AppArmor’s logind session hooks: syncing policy does not immediately lock down every mapped user on the device.

On agent startup and on logind `SessionNew` / `SessionRemoved` events, the agent re-queries seat0 and reconciles. **Unenroll** clears the catalog and removes all enforcement.

### Enforcement

| Field | Mechanism |
|-------|-----------|
| Polkit toggles | Rules in `/etc/polkit-1/rules.d/50-timekpr-<username>.rules` (only when that user is the active seat0 session) |
| `terminalAccessDisabled` | Netlink exec monitor kills common shells and terminal emulators for the active session user only |
| `bluetoothDisabled` | `rfkill block bluetooth` when the active session user’s policy disables it (device-wide; hardware switches may override) |

Terminal blocking covers common paths such as `/usr/bin/bash`, `/bin/sh`, `/usr/bin/konsole`, and `/usr/bin/gnome-terminal`. Custom shells or interpreters are not blocked in v1.

Because Bluetooth rfkill is device-wide, a parent session may still see Bluetooth disabled while a child’s session is active. The admin UI notes this limitation.

**Browser managed policies** (Chromium/Firefox incognito, extensions, SafeSearch) are planned for a future release.

## Manual E2E checklist

1. Connect a Linux device with the Rust agent; link a child profile with allowlist app launch mode.
2. Assign an App Policy with Firefox `allowed`; leave Steam unapproved.
3. Launch Steam as the child — process is killed; a pending approval appears in the server UI.
4. Approve Steam from the child profile **Installed Apps** list (Linux paths use `target_kind: executable`).
5. Re-launch Steam — it should start normally.
6. Assign a domain blocklist with `approval_on_block`; visit a blocked site.
7. Confirm a desktop notification and pending domain request; approve the domain grant; site resolves.

## Auto-update integrity

When the server reports a version mismatch, the agent automatically downloads the matching GitHub release tarball (`.tar.gz`) along with its companion `.sha256` checksum file.

Before applying the update, the agent calculates the SHA-256 hash of the downloaded archive and compares it with the hash stored in the `.sha256` file to ensure the integrity of the download. If the hashes do not match, the update is rejected and the active binary remains unchanged.

The `.sha256` files are automatically generated during release builds in the GitHub Actions CI workflow, eliminating the need to manage secret signing keys in repository settings.

## Build and test

```bash
cd agent && cargo build --release
cargo test
```
