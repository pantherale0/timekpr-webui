# Nintendo Switch (cloud)

Nintendo Switch consoles are managed through Nintendo's **Parental Controls cloud API**. There is no on-console Guardian agent.

## Prerequisites

- Parental Controls enabled on the Switch (Nintendo Switch Parental Controls app or system settings)
- A Nintendo Account that owns/manages those parental controls

## Link your Nintendo Account (once)

1. Open **Settings → Nintendo Switch Account** in the Web UI.
2. Click **Sign In to Nintendo Account** and complete browser login.
3. On the final **Select this person** screen, copy the button link and paste it into the redirect URL field if required.
4. Click **Link Account**. The session is stored server-side for future imports.

Re-link from the same page if sync errors indicate an expired session.

## Add a console

1. **Add Device → Nintendo Switch** in the onboarding wizard.
2. Select the console, assign a label, and map a **player profile** (Mii nickname) to a child account.
3. The console appears under **Admin → Devices** with cloud sync status.

Consoles skip the pending-approval queue—they import as approved cloud devices.

## What syncs

| Guardian feature | Nintendo cloud |
|------------------|----------------|
| Daily playtime limits | Pushed from weekly schedules |
| Bedtime / sleep schedule | Pushed as bedtime alarm |
| Playtime reporting | Pulled into dashboard and child profile |
| Domain blocklists | Not supported |
| Per-app policies | Not supported |

The background worker polls approximately every five minutes (throttled). Use **Sync Now** on the device detail page for an immediate refresh.

## API endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/nintendo/login-url` | OAuth login URL |
| `POST /api/nintendo/authenticate` | Complete link with redirect URL |
| `GET /api/nintendo/devices` | List consoles |
| `POST /api/nintendo/import-device` | Enroll console |
| `POST /api/nintendo/sync` | Force sync |

## Notes

- The parental controls PIN is set on the console and is **not** exposed by the API.
- Console **active** status is inferred from recent playtime changes (no persistent connection).
- Ensure the **tasks** worker runs with `TIMEKPR_TASKS_UPDATE_USER_DATA` enabled.

## Related

- [Cloud console setup](../workflows/cloud-console-setup.md)
- [Policy matrix](../reference/policy-matrix.md)
