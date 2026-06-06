# TimeKpr WebUI

A unified, secure, and modern parental controls dashboard for managing screen time, application execution, and domain policies across Linux and Android devices.

This project implements a secure **server-agent architecture**. The Flask server hosts the Web UI, REST APIs, and a WebSocket hub, while each managed device runs a client agent (a compiled Rust agent for Linux, a Kotlin agent for Android, or a Python simulator for debugging) that connects outbound to the server. This outbound-only architecture eliminates the need to expose inbound management ports on child devices.

---

## Architecture Overview

```mermaid
graph TD
    Parent[Parent / Administrator] -->|1. Configure Policies / Approve| WebUI[Flask Web UI]
    subgraph Server Stack
        WebUI <--> DB[(SQLite / PostgreSQL)]
        Worker[Background Task Worker] <--> DB
    end
    subgraph Linux Client
        RustAgent[Rust Agent] <-->|2. WS Connection /ws| WebUI
        RustAgent -->|Enforces| AppArmor[AppArmor / SIGKILL]
        RustAgent -->|Enforces| Polkit[Polkit / Seat0 Session]
        RustAgent -->|Enforces| LinuxDNS[Local DNS Sinkhole]
    end
    subgraph Android Client
        KotlinAgent[Kotlin Agent] <-->|3. Ephemeral WS Connection| WebUI
        Firebase[Firebase Cloud Messaging FCM] -.->|4. Push Wake Message| KotlinAgent
        WebUI -.->|Trigger Wake| Firebase
        KotlinAgent -->|Enforces| DeviceOwner[Device Owner / Package Suspensions]
        KotlinAgent -->|Enforces| AndroidVPN[DNS VPN Service]
    end
```

### Key System Features

*   **Outbound-Only Connections**: Client agents connect outbound to the server WebSocket endpoint (`/ws`), bypassing firewall constraints on client machines or mobile networks.
*   **HMAC Challenge-Response Authentication**: Enrolled agents authenticate by signing a server-issued challenge with HMAC-SHA256 using a unique per-device secret. The shared bootstrap token is never sent over the wire.
*   **Pending-Device Approval Flow**: Newly registered agents enter a `pending` state. An administrator must approve them in the Web UI before any policies are issued.
*   **Offline-Safe Command Queueing**: Configuration changes and remote commands are queued on the server and applied automatically when the agent next connects.
*   **Deduplicated App Discovery & Inventory**: Agents scan installed applications (launcher-visible packages on Android, desktop entries on Linux) and push metadata along with content-addressed 64x64 PNG icons, which are used to configure app policies in the UI.

---

## Platform Features

### 🐧 Linux Rust Agent (`agent/`)
*   **D-Bus Enforced Controls**: Maps time management actions onto the upstream TimeKpr-nExT D-Bus API.
*   **Process Restricting**: Uses netlink process monitoring and SIGKILL to enforce allowed and blocked executable lists.
*   **Seat0 Active Session Reconciliation**: Restricts polkit permissions (software installs, mount removal media, account modifications, system power commands) and blocks terminal access *only* for the user signed into the active desktop session (logind seat0).
*   **Local DNS Sinkhole**: Blocks domain access at the DNS level for configured users, showing desktop notifications on block.
*   **Cryptographically Signed Auto-Updates**: Downloads update binaries and verifies them against an embedded Minisign public key before upgrading.

### 🤖 Android Kotlin Agent (`android-agent/`)
*   **Battery-Efficient Connectivity**: Integrates with Firebase Cloud Messaging (FCM). Instead of maintaining a persistent WebSocket, the server sends FCM wake messages when policy changes are made, prompting the agent to start a brief WebSocket sync session.
*   **Screen Lockout Overlay**: Displays a persistent overlay banner (`TimeExhaustedOverlay`) when daily limits are exhausted, while allowing phone calls to go through if an active SIM is present.
*   **Package Suspension**: Suspends non-exempt apps via Device Owner `setPackagesSuspended` during lockout or app restriction windows.
*   **DNS Filtering VPN**: Runs a local `VpnService` that intercepts DNS queries and blocks blacklisted domains, offering "Request access" alerts.
*   **AMAPI-Aligned Restrictions**: Restricts system options (disable camera, microphone, screen capture, USB data transfer, bluetooth, app installs/uninstalls, and developer settings) and applies custom parent messages.
*   **Remote Factory Reset**: Supports remote data wiping (`wipeData`) from the parent dashboard when provisioned as Device Owner.

