# Unenroll and factory reset

Remove family management from devices from **Admin → Devices** or device detail.

## Unenroll (all platforms with agents)

| Action | Agent behavior | Server |
|--------|----------------|--------|
| **Unenroll** | Clears token; stops enforcement | Device status `rejected` |

Linux: clears `/etc/timekpr-agent/config.json` token. Android: `AgentConfigStore.clearEnrollmentState()`. Windows: service removes policies and disconnects.

API: `POST /api/device/<system_id>/unenroll`

## Factory reset (Android Device Owner only)

| Channel | Behavior |
|---------|----------|
| WebSocket `factory_reset` | `DevicePolicyManager.wipeData()` |
| FCM `factory_reset` | Immediate wipe without full sync wait |

Requires **Device Owner**. If device offline, server sets `pending_factory_reset` and retries on reconnect + FCM.

!!! danger
    Factory reset erases all user data on the device. Use only with explicit consent.

## Cloud consoles

Remove mapping or delete device entry in UI. Unlink Nintendo/Xbox accounts under **Settings** if removing all consoles.

## Related

- [Device management](../web-ui/device-management.md)
- [Android agent — unenrollment](../platforms/android-agent.md#device-unenrollment-and-factory-reset)
