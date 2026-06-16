# Site Registration Approvals & Online Account Audit

Guardian can detect when a managed child attempts to **sign up for a new online account** on any website and block the registration until a parent or guardian explicitly approves it. At the same time, the extension passively records **login events** to build an inventory of websites and services the child already has accounts on.

!!! info "Platform availability"
    Site Registration Approvals and Online Account Audit require the **Guardian browser extension** (Chrome, Brave, or Edge) and the **Linux or Windows agent** running on the managed device. Android and console platforms are not covered by this feature.

---

## How it works

### 1. Signup detection

When the Guardian browser extension loads on any web page, it runs a detection pass across five complementary signal layers, stopping as soon as any layer matches:

1. **URL heuristics** — paths containing `/signup`, `/register`, `/join`, `/create-account`, etc. are flagged immediately without inspecting the DOM.
2. **`autocomplete="new-password"`** — the most reliable single signal. The HTML spec requires registration forms to set this attribute on new-password fields (and `current-password` on login fields). All major services follow this convention: Google, GitHub, Facebook, DigitalOcean, and more.
3. **Multiple password fields** — a password field alongside a confirm-password field is a clear registration indicator.
4. **Registration-specific form fields** — fields for first/last name, phone number, or date of birth (`autocomplete="given-name"`, `autocomplete="bday"`, `input[type="tel"]`, etc.) are essentially never present on login forms.
5. **Page-level text** — headings (`h1`–`h3`), submit buttons, and navigation links are scanned for text such as *Sign Up*, *Create Account*, *Join*, or *Register*.

If the page matches, the extension immediately asks the native agent (`com.guardian.agent`) whether registration on that domain is currently allowed for this user.

### 2. Approval check

The agent calls `/api/registration/check` on the Guardian server using the device's secure token. The server returns one of three states:

| Response | Meaning |
|----------|---------|
| `allowed: true` | Registrations are not enforced, or a prior approval grant exists — page loads normally. |
| `allowed: false, pending: true` | A previous request is still awaiting approval. |
| `allowed: false, pending: false` | No request has been submitted yet. |

### 3. Block page redirect

If the response indicates the registration is blocked, the extension redirects the tab to a built-in block page (`blocked.html`). The child sees a clear, friendly explanation and two action buttons:

- **Request Approval** — sends a `REQUEST_REGISTRATION` message to the agent, which creates a pending `ApprovalRequest` in the Guardian server. The parent sees a *Site Sign-Up* badge in the [Access Requests](../web-ui/access-requests.md) panel.
- **Check Approval Status** — manually re-queries the agent. The page also polls automatically every **10 seconds** and whenever the browser tab regains focus.

Once the parent approves the request in the admin panel, the block page detects the approval on the next poll and automatically redirects the child back to the original sign-up URL.

---

## Enabling sign-up approvals

1. Navigate to **Admin → Users → [child account] → Mappings**.
2. Expand the mapping for the managed device.
3. Under **Access Approval Policies**, enable the **Enforce Sign-up Approvals** toggle.
4. Click **Save Approval Settings**.

The setting takes effect immediately — no agent restart is required.

---

## Online Account Audit

In addition to blocking registrations, the extension passively monitors **login form submissions** to build a catalogue of websites the child already has accounts on.

### How login events are captured

When a page contains exactly **one** password field and does not match the signup heuristics above (indicating a login, not a registration), the extension attaches a listener to the login form's `submit` event. On submission it captures:

- The **domain** (`window.location.hostname`).
- The **username or email** typed into the form's text/email input.

!!! warning "Privacy guarantee"
    The password field value is **never** read or transmitted. Only the plaintext username or email is recorded.

The captured data is forwarded to the agent via a `LOGIN_DETECTED` message, which calls `/api/registration/log-login`. The server inserts or updates a row in the `UserOnlineAccount` table, recording:

| Field | Description |
|-------|-------------|
| **Service Domain** | Hostname of the site |
| **Username / Email** | Value from the login form |
| **First Detected** | Timestamp of the first observed login |
| **Last Active** | Timestamp of the most recent login |

### Viewing the report

Navigate to **Admin → Users → [child account] → Online Accounts**. The page displays a searchable, sortable table of all recorded accounts. Use the search bar to filter by domain or username.

---

## Handling approval requests

Pending site sign-up requests appear in the global **Access Requests** list alongside time extension, app access, and domain unblock requests. They are identified by the **Site Sign-Up** amber badge.

To approve or deny:

1. Go to **Admin → Access Requests**.
2. Find the entry with the *Site Sign-Up* badge and the relevant domain.
3. Click **Approve** to grant a one-time or persistent registration grant, or **Deny** to reject it.

Once approved, the child's browser block page will detect the grant within 10 seconds and redirect automatically.

---

## Architecture overview

```
Child's Browser (Extension)
        │
        ▼
content.js — detects signup/login pages
        │
        ▼ (chrome.runtime.sendMessage)
background.js — routes CHECK_REGISTRATION / REQUEST_REGISTRATION / LOGIN_DETECTED
        │
        ▼ (Native Messaging: com.guardian.agent)
Guardian Rust Agent
        │
        ▼ (HTTPS with device token)
Guardian Server REST API
  ├─ GET  /api/registration/check    → allowed / pending / blocked
  ├─ POST /api/registration/request  → creates ApprovalRequest
  └─ POST /api/registration/log-login → upserts UserOnlineAccount
```

---

## Platform requirements

| Component | Requirement |
|-----------|-------------|
| Browser extension | Guardian extension v0.57.0+ |
| Supported browsers | Chrome, Brave, Edge (Chromium) |
| Native agent | Guardian Linux or Windows agent v0.57.0+ |
| Server | Guardian server v0.57.0+ |

---

## Related guides

- [Access Requests](../web-ui/access-requests.md) — approving and denying child requests
- [Browser Restrictions](browser-restrictions.md) — extension-based browser policies
- [Web & Video History](web-video-history.md) — browsing and YouTube history monitoring
- [Child Accounts & Mappings](../web-ui/child-accounts-and-mappings.md) — per-mapping policy settings
