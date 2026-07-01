# WebSocket protocol

Endpoint: **`/ws`** (Flask-Sock). JSON messages, one object per frame.

## Agent → server: `hello`

First message after connect (10s timeout):

```json
{
  "type": "hello",
  "system_id": "uuid",
  "system_hostname": "hostname",
  "agent_version": "v1.0.0",
  "platform": "linux",
  "linux_users": [{"username": "child", "uid": 1000, "platform": "linux"}],
  "paired": true,
  "registration_token": "household-enrollment-or-global-token",
  "fcm_token": "android-only",
  "is_device_owner": false
}
```

Server updates device record (`linux_users_json`, hostname, IP, push metadata).

### Registration / enrollment token

New devices must present a token the server accepts:

| Token source | When accepted |
|--------------|---------------|
| Household `enrollment_token` | Matches the household that will own the pending device |
| `REGISTRATION_TOKEN` env | Matches global server token (assigns default/first household) |
| *(none configured)* | Open registration — first/default household (dev/single-tenant only) |

Invalid or missing tokens on a **new** `hello` are rejected with `auth_result` failure.

## Version check

If `agent_version` ≠ `TIMEKPR_SERVER_VERSION` (release servers), server responds with `auth_result` failure and Android may receive `update_required`, `apk_url`, `signature_checksum`.

## Pending approval

New devices → `status: pending`. Server sends `pairing_status` and holds connection until admin approval or disconnect.

## Pairing approved

Server → agent (only after admin approval **and** valid enrollment/registration token when client reports `paired: false`):

```json
{
  "type": "pairing_approved",
  "token": "per-device-secret"
}
```

Agents store the token and reconnect with `paired: true`. Cleartext token delivery over an unauthenticated WebSocket is never performed without enrollment proof.

## Authentication (paired devices)

1. Server → `{ "type": "challenge", "challenge": "<64-byte hex>" }`
2. Agent → `{ "type": "register", "system_id": "...", "signature": "hmac-sha256(challenge+system_id)" }`
3. Server → `{ "type": "auth_result", "success": true }`

Rejected/banned devices receive `success: false` (except Android pending factory reset edge case).

## Post-auth agent messages

| Type | Purpose |
|------|---------|
| `command_response` | Reply to server RPC |
| `policy_sync_check` | Request domain policy refresh |
| `alert_event` | Usage/security alerts |
| `credential_escrow` | Agent-initiated secret escrow (Windows local Administrator password) |
| `installed_apps_report` | App inventory chunks |
| `app_icon_report` | PNG icon upload |
| `screenshot_report` | Desktop screenshot upload |

### `credential_escrow` (Windows)

Sent after the agent rotates the built-in local Administrator password:

```json
{
  "type": "credential_escrow",
  "credential_type": "windows_local_admin",
  "rotation_id": "uuid",
  "occurred_at": "2026-06-20T12:00:00Z",
  "password": "plaintext-over-wss"
}
```

The server encrypts the password at rest; it is never written to the device filesystem.

### `alert_event` integrity types

| `event_type` | Meaning |
|--------------|---------|
| `clock_tamper` | Wall-clock skew detected |
| `boot_config_tamper` | Unauthorized Safe Mode / BCD change detected or intercepted |

## Server → agent commands

Delivered as JSON with `action` field (via `AgentClient`), including:

- `validate_user`, `modify_time_left`, `set_weekly_time_limits`, `set_allowed_hours`
- Domain policy sync sequence (`begin_domain_policy_sync`, chunks, manifest, finalize)
- `sync_apparmor_policy`, `sync_linux_device_policy`, `sync_android_device_policy`
- `refresh_installed_apps`, `unenroll`, `factory_reset`
- `clear_safe_mode_lockdown` (Windows Safe Mode lockdown override)

See platform docs and [App discovery](../features/app-discovery.md).

## Android notes

- Registers `fcm_token` + `platform: android` in hello
- Server sends FCM data messages (`sync_policies`, `pairing_approved`, `factory_reset`) when offline
- May receive `persistent_connection: true` on auth success (platform-specific)

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Security](../getting-started/security.md)
