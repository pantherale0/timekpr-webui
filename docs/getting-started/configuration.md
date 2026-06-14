# Configuration reference

Environment variables for the Guardian server. Docker Compose reads these from `.env`.

## Required

| Variable | Description |
|----------|-------------|
| `AGENT_TOKEN` | Bootstrap token for initial agent pairing (never sent over the wire after HMAC enrollment) |

## Core

| Variable | Description | Default |
|----------|-------------|---------|
| `TZ` | Server timezone for schedules and display | `UTC` |
| `TIMEKPR_SERVER_VERSION` | Version string checked at agent `hello` | `v0.0.0-dev` |
| `DATABASE_URL` | SQLAlchemy URI (`sqlite:///…` or `postgresql://…`) | SQLite file |
| `TIMEKPR_AGENT_WS_URL` | Public WebSocket URL in pairing QR codes | Auto-detected |
| `DEBUG` | Flask debug mode | off in production |

## Pairing and Android

| Variable | Description |
|----------|-------------|
| `REGISTRATION_TOKEN` | Optional firewall: new pairings must include this token in `hello` |
| `FCM_SERVER_KEY` | Firebase legacy HTTP API key for Android push wake |
| `FIREBASE_CREDENTIALS_JSON` | Path or inline JSON for Firebase HTTP v1 (preferred) |
| `FIREBASE_PROJECT_ID` | Optional when service account JSON omits `project_id` |

## Background tasks

| Variable | Description | Default |
|----------|-------------|---------|
| `TIMEKPR_ENABLE_BACKGROUND_TASKS` | Master switch when app imported as module | off |
| `TIMEKPR_TASKS_REFRESH_EXTERNAL` | Refresh external blocklist sources | enabled |
| `TIMEKPR_TASKS_UPDATE_USER_DATA` | User sync + Nintendo + Xbox cloud | enabled |
| `TIMEKPR_TASKS_SYNC_DOMAIN_POLICIES` | Domain policy sync flag | enabled |
| `TIMEKPR_TASKS_DELIVER_ALERTS` | Outbound alert webhook delivery | enabled |

Set a task flag to `0`, `false`, `no`, or `off` to disable. See [Background worker](../reference/background-worker.md).

## OIDC (optional)

See [Auth & OIDC](../reference/auth-and-oidc.md) for `OIDC_*` and `ALLOWED_OIDC_*` variables.

## Agent-side configuration

| Platform | Location |
|----------|----------|
| Linux | `/etc/timekpr-agent/config.json` (root `0600`) |
| Windows | Service + per-user agent config via installer |
| Android | `AgentConfigStore` (SharedPreferences) |
| Debug agent | `server/debug-agent.json` |

## Version matching

Release servers require matching `agent_version` and `TIMEKPR_SERVER_VERSION`. Dev servers (`v0.0.0-dev`) accept any agent version.
