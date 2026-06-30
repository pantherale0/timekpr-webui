# Android bypass matrix

This page catalogs **known Android parental-control bypass techniques** — mostly reported against Google Family Link and similar cloud-supervised products — and records how **Guardian** responds to each vector on a managed device.

Use it when hardening a deployment, choosing a [policy preset](../features/policy-presets.md) bypass-risk level, or triaging a suspected bypass incident.

## How to read the status column

| Status | Meaning |
|--------|---------|
| **Protected** | Blocked on a Device Owner deployment with **medium or high** bypass-risk preset (or by architecture regardless of preset). |
| **Partial** | Mitigated for some configurations, intentional trade-off, or residual OEM-specific risk. |
| **Gap** | Meaningful exposure even with recommended preset; treat as hardening backlog. |
| **N/A** | Technique targets a supervision model Guardian does not use (e.g. Google account supervision only). |
| **Excluded** | Out of scope per [accountability policy](../../AGENTS.md) (rooted device, child granted admin, etc.). |

**Baseline assumption:** Android agent provisioned as **Device Owner** via MDM QR, child profile uses **medium or high** bypass-risk [policy preset](../features/policy-presets.md). Low presets deliberately leave more freedom.

### Hardening stack (medium+ presets)

Guardian applies several layers together:

| Layer | What it does |
|-------|----------------|
| **Android device policy** (preset) | Developer options off, install/uninstall blocks, USB lockdown, factory-reset protection, account-change block |
| **User restrictions** (agent) | `DISALLOW_ADD_USER`, `DISALLOW_REMOVE_USER`, `DISALLOW_USER_SWITCH` when developer options are disabled |
| **Anti-bypass app policy** (preset) | Blocks Island, Shelter, Test DPC, Tasker, Samsung Internet |
| **Agent blocklist** (device) | Same bypass-tool packages also suspended when dev/install lockdown is active |
| **Lockout suspension** | All launcher apps **plus** OEM Settings packages (`com.android.settings`, `com.samsung.android.settings`) |
| **DNS VPN** | Domain blocklists apply inside Settings/Spotify WebViews |
| **Usage monitor** | Concurrent picture-in-picture / split-screen sessions bill all active packages each tick |
| **Boot reconcile** | Enforcement re-applied immediately when `UsageMonitorService` starts |

---

## Matrix

### ADB, developer mode, and supervision removal

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| ADB `am start_in_vsync` supervision removal | With USB debugging on, launch a hidden supervision-removal activity via shell | Patched by Google (2025-09 security patch) | **Protected** | Medium+ preset: `DEVELOPER_SETTINGS_DISABLED`, USB data blocked, `DISALLOW_SAFE_BOOT`. Guardian is not Google supervision. |
| Third-party dev-options enablers (e.g. FLToolKit) | App or guide walks child through enabling developer options / USB debugging | Active on some OEM builds | **Protected** | Dev options disabled; install blocked on medium/high where preset blocks installs. |
| Power-button shortcut → disable supervision | Map power action to a Settings component that opens hidden removal UI | Pre-2025-09 patch on affected builds | **N/A** | No equivalent activity on Guardian. Settings WebView escapes covered by lockout suspension + DNS VPN. |
| Zygotroller / pre-2024 profile-owner removal chains | Exploit chain + dev mode to strip profile owner | Historical on old patch levels | **Protected** | Device Owner + dev/USB lockdown on medium+. Pre-patched OS versions excluded. |
| PC-side ADB multi-device tools | Desktop tool with OEM-specific tricks (extra profiles, disable monitoring apps) | Active wherever ADB is enabled | **Protected** | Requires USB debugging; blocked by medium+ device policy. Local enforcement persists if server sync is blocked. |

