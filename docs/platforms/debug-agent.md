# Debug agent (Python simulator)

The debug agent (`server/debug_agent.py`) simulates a Linux agent for local development—WebSocket handshake, policy commands, alerts, and validation—without installing the Rust binary.

## Usage

```bash
cd server
pip install -r requirements.txt
python debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "v0.0.0-dev"
```

Match `--agent-version` to the server's `TIMEKPR_SERVER_VERSION`.

## CLI options

| Option | Description |
|--------|-------------|
| `--strict-users` | Reject validation for users not listed in `debug-agent.json` |
| `--emit-startup-alert` | Send synthetic alerts immediately after handshake |
| `--activity-interval SECS` | Periodic mock traffic; `0` disables background events |

## State file

Persists to `server/debug-agent.json`. Delete it to simulate a fresh client and new pending device.

## Approval

Approve pending debug devices at **Admin → Devices** like any other agent.

## When to use

- UI and API development without physical devices
- pytest-style manual testing of schedules, blocklists, and alerts
- WebSocket protocol debugging alongside [WebSocket protocol](../reference/websocket-protocol.md)

Not suitable for production enforcement testing—behavior differs from Rust/Android agents.

## Related

- [Local development](../development/local-dev.md)
- [Contributing](../development/contributing.md)
