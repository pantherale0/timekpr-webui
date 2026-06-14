# Policy assignment

Policies attach to **child accounts** and flow to devices through **mappings**.

## Order of operations

1. **Child account** exists with weekly schedule and allowed hours
2. **Device mapping** links child to `(system_id, linux_username)`
3. **Optional overlays:**
   - Web filter sources (domain manifest)
   - App policy profiles
   - Approval modes
   - Platform device policy (Linux polkit / Android AMAPI fields)

## Sync path

| Trigger | Behavior |
|---------|----------|
| Agent online (Linux/Windows) | Immediate WebSocket commands |
| Agent offline | Queued; applied on reconnect |
| Android idle | FCM `sync_policies` wake or ~4h WorkManager |
| Nintendo/Xbox | Worker cloud push on schedule change |

Agents may send `policy_sync_check` to pull latest domain manifest hashes.

## Verify

Use **Verify** on a mapping or child profile to run `validate_user` and refresh sync badges.

## Related

- [Schedules & limits](../web-ui/schedules-and-limits.md)
- [Web filters](../web-ui/web-filters.md)
- [App policies](../web-ui/app-policies.md)
- [Device restrictions](../features/device-restrictions.md)
