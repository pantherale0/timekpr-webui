# Guardian Space Overlay

The **Guardian Space overlay** is a full-screen blocked page shown to managed children whenever Guardian intercepts their session.  It replaces a hard lock screen with an age-appropriate, visually calm experience that explains *why* the device is paused and gives children a safe channel to send a message to their parent.

## Overview

When a managed child hits a time limit, a blocked website, or a sign-up interception point, Guardian replaces the normal screen with the Guardian Space overlay.  The overlay:

- Displays an age-tuned headline and description based on the configured age tier.
- Shows a personalised note from the parent or carer.
- Offers a **breathing exercise** and a **drawing sketchpad** to keep younger children occupied.
- Lets the child **send a request** to the parent dashboard — either via quick preset buttons (younger children) or a free-text field (teenagers).

The same `blockedv2.html` asset is used across all supported surfaces, keeping the experience consistent.

## Trigger scenarios

| Reason | When it appears |
|--------|-----------------|
| `sleep` | The device has entered the scheduled sleep / wind-down period. |
| `filtered` | A domain blocked by the active domain policy was accessed. |
| `locked` | The user's screen-time allowance has been exhausted. |
| `signup` | The browser extension detected a sign-up form on a restricted domain. |

## Age tiers

| Tier | Audience | Language style |
|------|----------|----------------|
| `under8` | Young children | Simple, playful; brand name shown as "Safe Sandbox" |
| `eight12` | Pre-teens | Clear, friendly; default tier when none is set |
| `teen` | Teenagers | Direct, respectful; free-text access request |

When no age tier is configured for a user, the overlay defaults to `eight12`.

## Platform coverage

### Browser Extension (Chrome / Brave / Edge)

`blockedv2.html` is bundled as a web-accessible resource in the Guardian browser extension.  The background service worker redirects blocked tabs to this page, appending URL query parameters:

```
blockedv2.html?reason=filtered&age=teen&device=example.com&note=Study+time+rules+apply
```

Access requests entered by the child are forwarded to the native agent via `chrome.runtime.sendMessage({ type: "ACCESS_REQUEST", ... })`, which the native messaging bridge sends on to the server.

### Linux Agent (Rust)

The Linux agent receives `show_overlay` and `dismiss_overlay` commands from the server via the WebSocket `command_request` protocol.

- **`show_overlay`** spawns the `guardian-overlay-helper` binary, which embeds `blockedv2.html` (installed at `/usr/share/guardian-agent/blockedv2.html`) in a borderless, fullscreen **Chromium Embedded Framework (CEF)** window.  Child access requests are forwarded to the agent daemon via `/run/guardian-agent/ipc.sock`.
- **`dismiss_overlay`** kills the helper process.

Access requests from the CEF overlay use the same `ACCESS_REQUEST` IPC message as the browser extension, routed through the agent to `POST /api/access-request`.

!!! note "Building the overlay helper"
    The helper requires the `cef-overlay` Cargo feature:

    ```bash
    cargo build --release --bin guardian-overlay-helper --features cef-overlay
    ```

    CEF shared libraries are downloaded automatically at build time.  For production bundles, use the `bundle-cef-app` utility from [cef-rs](https://github.com/tauri-apps/cef-rs) to assemble the required runtime layout alongside the binary.

### Windows Agent

The Windows agent handles `show_overlay` and `dismiss_overlay` commands via the same `command_request` WebSocket protocol.  The `guardian-overlay-helper.exe` binary uses the same CEF kiosk window (`cef-overlay` feature) and forwards access requests via the `\\.\pipe\guardian-agent-ipc` named pipe.

### Android Agent (Kotlin)

The Android agent launches `GuardianOverlayActivity`, a full-screen `Activity` that loads the bundled `blockedv2.html` asset inside a `WebView`.

### Windows

The Windows service broadcasts overlay IPC messages to the per-session user agent. The user agent launches Microsoft Edge in kiosk app mode, loading `blockedv2.html` from `C:\Program Files\Guardian\` (bundled in the MSI). Clock tamper lockdown uses `reason=clock_tamper`; parent override uses the `clear_clock_tamper` server command.

- Intent extras (`guardian_reason`, `guardian_age_tier`, `guardian_parent_note`, `guardian_device_name`) are passed when launching the activity.
- The `WebView` injects runtime values via JavaScript after page load: `setAge(...)`, `setReason(...)`, `setDeviceInfo(...)`.
- A `JavascriptInterface` named `guardianBridge` exposes `sendAccessRequest(reason, message)` so that preset buttons and the free-text field in the HTML page can reach the server via `AlertEventBus`.

The overlay is launched by `TimeExhaustedOverlay` when screen time is exhausted.  The existing banner fallback (`TimeExhaustedOverlay.Mode.TIME_EXHAUSTED`) remains active in case `SYSTEM_ALERT_WINDOW` permission is not granted.

## Configuration

Overlay settings are stored per managed user and can be changed in the **Guardian Space Overlay** card on the user's edit page (`/admin/users/<id>/edit`) or via the API.

### Admin UI

Open **Admin → Managed Users → Edit** for the target child.  In the right sidebar, expand the **Guardian Space Overlay** card and set:

- **Age Tier** — selects the tone and content map.
- **Parent Note** — an optional personalised message displayed on the overlay card.

Click **Save Overlay Settings** to persist.

### API

```http
PATCH /managed-users/<user_id>/overlay
Content-Type: application/json
```

```json
{
  "overlay_age_tier": "teen",
  "overlay_parent_note": "Bedtime rules active. Submit a note if you have a project deadline."
}
```

Both fields are optional.  Send `null` to clear.

## Access request flow

1. The child taps a **preset button** (younger age tiers) or types a free-text message (teen tier) on the overlay.
2. The overlay JavaScript calls `chrome.runtime.sendMessage({ type: "ACCESS_REQUEST", reason, message })` (browser extension) or `window.guardianBridge.sendAccessRequest(reason, message)` (Android WebView).
3. The browser extension's background worker forwards the request to the native agent via the native messaging IPC socket (`com.guardian.agent`).
4. The Rust/Android agent sends a `POST /api/access-request` to the server with:
   - `Authorization: Bearer <per-device secure_token>` — the token from `pairing_approved`, not the bootstrap `AGENT_TOKEN`
   - `system_id` — the device UUID (optional; must match the token if sent)
   - `linux_username` — the managed account
   - `reason` — overlay trigger reason
   - `message` — the child's message
5. The server stores the request as an `access_requested` `AgentAlert`.
6. The request appears on the parent dashboard under **Alerts**.

## Database migration

The `overlay_age_tier` and `overlay_parent_note` columns were added to the `managed_user` table in migration `n5i0j1k2l3m4`.  Run `flask db upgrade` (or allow auto-migration on startup) to apply the schema change before using these fields.

## Related documentation

- [Site Registration Approvals](registration-approvals.md) — the `signup` trigger and sign-up blocking flow
- [Browser Restrictions](browser-restrictions.md) — domain filtering that triggers the `filtered` overlay
- [Alerts & Webhooks](alerts-and-webhooks.md) — how `access_requested` alerts are delivered
- [Android Agent](../platforms/android-agent.md) — Android Device Owner permissions required for the overlay activity
- [Linux Agent](../platforms/linux-agent.md) — AppArmor and domain enforcement that triggers the `locked`/`filtered` overlay
