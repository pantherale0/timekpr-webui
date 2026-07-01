# Auth and OIDC

Guardian supports **local admin login** and optional **OpenID Connect** SSO for the web UI.

## Local login

Default credentials after install: **admin** / **admin**. Change under **Settings** (new password must be at least **12 characters**).

Password hash stored in the database settings table. Failed login attempts are rate-limited (10 per client IP per minute).

## Enable OIDC

Set all three required variables:

| Variable | Description |
|----------|-------------|
| `OIDC_ISSUER_URL` | IdP issuer URL (discovery at `/.well-known/openid-configuration`) |
| `OIDC_CLIENT_ID` | OAuth client ID |
| `OIDC_CLIENT_SECRET` | OAuth client secret |

Optional:

| Variable | Description |
|----------|-------------|
| `OIDC_REDIRECT_URI` | Override callback URL (default built from request host) |
| `OIDC_VERIFY_SSL` | Default `true`; set false only for lab IdPs with bad TLS |

## Admin authorization

After OIDC login, userinfo must pass **`is_authorized_admin`** unless bypass is enabled.

Configure at least one allowlist:

| Variable | Matches |
|----------|---------|
| `ALLOWED_OIDC_ADMINS` | Email addresses (comma-separated) |
| `ALLOWED_OIDC_ADMIN_DOMAINS` | Email domains |
| `ALLOWED_OIDC_ADMIN_ROLES` | `roles` or `role` claim |
| `ALLOWED_OIDC_ADMIN_GROUPS` | `groups` or `group` claim |

Bypass (not recommended; **refused when `TESTING` is not `true`**):

| Variable | Effect |
|----------|--------|
| `OIDC_ALLOW_ANY_AUTHENTICATED=true` | Any authenticated IdP user gets admin UI access (test environments only) |

## Flow

1. User visits `/` — redirected to IdP when OIDC enabled
2. IdP redirects to `/callback` with authorization code
3. Server exchanges code, fetches userinfo, checks allowlists
4. Session flag `logged_in` set on success

If OIDC initialization fails, UI falls back to local password form.

## Household invites

Share links resolve to `/invite/redeem/<token>`. Visiting the link while signed in shows a confirmation page; accepting the invite requires **POST** (or the confirm form submit). Expired or exhausted invites return HTTP 410.

## Agent authentication

OIDC protects the **web UI only**. Agents use WebSocket HMAC tokens independent of OIDC sessions.

## Related

- [Security](../getting-started/security.md)
- [Settings](../web-ui/settings.md)
