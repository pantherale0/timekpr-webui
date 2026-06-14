# Child accounts and mappings

**Admin → Child Accounts** (`/admin/users`) lists managed users. Each child can have multiple **device mappings** linking them to hardware profiles.

## Create a child

1. **Add user** with username and optional display settings.
2. Open **Edit Profile** for schedules, blocklists, app policies, and mappings.

## Device mapping

Each mapping ties `(system_id, linux_username, linux_uid)` to the child:

| Platform | `linux_username` meaning |
|----------|-------------------------|
| Linux | PAM / `/etc/passwd` username |
| Windows | Windows account name |
| Android | Profile name from agent `linux_users` hello payload |
| Nintendo / Xbox | Player ID (UI shows Mii nickname / gamertag) |

### Mapping wizard

The **Add Device** onboarding wizard creates the device record and mapping in one flow.

### Manual mapping

On the child profile, **Link New Hardware Account Mapping**:

1. Select approved device
2. Choose discovered profile from datalist (or type manually with optional UID)
3. **Verify** mapping to confirm agent acknowledges the user (`validate_user` command)

### Android profile provisioning

When the device is **Device Owner**, you can choose **Create Restricted Profile** or **Create Standard User** to provision via `createAndManageUser` instead of mapping an existing profile.

## Validation status

Mappings show **Verified** / **Unverified** based on last successful `validate_user` sync.

## Related

- [Device management](device-management.md)
- [Pairing & approval](../workflows/pairing-and-approval.md)
