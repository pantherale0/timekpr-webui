# TimeKpr Android Agent

The Android agent (`android-agent/`) is a Kotlin port of the Rust Linux client. It connects to the same Flask WebSocket hub (`/ws`), uses the identical JSON protocol, and enforces policies using Android-native APIs.

## Battery-efficient connectivity (FCM)

Android does **not** keep a WebSocket open 24/7. That would drain the battery quickly.

| Trigger | Behavior |
|---------|----------|
| **FCM data message** | Server pushes `sync_policies`, `command_wake`, or `pairing_approved` → app runs a **short WebSocket session** (connect, sync, disconnect) |
| **WorkManager** | Periodic sync every 4 hours (matches Linux agent policy timer) |
| **Pairing poll** | Every 15 minutes while unpaired (replaces holding WS open during approval) |
| **User / boot** | Manual reconnect or startup schedules an expedited sync |

Linux agents remain on a persistent WebSocket. Android registers `fcm_token` + `platform: android` in `hello`; the server stores the token and uses FCM when the device is offline.

### Server FCM configuration

Set one of:

- `FCM_SERVER_KEY` — legacy HTTP API server key
- `FIREBASE_CREDENTIALS_JSON` — path or inline JSON for a Firebase service account (HTTP v1 API)

Optional: `FIREBASE_PROJECT_ID` when using service account JSON without `project_id`.

Copy `android-agent/app/google-services.json.example` → `google-services.json` from the Firebase console.

## Architecture mapping

| Linux Rust agent | Android agent |
|------------------|---------------|
| TimeKpr D-Bus (`timekpr_dbus.rs`) | `TimeLimitStore` + `UsageMonitorService` (local screen-time state) |
| AppArmor profiles (`apparmor.rs`) | `AppPolicyStore` + Device Admin `setPackagesSuspended` |
| iptables + local DNS (`firewall.rs`, `local_dns.rs`) | `DomainBlockVpnService` (VPN tunnel with DNS filtering) |
| Netlink process monitor (`netlink.rs`) | `UsageStatsManager` event stream |
| `/etc/timekpr-agent/config.json` | `AgentConfigStore` (EncryptedSharedPreferences-ready SharedPreferences) |
| logind session alerts | `user_signed_in` / `app_usage` alerts via usage events |
| Persistent WebSocket loop | FCM wake + ephemeral `AgentWebSocketClient` sessions |

## Pairing flows

TimeKpr supports two Android enrollment paths. Both end with admin approval in **Admin → Devices** and a `pairing_approved` WebSocket message.

### In-app pairing QR (installed APK)

Use when the APK is already on the device (sideload, adb install, or manual download).

1. Open **Settings → Agent pairing** in the TimeKpr WebUI.
2. On the phone, open the app and tap **Scan server QR code** (or complete first-run setup).
3. The app stores `server_url` (and optional `registration_token`) then opens a WebSocket `hello`.
4. Approve the pending device in **Admin → Devices**.
5. The server issues `pairing_approved`; the app stores the per-device token and reconnects with HMAC auth.

Payload schema:

```json
{
  "type": "timekpr_pairing",
  "server_url": "wss://your-server.example/ws",
  "registration_token": "optional-firewall-token"
}
```

### Android MDM provisioning QR (factory-reset / 6-tap)

