**AGENTS.md (Refined)**

This repository implements **Guardian**, a multi-tenant, secure, cross-platform parental control and Mobile Device Management (MDM) system. It uses a simple **outbound-only server–agent architecture** to manage open platforms (Linux, Windows, Android) and provides visibility into closed gaming ecosystems (Xbox, Nintendo Switch) via API ingestion where possible.

The design prioritizes **security, maintainability, and long-term human support**. Architecture and UI are kept deliberately simple: standard patterns, minimal moving parts, clear separation of concerns, and human-readable code/configs.

### Core Principles
- **Zero-trust outbound-only model**: Agents initiate all connections (no inbound firewall holes).
- **Defense in depth with simplicity**: Use proven OS primitives; avoid custom kernels or overly exotic solutions.
- **Human maintainability**: Code, configs, and UI must be understandable by a competent developer or sysadmin without deep domain expertise.
- **Fail-safe defaults**: Security boundaries must degrade gracefully; never brick devices.
- **Auditability and accountability**: All critical actions are logged; bypass claims are taken seriously.

---

### 🛠️ Toolchain & Development Environment

**Server (Python)**
- Python 3.12+ for development.
- Production: Python 3.12/3.13 base in minimal OCI images.
- Web framework: Lightweight Flask + SQLAlchemy + Gunicorn (gevent worker) + WebSockets.
- Database: PostgreSQL (production), SQLite (dev/testing).

**Client Agents**
- **Rust (Linux/Windows)**: Edition 2021 or 2024, stable toolchain ≥1.80. Focus on safety and minimal dependencies.
- **Kotlin (Android)**: SDK 34+, Device Owner mode for robust background operation.

**Environment Variables** (documented in `.env.example`)

| Variable              | Used By     | Purpose                                      |
|-----------------------|-------------|----------------------------------------------|
| `AGENT_TOKEN`         | Server/Agent| Bootstrap secret (never sent in clear after pairing) |
| `REGISTRATION_TOKEN`  | Server      | Optional enrollment gate                     |
| `DATABASE_URL`        | Server      | PostgreSQL or SQLite                         |
| `TZ`                  | All         | Consistent timezone handling                 |
| `TIMEKPR_SERVER_VERSION` | Handshake | Schema compatibility enforcement             |

---

### 🧬 System Architecture & Network Flow

```
Managed Endpoint (Agent) ──(outbound wss://)──► Traefik (TLS termination) ──► Flask Server
                                                              │
                                                              ▼
                                                       SQLAlchemy + PostgreSQL
                                                              │
                                                              ▼
                                                       Background Task Queue
```

**Key Flows**

1. **Registration & Pairing**
   - Unpaired agent connects with registration payload (optionally protected by `REGISTRATION_TOKEN`).
   - Device appears in “Pending Approvals” dashboard.
   - Admin approval → server generates cryptographically secure random 64-char `secure_token`.
   - Future connections use HMAC-SHA256 challenge-response authentication.

2. **Command & Policy Synchronization**
   - Persistent WebSocket for real-time commands and heartbeats.
   - **Disconnected operation**: Server queues policy changes in `pending_commands` table.
   - On reconnect → queue is replayed in order → agent reaches consistent state.
   - Use exponential backoff + jitter for reconnection.

**Security Boundaries (Strict)**
- All external communication uses WSS with certificate pinning where feasible.
- Agents run with least privilege (drop root/CAPs after startup where possible).
- No agent should ever execute arbitrary code from the server.

---

### ⚔️ Platform Enforcement (Keep Simple)

**Linux**
- Process monitoring via `netlink` (CN_PROC) + `/proc` checks for race mitigation.
- AppArmor profiles for application sandboxing (dynamic generation + `apparmor_parser -r`).
- `iptables`/`nftables` for DNS and basic network control (prefer nftables for modernity).
- Systemd service with restricted capabilities:
  ```ini
  AmbientCapabilities=CAP_NET_ADMIN CAP_MAC_ADMIN
  CapabilityBoundingSet=CAP_NET_ADMIN CAP_MAC_ADMIN
  ProtectSystem=strict
  PrivateTmp=yes
  ```

**Android**
- Device Owner provisioning (factory reset + QR code).
- Use `DevicePolicyManager` for suspension, app restrictions, and usage controls.
- Avoid lock screen password reset to prevent bricking on network loss.

**Windows** (future/partial)
- Use modern APIs (WDAC, AppLocker, Windows Filtering Platform) instead of brittle registry hacks.