### Alternate profiles, containers, and user spaces

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| Unmanaged work profile (Island / Shelter) | Create a work profile while supervision is briefly off; run unsupervised apps inside | Active (especially where Secure Folder was patched) | **Protected** | `DISALLOW_ADD_USER` when dev options disabled; Island/Shelter/Test DPC blocked via preset app policy + agent `BypassHardening` list. |
| Xiaomi Second Space | OEM “second space” with separate account/storage, switched by PIN | Active on Xiaomi | **Partial** | `DISALLOW_USER_SWITCH` on medium+. OEM-specific — monitor for new users via `installed_apps_report`; use high-preset allowlist. |
| Supervised container via parent PC browser | Parent supervises a secondary container account from a browser to loosen install rules | Niche (Samsung Secure Folder) | **Partial** | Guardian does not use Google supervision accounts. Unmanaged OEM containers need same mitigations as profile escape. |
| Downgrade container APK via ADB | Install older version of OEM secure-folder app to evade blocks | Unverified / mostly failed | **N/A** | OEM + cloud-supervision specific. |
| Root — delete supervision marker files | Rooted device removes `profile_owner.xml` or equivalent | Active on rooted phones | **Excluded** | Rooted devices out of accountability scope. |

### Screen time, downtime, and usage accounting

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| Rapid UI “refresh” loop | Reboot and repeatedly open parental-controls UI to desync cloud limits | Reported on several OEMs | **Protected** | Local `TimeLimitStore` enforcement; no cloud refresh dependency. |
| Forced-restart race | Repeated hard reboots confuse monitoring services → temporary no limits | Patched on some Samsung builds (late 2024) | **Partial** | Local enforcement + immediate `reconcileAllUsers()` on monitor start reduces boot-window gap. |
| Revoke usage-stats permission on parental-controls package | Wireless debugging + permission manager strips `PACKAGE_USAGE_STATS` from supervisor app | Patched on recent One UI | **Protected** | Device Owner auto-grants Guardian usage access; no separate supervisor package to target. |
| Pop-up view / split-screen time laundering | Keep an unlimited-time app resumed; run target app in PiP so the wrong app is counted | Long-standing on several products | **Protected** | `UsageMonitorService` bills all concurrently active packages each tick when ≥2 sessions are open. Lockout suspends everything. |
| Automation app shortcuts during downtime (e.g. Tasker) | Shortcuts into Settings WebViews or other apps while downtime screen is shown | Niche Samsung reports | **Protected** | Tasker in anti-bypass blocklist; suspended at lockout. |
| Accessibility menu + recents stack | Open accessibility overlay → recents → back into blocked app during downtime | Intermittent | **Protected** | Blocked apps suspended with launcher apps when time exhausted. |
| Alarm / assistant menu → exempt media app | Trigger alarm with Spotify (or similar) ringtone to open exempt app at downtime | Niche | **Partial** | Intentional trade-off for sleep audio if app is screentime-exempt. Web escape blocked by DNS VPN. |

### Settings and in-app WebView escape hatches

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| Settings → legal / regulatory web links → YouTube / Google | Chain embedded links inside Settings to reach blocked sites (common on Samsung) | Active | **Protected** | Settings packages suspended at lockout; domain VPN blocks listed sites during allowed hours. |
| OEM AI settings → terms → embedded browser | Galaxy AI and similar flows open Google/YouTube from settings WebViews | Active on supported Samsung devices | **Protected** | Samsung Settings suspended at lockout; domain blocks cover Google/YouTube. |
| In-app browser inside exempt media app | Spotify (or similar) premium/support WebView → social site → open browser | Active when media app is always allowed | **Partial** | DNS VPN blocks social/search domains. Risk only if app is screentime-exempt **and** parent allows those domains. |

### Network, cloud, and account disconnection

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| DNS block on supervisor push channel | Sinkhole DNS so cloud parental app never receives lock/limit FCM pushes | Active | **Protected** | Limits enforced locally; blocking Guardian server delays policy **updates** only. Parent sees offline device. |
| Remove Google account via device-activity portal | Disconnect supervised account from device using myaccount.google.com | Risky for child; reported | **Protected** | Guardian does not depend on Google supervision. Medium+ preset sets `modifyAccountsDisabled`. |

