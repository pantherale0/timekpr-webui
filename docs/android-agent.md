# TimeKpr Android Agent

The Android agent (`android-agent/`) is a Kotlin port of the Rust Linux client. It connects to the same Flask WebSocket hub (`/ws`), uses the identical JSON protocol, and enforces policies using Android-native APIs.

## Battery-efficient connectivity (FCM)

Android does **not** keep a WebSocket open 24/7. That would drain the battery quickly.

| Trigger | Behavior |
|---------|----------|
| **FCM data message** | Server pushes `sync_policies`, `command_wake`, `factory_reset`, or `pairing_approved` → app runs a **short WebSocket session** (connect, sync, disconnect) or wipes immediately for `factory_reset` |
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

## Screen time lockout

When daily time is exhausted or the current hour falls outside allowed windows (`TimeLimitStore.isAccessAllowed()`), the agent **does not** lock the device screen. Instead:

1. A **persistent top banner** (`TimeExhaustedOverlay`) informs the user that screen time is used up.
2. All launcher apps are **suspended** via Device Admin `setPackagesSuspended`, except packages in the exempt set.
3. When access is restored (parent adds time, schedule changes, etc.), the banner is dismissed and suspensions from the lockout are cleared; normal app-policy suspensions remain.

### Phone exemption

On devices with telephony and an **active SIM** (`READ_PHONE_STATE` + `TelephonyManager.SIM_STATE_READY`):

- The default dialer and in-call UI packages stay unsuspended so calls can be placed and received.
- The overlay shows a **Make a call** button that opens the system dialer.

Tablets and phones without a ready SIM get banner-only lockout with all apps suspended.

### Future screentime whitelist

`TimeLimitStore.screentimeExemptPackages()` holds packages that may run regardless of screen-time limits (for example, educational apps on a tablet). The set is persisted locally and empty by default; a future server command will populate it. Whitelisted packages use the same exempt-package path as the dialer on phones.

Server screen-time inputs are unchanged: `set_weekly_time_limits`, `set_allowed_hours`, and `modify_time_left`.

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

### Generating and Encoding the Keystore for CI/CD

To generate a keystore and convert it to Base64 for GitHub Secrets:

1. **Generate the Keystore File**:
   Use Java's `keytool` to generate a new key pair in a keystore:
   ```bash
   keytool -genkeypair -v \
     -keystore release.keystore \
     -alias timekpr-alias \
     -keyalg RSA \
     -keysize 2048 \
     -validity 10000
   ```
   Provide a keystore password and key password when prompted.

2. **Encode the Keystore File to Base64**:
   Convert the generated binary `.keystore` file to a single Base64-encoded string:
   ```bash
   base64 -w 0 release.keystore > keystore_base64.txt
   ```

3. **Configure the GitHub Repository Secrets**:
   - `ANDROID_KEYSTORE_BASE64`: Paste the entire content of `keystore_base64.txt`.
   - `ANDROID_KEYSTORE_PASSWORD`: The password you set for the keystore.
   - `ANDROID_KEY_ALIAS`: The alias you used (e.g., `timekpr-alias`).
   - `ANDROID_KEY_PASSWORD`: The password you set for the specific key alias.

Local release signing uses the same variables via `ANDROID_KEYSTORE_PATH` or `android.keystore.*` Gradle properties.

## Required permissions

| Capability | Permission / API |
|------------|------------------|
| Background connection | Foreground service (`AgentWebSocketService`) |
| Screen time enforcement | Device Admin (`TimeKprDeviceAdminReceiver`) |
| App usage monitoring | `PACKAGE_USAGE_STATS` (Usage Access) |
| Installed app inventory | `PackageManager` (launcher-visible apps; no extra permission beyond normal install visibility) |
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

## Access approvals (app launch + domains)

When a child profile uses approval modes on the server, the Android agent consumes additive sync fields and emits parent-review alerts.

### App launch (`sync_apparmor_policy`)

When `app_launch_mode` is `allowlist` or `blocklist`, the server includes:

```json
{
  "policies": [ ... ],
  "approval_policy": {
    "app_launch_mode": "allowlist",
    "approved_packages": ["com.approved.app"],
    "blocked_packages": ["com.unapproved.app"]
  }
}
```