### 🛠️ Server & Database Engine (`server/`)
*   **Multi-Database Support**: Fully compatible with SQLite and PostgreSQL out-of-the-box.
*   **Zero-Downtime Migration Helper**: Auto-detects local SQLite files on boot, migrates all tables to PostgreSQL in memory-efficient chunked batches (10,000 rows, OOM-safe) in topological order, updates Alembic status, and cleans up old database files.
*   **Database Schema Versioning**: Powered by Flask-Migrate & Alembic for programmatic database upgrades on app launch.
*   **Timezone-Aware Consistency**: Fully timezone-aware date and time tracking across all schema models (`DateTime(timezone=True)`) to support global scheduling without offset bugs.
*   **Separated Worker Threading**: A dedicated worker (`task_worker.py`) handles external blocklist syncs, domain policy updates, and FCM pushes to prevent blocking Gunicorn workers.

---

## 1. Server Deployment

### Prerequisites
*   Docker and Docker Compose.
*   (For Android Provisioning) `apksigner` in `ANDROID_HOME` or `~/Android/Sdk/build-tools` on the server host to automatically sign release APKs during dev provisioning.

### Docker Compose Run (Recommended)
1.  **Clone the Repository**:
    ```bash
    git clone https://github.com/adambie/timekpr-webui.git
    cd timekpr-webui
    ```
2.  **Configure `.env`**:
    Copy `.env.example` to `.env` and configure your settings:
    ```env
    TZ=Europe/London
    AGENT_TOKEN=your-random-bootstrap-token
    # Optional pairing firewall token
    REGISTRATION_TOKEN=your-pairing-token
    # Optional Database (defaults to SQLite if commented out)
    # DATABASE_URL=postgresql://user:password@postgres-host:5432/dbname
    # Optional FCM Integration for Android Agent
    # FIREBASE_CREDENTIALS_JSON=instance/firebase_service_account.json
    ```
3.  **Start the Stack**:
    ```bash
    docker-compose up -d --build
    ```
    This launches the Flask application (`web`) and the background task worker (`tasks`).
4.  **Log In**:
    Navigate to the dashboard and log in with default credentials. Change the password immediately:
    *   **Username**: `admin`
    *   **Password**: `admin`

---

## 2. Client Agent Deployment

### Option A: Linux Agent (Automatic script)
We provide an interactive script `install-agent.sh` that detects your architecture, downloads the release, writes the root config, and registers the systemd service.

```bash
curl -fsSLo /tmp/install-timekpr-agent.sh \
  https://raw.githubusercontent.com/pantherale0/timekpr-webui/master/scripts/install-agent.sh
chmod 0755 /tmp/install-timekpr-agent.sh

# Run the installer
sudo /tmp/install-timekpr-agent.sh --server-url "wss://your-domain.com/ws"
```
#### Options:
*   `--server-url`: Public URL of your server's WebSocket endpoint.
*   `--agent-token-file`: Path to a file containing the bootstrap `AGENT_TOKEN`.
*   `--registration-token-file`: Path to a file containing the `REGISTRATION_TOKEN`.
*   `--replace-agent-token`: Re-writes the per-device secret (for repair/re-enrolling).

### Option B: Android Agent (Pairing & Provisioning)

#### 1. In-App QR Code Pairing
Use this flow if the agent application is already installed on the target device:
1.  Navigate to **Settings → Agent pairing** in the Web UI.
2.  Open the TimeKpr agent app on the phone, choose **Scan server QR code**, and scan the displayed QR code.
3.  Approve the device in **Admin → Devices**. The agent receives a per-device token and establishes secure communications.

#### 2. Android MDM Provisioning QR (Zero-Touch Device Owner)
To block app uninstalls and lock down settings completely, the agent must be set as the **Device Owner**. On a factory-reset device:
1.  Navigate to **Settings → Agent pairing → Android MDM provisioning QR** on the server.
2.  Tap the welcome screen of the factory-reset device 6 times to activate the QR scanner.
3.  Scan the displayed MDM QR code.
4.  The device downloads the APK, sets it as Device Owner, applies the server WebSocket URL, and opens a pending connection. Approve the device under **Admin → Devices**.

*For local testing without release assets*, compile the release APK locally and upload it on the MDM QR page:
```bash
cd android-agent
./gradlew assembleRelease
```

---

### Option C: Python Debug Agent (Simulator)
For developers testing policies, schedule synchronizations, or alerts without physical devices:

1.  Navigate to the `server/` directory:
    ```bash
    cd server
    pip install -r requirements.txt
    ```
2.  Start the debug agent:
    ```bash
    python debug_agent.py --server-url "ws://127.0.0.1:5000/ws" --agent-version "v0.0.0-dev"
    ```
#### Debug Agent CLI Options:
*   `--strict-users`: Rejects validation requests for user profiles not configured in the local `debug-agent.json`.
*   `--emit-startup-alert`: Sends synthetic alerts immediately upon successful handshake.
*   `--activity-interval <seconds>`: Sends a mock alert or policy check on a set interval (default: random). Use `0` to disable background traffic.

