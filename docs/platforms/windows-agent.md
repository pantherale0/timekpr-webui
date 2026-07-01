# Windows agent

The Windows agent is built from the same Rust crate as the Linux agent (`agent/`) with Windows-specific service and session modules. It connects outbound to the Guardian WebSocket hub and enforces screen time, app blocking, and domain policies on Windows 10/11.

## Architecture

Two processes cooperate on each PC:

| Process | Role |
|---------|------|
| **Windows service** (`GuardianAgent`) | Runs as SYSTEM; WebSocket loop, DNS proxy, process monitor, policy catalog |
| Per-session user agent | Runs in each logged-in user session; named-pipe IPC for toast notifications and full-screen overlay (`blockedv2.html` via Edge kiosk mode) |

The service exposes a named pipe at `\\.\pipe\timekpr_ipc` and broadcasts JSON events (for example block notifications) to user-session agents.

### Enforcement modules

- **DNS proxy** — redirects adapters to `127.0.0.1` for domain blocklists; outbound UDP/53 firewall rule reduces bypass
- **Process monitor** — ToolHelp snapshot loop; terminates blocked executables and enforces time lockouts
- **Clock integrity** — periodic wall-clock tamper detection via `QueryInterruptTime` cross-check and SNTP; triggers process lockout, parent alerts, and a full-screen Edge overlay on tamper
- **Policy store** — Windows user enumeration (RID ≥ 1000); AppArmor-like app rules per mapped username, persisted under `C:\ProgramData\Guardian\` for offline enforcement
- **Safe Mode defense** — `GuardianAgent` is registered under SafeBoot Minimal and Network hives; on Safe Mode boot the service enters lockdown using cached local policy and blocks diagnostic shells
- **Local Administrator LAPS** — rotates the enabled built-in Administrator account when it uses a weak or unmanaged password; escrow travels to the server over WSS (never stored locally in plain text)
- **BCD integrity** — monitors boot configuration for unauthorized Safe Mode loops and intercepts `bcdedit` / `msconfig` tamper attempts

On service stop, DNS settings and policies are restored.

## Installation

### Add Device wizard (recommended)

1. Open **Add Device** in the Web UI and choose **Windows PC**.
2. Copy the PowerShell install command or download the MSI from `/api/pairing/windows/msi` (requires a signed-in admin session).
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
| Hardware baseline | Manual BIOS audit/apply via vendor CLIs (Dell CCTK, HP CMSL, Lenovo WMI, Surface SEMM); payloads downloaded from server |
| Safe Mode lockdown | Automatic strict process lockdown in Safe Mode; parents can release via device detail |
| Local Administrator LAPS | Automatic rotation + server escrow for the built-in Administrator account |
| Boot configuration | Continuous BCD audit and interception of boot-tool tampering |

App policy UI currently emphasizes Linux and Android; Windows uses the same underlying sync commands.

## Safe Mode and boot hardening

The MSI registers `GuardianAgent` under:

- `HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Minimal\GuardianAgent`
- `HKLM\SYSTEM\CurrentControlSet\Control\SafeBoot\Network\GuardianAgent`

The service self-heals these keys at startup if they are missing. During Safe Mode the agent skips DNS changes (Minimal mode may have no network stack) and relies on cached app/device policy files under `C:\ProgramData\Guardian\`.

Parents can release an active Safe Mode lockdown from the device detail page (`POST /api/devices/<system_id>/windows-laps/clear-safe-mode-lockdown`), which sends the `clear_safe_mode_lockdown` command to the agent.

## Local Administrator password escrow

After a successful WebSocket authentication on normal boot, the agent audits the built-in Administrator account (RID 500). If the account is enabled and unmanaged or weakly protected, the agent sets a random password and sends:

```json
{
  "type": "credential_escrow",
  "credential_type": "windows_local_admin",
  "rotation_id": "<uuid>",
  "occurred_at": "2026-06-20T12:00:00Z",
  "password": "<plaintext over WSS/TLS>"
}
```

The server encrypts this value with Fernet and exposes it through `GET /api/devices/<system_id>/windows-laps` and `POST .../windows-laps/reveal-password` (reveal requires `can_manage_policies` or household admin).

## Limitations

- No AppArmor or polkit — enforcement is process/DNS based
- Service runs elevated; user agent handles session UI only
- Requires administrator install for the MSI/service registration

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Screenshots](../features/screenshots.md)
- [Policy matrix](../reference/policy-matrix.md)
