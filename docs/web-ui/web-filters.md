# Web filters (domain blocklists)

Manage under **Admin → Web Content Filters** (`/admin/restrictions`).

## Blocklist sources

Create sources that hold domain lists:

- **Manual** — enter domains in the UI
- **External URL** — periodic refresh by the background worker (`TIMEKPR_TASKS_REFRESH_EXTERNAL`). URLs must be public `http`/`https` endpoints; private, loopback, and link-local hosts are rejected (SSRF protection).

## Assign to children

On each child profile (**Edit Profile → Web filters**), select which sources apply. The server builds a per-user domain policy manifest and syncs to agents via WebSocket domain policy commands.

## Sync status

Per-user sync badges show whether agents acknowledged the latest manifest. Android devices may delay sync until FCM wake or periodic WorkManager cycle.

## Approval mode

When **approval on block** is enabled for a mapping, blocked domains can trigger **access requests** instead of silent blocks. See [Access requests](access-requests.md).

## Related

- [Linux agent — domain policy](../platforms/linux-agent.md)
- [Android agent — domain VPN](../platforms/android-agent.md)
