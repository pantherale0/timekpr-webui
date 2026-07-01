# Security

## Transport security

Always terminate TLS in production. Agents should use **`wss://`** endpoints. The pairing handshake uses HMAC challenge–response; configuration payloads still require transport encryption.

## Token lifecycle

1. **`AGENT_TOKEN`** — server-wide bootstrap secret used only during initial pairing.
2. **Per-device token** — issued on approval (`pairing_approved`); stored in agent config; used for HMAC `register` messages and agent HTTP APIs such as `POST /api/access-request`.
3. **`REGISTRATION_TOKEN`** — optional global gate on new `hello` messages when set on server and agent.
4. **Household `enrollment_token`** — per-household pairing secret (shown in pairing QR when multi-tenant). Either this token or `REGISTRATION_TOKEN` must be presented in `hello` for new devices, and again when an approved but unpaired agent requests token delivery over WebSocket.

If an agent config file leaks, **revoke the device** in the admin UI rather than rotating only `AGENT_TOKEN`.

## File permissions (Linux agent)

Keep `/etc/guardian-agent/config.json` owned by `root:root` with mode `0600`.

## Registration firewall

Set **`REGISTRATION_TOKEN`** on the server and include it in agent pairing config (or use the household **enrollment token** from **Settings → Agent pairing**) to prevent unauthorized pending devices on your dashboard. Without a configured token, open-registration mode still works for single-tenant installs but is not recommended on internet-exposed servers.

Approved agents that report `paired: false` no longer receive `pairing_approved` over WebSocket unless they resubmit a valid enrollment or registration token.

## Web UI session hardening

- Session cookies are **HttpOnly** with **SameSite=Lax** by default.
- Set `SESSION_COOKIE_SECURE=true` behind HTTPS so cookies are not sent over plain HTTP.
- Optional override: `SESSION_COOKIE_SAMESITE` (`Lax`, `Strict`, or `None`).
- Local admin passwords must be **at least 12 characters** (change default `admin` / `admin` immediately after install).
- Local login is rate-limited (10 attempts per client IP per minute).
- Household invite links (`/invite/redeem/<token>`) show a confirmation page on GET; redemption is a POST after sign-in (no automatic GET side effects).

## Parent authorization

Beyond household membership, sensitive device actions (approve/reject, unenroll, screenshot capture, hardware baseline apply, Windows LAPS reveal, etc.) require **`can_manage_policies`** on a child mapped to that device, or household owner/admin role. Read-only parents cannot perform these mutations.

## Outbound URL safety (SSRF)

The background worker validates **external blocklist URLs** and **alert webhook URLs** before fetching or POSTing. Only `http`/`https` URLs that resolve to routable public addresses are allowed; private, loopback, and link-local targets are rejected. Webhook delivery does not follow HTTP redirects.

## Nintendo and Xbox sessions

Linked cloud account tokens are stored in the server database. Restrict admin UI access and unlink accounts from **Settings** if the server may have been compromised.

## OIDC

When OIDC is enabled, configure allowlists (`ALLOWED_OIDC_ADMINS`, domains, roles, or groups). **`OIDC_ALLOW_ANY_AUTHENTICATED=true` is refused in production** (only honored when `TESTING=true`). Do not rely on it outside automated tests.

See [Auth & OIDC](../reference/auth-and-oidc.md).

## Alert webhooks

Webhook URLs configured in **Settings** receive JSON POSTs. Enable HMAC signing in settings to verify `X-Timekpr-Signature` on your receiver.

See [Alerts & webhooks](../features/alerts-and-webhooks.md).

## Android factory reset

Remote wipe requires Device Owner. Use only on family-managed devices with explicit parent consent.

See [Unenroll & factory reset](../workflows/unenroll-and-factory-reset.md).
