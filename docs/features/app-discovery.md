# Application discovery and inventory reporting

Agents discover installed applications on managed devices and push inventory metadata (and optional icons) to the server. The web UI uses this inventory when configuring application policy rules.

## Protocol messages

### `installed_apps_report` (agent → server)

Chunked metadata report. Icons are **not** embedded in this message.

```json
{
  "type": "installed_apps_report",
  "report_id": "uuid",
  "linux_username": "alice",
  "chunk_index": 0,
  "chunk_total": 1,
  "is_final": true,
  "reported_at": "2026-06-05T12:00:00Z",
  "apps": [
    {
      "application_name": "Firefox",
      "identifier": "/usr/bin/firefox",
      "match_type": "executable",
      "version_name": "128.0",
      "icon_hash": "sha256hex"
    }
  ]
}
```

The server responds with `installed_apps_report_ack`:

```json
{
  "type": "installed_apps_report_ack",
  "report_id": "uuid",
  "success": true,
  "apps_upserted": 12,
  "apps_removed": 1,
  "apps_total": 12
}
```

When `is_final` is false, the ack includes `"pending": true`.

### `app_icon_report` (agent → server)

Content-addressed PNG upload (max 32 KB after agent-side resize):

```json
{
  "type": "app_icon_report",
  "content_hash": "sha256hex",
  "mime_type": "image/png",
  "data_base64": "..."
}
```

Duplicate hashes are ignored.

### `refresh_installed_apps` (server → agent RPC)

On-demand rescan. Response data: `{ "queued": true }`. The agent pushes fresh `installed_apps_report` messages after responding.

## Linux agent discovery

Sources scanned per monitored `linux_username`:

- `/usr/share/applications/`
- `/usr/local/share/applications/`
- `/var/lib/flatpak/exports/share/applications/`
- `/var/lib/snapd/desktop/applications/`
- `$HOME/.local/share/applications/`
- `$HOME/.local/share/flatpak/exports/share/applications/`

`.desktop` entries with `Hidden=true` or `NoDisplay=true` are skipped. The first absolute path token from `Exec=` becomes the identifier.

Icons are resolved from PNG pixmaps or common icon theme paths when available.

Reports are sent after authentication, every 24 hours while connected, and when `refresh_installed_apps` is invoked.

## Android agent discovery

Uses `PackageManager` to enumerate launcher-visible applications. Identifiers use the `/android/package/<packageName>` convention with `match_type: package`.

On multi-user devices, the device-owner process (user 0) reports inventory separately for each managed profile using `createPackageContextAsUser`, matching the `linux_username` values advertised in the hello `linux_users` payload. Secondary profile processes report only their own user when they connect independently.

Icons are rendered to 64×64 PNG and hashed before upload. Inventory is pushed after each authenticated sync session and on `refresh_installed_apps`.

## Server storage

- `device_installed_application` — per `(system_id, linux_username, identifier, match_type)`
- `application_icon` — deduplicated PNG blobs keyed by SHA-256
- `agent_device.installed_apps_*` — last report hash, timestamp, and count

## HTTP API

| Endpoint | Description |
|----------|-------------|
| `GET /api/devices/<system_id>/installed-apps` | Device inventory |
| `GET /api/managed-users/<id>/installed-apps` | Union across child device mappings |
| `GET /api/apps/icons/<content_hash>` | PNG icon bytes |
| `POST /api/devices/<system_id>/installed-apps/refresh` | Trigger agent rescan |

Session authentication is required for all endpoints except icon GET (public cacheable PNG).

## UI integration

- **App Policies** — “Discovered Apps” picker populated from devices linked to assigned children
- **Child profile** — read-only installed applications panel
- **Device detail** — per-mapping inventory with refresh button when the agent is online

Discovered entries feed `AppPolicyRule` creation using the same identifiers enforced by application policy sync.