---

## Policy and Restriction Matrix

The Web UI manages policies that map differently onto each OS layer:

| Policy Area | Linux Agent (Rust) | Android Agent (Kotlin) |
|---|---|---|
| **Daily Limits & Schedule** | Enforced via TimeKpr-nExT system D-Bus. | Enforced via local `UsageMonitorService` tracking screen state. |
| **Lockout Action** | Handled by TimeKpr-nExT logind session locking. | Displays fullscreen `TimeExhaustedOverlay`; suspends all non-exempt packages. |
| **App Execution** | Netlink process monitor sends `SIGKILL` to blocked binaries. | Device Owner suspends packages via `setPackagesSuspended()`. |
| **App Approval Mode** | Kills unapproved paths; requests logged in the Web UI. | Launches Request Access overlays; blocks startup of unapproved packages. |
| **Domain Blocklists** | Local DNS resolution sinkhole per user. | Runs a local loopback `VpnService` with custom DNS filtering. |
| **Hardware Restrictions** | rfkill blocks Bluetooth; polkit rules block removable media. | Disables Camera, Microphone, Bluetooth, and USB data transfer. |
| **System Restrictions** | polkit restricts package managers (apt, snap, flatpak). | Blocks app installation/uninstallation and developer settings. |

---

## Configuration Variables

Configure these variables inside your docker compose environment or export them in your terminal session:

| Variable | Description | Default / Requirement |
|---|---|---|
| `AGENT_TOKEN` | Server bootstrap token. Used to establish the first pairing handshake. | **Required** |
| `REGISTRATION_TOKEN` | Optional firewall token. Restricts pairing requests to authorized agents. | Optional |
| `DATABASE_URL` | SQLAlchemy URI (e.g. `postgresql://...` or `sqlite:///timekpr.db`). | SQLite file fallback |
| `TZ` | System timezone (e.g. `Europe/London`). | UTC |
| `TIMEKPR_SERVER_VERSION` | Version indicator for server-agent handshake validations. | `v0.0.0-dev` |
| `TIMEKPR_AGENT_WS_URL` | Public WebSockets endpoint URL shown in pairing QR codes. | Auto-resolved |
| `FCM_SERVER_KEY` | Firebase legacy HTTP API server key. | Optional |
| `FIREBASE_CREDENTIALS_JSON` | Path or JSON string for Firebase HTTP v1 service accounts. | Optional |

---

## Security Recommendations

1.  **Always Terminate TLS**: Handshakes avoid plain-text token exchanges, but payloads (such as commands or configuration updates) require transport security. Point agents to a `wss://` endpoint backed by a secure reverse proxy (e.g. Nginx, Caddy, Traefik).
2.  **Lock Config Permissions**: Keep the `/etc/timekpr-agent/config.json` file on client machines owned by `root:root` with permissions set to `0600`.
3.  **Use a Registration Token**: Enabling `REGISTRATION_TOKEN` prevents rogue devices on the network from generating spam pending approval requests on the dashboard.
4.  **Understand the Token Lifecycle**: The bootstrap `AGENT_TOKEN` is only used for pairing. Once approved, the agent config updates to a per-device token. If a client configuration file is leaked, revoke and delete that device mapping in the Web UI.

---

## Troubleshooting

### Agent remains offline in dashboard
*   Check client logs:
    ```bash
    # Linux
    journalctl -u timekpr-agent.service -n 50 --no-pager
    ```
*   Verify the endpoint is accessible from the client: `curl -i http://<your-server-ip>:5000/ws`
*   Ensure that the `agent_version` of your agent matches the server's version. If running a development server (`v0.0.0-dev`), it will bypass version mismatch blocks.

### Policy adjustments do not sync (Android)
*   Ensure the background task worker (`task_worker.py`) is running.
*   If testing without FCM configured, policy updates will not be pushed instantly when the device is idle; they will sync during the WorkManager periodic cycle (every 4 hours) or when the app is manually opened.

### CI/CD Release Signing Failures
*   **Linux Agent Signing (`Missing encoded key in secret key`)**: Make sure your `TIMEKPR_AGENT_UPDATE_SIGNING_KEY` secret contains the complete multi-line content of your unencrypted Minisign secret key file (including the `untrusted comment:` header). See [linux-agent.md](docs/linux-agent.md#generating-keys-for-cicd) for key generation steps.
*   **Android Agent Signing (`unsigned APK / check keystore secrets`)**: Ensure that `ANDROID_KEYSTORE_BASE64` contains the full Base64-encoded output of your `.keystore` file, and that password/alias values match exactly. See [android-agent.md](docs/android-agent.md#generating-and-encoding-the-keystore-for-cicd) for keystore setup steps.

---

## License

This project is licensed under the MIT License. See `LICENSE` for details.