### Parent / OTP code attacks

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| TOTP shared-secret extraction | Derive parent access code after learning shared secret | Active where products use TOTP parent codes | **Partial** | Guardian OTP is HMAC of per-device agent token in `EncryptedSharedPreferences`. Not extractable without root/device access. Revoke/re-pair if token leaks. |
| Offline TOTP generator | Standalone app generates codes once secret is known | Depends on prior secret leak | **Partial** | Security reduces to token confidentiality. |

### Sideloading and installs

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| Browser-based APK install without prompt | Download and install APK from OEM browser (e.g. Samsung Internet) without parent approval | Active on some Samsung builds | **Protected** | Medium+ preset: `installAppsDisabled` + Samsung Internet in anti-bypass blocklist. |

### Non-Android (for context)

| Technique | What attackers try | Typical on cloud parental controls | Guardian status | Mitigations |
|-----------|-------------------|--------------------------------------|-----------------|-------------|
| Chromebook sign-out / recover-user flow | Wi‑Fi toggle + wrong-password recovery adds unsupervised user | Active when child account is device owner | **N/A** | No Chromebook agent. |

---

## Summary scorecard

Counts assume **Device Owner + medium or high bypass-risk preset**.

| Status | Count (of 22 Android-relevant techniques) |
|--------|---------------------------------------------|
| Protected | 17 |
| Partial | 5 |
| Gap | 0 |
| N/A / Excluded | 6 |

### Remaining partial vectors

1. **Xiaomi Second Space** — OEM feature; `DISALLOW_USER_SWITCH` helps but is not a full block.
2. **Forced-reboot race** — boot reconcile reduces the window; no cloud desync vector on Guardian.
3. **Spotify alarm / in-app browser** — intentional sleep-audio trade-off; do not screentime-exempt Spotify if concerned.
4. **TOTP secret extraction** — requires root or physical access to a paired device; re-pair if compromised.

### Implementation references

| Component | Location |
|-----------|----------|
| Bypass package lists + settings escape detection | `android-agent/.../enforcement/BypassHardening.kt` |
| Suspension, user restrictions, lockout | `android-agent/.../enforcement/EnforcementController.kt` |
| PiP / concurrent session billing | `android-agent/.../monitor/UsageMonitorService.kt` |
| Preset bypass app blocks | `server/src/policy/android_bypass.py` |
| Preset device-policy bundles | `server/src/policy/policy_preset_matrix.json` |

---

## Hardening checklist

For **medium or high** bypass risk, applying a [policy preset](../features/policy-presets.md) enables the full stack automatically.

For **low** bypass risk or custom profiles, configure under **Device detail → Android device policy**:

- `developerSettings`: `DEVELOPER_SETTINGS_DISABLED`
- `installAppsDisabled`: **on**
- `factoryResetDisabled`: **on**
- `modifyAccountsDisabled`: **on**
- `usbDataAccess`: `DISALLOW_USB_DATA_TRANSFER`

Also block Island, Shelter, Test DPC, Tasker, and Samsung Internet in app policies; enable VPN/proxy domain packs; wire [alerts & webhooks](../features/alerts-and-webhooks.md).

---

## Related

- [Android agent](android-agent.md)
- [Device restrictions](../features/device-restrictions.md)
- [Policy presets](../features/policy-presets.md)
- [Policy matrix](../reference/policy-matrix.md)
- [vs commercial parental controls](../getting-started/comparison.md)

## Maintaining this page

When new bypass techniques are reported:

1. Add a row with a neutral technique name — not a step-by-step reproduction.
2. Validate against `BypassHardening.kt`, the preset matrix, and current enforcement code.
3. Update the summary scorecard.
