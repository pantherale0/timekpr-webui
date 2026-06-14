# Overview

Guardian manages child accounts, devices, and policies from a single web dashboard. Each **managed user** (child profile) can be linked to one or more **device mappings**—local OS usernames on Linux/Windows, Android user profiles, or cloud player IDs on Nintendo/Xbox.

## Components

| Component | Role |
|-----------|------|
| **Flask web app** (`server/app.py`) | UI, REST API, WebSocket hub at `/ws` |
| **Background worker** (`server/task_worker.py`) | Blocklist refresh, policy push hints, Nintendo/Xbox sync, alert webhooks |
| **Linux/Windows agent** (`agent/`) | Persistent WebSocket; enforces time, apps, domains, device policy |
| **Android agent** (`android-agent/`) | FCM-wake + short WebSocket sessions; Device Owner enforcement |
| **Debug agent** (`server/debug_agent.py`) | Python simulator for development |

## Typical deployment flow

1. [Deploy the server](server-deployment.md) (Docker recommended).
2. Sign in and change the default password.
3. Create child accounts under **Admin → Child Accounts**.
4. Pair agents or import cloud consoles ([pairing workflow](../workflows/pairing-and-approval.md)).
5. Map each device profile to a child account.
6. Configure [schedules](../web-ui/schedules-and-limits.md), [web filters](../web-ui/web-filters.md), and [app policies](../web-ui/app-policies.md).

## Architecture principles

- **Outbound-only** — child devices initiate connections; suitable for home NAT and mobile networks.
- **Per-device secrets** — after approval, each agent stores its own token; revoking a device does not rotate the global bootstrap token.
- **Platform-native enforcement** — Linux uses AppArmor and DNS sinkhole; Android uses VPN and package suspension; cloud platforms use vendor parental-control APIs.

Next: [Server deployment](server-deployment.md)
