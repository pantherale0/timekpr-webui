# Linux (Rust) agent

The Rust agent connects outbound to the TimeKpr server over WebSocket, enforces AppArmor policies, domain blocklists via local DNS, and reports usage/alerts.

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

## Manual E2E checklist

1. Connect a Linux device with the Rust agent; link a child profile with allowlist app launch mode.
2. Assign an App Policy with Firefox `allowed`; leave Steam unapproved.
3. Launch Steam as the child — process is killed; a pending approval appears in the server UI.
4. Approve Steam from the child profile **Installed Apps** list (Linux paths use `target_kind: executable`).
5. Re-launch Steam — it should start normally.
6. Assign a domain blocklist with `approval_on_block`; visit a blocked site.
7. Confirm a desktop notification and pending domain request; approve the domain grant; site resolves.

## Build and test

```bash
cd agent && cargo build --release
cargo test
```
