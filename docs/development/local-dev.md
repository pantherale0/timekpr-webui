# Local development

Minimal workflow for hacking on Guardian server and agents.

## Server

```bash
cd server
pip install -r requirements.txt
export AGENT_TOKEN="$(openssl rand -hex 32)"
export TZ=UTC
export TIMEKPR_SERVER_VERSION=v0.0.0-dev
export TIMEKPR_AGENT_WS_URL=ws://127.0.0.1:5000/ws
python app.py
```

Second terminal:

```bash
cd server && python task_worker.py
```

Default login: **admin** / **admin**. Approve devices at `/admin/devices`.

## Debug agent

```bash
cd server
python debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "v0.0.0-dev"
```

Delete `debug-agent.json` to simulate a new device.

## Rust agent (Linux)

Requires TimeKpr-nExT D-Bus on the target machine for full enforcement.

```bash
cd agent && cargo build --release
```

## Android

```bash
cd android-agent
cp app/google-services.json.example app/google-services.json  # optional FCM
./gradlew assembleDebug
```

## Docs site

```bash
pip install -r requirements-docs.txt
mkdocs serve   # http://127.0.0.1:8000
mkdocs build --strict
```

## Toolchain notes

- Python 3.12+ for local dev; Docker images may use newer Python
- Rust **1.85+** required (edition 2024 manifest)
- Android SDK + JDK 17 for Gradle builds

## Related

- [Contributing](contributing.md)
- [Debug agent](../platforms/debug-agent.md)
