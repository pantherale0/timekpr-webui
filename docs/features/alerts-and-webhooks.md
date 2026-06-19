# Alerts and webhooks

Agents emit **alert events** over WebSocket; the server stores them for the admin UI and optional outbound webhooks.

## Allowed alert types

Enforced in `ALLOWED_AGENT_ALERT_TYPES`:

| Category | Types |
|----------|-------|
| System | `system_startup`, `system_sleep`, `system_resume`, `system_restart`, `system_shutdown` |
| Session | `user_signed_in`, `user_signed_out` |
| Apps | `app_launched`, `app_blocked`, `app_usage` |
| Approvals | `access_requested`, `terminal_command` |
| Integrity | `clock_tamper` (Linux, Android, and Windows agents) |

Unknown types are rejected at ingest.

## Payload shape

```json
{
  "type": "alert_event",
  "event_type": "access_requested",
  "linux_username": "child",
  "occurred_at": "2026-06-14T12:00:00Z",
  "details": { }
}
```

Timestamps must be ISO-8601 (`Z` normalized to UTC).

## Admin UI

**Admin → Alerts** (API `/api/alerts`) lists recent events. Access requests also surface under **Access Requests**.

Retention pruning: `POST /api/alerts/prune` (automated by worker when configured).

## Webhook delivery

Configure under **Settings**:

1. Enable webhook + URL
2. Optional shared secret for HMAC header `X-Timekpr-Signature: sha256=…`

The worker (`TIMEKPR_TASKS_DELIVER_ALERTS`) POSTs JSON for pending alerts. Failed deliveries remain retryable according to server logic.

## Related

- [Access requests](../web-ui/access-requests.md)
- [WebSocket protocol](../reference/websocket-protocol.md)
