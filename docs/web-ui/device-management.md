# Device management

**Admin → Devices** (`/admin/devices`) lists pending and approved hardware.

## Pending devices

New agent connections appear here until approved. Review hostname, platform, IP, and reported user profiles before approving.

Actions:

- **Approve** — issues per-device secret; agent receives `pairing_approved`
- **Reject** — blocks future auth (unless pending factory reset exception on Android)

## Approved devices

Click a device name for **device detail** (`/devices/<system_id>`):

- Platform-specific policy tabs (Android device policy, Linux restrictions, cloud stats)
- Per-mapping inventory and **Refresh installed apps**
- Screenshot gallery (Linux/Windows when enabled)
- **Sync Now** for Nintendo/Xbox
- **Unenroll** / **Factory reset** (Android DO)

## Online indicator

**Online** badge when WebSocket connected (agents) or inferred recent cloud activity (consoles).

## Labels

Friendly display names are editable on device detail; internal `system_id` UUID remains stable.

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Unenroll & factory reset](../workflows/unenroll-and-factory-reset.md)
