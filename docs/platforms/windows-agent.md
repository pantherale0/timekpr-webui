# Windows agent

The Windows agent is built from the same Rust crate as the Linux agent (`agent/`) with Windows-specific service and session modules. It connects outbound to the Guardian WebSocket hub and enforces screen time, app blocking, and domain policies on Windows 10/11.

## Architecture

Two processes cooperate on each PC:

| Process | Role |
|---------|------|
| **Windows service** (`TimeKprAgent`) | Runs as SYSTEM; WebSocket loop, DNS proxy, process monitor, policy catalog |
| Per-session user agent | Runs in each logged-in user session; named-pipe IPC for toast notifications and full-screen overlay (`blockedv2.html` via Edge kiosk mode) |

The service exposes a named pipe at `\\.\pipe\timekpr_ipc` and broadcasts JSON events (for example block notifications) to user-session agents.

### Enforcement modules

- **DNS proxy** — redirects adapters to `127.0.0.1` for domain blocklists; outbound UDP/53 firewall rule reduces bypass
- **Process monitor** — ToolHelp snapshot loop; terminates blocked executables and enforces time lockouts
- **Clock integrity** — periodic wall-clock tamper detection via `QueryInterruptTime` cross-check and SNTP; triggers process lockout, parent alerts, and a full-screen Edge overlay on tamper
- **Policy store** — Windows user enumeration (RID ≥ 1000); AppArmor-like app rules per mapped username

On service stop, DNS settings and policies are restored.

## Installation

### Add Device wizard (recommended)

1. Open **Add Device** in the Web UI and choose **Windows PC**.
2. Copy the PowerShell install command or download the MSI from `/api/pairing/windows/msi`.
3. Run the installer **as Administrator** on the target PC.
4. Approve the pending device under **Admin → Devices**.
5. Map the Windows username to a child account.

Release MSIs are published on GitHub tagged releases (`guardian-agent-x86_64-pc-windows-msvc.msi`).

### Manual build

```bash
cargo build --release --manifest-path agent/Cargo.toml --target x86_64-pc-windows-msvc
```

CI compiles the MSI with WiX on tagged releases. See [CI & releases](../development/ci-release.md).

## Pairing and protocol

Uses the same `/ws` handshake as Linux: `hello` → approval → `pairing_approved` → `challenge` / `register` (HMAC). See [WebSocket protocol](../reference/websocket-protocol.md).

## Policies

| Policy | Windows behavior |
|--------|------------------|
| Screen time / schedule | Local time-limit store + process lockout |
| App policies | Process termination for blocked executables |
| Domain blocklists | Local DNS proxy (same manifest sync as Linux) |
| Device policy | Linux-style mapping restrictions where applicable |
| Screenshots | Supported on device detail page when enabled |
| YouTube history | Chrome extension force-installed via HKLM Registry |

App policy UI currently emphasizes Linux and Android; Windows uses the same underlying sync commands.

## Limitations

- No AppArmor or polkit — enforcement is process/DNS based
- Service runs elevated; user agent handles session UI only
- Requires administrator install for the MSI/service registration

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Screenshots](../features/screenshots.md)
- [Policy matrix](../reference/policy-matrix.md)
