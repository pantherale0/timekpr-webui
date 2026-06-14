# App policies

Manage reusable profiles under **Admin → App Policies** (`/admin/app-policies`).

## Policy rules

Each rule targets an application by identifier:

| Platform | Identifier examples |
|----------|---------------------|
| Linux | `/usr/bin/firefox`, `$HOME/Games/**` |
| Android | `com.example.app` or `/android/package/com.example.app` |
| Windows | Executable paths (same family as Linux) |

**Presets:** `blocked`, `no_internet`, `complain` (alert-only).

## Discovered apps picker

Rules can be created from **installed app inventory** reported by agents. See [App discovery](../features/app-discovery.md).

## Assign to children

Link policies on the child profile edit page. The server sends `sync_apparmor_policy` to mapped devices with optional **approval policy** overlays (allowlist/blocklist modes).

## Platform note

The admin UI emphasizes Linux and Android discovery; Windows agents consume the same sync payload where supported.

## Related

- [Access requests](access-requests.md)
- [Policy assignment](../workflows/policy-assignment.md)
