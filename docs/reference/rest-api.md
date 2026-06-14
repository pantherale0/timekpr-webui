# REST API overview

Guardian exposes JSON and form endpoints through Flask blueprints under `server/src/blueprints/api/`. Session cookie auth applies to most admin routes unless noted.

!!! note
    This reference lists primary routes; see source blueprints for full request bodies and CSRF requirements.

## Devices

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/devices/pending` | List pending devices |
| POST | `/api/device/approve/<system_id>` | Approve device |
| POST | `/api/device/reject/<system_id>` | Reject device |
| POST | `/api/device/<system_id>/unenroll` | Unenroll device |

## Pairing

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/pairing/config` | In-app QR JSON |
| GET | `/api/pairing/qr.png` | QR image |
| GET | `/api/pairing/provisioning/config` | MDM QR JSON |
| GET | `/api/pairing/provisioning/qr.png` | MDM QR image |
| GET | `/api/pairing/provisioning/apk` | Dev/uploaded APK |
| GET | `/api/pairing/windows/msi` | Windows MSI download |

## Users and mappings

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/users` | List managed users |
| POST | `/api/user/create` | Create user (JSON) |
| GET | `/api/user/<id>/usage` | Usage snapshot |
| GET | `/api/user/<id>/stats` | Extended stats |
| POST | `/managed-users/add` | Form: add user |
| POST | `/managed-users/<id>/mappings/add` | Add mapping |
| GET | `/users/validate/<id>` | Validate all mappings |

## Schedule and time

| Method | Path | Description |
|--------|------|-------------|
| POST | `/weekly-schedule/update` | Update weekly limits |
| POST | `/api/modify-time` | +/- time adjustment |
| GET | `/api/schedule-sync-status/<user_id>` | Sync badges |

## Blocklists

| Method | Path | Description |
|--------|------|-------------|
| POST | `/blocklists/sources/add` | Create source |
| POST | `/blocklists/sources/<id>/refresh` | Refresh external URL |
| POST | `/managed-users/<id>/blocklists/update` | Assign sources |
| GET | `/api/user/<id>/blocklists/sync-status` | Sync state |

## Installed apps

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/devices/<system_id>/installed-apps` | Device inventory |
| GET | `/api/managed-users/<id>/installed-apps` | Union for child |
| GET | `/api/apps/icons/<hash>` | PNG icon (public cache) |
| POST | `/api/devices/<system_id>/installed-apps/refresh` | Trigger rescan |

## Approvals

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/approvals` | List requests |
| POST | `/api/approvals/<id>/approve` | Approve |
| POST | `/api/approvals/<id>/deny` | Deny |
| GET/POST | `/api/mappings/<id>/approval-settings` | Mode config |
| GET/POST | `/api/mappings/<id>/approval-grants` | Grants |

## Device policy

| Method | Path | Description |
|--------|------|-------------|
| GET/PUT | `/api/devices/<system_id>/android-device-policy` | Android policy |
| GET/PUT | `/api/mappings/<id>/android-device-policy` | Per-mapping Android |
| GET/PUT | `/api/mappings/<id>/linux-device-policy` | Linux restrictions |

## Nintendo / Xbox

See [Nintendo Switch](../platforms/nintendo-switch.md) and [Xbox](../platforms/xbox.md) for `/api/nintendo/*` and `/api/xbox/*` routes.

## Screenshots

See [Screenshots](../features/screenshots.md).

## Dashboard and tasks

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/dashboard` | Dashboard JSON |
| GET | `/api/dashboard/events` | Live events stream |
| GET | `/api/task-status` | Worker heartbeat |
| POST | `/restart-tasks` | Restart worker hooks |

## Alerts

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/alerts` | List alerts |
| POST | `/api/alerts/prune` | Prune old alerts |

## Related

- [WebSocket protocol](websocket-protocol.md)
- [Auth & OIDC](auth-and-oidc.md)
