# Dashboard

The main dashboard (`/dashboard`) shows one card per **managed user** (child account).

## Card contents

- **Online status** — green when any mapped device has an active agent connection (or recent cloud activity for Switch/Xbox)
- **Time remaining today** — from schedule limits minus usage across valid mappings
- **Usage chart** — recent daily usage trend
- **Device labels** — mapped hardware with host UUID / IP snapshot where available

## Time adjustment

Use **+15m** / **-15m** (or the time adjust modal) for immediate credit or debit. Changes sync to connected agents via the worker and WebSocket commands (`modify_time_left`).

## Live updates

The dashboard polls `/api/dashboard` and may subscribe to `/api/dashboard/events` for server-sent style updates when device status changes.

## Related

- [Schedules & limits](schedules-and-limits.md)
- [Child accounts & mappings](child-accounts-and-mappings.md)
