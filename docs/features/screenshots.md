# Screenshots

Guardian can capture periodic or on-demand desktop screenshots from **Linux and Windows** agents for review in the device detail page.

## Enable

On device detail → **Screenshots** tab:

1. Configure interval and quality settings (`PUT /api/devices/<system_id>/screenshot-settings`)
2. **Sync settings** to push policy to agent
3. Use **Capture now** for immediate snapshot

## Storage

Screenshots upload via WebSocket `screenshot_report` messages and are stored server-side. List via `GET /api/devices/<system_id>/screenshots`.

## Privacy

!!! warning
    Screenshot capture is invasive. Enable only with household agreement and secure server access.

## Not supported

- Android (OS sandbox prevents equivalent capture in agent model)
- Nintendo / Xbox cloud devices

## API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/devices/<system_id>/screenshot-settings` | GET/PUT | Policy |
| `/api/devices/<system_id>/screenshot-settings/sync` | POST | Push to agent |
| `/api/devices/<system_id>/screenshots/capture` | POST | On-demand |
| `/api/devices/<system_id>/screenshots` | GET/DELETE | List / purge |
| `/api/screenshots/<id>` | GET | Image bytes |

Session auth required.

## Related

- [Windows agent](../platforms/windows-agent.md)
- [WebSocket protocol](../reference/websocket-protocol.md)
