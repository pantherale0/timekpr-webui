# Settings

**Settings** (`/settings`) covers server administration and integration setup.

## Account

- Change local admin password
- **OIDC** — when env vars configured, login redirects to IdP (see [Auth & OIDC](../reference/auth-and-oidc.md))

## Agent pairing

- **In-app QR** — JSON payload with `wss://` server URL and optional `registration_token`
- **Android MDM provisioning QR** — factory-reset 6-tap flow; upload dev release APK when running `v0.0.0-dev`
- Set **`TIMEKPR_AGENT_WS_URL`** when behind a reverse proxy so QR codes are correct

## Cloud accounts

- **Nintendo Switch Account** — link once for console import
- **Xbox Live Account** — Microsoft Family Safety OAuth

## Alerts

- Enable outbound **webhook** URL
- Optional HMAC secret for signature verification

## App settings

Miscellaneous server toggles stored in the settings table (timezone display, retention, etc.).

## Related

- [Configuration reference](../getting-started/configuration.md)
- [Cloud console setup](../workflows/cloud-console-setup.md)
