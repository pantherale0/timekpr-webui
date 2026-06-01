# TimeKpr Android Agent

Kotlin/Android port of the TimeKpr Rust Linux agent. See [docs/android-agent.md](../docs/android-agent.md) for architecture and deployment notes.

## Quick start

1. Build: `./gradlew assembleDebug`
2. Install the APK on a test device.
3. Scan the pairing QR from the server **Settings** page.
4. Approve the device in the WebUI **Admin → Devices** screen.
5. Grant Device Admin, Usage Access, and VPN permissions from the main activity.

## Project layout

- `protocol/` — WebSocket JSON messages and command dispatch
- `service/` — Foreground WebSocket agent service
- `policy/` — Time limits, domain blocklists, app rules
- `vpn/` — Domain filtering VPN tunnel
- `monitor/` — Usage stats and alert generation
- `admin/` — Device Admin receiver
- `ui/` — Pairing wizard, QR scanner, status screen
