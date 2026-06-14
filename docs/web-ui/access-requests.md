# Access requests

When a child uses **allowlist**, **blocklist**, or **approval on block** modes, agents emit `access_requested` alerts. Review them under **Admin → Access Requests** (`/admin/approvals`).

## Request types

| Target | Source |
|--------|--------|
| Executable / package | Blocked app launch |
| Domain | DNS/VPN block with approval mode |

## Actions

- **Approve** — grants temporary or persistent access (app grant or domain allowlist entry)
- **Deny** — dismisses request; block remains

Approved apps/domains sync back to agents on the next policy cycle.

## Configuration

Per device mapping under **Approval settings**:

- App launch mode: `open`, `allowlist`, `blocklist`
- Domain access mode: standard block vs `approval_on_block`

API: `/api/mappings/<id>/approval-settings`, `/api/mappings/<id>/approval-grants`

## Related

- [Linux agent — access approvals](../platforms/linux-agent.md)
- [Android agent — access approvals](../platforms/android-agent.md)
- [Alerts & webhooks](../features/alerts-and-webhooks.md)
