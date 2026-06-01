# TimeKpr Android Agent

The Android agent (`android-agent/`) is a Kotlin port of the Rust Linux client. It connects to the same Flask WebSocket hub (`/ws`), uses the identical JSON protocol, and enforces policies using Android-native APIs.

## Architecture mapping

| Linux Rust agent | Android agent |
|------------------|---------------|
| TimeKpr D-Bus (`timekpr_dbus.rs`) | `TimeLimitStore` + `UsageMonitorService` (local screen-time state) |
| AppArmor profiles (`apparmor.rs`) | `AppPolicyStore` + Device Admin `setPackagesSuspended` |
| iptables + local DNS (`firewall.rs`, `local_dns.rs`) | `DomainBlockVpnService` (VPN tunnel with DNS filtering) |
| Netlink process monitor (`netlink.rs`) | `UsageStatsManager` event stream |
| `/etc/timekpr-agent/config.json` | `AgentConfigStore` (EncryptedSharedPreferences-ready SharedPreferences) |
| logind session alerts | `user_signed_in` / `app_usage` alerts via usage events |

## Pairing flow

1. Open **Settings → Agent pairing** in the TimeKpr WebUI and display the QR code.
2. On the phone, install the APK and complete first-run setup **or** open the main activity and tap **Scan server QR code**.
3. The app stores `server_url` (and optional `registration_token`) then opens a WebSocket `hello`.
4. Approve the pending device in **Admin → Devices** (same flow as Linux agents).
5. The server issues `pairing_approved`; the app stores the per-device token and reconnects with HMAC auth.

QR payload schema:

```json
{
  "type": "timekpr_pairing",
  "server_url": "wss://your-server.example/ws",
  "registration_token": "optional-firewall-token"
}
```

## Required permissions

| Capability | Permission / API |
|------------|------------------|
| Background connection | Foreground service (`AgentWebSocketService`) |
| Screen time enforcement | Device Admin (`TimeKprDeviceAdminReceiver`) |
| App discovery & usage | `PACKAGE_USAGE_STATS` (Usage Access) |
| Web/domain policies | `VpnService` consent |
| Boot persistence | `RECEIVE_BOOT_COMPLETED` |

For full MDM-style control (silent installs, kiosk, work profiles), provision the app as **Device Owner** using your EMM or `adb dpm set-device-owner com.timekpr.agent/.admin.TimeKprDeviceAdminReceiver`.

## App policies on Android

Server AppArmor rules sync to the agent via `sync_apparmor_policy`. Use either:

- `match_type: "package"` with `executable_path: "com.example.app"`, or
- `executable_path: "/android/package/com.example.app"` (legacy-compatible prefix)

Presets:

- `blocked` → package suspended + launch blocked
- `no_internet` → tracked for future per-app network rules (domain VPN still applies globally)
- `complain` → usage alerts without blocking

## Building

```bash
cd android-agent
./gradlew assembleDebug
```

Set `TIMEKPR_AGENT_WS_URL` on the server when behind reverse proxies so QR codes embed the public WebSocket URL.

## Versioning

The Android agent reports `agent_version` (default `v0.1.0-android`). Match this with `TIMEKPR_SERVER_VERSION` or relax version checks during development.
