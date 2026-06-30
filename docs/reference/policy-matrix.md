# Policy matrix

How Guardian policies map to each platform enforcement layer.

| Policy area | Linux agent | Android agent | Windows agent | Nintendo Switch | Xbox |
|-------------|-------------|---------------|---------------|-----------------|------|
| **Daily limits & schedule** | TimeKpr-nExT D-Bus | Local `UsageMonitorService` | Local time-limit store | Nintendo Parental Controls API | Xbox Family Safety |
| **Lockout action** | TimeKpr-nExT session lock | Overlay + package suspension | Process lockout | Console-native | Console-native |
| **App execution** | Netlink SIGKILL | `setPackagesSuspended` | Process termination | Not supported | Not supported |
| **App approval mode** | Exec path blocking + alerts | Suspension + overlay | Process monitor | Not supported | Not supported |
| **Domain blocklists** | Local DNS sinkhole | DNS VPN service | DNS proxy | Not supported | Not supported |
| **Hardware restrictions** | Polkit, rfkill, terminal block | Camera, mic, USB, BT, etc. | Limited | Not supported | Not supported |
| **System restrictions** | Package managers (apt/snap/flatpak) | Install/uninstall, dev settings | Service-level | Not supported | Not supported |
| **Browser restrictions** | Chrome Enterprise policies | Not supported | Not supported | Not supported | Not supported |
| **Bedtime / sleep** | Schedule intervals | Schedule windows | Schedule windows | Bedtime alarm push | Device limits schedule |
| **Screenshots** | Supported | Not supported | Supported | Not supported | Not supported |
| **YouTube history** | Browser extension (Chrome) | Accessibility Service | Browser extension (Chrome) | Not supported | Not supported |
| **Remote wipe** | Not supported | Device Owner `wipeData` | Not supported | Not supported | Not supported |

## Connection model

| Platform | Transport |
|----------|-----------|
| Linux / Windows | Persistent WebSocket `/ws` |
| Android | FCM wake + ephemeral WebSocket |
| Nintendo / Xbox | Server HTTPS polling (worker) |

## Related

- [Android bypass matrix](../platforms/android-bypasses.md)
- [Platform docs](../platforms/linux-agent.md)
- [Policy assignment](../workflows/policy-assignment.md)
