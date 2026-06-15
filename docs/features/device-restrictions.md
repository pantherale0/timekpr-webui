# Device restrictions

Platform-specific hardware and system restrictions configured per **device mapping** in the admin UI.

## Linux (`sync_linux_device_policy`)

Polkit rules, Bluetooth rfkill, and terminal exec blocking for the **active seat0 session user** only.

| Category | Examples |
|----------|----------|
| Polkit | Block software install/uninstall, removable media, account changes, power actions, pkexec |
| Connectivity | Bluetooth disabled |
| Exec | Terminal/shell blocking via process monitor |

Catalog stored in `/var/lib/guardian-agent/linux-device-policy.json`. See [Linux agent](../platforms/linux-agent.md). For browser-specific security policies (like Incognito blocking and YouTube restricted mode), see [Browser restrictions](browser-restrictions.md).

## Android (`sync_android_device_policy`)

AMAPI-aligned fields pushed to Device Owner agents:

- Camera, microphone, screen capture
- App install/uninstall, factory reset protection
- Bluetooth, USB data transfer, developer settings
- Custom short/long support messages

!!! warning
    Device-admin-only Android installs show a UI warning and skip most restrictions. Package suspension and lockout also require Device Owner or profile owner.

See [Android agent](../platforms/android-agent.md).

## Windows

Process and DNS enforcement cover most parental scenarios; dedicated device-policy UI parity with Linux is limited.

## Cloud consoles

No hardware restriction sync for Nintendo/Xbox.

## Related

- [Browser restrictions](browser-restrictions.md)
- [Policy matrix](../reference/policy-matrix.md)
- [Policy assignment](../workflows/policy-assignment.md)
