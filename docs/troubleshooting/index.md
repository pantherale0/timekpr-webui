# Troubleshooting

## Agent remains offline (Linux / Windows)

1. Check agent logs:

   ```bash
   journalctl -u timekpr-agent.service -n 50 --no-pager
   ```

2. Verify WebSocket reachability from client network (`wss://your-server/ws`).
3. Confirm device is **approved** in Admin → Devices.
4. Match `agent_version` to `TIMEKPR_SERVER_VERSION` (dev server `v0.0.0-dev` accepts any version).

## Android policy not syncing

1. Ensure **task worker** is running (`task_worker.py` or Docker `tasks`).
2. Without FCM, policies sync on WorkManager interval (~4 hours) or when app opens.
3. Configure `FCM_SERVER_KEY` or `FIREBASE_CREDENTIALS_JSON` for push wake.
4. Confirm Usage Access and VPN consent on non-Device-Owner installs.

## Android multi-user and Device Owner

### Symptom: server does not see child profiles

**Cause:** Device Admin on the parent (User 0) profile **cannot enumerate** secondary Android users. Guardian only calls `getSecondaryUsers()` when the app is **Device Owner** on User 0.

**Fix (full shared-tablet management):**

1. Provision **Device Owner** (MDM factory-reset QR recommended)
2. Pair from User 0
3. Create or map child profiles
4. Choose **Secondary users** management mode during MDM setup when prompted

### Cannot set Device Owner

Android blocks ADB `dpm set-device-owner` when:

- Google or other **accounts** exist on User 0
- **Secondary users** already exist on the device

You must remove accounts and extra users (or factory reset) before Device Owner provisioning—not a Guardian limitation.

### Workaround without Device Owner

Install and pair Guardian **inside the child profile only** as a separate device registration. Expect reduced enforcement (package suspension requires Device Owner). See [Android agent — multi-user](../platforms/android-agent.md#multi-user-support).

## Nintendo Switch stale or failing sync

1. Run background worker with `TIMEKPR_TASKS_UPDATE_USER_DATA=1`.
2. Click **Sync Now** on device detail.
3. Re-link Nintendo Account under Settings if session expired.
4. Player nicknames populate after first successful sync.

## Xbox stale or failing sync

Same pattern as Nintendo: worker enabled, **Sync Now**, re-link Xbox account, validate with account status API.

## Version mismatch / Android update loop

Release server rejects hello when versions differ. Android receives `update_required` with APK URL and signature checksum. Ensure GitHub release assets exist or upload dev APK in Settings.

## CI Android signing failures

Verify `ANDROID_KEYSTORE_BASE64` and password/alias secrets match the release keystore. See [CI & releases](../development/ci-release.md).

## Policy not applying on Linux mapping

1. Verify mapping **Verified** status.
2. Linux device restrictions apply only to **seat0 active session** user—another user logged in on console won't receive polkit/terminal blocks.
3. Check agent logs for AppArmor or DNS errors.

## Webhook not firing

1. Enable webhook in Settings with valid URL.
2. Confirm `TIMEKPR_TASKS_DELIVER_ALERTS` is not disabled.
3. Verify receiver accepts POST JSON and optional HMAC signature.

## Related

- [Android agent](../platforms/android-agent.md)
- [Configuration](../getting-started/configuration.md)
- [Background worker](../reference/background-worker.md)
