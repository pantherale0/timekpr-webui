# Guardian vs commercial parental controls

Guardian is a **self-hosted**, open-source parental control stack—not a subscription app from a vendor cloud. This page compares what Guardian offers against:

- **Built-in platform tools** — Apple Screen Time, Google Family Link, Microsoft Family Safety (recommended first by many reviewers including [PCMag](https://uk.pcmag.com/parental-control-monitoring/67305/the-best-parental-control-software))
- **Third-party subscription apps** — from roundups such as [PCMag 2026](https://uk.pcmag.com/parental-control-monitoring/67305/the-best-parental-control-software), [SafetyDetectives Windows 2026](https://www.safetydetectives.com/best-parental-control/windows/), [All About Cookies 2026](https://allaboutcookies.org/best-parental-control-apps), [SafeWise](https://www.safewise.com/kids-safety/parental-control-apps/), and common [Reddit r/software threads](https://www.reddit.com/r/software/comments/1l4rouh/what_is_the_best_parental_control_app_in_2025/) (Bark vs Qustodio vs Family Link debates)
- **Network / gaming-focused suites** — [Aura Parental Controls](https://www.meetcircle.com/) (formerly **Circle**), which now bundles mobile parental controls and PC **Safe Gaming** chat monitoring

!!! info "How to read the tables"
    **Yes** = supported today in Guardian for at least one managed platform.  
    **Partial** = limited scope, requires extra setup, or platform-specific.  
    **No** = not implemented.  
    **Vendor** = provided by the commercial/built-in product, not Guardian.

Pricing and feature lists for commercial products change frequently—verify on each vendor's site before buying.

## At a glance

| | Guardian | Built-in (Family Link / Family Safety / Screen Time) | Typical commercial apps |
|---|----------|------------------------------------------------------|-------------------------|
| **Cost** | Free software; you pay for hosting/hardware | Free with platform account | ~$3–15+/month subscription |
| **Data location** | Your server (SQLite/PostgreSQL) | Vendor cloud (Google/Microsoft/Apple) | Vendor cloud |
| **Setup effort** | High (server + agents + pairing) | Low (account linking) | Medium (install app, create account) |
| **iOS/iPhone** | No native agent | Strong (Screen Time) | Strong on most products |
| **Android** | Strong with Device Owner | Strong (Family Link) | Strong |
| **Linux desktop** | Strong (Rust agent) | No | Rare (Qustodio, Mobicip) |
| **Windows PC** | Supported (agent + MSI) | Strong (Family Safety) | Common |
| **Game consoles** | Nintendo + Xbox cloud sync | Platform parental apps | Usually not covered |
| **SMS / social spy** | No | No / limited | Often on Android (Bark, Qustodio Complete, etc.) |
| **Whole-home / router filtering** | No (per-device agents) | No | Circle (legacy hardware); Aura mobile VPN-style |
| **PC in-game chat monitoring** | No | No | Aura Safe Gaming (Windows) |

## Review landscape (2025–2026)

Products that appear repeatedly across independent roundups:

| Product | Often “best for” | Typical price signal | Sources |
|---------|------------------|----------------------|---------|
| **Qustodio** | Location, all-in-one, Windows filtering | Free tier + ~$55–110/yr | PCMag, SafetyDetectives, All About Cookies, Reddit |
| **Bark** | Social/SMS AI alerts, image scan | ~$99/yr | PCMag, All About Cookies, SafeWise, Reddit |
| **Norton Family** | Web rules, YouTube/Hulu, unlimited kids | ~$50/yr | PCMag, SafetyDetectives, All About Cookies |
| **Google Family Link** | Free Android/Chromebook | Free | PCMag, Reddit, All About Cookies |
| **Mobicip** | Screen time, schools/Chromebook | ~$50–96/yr | PCMag, SafetyDetectives |
| **Net Nanny** | Content filter, large families | ~$40–90/yr | PCMag, SafetyDetectives |
| **FamilyTime** | Location, SOS, SMS (Android) | ~$27–29/yr | PCMag, SafeWise |
| **Boomerang** | Android-heavy monitoring | ~$20–40/yr | PCMag |
| **FamiSafe** | Windows downtime, live screen view | ~$5/mo | [SafetyDetectives Windows](https://www.safetydetectives.com/best-parental-control/windows/) |
| **McAfee Safe Family** | Windows app categories, bundled AV | McAfee+ bundle | SafetyDetectives |
| **Kaspersky Safe Kids** | YouTube search on Windows | ~$28/yr | SafetyDetectives (not sold in US) |
| **Aura** | Gaming chat safety + mobile suite | ~$8–15/mo | SafetyDetectives, [Meet Circle](https://meetcircle.com/) |
| **FamilyKeeper** | Lightweight basics, fast setup | ~$8/mo | [All About Cookies](https://allaboutcookies.org/best-parental-control-apps) |
| **Canopy** | Porn / explicit content blocking | ~$100/yr | SafeWise, Rank Vault |
| **OurPact** | Screen time blocking | ~$69/yr | Rank Vault |
| **MMGuardian** | Tweens, SMS + image scan | ~$70/yr | SafeWise |

Reddit and parenting forums often converge on: **start with Family Link or Screen Time**, add **Qustodio** for granular limits or **Bark** for social monitoring—not one app for everything.

## Aura, Circle, and network-level products

[Circle](https://meetcircle.com/) was known for a **home Wi‑Fi device** that filtered all traffic on the LAN. Circle is now part of **Aura Parental Controls**, marketed as a mobile-first subscription:

| Aura / Circle capability | Guardian |
|--------------------------|:--------:|
| Block apps & sites on **mobile** across any network | Partial (Android agent + DNS/VPN; no iOS) |
| **Pause the internet** on child devices | Partial (time lockout / suspend apps; not carrier-level pause) |
| Per-app screen time limits (Roblox, Snapchat, etc.) | Partial (app policies + schedules) |
| Activity / blocked-site attempt logs | Yes (alerts, usage events, access requests) |
| **Safe Gaming** — voice/text scan in 200+ PC games | No |
| Whole-home filtering without per-device install | No (Guardian is agent-based, not router/DNS-at-gateway) |
| Malicious/adult site blocklists at ISP/router scale | Partial (custom domain blocklists per child) |
| Self-hosted; no Aura subscription | Yes |

**Takeaway:** Aura/Circle targets **mobile-first families** who want a polished cloud dashboard and optional **gaming chat surveillance** on Windows. Guardian targets **homelab operators** who want **deep OS enforcement** on Linux/Windows/Android they administer, plus **Switch/Xbox** sync—without a recurring parental-control SaaS bill.

Historical **Circle Home Plus** hardware is a different model (LAN DNS/filter). Guardian does not ship router firmware; you could combine Guardian agents on PCs with a separate Pi-hole or router filter for whole-home DNS.

## Feature comparison (core controls)

| Feature | Guardian | Google Family Link | Microsoft Family Safety | Apple Screen Time | Qustodio / Norton / Mobicip / Net Nanny | Bark | Aura / FamiSafe / FamilyKeeper |
|---------|:--------:|:------------------:|:-----------------------:|:-----------------:|:---------------------------------------:|:----:|:------------------------------:|
| Daily screen time limits | Yes | Vendor | Vendor | Vendor | Vendor | Vendor | Vendor |
| Weekly schedules / bedtime | Yes | Vendor | Vendor | Vendor | Vendor | Vendor | Vendor |
| Per-app time limits | Partial | Vendor | Partial | Vendor | Vendor | Partial | Vendor |
| App blocking / allowlists | Yes | Vendor | Partial | Vendor | Vendor | Vendor | Vendor |
| Web / domain filtering | Yes | Vendor | Vendor | Partial | Vendor | Partial | Vendor |
| DNS-bypass resistant filtering (on managed device) | Yes | Partial | Partial | Partial | Partial | Partial | Partial |
| YouTube history monitoring | No | Partial | Partial | Partial | Vendor | Vendor | Partial (Kaspersky, Norton) |
| Geofencing / GPS tracking | No | Vendor | Vendor | Vendor | Vendor | Partial | Vendor |
| Location / panic button | No | Vendor | Partial | Partial | Vendor (Qustodio) | Partial | Vendor |
| SMS / call logging | No | No | No | No | Partial (Android) | Partial | Partial (Bark, FamiSafe tiers) |
| Social media message scanning | No | No | No | No | Partial (paid tiers) | Vendor (AI) | Partial (Bark, Aura mobile) |
| Email monitoring | No | No | No | No | Partial | Vendor | Partial |
| In-game voice/text monitoring | No | No | No | No | No | Partial | Vendor (Aura Safe Gaming, Windows) |
| Live screen view (remote) | No | No | No | No | No | No | Vendor (FamiSafe Screen Viewer) |
| Remote device lock | Partial | Vendor | Vendor | Vendor | Vendor | Vendor | Vendor |
| Remote factory reset | Partial (Android DO) | No | No | No | No | Partial (Bark Phone) | No |
| Screenshots (desktop) | Yes (Linux/Windows) | No | No | No | Rare | No | Rare |
| Access approval workflow | Yes | Partial | Partial | Partial | Partial | No | Partial |
| Self-hosted / no vendor account | Yes | No | No | No | No | No | No |
| Open source | Yes | No | No | No | No | No | No |

**Guardian partial notes:**

- **Per-app limits** — policy presets and lockout suspend/block apps; not identical to vendor “30 minutes per app per day” UIs.
- **DNS-bypass resistant filtering** — blocklists are enforced **locally on each managed device** (Linux/Windows per-user DNS sinkhole; Android local DNS VPN with always-on VPN when Device Owner), so switching Wi‑Fi or cellular does **not** bypass filtering the way router or account-level DNS products can. Residual bypass vectors are narrower: another unmanaged device, disabling the agent (requires admin access), direct IP connections, or a third-party VPN app if not blocked by policy—Guardian can suspend non-allowlisted apps on Android Device Owner, but does not auto-block all VPN clients today.
- **Remote lock** — time lockout and app suspension, not always a full device PIN lock.
- **Factory reset** — Android Device Owner only.

## Platform coverage

| Platform | Guardian | Family Link | Family Safety | Screen Time | Commercial apps (typical) |
|----------|:--------:|:-----------:|:-------------:|:-----------:|:-------------------------:|
| Android phone/tablet | Yes | Yes | — | — | Yes |
| iPhone / iPad | No | — | — | Yes | Yes |
| Windows PC | Yes | — | Yes | — | Yes |
| macOS | No | — | — | Yes | Yes (Qustodio, Mobicip, Net Nanny) |
| Linux desktop | Yes | — | — | — | Rare |
| Chromebook | Partial | Yes | Partial | — | Yes (Mobicip, Bark) |
| Nintendo Switch | Yes (cloud) | — | — | — | No |
| Xbox | Yes (cloud) | — | Partial | — | No |

Guardian’s console support is **playtime and schedule sync** via cloud APIs—not in-console app blocking. See [Nintendo Switch](../platforms/nintendo-switch.md) and [Xbox](../platforms/xbox.md).

## Enforcement depth (where Guardian differs)

Commercial apps usually operate inside the OS sandbox their MDM/profile allows. Guardian goes further on platforms you control:

### Linux

| Capability | Guardian | Typical commercial app |
|------------|:--------:|:----------------------:|
| Process kill on blocked executables | Yes | Rare |
| AppArmor / path rules | Yes | No |
| Per-user DNS sinkhole | Yes | Browser-only or VPN filter |
| Polkit (block installs, media mount, power off) | Yes | No |
| Terminal / shell blocking (active session) | Yes | No |
| Bluetooth rfkill | Yes | No |

See [Linux agent](../platforms/linux-agent.md).

### Android

| Capability | Guardian | Family Link | Qustodio / Norton / etc. |
|------------|:--------:|:-----------:|:------------------------:|
| Package suspension at lockout | Yes (Device Owner) | Yes | Yes |
| DNS VPN domain blocklist | Yes | Partial | Partial |
| AMAPI-style device restrictions (camera, USB, dev settings) | Yes (Device Owner) | Partial | Partial |
| Multi-user tablet (parent + child profiles) | Yes (Device Owner) | Partial | Partial |
| Shared-tablet without Device Owner | Partial | Partial | Partial |

Device Admin alone is **not** enough for multi-user discovery—see [Troubleshooting](../troubleshooting/index.md#android-multi-user-and-device-owner).

### Windows

| Capability | Guardian | Family Safety | Norton / Mobicip |
|------------|:--------:|:-------------:|:----------------:|
| Persistent agent service | Yes | Yes | Yes |
| DNS proxy blocklists | Yes | Partial | Partial |
| Process termination lockout | Yes | Partial | Partial |
| Screenshots | Yes | No | Rare |

See [Windows agent](../platforms/windows-agent.md).

## What commercial products do better

Guardian is **not** a drop-in replacement for every feature in a paid suite. Vendors invest heavily in areas Guardian deliberately does not cover:

### Surveillance and communications

Products like **Bark**, **Qustodio Complete**, and **Boomerang (Android)** focus on:

- AI or keyword scanning of texts, email, and social posts
- YouTube watch history
- Call/SMS logs (often Android-only, sometimes sideloaded)

Guardian emits **usage and access-request alerts** (`app_launched`, `access_requested`, etc.) but does **not** ingest message content or social feeds. See [Alerts & webhooks](../features/alerts-and-webhooks.md).

### Location and safety

**Qustodio**, **Norton Family**, **Mobicip**, and **FamilyTime** commonly offer:

- GPS location history
- Geofences and enter/leave alerts
- Panic / Pick Me Up buttons

Guardian has **no GPS or geofencing**—it is designed for policy enforcement on devices you manage, not real-time tracking.

### Mobile-first convenience

**Family Link**, **Family Safety**, and **Screen Time** are free, polished, and deeply integrated with child Google/Microsoft/Apple accounts. Setup is minutes; no server required.

Guardian requires deploying a server, installing agents, approving devices, and mapping profiles—better for homelab parents, multi-PC households, or mixed Linux/Android/Windows/console environments.

### iOS

Guardian has **no iOS agent**. For iPhones and iPads, Apple **Screen Time** (or a commercial iOS-capable product) remains the practical choice. Guardian can still manage other family devices from the same dashboard.

### Streaming services

As PCMag notes, **Netflix, Disney+, and similar profile restrictions** are configured inside each streaming app—not via Guardian or most parental control suites. Guardian does not override streaming parental PINs or per-service age ratings.

## Side-by-side: PCMag 2026 picks vs Guardian

Summary aligned with [PCMag’s 2026 parental control roundup](https://uk.pcmag.com/parental-control-monitoring/67305/the-best-parental-control-software):

| Product | PCMag “best for” | Overlap with Guardian | Guardian advantage | Guardian gap |
|---------|------------------|----------------------|--------------------|--------------|
| **Qustodio** | Location tracking | Screen time, web filter, app block | Self-hosted; deeper Linux enforcement | GPS, iOS+macOS app, social/call monitoring |
| **Norton Family** | Online rules / dialogue | Schedules, web filter, unlimited devices on your server | No per-seat subscription; console sync | Geofencing, Mac agent, House Rules UX |
| **Boomerang** | Android monitoring | Android time/apps/filter (partial) | Device Owner depth; domain VPN | Mobile-only focus; SMS on Android |
| **Mobicip** | Screen time | Multi-OS time/filter (except iOS) | Linux + self-host; Windows agent | 20-device SaaS polish; social monitoring |
| **FamilyTime** | Busy parents / SOS | App block, schedules (partial) | No cloud subscription | Location, SOS, SMS, iOS parity |
| **Bark** | AI surveillance | App/block alerts only | Data stays on your server | AI scan of messages/social/email |
| **Net Nanny** | Big families | Web filter, screen time, app block | Unlimited mappings on your infra | No Android app; macOS/Windows SaaS UX |
| **Google Family Link** | (PCMag recommended built-in) | Android time/apps/location | Linux/Windows/console; local data | Free Google integration; iOS N/A |
| **Microsoft Family Safety** | (PCMag recommended built-in) | Windows/Xbox time; Xbox in Guardian | Linux desktop; Nintendo; self-host | Native Windows/Xbox UX; location driving reports |
| **Apple Screen Time** | (PCMag recommended built-in) | Schedules conceptually | Non-Apple platforms | Required for iOS/iPadOS |

### SafetyDetectives Windows 2026 & All About Cookies picks

| Product | Review highlight | Overlap with Guardian | Guardian advantage | Guardian gap |
|---------|------------------|----------------------|--------------------|--------------|
| **FamiSafe** | Windows downtime, live screen viewer | Schedules, app block, web filter | Linux depth; self-host; desktop screenshots | Live screen view; mobile SaaS UX |
| **McAfee Safe Family** | Windows app categories + AV bundle | App filtering, web categories | No McAfee bundle required; open source | Incognito/VPN-resistant filter marketing |
| **Kaspersky Safe Kids** | YouTube Safe Search on Windows | Web filter, schedules | No geo-blocked vendor; Nintendo/Xbox | YouTube-specific monitoring UI |
| **Aura** | Safe Gaming chat scan (US, Windows) | Alerts only | Full PC policy stack; not gaming-only | AI gaming predator alerts |
| **FamilyKeeper** | Fast setup, basics | Time, filter, location conceptually | Data custody; console agents | Turnkey 10-minute onboarding |
| **Canopy** | Explicit content / porn blocking | Domain blocklists | Broader OS rules beyond adult content | Purpose-built adult-content AI |
| **OurPact** | Screen time blocking | Schedules | Lower bypass surface on managed agents | Polished mobile pause UX |
| **MMGuardian** | SMS + image scan for tweens | Usage alerts | No message/image ingestion | Image scanning in SMS |

### Community patterns (Reddit / parenting forums)

Threads such as [r/software — best parental control 2025](https://www.reddit.com/r/software/comments/1l4rouh/what_is_the_best_parental_control_app_in_2025/) typically recommend:

| Pattern | Example stack | vs Guardian |
|---------|---------------|-------------|
| **Free baseline** | Family Link (Android) + Screen Time (iOS) | Guardian replaces neither on phones; adds PCs/Linux/consoles |
| **Management layer** | Qustodio or Mobicip | Overlaps schedules/apps/web; Guardian adds Linux polkit, DO Android, self-hosting |
| **Monitoring layer** | Bark or MMGuardian | Guardian does not read DMs/social; use Bark **alongside** if needed |
| **Layer both** | Qustodio + Bark | Common “best of both” advice; Guardian ≈ management side for owned devices |
| **Router / whole home** | Circle hardware, Pi-hole, OpenDNS | Complements Guardian agents; not a substitute |

## When Guardian is a strong fit

- You run **Linux desktops** or want **deep OS-level rules** (package managers, terminals, DNS sinkhole).
- You want **one dashboard** for Linux, Windows, Android, Switch, and Xbox without per-device SaaS fees.
- You prefer **data on your own server** (home NAS, VPS, Docker) with optional OIDC.
- You want **approval workflows** (child requests app/domain access; parent grants in UI).
- You need **Android Device Owner**-class control on family tablets (with acceptable provisioning constraints).

## When to use something else (or combine)

| Situation | Recommendation |
|-----------|----------------|
| Child’s primary device is an **iPhone/iPad** | Apple Screen Time (+ your rules conversation) |
| Child uses only **Android + Google account** | Google Family Link may be enough alone |
| You need **GPS / geofencing / SOS** | Qustodio, Norton Family, FamilyTime, or platform locators |
| You need **social/SMS AI monitoring** | Bark, Aura mobile, or Qustodio Complete—not Guardian |
| You need **in-game voice/text predator alerts** | [Aura Safe Gaming](https://meetcircle.com/) on Windows (US)—not Guardian |
| You need **live remote screen viewing** | FamiSafe Screen Viewer—not Guardian |
| You want **whole-home filtering** without per-PC agents | Circle/Aura mobile, router DNS, or Pi-hole—not Guardian alone |
| You want **zero server maintenance** | Family Link, Family Safety, Aura, or a commercial subscription |
| **Shared Android tablet**, can’t set Device Owner | Family Link on child profile, or Guardian on child profile only (limited)—see [troubleshooting](../troubleshooting/index.md) |
| Child’s PC gaming toxicity is top concern | Consider Aura Safe Gaming **plus** Guardian for time/app rules |

Many households use **Guardian for PCs, Linux, and consoles** plus **Family Link or Screen Time on phones**, and optionally **Bark or Aura** for message/gaming monitoring.

## Philosophy

PCMag’s 2026 guide and [All About Cookies’ testing methodology](https://allaboutcookies.org/best-parental-control-apps) both note that invasive spyware can be counterproductive and that determined children often bypass **browser- or account-level** filters (VPN, alternate browsers, alternate devices). On devices you fully manage, Guardian’s local agents reduce the “just join another Wi‑Fi” bypass common to router DNS and some mobile filter apps. Guardian leans toward:

- **Enforcement** on devices you administer (block, suspend, DNS filter, polkit)
- **Transparent access requests** instead of covert message reading
- **Self-hosting** so you control retention and who sees alerts

It does **not** replace open conversation, school cyber-safety programs, or platform-native controls on devices Guardian does not manage.

## Related documentation

- [Policy matrix](../reference/policy-matrix.md) — per-platform enforcement detail
- [Overview](overview.md) — Guardian architecture
- [Security](security.md) — tokens, TLS, data custody
- [Android agent](../platforms/android-agent.md) — Device Owner requirements