Use for zero-touch device-owner rollout on a factory-reset device. The QR follows [Android Enterprise provisioning](https://developers.google.com/android/management/provision-device#about_qr_codes) and installs the agent automatically.

1. Open **Settings → Agent pairing → Android MDM provisioning QR** in the WebUI.
2. On a factory-reset device, tap the welcome screen six times and scan the MDM QR.
3. Android downloads the APK, sets `com.timekpr.agent` as device owner, and applies the server URL from admin extras.
4. Approve the pending device in **Admin → Devices** (same as in-app pairing).

The server emits standard `android.app.extra.PROVISIONING_*` keys. Admin extras use:

- `com.timekpr.agent.EXTRA_SERVER_URL`
- `com.timekpr.agent.EXTRA_REGISTRATION_TOKEN` (optional)

#### Release servers

When `TIMEKPR_SERVER_VERSION` matches a GitHub release tag (e.g. `v1.2.3`), the WebUI defaults to:

- APK: `https://github.com/pantherale0/timekpr-webui/releases/download/{tag}/timekpr-android-agent-{tag}.apk`
- Checksum: companion `timekpr-android-agent-{tag}.signature-checksum` asset

#### Development servers

When the server runs as `v0.0.0-dev`, no release assets exist. Build a **release** APK locally and upload it in **Settings → Android MDM provisioning QR**. The server stores the file, serves it at `/api/pairing/provisioning/apk`, and computes the signature checksum automatically (requires `apksigner` in `ANDROID_HOME` or `~/Android/Sdk/build-tools` on the server host).

```bash
cd android-agent
./gradlew assembleRelease
```

Release CI publishes the checksum asset when these GitHub Actions secrets are configured:

- `ANDROID_KEYSTORE_BASE64`
- `ANDROID_KEYSTORE_PASSWORD`
- `ANDROID_KEY_ALIAS`
- `ANDROID_KEY_PASSWORD`

Local release signing uses the same variables via `ANDROID_KEYSTORE_PATH` or `android.keystore.*` Gradle properties.

## Required permissions

| Capability | Permission / API |
|------------|------------------|
| Background connection | Foreground service (`AgentWebSocketService`) |
| Screen time enforcement | Device Admin (`TimeKprDeviceAdminReceiver`) |
| App discovery & usage | `PACKAGE_USAGE_STATS` (Usage Access) |
| Web/domain policies | `VpnService` consent |
| Boot persistence | `RECEIVE_BOOT_COMPLETED` |

For full MDM-style control, provision the app as **Device Owner** using the MDM provisioning QR above, your EMM, or:

```bash
adb dpm set-device-owner com.timekpr.agent/.admin.TimeKprDeviceAdminReceiver
```

The agent implements Android 12+ provisioning handlers (`GET_PROVISIONING_MODE`, `ADMIN_POLICY_COMPLIANCE`) so QR-based device-owner enrollment applies server config automatically.

When device owner, the app auto-grants itself Usage Access, overlay (`SYSTEM_ALERT_WINDOW`), and notification permission (Android 13+) via `DevicePolicyManager` — no manual Settings taps required. Always-on VPN is enabled only when domain block policies are active. Device Admin alone still needs the user to approve Usage Access and VPN in system settings.

## Domain block notifications

When the DNS VPN blocks a domain, the agent shows deduplicated user feedback:

| Scenario | UI |
|----------|-----|
| Single blocked domain (e.g. `facebook.com`) | Small overlay card on top of the current app |
| Burst / ad-list (≥3 blocks or ≥2 distinct domains in 10s) | One notification: *"Some traffic on this website has been blocked"* (auto-dismisses after 10s) |

Rules:

- No UI when the screen is off
- 10s cooldown after any alert (DNS retries for the same domain are collapsed)
- Overlay requires `SYSTEM_ALERT_WINDOW` (auto-granted on device owner); sideloaded installs without overlay permission get a heads-up notification instead

Implementation: `BlockNotificationCoordinator` in the VPN service, `BlockedDomainOverlay` for single blocks, `BlockBurstNotifier` for bursts.

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
./gradlew assembleDebug      # local testing
./gradlew assembleRelease    # MDM QR provisioning (requires release signing)
```

Set `TIMEKPR_AGENT_WS_URL` on the server when behind reverse proxies so QR codes embed the public WebSocket URL.

Debug APKs cannot be used for MDM QR provisioning; always use a signed release build and matching signature checksum.

## Versioning

The Android agent reports `agent_version` (`v0.0.0-dev` for debug builds, `v0.1.0-android` for release). Release agents must match `TIMEKPR_SERVER_VERSION`; dev servers (`v0.0.0-dev`) accept any agent version.
