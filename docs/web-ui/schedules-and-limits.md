# Schedules and limits

Configure under **Weekly Schedule** (`/weekly-schedule`).

## Weekly limits

Set per-day time allowances (hours/minutes) for each child. Values sync to:

- Linux/Windows/Android agents (`set_weekly_time_limits`)
- Nintendo/Xbox cloud schedules when mappings exist

## Allowed hours

Define daily time **windows** (e.g. only 16:00–20:00 on school nights). Outside windows, agents treat screen time as exhausted even if daily budget remains.

## Sync status

The UI shows sync badges (~5 second refresh) indicating whether schedule data reached online devices. Offline agents apply changes on next connection.

## API

- `POST /weekly-schedule/update` — form POST from UI
- `GET /api/schedule-sync-status/<user_id>` — JSON sync state

## Related

- [Policy assignment](../workflows/policy-assignment.md)
- [Dashboard](dashboard.md)
