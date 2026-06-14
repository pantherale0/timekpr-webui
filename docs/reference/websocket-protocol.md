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
  "registration_token": "optional",
  "fcm_token": "android-only",
  "is_device_owner": false
}
```

Server updates device record (`linux_users_json`, hostname, IP, push metadata).

## Version check

If `agent_version` ≠ `TIMEKPR_SERVER_VERSION` (release servers), server responds with `auth_result` failure and Android may receive `update_required`, `apk_url`, `signature_checksum`.

## Pending approval

New devices → `status: pending`. Server sends `pairing_status` and holds connection until admin approval or disconnect.

## Pairing approved

Server → agent:

```json
{
  "type": "pairing_approved",
  "token": "per-device-secret"
}
```

Agent stores token and reconnects with `paired: true`.

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
| `installed_apps_report` | App inventory chunks |
| `app_icon_report` | PNG icon upload |
| `screenshot_report` | Desktop screenshot upload |

## Server → agent commands

Delivered as JSON with `action` field (via `AgentClient`), including:

- `validate_user`, `modify_time_left`, `set_weekly_time_limits`, `set_allowed_hours`
- Domain policy sync sequence (`begin_domain_policy_sync`, chunks, manifest, finalize)
- `sync_apparmor_policy`, `sync_linux_device_policy`, `sync_android_device_policy`
- `refresh_installed_apps`, `unenroll`, `factory_reset`

See platform docs and [App discovery](../features/app-discovery.md).

## Android notes

- Registers `fcm_token` + `platform: android` in hello
- Server sends FCM data messages (`sync_policies`, `pairing_approved`, `factory_reset`) when offline
- May receive `persistent_connection: true` on auth success (platform-specific)

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Security](../getting-started/security.md)
