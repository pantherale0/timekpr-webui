# Server deployment

## Docker Compose (recommended)

### Prerequisites

- Docker and Docker Compose
- For Android MDM dev provisioning: `apksigner` in `ANDROID_HOME` or `~/Android/Sdk/build-tools` on the host (optional)

### Steps

1. Clone the repository:

   ```bash
   git clone https://github.com/pantherale0/timekpr-webui.git
   cd timekpr-webui
   ```

2. Copy `.env.example` to `.env` and configure:

   ```env
   TZ=Europe/London
   AGENT_TOKEN=your-random-bootstrap-token
   # REGISTRATION_TOKEN=optional-pairing-firewall
   # DATABASE_URL=postgresql://user:password@host:5432/dbname
   # FIREBASE_CREDENTIALS_JSON=path/to/service-account.json
   ```

3. Start the stack:

   ```bash
   docker-compose up -d --build
   ```

   This runs the Flask **web** service and the **tasks** background worker.

4. Open the dashboard and log in (`admin` / `admin` by default). Change the password under **Settings** (minimum **12 characters**).

## Manual deployment

From the `server/` directory:

```bash
pip install -r requirements.txt
export AGENT_TOKEN=... TZ=UTC TIMEKPR_SERVER_VERSION=v0.0.0-dev
python app.py          # terminal 1 — web UI (also starts in-process tasks when run directly)
python task_worker.py  # terminal 2 — recommended separate worker in production
```

Use Gunicorn or similar for production WSGI; keep the worker in a **separate process** so long-running sync jobs do not block web workers.

## Reverse proxy and TLS

Point agents at a public **`wss://`** URL. Set `TIMEKPR_AGENT_WS_URL` so pairing QR codes embed the correct WebSocket endpoint behind nginx, Caddy, or Traefik.

See [Security](security.md) for TLS and token guidance.

## PostgreSQL

Guardian supports SQLite (default) and PostgreSQL via `DATABASE_URL`. On startup, the server can migrate an existing SQLite file to PostgreSQL in chunked batches when configured.

## Related

- [Configuration reference](configuration.md)
- [Background worker](../reference/background-worker.md)
