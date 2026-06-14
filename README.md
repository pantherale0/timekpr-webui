# Guardian parental controls

[![Documentation](https://img.shields.io/badge/docs-GitHub%20Pages-blue)](https://pantherale0.github.io/timekpr-webui/)

Guardian is a cross-platform parental control system with a secure **server–agent architecture**. A Flask server hosts the Web UI, REST APIs, and WebSocket hub. Managed devices run outbound client agents (Rust on Linux and Windows, Kotlin on Android). **Nintendo Switch** and **Xbox** consoles integrate via cloud parental-control APIs—no on-console agent required.

## Documentation

**Full documentation:** [https://pantherale0.github.io/timekpr-webui/](https://pantherale0.github.io/timekpr-webui/)

| Topic | Guide |
|-------|--------|
| Deploy the server | [Server deployment](https://pantherale0.github.io/timekpr-webui/getting-started/server-deployment/) |
| Compare vs Family Link, Bark, etc. | [vs commercial parental controls](https://pantherale0.github.io/timekpr-webui/getting-started/comparison/) |
| Linux agent | [Linux agent](https://pantherale0.github.io/timekpr-webui/platforms/linux-agent/) |
| Android agent | [Android agent](https://pantherale0.github.io/timekpr-webui/platforms/android-agent/) |
| Windows agent | [Windows agent](https://pantherale0.github.io/timekpr-webui/platforms/windows-agent/) |
| Nintendo / Xbox | [Cloud consoles](https://pantherale0.github.io/timekpr-webui/workflows/cloud-console-setup/) |
| Troubleshooting | [Troubleshooting](https://pantherale0.github.io/timekpr-webui/troubleshooting/) |

Build docs locally:

```bash
pip install -r requirements-docs.txt
mkdocs serve
```

## Quick start (Docker)

```bash
git clone https://github.com/pantherale0/timekpr-webui.git
cd timekpr-webui
cp .env.example .env   # set AGENT_TOKEN and TZ
docker-compose up -d --build
```

Sign in at the dashboard with **admin** / **admin** and change the password under **Settings**.

## Install Linux agent

```bash
curl -fsSLo /tmp/install-timekpr-agent.sh \
  https://raw.githubusercontent.com/pantherale0/timekpr-webui/master/scripts/install-agent.sh
chmod 0755 /tmp/install-timekpr-agent.sh
sudo /tmp/install-timekpr-agent.sh --server-url "wss://your-domain.com/ws"
```

Approve the device under **Admin → Devices**.

## Repository layout

| Path | Description |
|------|-------------|
| `server/` | Flask web app, worker, tests |
| `agent/` | Rust Linux/Windows agent |
| `android-agent/` | Kotlin Android agent |
| `docs/` | MkDocs documentation source |
| `scripts/` | Install and release helpers |

## Contributing

See [Contributing](https://pantherale0.github.io/timekpr-webui/development/contributing/) and [AGENTS.md](AGENTS.md) for developer conventions.

## License

MIT — see [LICENSE](LICENSE).