The agent suspends packages from `blocked_packages` (server-precomputed). When `approval_policy` is omitted (`open` mode), only static `blocked` rules from `policies` apply.

On blocked launch under an approval overlay, the agent emits `access_requested` (and `app_blocked` with `reason: not_approved` as fallback).

### Domain access (`update_domain_policy_manifest`)

Per-UID manifest entries may include:

```json
{
  "linux_username": "child",
  "source_ids": ["1"],
  "domain_access_mode": "approval_on_block",
  "allowed_domains": ["wikipedia.org"]
}
```

Granted domains bypass the DNS VPN block. When `domain_access_mode` is `approval_on_block`, blocked domains show a **Request access** overlay button and emit `access_requested` alerts (rate-limited on device).

FCM `sync_policies` wakes a short WebSocket session; policy JSON is delivered via existing sync commands, not in the FCM payload.

## Device restrictions (`sync_android_device_policy`)

Per Android device mapping, the admin UI can configure AMAPI-aligned device restriction fields. The server pushes them via `sync_android_device_policy` (on save, and again when the agent reconnects after an FCM `sync_policies` wake).

Payload shape (field names match [Android Management API Policy](https://developers.google.com/android/management/reference/rest/v1/enterprises.policies)):

```json
{
  "device_policy": {
    "screenCaptureDisabled": false,
    "cameraAccess": "CAMERA_ACCESS_DISABLED",
    "microphoneAccess": "MICROPHONE_ACCESS_DISABLED",
    "installAppsDisabled": true,
    "uninstallAppsDisabled": false,
    "factoryResetDisabled": true,
    "adjustVolumeDisabled": false,
    "modifyAccountsDisabled": true,
    "mountPhysicalMediaDisabled": false,
    "bluetoothDisabled": true,
    "outgoingCallsDisabled": false,
    "smsDisabled": false,
    "advancedSecurityOverrides": {
      "developerSettings": "DEVELOPER_SETTINGS_DISABLED"
    },
    "deviceConnectivityManagement": {
      "usbDataAccess": "DISALLOW_USB_FILE_TRANSFER"
    },
    "shortSupportMessage": {
      "defaultMessage": "This setting is managed by your parent through TimeKpr."
    },
    "longSupportMessage": {
      "defaultMessage": "This device is protected by TimeKpr parental controls. Your parent manages screen time, apps, and websites. Ask them if you need something changed."
    }
  }
}
```

Supported `cameraAccess` values: `CAMERA_ACCESS_UNSPECIFIED`, `CAMERA_ACCESS_DISABLED`, `CAMERA_ACCESS_USER_CHOICE`, `CAMERA_ACCESS_ENFORCED`.

Supported `microphoneAccess` values: `MICROPHONE_ACCESS_UNSPECIFIED`, `MICROPHONE_ACCESS_DISABLED`, `MICROPHONE_ACCESS_USER_CHOICE`, `MICROPHONE_ACCESS_ENFORCED`.

Supported `usbDataAccess` values (under `deviceConnectivityManagement`): `USB_DATA_ACCESS_UNSPECIFIED`, `ALLOW_USB_DATA_TRANSFER`, `DISALLOW_USB_FILE_TRANSFER`, `DISALLOW_USB_DATA_TRANSFER`.

Supported `developerSettings` values: `DEVELOPER_SETTINGS_UNSPECIFIED`, `DEVELOPER_SETTINGS_DISABLED`, `DEVELOPER_SETTINGS_ALLOWED`.

`shortSupportMessage` and `longSupportMessage` use AMAPI `UserFacingMessage` objects (`defaultMessage` string). Defaults are parental-controls wording, not enterprise/work policy text:

- Short: *"This setting is managed by your parent through TimeKpr."*
- Long: *"This device is protected by TimeKpr parental controls…"*

Parents can customize both messages per Android device mapping in the admin UI. The agent applies them via `DevicePolicyManager.setShortSupportMessage()` / `setLongSupportMessage()`.

Enforcement uses `DevicePolicyManager` and requires **device owner** provisioning. Device-admin-only installs show a warning in the admin UI and skip most restrictions.

## Device unenrollment and factory reset

Admins can remove devices from family management from the device detail page or devices list.

| Command / FCM action | Behavior |
|----------------------|----------|
| `unenroll` | Stops enforcement, clears VPN/usage monitoring, clears stored agent token and pairing state via `AgentConfigStore.clearEnrollmentState()`. Server revokes trust (`status=rejected`). |
| `factory_reset` (WebSocket) | Requires **device owner**. Calls `DevicePolicyManager.wipeData(admin, 0)`. |
| `factory_reset` (FCM) | Immediate wipe on a background thread without waiting for a full policy sync cycle. |

`device_admin.xml` declares `<wipe-data />` so device-owner agents can perform remote wipes.

If a factory reset is requested while the device is offline, the server sets `pending_factory_reset` on the `AgentDevice` row, revokes management trust, and retries delivery on the next WebSocket connection (FCM `factory_reset` is also sent when an FCM token is available).

Linux agents handle `unenroll` by clearing `/etc/timekpr-agent/config.json` agent token and stopping the reconnect loop. Linux has no remote factory reset.

## App policies on Android

Server AppArmor rules sync to the agent via `sync_apparmor_policy`. Use either:

- `match_type: "package"` with `executable_path: "com.example.app"`, or
- `executable_path: "/android/package/com.example.app"` (legacy-compatible prefix)

Presets:

- `blocked` → package suspended + launch blocked
- `no_internet` → tracked for future per-app network rules (domain VPN still applies globally)
- `complain` → usage alerts without blocking

## Installed application inventory

After each authenticated sync session the agent scans launcher-visible packages and sends chunked `installed_apps_report` messages to the server, plus optional `app_icon_report` PNG uploads (64×64, content-addressed). The server uses this inventory in the application policies UI.

The agent also handles `refresh_installed_apps` RPC for on-demand rescans while connected.

See [app-discovery.md](app-discovery.md) for the full protocol reference.

## Building

```bash
cd android-agent
./gradlew assembleDebug      # local testing
./gradlew assembleRelease    # MDM QR provisioning (requires release signing)
```

Set `TIMEKPR_AGENT_WS_URL` on the server when behind reverse proxies so QR codes embed the public WebSocket URL.

Debug APKs cannot be used for MDM QR provisioning; always use a signed release build and matching signature checksum.

Release CI stamps the agent version from the git tag, matching the Rust agent and server:

```bash
export TIMEKPR_AGENT_VERSION=v1.2.3
./gradlew assembleRelease
```

## Versioning

The Android agent reports `agent_version` from `TIMEKPR_AGENT_VERSION` at build time (`v0.0.0-dev` for debug builds, e.g. `v1.2.3` for tagged release builds). Release agents must match `TIMEKPR_SERVER_VERSION`; dev servers (`v0.0.0-dev`) accept any agent version.

### Automatic updates

When a release server rejects a mismatched `agent_version` at WebSocket `hello`, it responds with `auth_result` containing:

- `update_required: true`
- `target_version` — the server's `TIMEKPR_SERVER_VERSION`
- `apk_url` — GitHub release APK or server-uploaded dev APK URL
- `signature_checksum` — signing certificate checksum for verification
- `update_available` — whether both URL and checksum are available

The Android agent handles this automatically:

1. Downloads the APK (from server-provided URL, or GitHub release fallback for older servers)
2. Verifies the APK signing certificate checksum (same format as MDM provisioning QR)
3. Installs via `PackageInstaller` (silent when device owner; may prompt on sideload-only installs per Android platform rules)
4. Reconnects after install via `PACKAGE_REPLACED` / install callback

**Release servers:** APK from `https://github.com/pantherale0/timekpr-webui/releases/download/{tag}/timekpr-android-agent-{tag}.apk` with companion `.signature-checksum` asset.

**Development servers:** Upload a signed release APK in **Settings → Android MDM provisioning QR**; the server serves it at `/api/pairing/provisioning/apk` and includes that URL in the update response.

If `update_available` is false (dev server without uploaded APK), the agent shows the server error message and retries on the next sync cycle.
