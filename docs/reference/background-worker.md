# Background worker

Long-running tasks run in **`BackgroundTaskManager`** — either embedded when running `python app.py` directly, or preferably as a separate **`task_worker.py`** process / Docker `tasks` service.

## Cycle

Default loop interval ~**10 seconds**. Each cycle runs enabled tasks based on environment flags.

## Master switch

`TIMEKPR_ENABLE_BACKGROUND_TASKS` — when importing the app as a WSGI module, set to `true`/`1`/`yes`/`on` to start in-process tasks. Direct `python app.py` always starts tasks.

## Per-task flags

Disabled when value is `0`, `false`, `no`, or `off`. Default: **enabled**.

| Variable | Task |
|----------|------|
| `TIMEKPR_TASKS_REFRESH_EXTERNAL` | Download/update external blocklist URLs |
| `TIMEKPR_TASKS_UPDATE_USER_DATA` | Push user schedule data to online agents; **Nintendo + Xbox cloud sync** |
| `TIMEKPR_TASKS_SYNC_DOMAIN_POLICIES` | Domain policy sync coordination |
| `TIMEKPR_TASKS_DELIVER_ALERTS` | POST alert webhooks |

## Cloud sync throttle

Nintendo and Xbox sync inside `UPDATE_USER_DATA` with ~**5 minute** minimum interval per device unless **Sync Now** forces `force=True`.

## Agent-initiated sync

Domain policies primarily sync when agents send `policy_sync_check` over WebSocket. Device policy helpers push on mapping save and reconnect.

## Offline pending commands

When an agent is offline, the server enqueues work in the `pending_command` table (`server/src/pending_commands_manager.py`):

- **Imperative commands** — screenshots, installed-app refresh, unenroll/factory reset; replayed FIFO with stored args.
- **Policy snapshots** — Linux/Android device policy, AppArmor policy, screenshot policy, weekly limits, allowed hours; coalesced per device/user and rebuilt from the database at flush time.
- **Domain reconcile** — one `domain_policy_reconcile` marker per device runs the full domain-policy sync pipeline.

On WebSocket authentication success, a background thread drains the queue for that device. Expired commands are pruned each worker cycle. Android devices may also receive FCM wake hints after enqueue.

## Monitoring

- `GET /api/task-status` — JSON status
- `POST /restart-tasks` — administrative restart hook

## Production guidance

!!! warning
    Do not run the worker inside each Gunicorn worker — use one dedicated `task_worker.py` process to avoid duplicate sync jobs.

## Related

- [Configuration reference](../getting-started/configuration.md)
- [Cloud console setup](../workflows/cloud-console-setup.md)
