# Settings

**Settings** (`/settings`) covers server administration and integration setup.

## Account

- Change local admin password (minimum **12 characters**)
- **OIDC** — when env vars configured, login redirects to IdP (see [Auth & OIDC](../reference/auth-and-oidc.md))

## Agent pairing

- **In-app QR** — JSON payload with `wss://` server URL and `registration_token` (household enrollment token or global `REGISTRATION_TOKEN`)
- **Android MDM provisioning QR** — factory-reset 6-tap flow; upload dev release APK when running `v0.0.0-dev`
- Set **`TIMEKPR_AGENT_WS_URL`** when behind a reverse proxy so QR codes are correct

!!! note "Installer downloads"
    Dev APK (`/api/pairing/provisioning/apk`) and Windows MSI (`/api/pairing/windows/msi`) require an authenticated admin session. Sign in to the dashboard before downloading or copying direct links into installers.

## Cloud accounts

- **Nintendo Switch Account** — link once for console import
- **Xbox Live Account** — Microsoft Family Safety OAuth

## Alerts

- Enable outbound **webhook** URL (must be a public `http`/`https` endpoint; private/internal hosts are rejected)
- Optional HMAC secret for signature verification

## App settings

Miscellaneous server toggles stored in the settings table (timezone display, retention, etc.).

## Related

- [Configuration reference](../getting-started/configuration.md)
- [Cloud console setup](../workflows/cloud-console-setup.md)
