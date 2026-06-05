# TimeKpr Android Agent

Kotlin/Android port of the TimeKpr Rust Linux agent. See [docs/android-agent.md](../docs/android-agent.md) for architecture and deployment notes.

## Quick start

1. Copy `app/google-services.json.example` → `app/google-services.json` (Firebase console).
2. Set server `FCM_SERVER_KEY` or `FIREBASE_CREDENTIALS_JSON`.
3. Build: `./gradlew assembleDebug`
4. Install the APK, scan the server Settings QR, approve in Admin → Devices.
5. Provision as **Device Owner** so capabilities are granted automatically:
   `adb dpm set-device-owner com.timekpr.agent/.admin.TimeKprDeviceAdminReceiver`
   (device must have no accounts; factory reset or new user profile). Without device owner, enable Device Admin, Usage Access, and VPN manually on the phone.

Connectivity uses **FCM + short WebSocket sessions**, not a 24/7 socket. See `docs/android-agent.md`.

## Project layout

- `protocol/` — WebSocket JSON messages and ephemeral `AgentWebSocketClient`
- `push/` — FCM (`TimeKprMessagingService`) and token registration
- `work/` — WorkManager periodic sync and pairing poll
- `service/` — `AgentSessionCoordinator` (schedules sync sessions)
- `policy/` — Time limits, domain blocklists, app rules
- `vpn/` — Domain filtering VPN tunnel
- `monitor/` — Usage stats and alert generation
- `admin/` — Device Admin receiver
- `ui/` — Pairing wizard, QR scanner, status screen
