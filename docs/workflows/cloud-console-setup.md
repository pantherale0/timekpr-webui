# Cloud console setup

Nintendo Switch and Xbox use the same high-level wizard; linking happens once per platform under **Settings**.

## Nintendo Switch

1. [Link Nintendo Account](../web-ui/settings.md) under Settings
2. **Add Device → Nintendo Switch**
3. Select console → label → map player (Mii nickname) → child account
4. Confirm playtime appears after worker sync or **Sync Now**

Details: [Nintendo Switch platform](../platforms/nintendo-switch.md)

## Xbox

1. [Link Xbox Live Account](../web-ui/settings.md)
2. **Add Device → Xbox**
3. Select console → map family player → child account
4. **Sync Now** to refresh Family Safety stats

Details: [Xbox platform](../platforms/xbox.md)

## Limitations

Cloud consoles do **not** support:

- Domain blocklists
- Per-app launch policies
- Agent-style screenshots or hardware USB restrictions

Only playtime limits, schedules/bedtime, and usage reporting sync.

## Worker requirement

Run `task_worker.py` (Docker `tasks` service) with `TIMEKPR_TASKS_UPDATE_USER_DATA` enabled.

## Related

- [Policy matrix](../reference/policy-matrix.md)
- [Troubleshooting — cloud sync](../troubleshooting/index.md)
