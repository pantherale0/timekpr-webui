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

## Monitoring

- `GET /api/task-status` — JSON status
- `POST /restart-tasks` — administrative restart hook

## Production guidance

!!! warning
    Do not run the worker inside each Gunicorn worker — use one dedicated `task_worker.py` process to avoid duplicate sync jobs.

## Related

- [Configuration reference](../getting-started/configuration.md)
- [Cloud console setup](../workflows/cloud-console-setup.md)
