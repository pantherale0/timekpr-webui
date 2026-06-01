# AGENTS.md

This repository implements a server–agent system for managing TimeKpr‑nExT across multiple Linux machines. The Flask server provides the web UI, REST APIs, and a WebSocket hub; each managed machine runs a Rust agent that connects outbound to the server. A lightweight Python debug agent is included for local testing.

## Essential commands

- Docker (recommended server run):
  - Start stack: `docker-compose up -d --build`
- Manual server run (from `server/`):
  - Install deps: `pip install -r requirements.txt`
  - Web app: `python app.py`
  - Background worker: `python task_worker.py` (run as separate process)
- Python debug agent (from `server/`):
  - `python debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "vX.Y"`
- Rust agent (from `agent/`):
  - Build: `cargo build --release`
- Android agent (from `android-agent/`):
  - Build: `./gradlew assembleDebug`
  - See `docs/android-agent.md` for pairing QR, permissions, and policy mapping.
- Tests (from `server/`):
  - `pytest` (project includes tests under `server/tests/`)

Environment (observed):
- `AGENT_TOKEN` bootstrap token (server)
- `REGISTRATION_TOKEN` optional pairing firewall (server and agent)
- `FCM_SERVER_KEY` or `FIREBASE_CREDENTIALS_JSON` optional FCM push for Android agents
- `DATABASE_URL` optional PostgreSQL URL; defaults to SQLite file when unset
- `TZ` timezone
- `TIMEKPR_SERVER_VERSION` server version string used in UI/protocol checks

## Architecture and flow

- Outbound-only agent connections: agents open a WebSocket to `/ws` (Flask‑Sock) and authenticate via challenge–response HMAC using a per-device secret.
- Device lifecycle: pending → approved → per-device token issuance. After approval, the agent stores a device-specific secret and reconnects.
- Server processes:
  - Flask web app (UI + REST + WS endpoint)
  - BackgroundTaskManager (separate process via `task_worker.py`) for blocklist refresh, user data sync, policy sync, and alert delivery.
- Data layer: SQLAlchemy models with Alembic/Flask‑Migrate. Compatible with SQLite and PostgreSQL. SQLite tuned via PRAGMA; optional auto-migration code supports bulk transfer to PostgreSQL.

## Code organization

- `server/app.py` — app factory/wiring, WS route, blueprint registration, DB config, background manager init, optional SQLite→PostgreSQL migration helper.
- `server/src/` — application modules:
  - `blueprints/` — UI (`ui_*`) and API (`api_*`) blueprints, plus `websocket` handler.
  - `database.py` — SQLAlchemy models, helpers, coercion utilities, and SQLite PRAGMA.
  - Managers: `agent_helper.py`, `alerts_manager.py`, `apparmor_manager.py`, `blocklists_manager.py`, `schedule_manager.py`, `settings_manager.py`, `task_manager.py`, `users_manager.py`.
  - Helpers: `helpers.py`, `oidc_helper.py`, `blocklist_helper.py`.
- `server/templates/` — Jinja2 templates for UI (admin, schedules, settings, etc.).
- `server/tests/` — pytest suite with route, helper, and manager coverage.
- `agent/` — Rust client agent (`Cargo.toml`, `src/`).
- `android-agent/` — Kotlin Android agent (WebSocket protocol parity, Device Admin, VPN domain policies).
- `scripts/install-agent.sh` — installer for released agent binaries (systemd setup, config management).

## Patterns and conventions

- Blueprints: all routes live under modular blueprints and are registered in `server/src/blueprints/__init__.py`. When calling `url_for` without blueprint prefix, `app.py` installs a `fallback_handler` that tries each blueprint namespace automatically.
- WebSocket: the single endpoint `/ws` is registered via Flask‑Sock; the handler is `src/blueprints/websocket.ws_agent_handler` (imported in `app.py` as `ws_agent_handler`).
- Logging: Python `logging` with module-level loggers (e.g., `_LOGGER = logging.getLogger(__name__)`). Avoid `print`.
- Timezones: timezone-aware datetimes (`DateTime(timezone=True)` in DB); helpers in `helpers.py` and Jinja filters (`localtime`). Avoid `datetime.utcnow()`, prefer aware UTC conversions.
- Security tokens: never transmit bootstrap token over the wire; handshake uses HMAC of a server challenge. After approval, agents store per-device tokens.
- Allowed alert types: enforced in `src/agent_helper.py` via `ALLOWED_AGENT_ALERT_TYPES` and payload normalization; reject events outside the allowlist.

## Testing approach

- Pytest tests under `server/tests/` exercise:
  - Auth/OIDC redirects and sessions
  - Dashboard and UI routes
  - WebSocket handshake and agent interactions via test doubles
  - Database helpers and managers (blocklists, tasks, OIDC helper, debug agent)
