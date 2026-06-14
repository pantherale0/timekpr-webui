# Contributing

Guide for developers working on the Guardian (timekpr-webui) repository.

## Repository layout

| Path | Purpose |
|------|---------|
| `server/` | Flask app, APIs, WebSocket hub, worker, tests |
| `agent/` | Rust Linux/Windows agent |
| `android-agent/` | Kotlin Android agent |
| `scripts/` | Installers and release helpers |
| `docs/` | MkDocs documentation source |

## Essential commands

```bash
# Server (Docker)
docker-compose up -d --build

# Server (manual)
cd server && pip install -r requirements.txt
python app.py              # terminal 1
python task_worker.py      # terminal 2

# Tests
cd server && pytest

# Rust agent
cargo check --manifest-path agent/Cargo.toml
cargo build --release --manifest-path agent/Cargo.toml

# Android
cd android-agent && ./gradlew assembleDebug

# Docs
pip install -r requirements-docs.txt
mkdocs serve
```

## Architecture conventions

- **Outbound WebSocket** at `/ws` with HMAC auth after approval
- **Blueprints** in `server/src/blueprints/` — register in `__init__.py`
- **Timezone-aware** datetimes everywhere; avoid naive UTC
- **Logging** via `logging` module, not `print`
- **Agent protocol** changes must stay backward compatible; update Rust, Android, and debug agent together

## Extending safely

1. **New API/UI** — add blueprint + template; use `url_for('bp.endpoint')` or global fallback handler
2. **Database** — update `database.py`, add Alembic migration under `server/migrations/`
3. **Background work** — add toggle in `BackgroundTaskManager` with `TIMEKPR_TASKS_*` env flag
4. **Alerts** — extend `ALLOWED_AGENT_ALERT_TYPES` and normalization in `agent_helper.py`

## Testing

Pytest fixtures in `server/tests/conftest.py`. WebSocket tests use in-memory stubs. Run full suite before PRs.

## Documentation

Update relevant pages under `docs/` when changing user-visible behavior. Build locally:

```bash
mkdocs build --strict
```

See [Local development](local-dev.md) and [AGENTS.md](https://github.com/pantherale0/timekpr-webui/blob/master/AGENTS.md) for AI/agent contributor notes.

## Related

- [CI & releases](ci-release.md)
- [WebSocket protocol](../reference/websocket-protocol.md)
