# Device management

**Admin → Devices** (`/admin/devices`) lists pending and approved hardware.

## Pending devices

New agent connections appear here until approved. Review hostname, platform, IP, and reported user profiles before approving.

Actions:

- **Approve** — issues per-device secret; agent receives `pairing_approved` (requires `can_manage_policies` or household admin)
- **Reject** — blocks future auth (unless pending factory reset exception on Android)

Parents without **Manage policies** permission on a linked child can view devices but cannot approve, reject, unenroll, capture screenshots, or apply hardware baseline changes.

## Approved devices

Click a device name for **device detail** (`/devices/<system_id>`). The default **At a glance** tab shows protection status, linked children, and console play time. Deeper controls are grouped by tab:

- **Device settings** — screen history, Nintendo/Xbox sync, Android routine locks, parental access code
- **Activity** — installed apps, runtime tracker, screenshot gallery
- **Advanced** — unenroll, factory reset, website-filter sync details, ADB setup, connection history, technical identifiers

## Online indicator

**Online** badge when WebSocket connected (agents) or inferred recent cloud activity (consoles).

## Labels

Friendly display names are editable on device detail; internal `system_id` UUID remains stable.

## Related

- [Pairing & approval](../workflows/pairing-and-approval.md)
- [Unenroll & factory reset](../workflows/unenroll-and-factory-reset.md)