**Firmware & Time Integrity**
- On boot and periodically: verify Secure Boot status.
- Cross-check system time against trusted NTP sources + monotonic counters.
- Detect large clock skew → trigger lockout + alert.

---

### Hardware Home Kit Appliance (Optional but Recommended)

A simple, low-power SBC (e.g., NanoPi NEO or equivalent) for network-level visibility and console protection via ARP spoofing / transparent proxying.

**Design Goals**
- Fully immutable/read-only root (SquashFS + tmpfs for logs/state).
- Power from router USB, minimal heat/footprint.
- Heartbeat to cloud; offline alerts.
- ARP poisoning only when explicitly enabled per console; fallback to dead MAC on block.

Keep appliance software minimal — primarily a Rust binary for filtering and WebSocket reporting.

---

### 🔒 Accountability – Bypass Credit Policy

If a child bypasses an active, correctly configured restriction due to a **verifiable defect** in Guardian (not user modification, rooting, or unsupported config), the household receives a credit.

**Eligible**: Code/logic flaws allowing unpermitted access while device shows “Protected”.
**Excluded**: Rooted devices, disabled UAC/Secure Boot, admin rights granted to child, unsupported platforms.

Credits are modest, capped annually, and non-cash. All verified incidents become public (anonymized) in the transparency ledger with a linked GitHub issue that must be fixed before closure.

---

### 🎨 UI/UX Guidelines (Keep It Simple & Human)

**Design Philosophy**
- Supportive, calm, educational tone — not surveillance dystopia.
- Minimize cognitive load: clear hierarchy, few colors, generous whitespace.
- Age-aware interfaces (simpler for younger children).
- Every block screen includes a parent message + calm offline activity (breathing exercise, doodle canvas).

**Color Palette**
- Primary: Sage Green `#4A6B5D` (safe/active)
- Backgrounds: Warm light `#FBFBF9`
- Text: Slate `#1E293B`

**Copy Rules**
- Use plain, friendly language everywhere user-facing.
- Replace technical jargon in UI, tooltips, and error messages.
- Examples:
  - “Your Home’s Control Centre” instead of “Multi-tenant Workspace”
  - “Routine Locks” instead of “Polkit/Registry Blocks”
  - “Anti-Bypass Watching” instead of internal mechanism names

---

### 🧪 Testing & Verification

- All backend changes require passing `server/tests/`.
- Use `MockWS` for WebSocket logic (no live sockets in unit tests).
- Rust: `cargo check`, `cargo test`, `cargo fmt`, `cargo clippy`.
- End-to-end: Minimal, focused on critical paths (pairing, policy sync, enforcement).

Local test commands:
```bash
# Server
cd server && python -m pytest

# Agent
cd agent && cargo check && cargo test
```

---

### 📂 Code Organization (Flat & Obvious)

```
server/
├── app.py                  # Factory, blueprints, WS setup
├── src/
│   ├── blueprints/         # ui_*, api_*, ws_*
│   ├── database.py         # Models + migrations
│   ├── managers/           # agent_helper, blocklists, task_manager, etc.
│   └── utils/              # security, crypto, validation
├── tests/
agent/                      # Rust core multi-platform agent
android-agent/              # Kotlin agent wrapper
scripts/                    # install-agent.sh, provisioning helpers
docs/                       # Service documentation (for both users and developers)
extension/                  # Files related to the web browser extension
i18n/                       # Internationalisation configuration files and UI strings, sorted by language folders (ISO 639-1), split into yaml files for each service
i18n/*/server.yaml          # Server internationalisation configuration
i18n/*/agent.yaml           # Agent internationalisation configuration
i18n/*/extension.yaml       # Browser extension internationalisation configuration
```

**Additional Rules for Contributors / AI Agents**
- Prefer explicit, readable code over clever abstractions.
- Configuration via files/env vars — avoid heavy frameworks.
- Security: Validate all inputs, use prepared statements, rate-limit, log sensibly (no PII where avoidable).
- Dependencies: Keep minimal and pinned.
- Breaking changes: Major version bump + clear migration path.
- Sub-agents: Check for relevant sub-agents based on the requested activity, always pull in and check `.agent/ARCHITECTURE.md` for guidance on their usage. 
- SQL Migrations: For boolean values always use `sa.false()` or `sa.true()` when representing a default. Not doing so will cause PostgresQL installations to fail.

This document serves as the **north star** for implementation. When in doubt, choose the simpler, more auditable solution that a human can debug and maintain years from now.

---

**Status**: This is the living reference. Update it as architecture decisions are finalized.