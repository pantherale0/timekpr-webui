# Security

## Transport security

Always terminate TLS in production. Agents should use **`wss://`** endpoints. The pairing handshake uses HMAC challenge–response; configuration payloads still require transport encryption.

## Token lifecycle

1. **`AGENT_TOKEN`** — server-wide bootstrap secret used only during initial pairing.
2. **Per-device token** — issued on approval (`pairing_approved`); stored in agent config; used for HMAC `register` messages.
3. **`REGISTRATION_TOKEN`** — optional extra gate on new `hello` messages when set on server and agent.

If an agent config file leaks, **revoke the device** in the admin UI rather than rotating only `AGENT_TOKEN`.

## File permissions (Linux agent)

Keep `/etc/timekpr-agent/config.json` owned by `root:root` with mode `0600`.

## Registration firewall

Enable `REGISTRATION_TOKEN` on both server and agents to prevent unauthorized pending devices on your dashboard.

## Nintendo and Xbox sessions

Linked cloud account tokens are stored in the server database. Restrict admin UI access and unlink accounts from **Settings** if the server may have been compromised.

## OIDC

When OIDC is enabled, configure allowlists (`ALLOWED_OIDC_ADMINS`, domains, roles, or groups). Avoid `OIDC_ALLOW_ANY_AUTHENTICATED=true` in production unless intentional.

See [Auth & OIDC](../reference/auth-and-oidc.md).

## Alert webhooks

Webhook URLs configured in **Settings** receive JSON POSTs. Enable HMAC signing in settings to verify `X-Timekpr-Signature` on your receiver.

See [Alerts & webhooks](../features/alerts-and-webhooks.md).

## Android factory reset

Remote wipe requires Device Owner. Use only on family-managed devices with explicit parent consent.

See [Unenroll & factory reset](../workflows/unenroll-and-factory-reset.md).
