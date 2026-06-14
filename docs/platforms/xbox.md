# Xbox (cloud)

Xbox consoles are managed through Microsoft's **Family Safety** cloud API via the `pyfamilysafety` library. Like Nintendo Switch, there is no on-console agent.

## Prerequisites

- An Microsoft account with Xbox Family Safety configured for the target console(s)
- Family members/player profiles visible in Family Safety

## Link your Xbox account (once)

1. Open **Settings → Xbox Live Account** in the Web UI.
2. Click sign-in and complete Microsoft OAuth in the browser.
3. Paste the redirect URL into the authenticate step (same pattern as Nintendo linking).
4. The refresh token is stored in server settings (`xbox_refresh_token`).

Use **Unlink** on the settings page to remove stored credentials.

## Add a console

1. **Add Device → Xbox** in the onboarding wizard.
2. Select the console from your linked account.
3. Assign a device label and map a **family player profile** to a child account.
4. The console appears under **Admin → Devices**.

## What syncs

| Guardian feature | Xbox Family Safety |
|------------------|-------------------|
| Daily / weekly playtime limits | Pushed as device limits schedule |
| Playtime reporting | Pulled into usage stats |
| Domain blocklists | Not supported |
| Per-app policies | Not supported |

Cloud sync runs inside the `TIMEKPR_TASKS_UPDATE_USER_DATA` worker cycle (~5 minute throttle). Force sync with **Sync Now** or `POST /api/xbox/sync`.

## API endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/xbox/login-url` | Microsoft OAuth URL |
| `POST /api/xbox/authenticate` | Store tokens from redirect |
| `GET /api/xbox/devices` | List consoles + players |
| `GET /api/xbox/account-status` | Link state; `?validate=true` checks token |
| `POST /api/xbox/import-device` | Enroll console |
| `POST /api/xbox/unlink` | Remove credentials |
| `POST /api/xbox/sync` | Force cloud sync |

## Stale data

Console detail stats may show stale after ~30 minutes without sync. Use **Sync Now** or verify the linked account session with account status validation.

## Related

- [Cloud console setup](../workflows/cloud-console-setup.md)
- [Nintendo Switch](nintendo-switch.md) (similar cloud pattern)
- [Policy matrix](../reference/policy-matrix.md)
