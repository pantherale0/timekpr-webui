# Policy presets

**Policy presets** bundle age-appropriate defaults into a single choice at child profile creation or from profile settings. Instead of configuring blocklists, screen time, Linux restrictions, Chrome policies, and approval modes separately, parents pick:

1. **Age bracket** — Under 7, 8–12, 13–15, or 16+
2. **Technical understanding (bypass risk)** — Low, medium, or high

Higher bypass risk applies **stricter** lockdown within the same age bracket (more filter packs, device locks, and approval requirements).

## What a preset configures

| Area | Applied by preset |
|------|-----------------|
| Curated filter packs | Subscribes marketplace blocklists (replaces existing marketplace subscriptions only) |
| Daily screen time | Weekday and weekend hour limits on the weekly schedule |
| Guardian Space overlay | Sets overlay age tier (`under8`, `eight12`, or `teen`) from the age bracket |
| Linux device restrictions | Per linked Linux/Windows mapping: polkit, terminal, install blocks |
| Chrome browser policies | SafeSearch, YouTube restrict, extension blocking, etc. |
| Access approvals | Domain and app launch modes, registration approval |
| Android profile type | `restricted` or `standard` on Android mappings when specified |

Presets do **not** lock configuration. After applying a preset, every individual toggle remains editable on the profile settings page.

## Applying a preset

### At profile creation

On **Child Accounts** (`/admin/users`), the create form walks through:

1. Child profile name
2. Age bracket
3. Technical understanding level

A live preview summarizes filter packs, screen time, and key restrictions before submit.

An **Advanced** section lets power users override marketplace blocklists with individual filter pack checkboxes.

### On an existing profile

On **Profile Settings** (`/admin/users/<id>`), the **Policy Preset** card shows the current selection or “Custom configuration.” **Change Preset** opens a modal with the same age and maturity options.

Re-applying a preset **overwrites** preset-controlled fields (with a confirmation dialog). Custom blocklist assignments outside the Filter Marketplace are not removed.

## Relationship to Filter Marketplace

Policy presets reference the same curated blocklist catalog as the [Filter Marketplace](../web-ui/web-filters.md) (`marketplace_presets.json`). The marketplace UI remains available for manual tweaks after a preset is applied.

## Bypass risk escalation

Within each age bracket, bundles escalate from low → high bypass risk:

- **Low** — Age-appropriate core filter packs; lighter device locks; `blocklist_only` domain mode; open app launch where age allows
- **Medium** — Additional categories (e.g. VPN/proxy, social); terminal or install blocks; `approval_on_block` for blocked sites
- **High** — Full relevant filter set; terminal, install, and pkexec blocks; `allowlist` app launch; extension blocking; registration approval where applicable

Younger age brackets and higher bypass risk both trend toward shorter daily screen-time limits.

## Configuration file

Bundle definitions live in `server/src/policy_preset_matrix.json` (12 cells: 4 ages × 3 maturity levels). To adjust defaults, edit the JSON and restart the server — no code change required unless new bundle fields are introduced.

## API routes

| Route | Method | Purpose |
|-------|--------|---------|
| `/managed-users/add` | POST | Create profile; optional `policy_age_bracket` + `policy_maturity_level` |
| `/managed-users/<id>/apply-policy-preset` | POST | Re-apply preset on existing profile |

## See also

- [Child accounts & mappings](../web-ui/child-accounts-and-mappings.md)
- [Web filters](../web-ui/web-filters.md)
- [Schedules & limits](../web-ui/schedules-and-limits.md)
- [Device restrictions](device-restrictions.md)
- [Guardian Space overlay](guardian-overlay.md)