- Typical pattern: create fixtures in `conftest.py`, use SQLAlchemy session, mock OIDC endpoints, and simulate WS with an in-memory stub (`MockWS` in tests).

## Gotchas and non-obvious details

- Run worker separately: `task_worker.py` must run in its own process; don’t embed in Gunicorn workers to avoid blocking.
- Token lifecycle: after device approval the stored `agent_token` in the agent config changes and no longer equals the server-wide `AGENT_TOKEN` — this is expected.
- TLS: use `wss://` in production; `ws://` is for trusted local testing only. Without TLS, agents cannot authenticate the server.
- Registration firewall: when `REGISTRATION_TOKEN` is set on the server, new clients must include it or pairing will be refused.
- SQLite tuning: custom PRAGMAs are applied on connect; be careful when swapping engines during tests.
- URL building: if you add routes in new blueprints, either reference them with `url_for('bp.endpoint')` or rely on the global fallback handler; duplicate endpoint names across BPs can cause ambiguity.
- Data migrations: Alembic folder lives in `server/migrations/`. The repo includes additional hand-written migration scripts; new models should include migrations.
- Alerts ingestion: timestamps must be ISO‑8601; `Z` is normalized to `+00:00` and stored/serialized as UTC.

## How to extend safely

- Adding APIs/UI: place new endpoints in an existing or new blueprint under `server/src/blueprints/`, register in `server/src/blueprints/__init__.py`, and ensure templates live under `server/templates/`.
- Database changes: update SQLAlchemy models in `server/src/database.py`, generate Alembic migration, and ensure tests cover upgrade paths.
- Agent protocol: keep the `/ws` handshake and message schema backward‑compatible; update both Rust agent and the Python debug agent when changing message formats; enforce validation in `agent_helper.py`.
- Background work: add toggles in `BackgroundTaskManager` and respect env flags similar to `TIMEKPR_TASKS_*` used in `app.py`.

## Minimal local dev workflow

1) `cd server && pip install -r requirements.txt`
2) `export AGENT_TOKEN=... TZ=...` (and optionally `DATABASE_URL`, `REGISTRATION_TOKEN`)
3) `python app.py` (terminal 1)
4) `python task_worker.py` (terminal 2)
5) Optionally run: `python debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "v0.0.0-dev"`
6) Run tests: `pytest`

## Cursor Cloud specific instructions

### Toolchain versions

- **Python**: system Python 3.12 works for local dev (`python3 app.py`). Production Docker images use Python 3.14.
- **Rust**: the agent crate uses **edition 2024** (`agent/Cargo.toml`). Ensure `rustup default stable` points at a recent stable toolchain (1.85+); the VM may ship with an older default (1.83) that cannot parse the manifest.
- **pip scripts**: `pip install --user` puts `flask`, `gunicorn`, etc. under `~/.local/bin`. Prefer `python3 -m pytest` / `python3 app.py` if that directory is not on `PATH`.

### Services for end-to-end local dev

| Service | Required? | Command (from repo root) |
|---------|-----------|--------------------------|
| Flask web app | **Yes** | `cd server && export AGENT_TOKEN=... TZ=UTC TIMEKPR_SERVER_VERSION=v0.0.0-dev && python3 app.py` |
| Background worker | Optional for UI/agent testing | `cd server && python3 task_worker.py` (separate terminal; `app.py` also starts in-process tasks) |
| Python debug agent | Optional (simulates Rust agent) | `cd server && python3 debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "v0.0.0-dev"` |
| Rust agent binary | Optional | `cd agent && cargo build --release` → `target/release/timekpr-agent` (Linux-only; needs D-Bus/TimeKpr-nExT at runtime) |

Default admin login: `admin` / `admin`. Approve new debug-agent devices at `/admin/devices`.

### Lint / test / build commands

- **Python tests**: `cd server && python3 -m pytest` (uses in-memory SQLite; no running server required).
- **Rust compile check (matches CI)**: `cargo check --manifest-path agent/Cargo.toml`
- **Rust release build**: `cargo build --release --manifest-path agent/Cargo.toml`
- **Pyright**: config in `pyrightconfig.json` (`typeCheckingMode: off`); no repo lint script.

### Gotchas

- Match `--agent-version` / `TIMEKPR_SERVER_VERSION` between server and debug agent (default `v0.0.0-dev`).
- `debug_agent.py` writes state to `server/debug-agent.json`; delete it to simulate a fresh client.
- Six pytest failures were observed in this environment (AppArmor UI routes + one task-manager thread test) with 72 passing — treat as pre-existing unless you are working on those areas.
