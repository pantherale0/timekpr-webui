#!/usr/bin/env bash
set -euo pipefail

REPO="pantherale0/timekpr-webui"
INSTALL_DIR="/usr/local/bin"
CONFIG_DIR="/etc/timekpr-agent"
CONFIG_PATH="${CONFIG_DIR}/config.json"
SERVICE_NAME="timekpr-agent.service"
SERVICE_PATH="/etc/systemd/system/${SERVICE_NAME}"
RELEASE_TAG=""
SERVER_URL="${TIMEKPR_SERVER_URL:-}"
AGENT_TOKEN="${TIMEKPR_AGENT_TOKEN:-}"
REGISTRATION_TOKEN="${TIMEKPR_REGISTRATION_TOKEN:-}"
AGENT_TOKEN_FILE=""
REGISTRATION_TOKEN_FILE=""
DOWNLOAD_ONLY=0
NO_START=0
REPLACE_AGENT_TOKEN=0
SECURITY_STACK_REBOOT_REQUIRED=0
SECURITY_STACK_KERNEL_BUG_DETECTED=0

usage() {
    cat <<'EOF'
Install or update the TimeKpr agent from the latest GitHub release.

Usage:
  install-agent.sh [options]

Options:
  --server-url URL                 WebSocket URL for the server, preferably wss://.../ws
  --agent-token TOKEN             Initial bootstrap token (matches server AGENT_TOKEN)
  --agent-token-file PATH         Read the bootstrap token from a file
  --registration-token TOKEN      Optional pairing firewall token
  --registration-token-file PATH  Read the pairing firewall token from a file
  --repo OWNER/REPO               GitHub repository to download from
  --tag TAG                       Install a specific release tag instead of the latest release
  --install-dir PATH              Directory for the agent binary
  --config-dir PATH               Directory for the agent config
  --replace-agent-token           Overwrite an existing config token
  --download-only                 Download and install the binary, but do not write config or service files
  --no-start                      Install and enable the service, but do not start/restart it
  --help                          Show this help message

Environment:
  TIMEKPR_SERVER_URL
  TIMEKPR_AGENT_TOKEN
  TIMEKPR_REGISTRATION_TOKEN

Notes:
  - On first install, the script prompts for missing secrets if they were not supplied.
  - On upgrades, an existing config token is preserved by default so you do not accidentally
    replace the per-device secret minted after pairing.
  - On full installs, the script attempts to install and enable AppArmor plus auditd so
    application monitoring works with minimal manual setup.
  - The script expects release assets named:
      timekpr-agent-x86_64-unknown-linux-gnu.tar.gz
      timekpr-agent-aarch64-unknown-linux-gnu.tar.gz
EOF
}

log() {
    printf '==> %s\n' "$*"
}

warn() {
    printf 'Warning: %s\n' "$*" >&2
}

die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

has_cmd() {
    command -v "$1" >/dev/null 2>&1
}

prompt() {
    local message="$1"
    local value
    read -r -p "$message" value
    printf '%s' "$value"
}

prompt_secret() {
    local message="$1"
    local value
    read -r -s -p "$message" value
    printf '\n' >&2
    printf '%s' "$value"
}

read_secret_file() {
    local path="$1"
    [[ -f "$path" ]] || die "Secret file not found: $path"
    python3 - "$path" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).read_text(encoding="utf-8").strip())
PY
}

detect_package_manager() {
    if has_cmd apt-get; then
        printf 'apt-get'
    elif has_cmd pacman; then
        printf 'pacman'
    elif has_cmd dnf; then
        printf 'dnf'
    elif has_cmd zypper; then
        printf 'zypper'
    else
        return 1
    fi
}

install_security_stack_packages() {
    local package_manager
    package_manager="$(detect_package_manager)" || die \
        "Could not detect a supported package manager for installing AppArmor/auditd"

    log "Installing AppArmor and auditd dependencies via ${package_manager}"
    case "$package_manager" in
        apt-get)
            DEBIAN_FRONTEND=noninteractive apt-get update
            DEBIAN_FRONTEND=noninteractive apt-get install -y \
                apparmor apparmor-utils auditd
            ;;
        pacman)
            pacman -Sy --noconfirm --needed apparmor audit
            ;;
        dnf)
            dnf install -y apparmor apparmor-utils audit
            ;;
        zypper)
            zypper --non-interactive install --no-confirm \
                apparmor-parser apparmor-utils audit
            ;;
        *)
            die "Unsupported package manager: ${package_manager}"
            ;;
    esac
}

systemd_unit_exists() {
    local unit_name="$1"
    local units
    units="$(systemctl list-unit-files "$unit_name" --no-legend 2>/dev/null || true)"
    [[ -n "$units" ]]
}

enable_service_now_if_possible() {
    local unit_name="$1"
    if ! systemd_unit_exists "$unit_name"; then
        warn "Systemd unit ${unit_name} was not found after package installation"
        return 1
    fi

    if [[ "$NO_START" -eq 1 ]]; then
        log "Enabling ${unit_name} (service start deferred by --no-start)"
        systemctl enable "$unit_name"
    else
        log "Enabling and starting ${unit_name}"
        systemctl enable --now "$unit_name"
    fi
}

apparmor_runtime_enabled() {
    [[ -r /sys/module/apparmor/parameters/enabled ]] || return 1
    [[ "$(< /sys/module/apparmor/parameters/enabled)" == "Y" ]]
}

ensure_security_stack() {
    install_security_stack_packages

    has_cmd apparmor_parser || die "AppArmor parser was not installed successfully"
    has_cmd aa-status || warn "aa-status is not available; AppArmor diagnostics will be limited"

    enable_service_now_if_possible apparmor.service || true
    enable_service_now_if_possible auditd.service || true

    if apparmor_runtime_enabled; then
        log "Verified that AppArmor is active in the running kernel"
    else
        SECURITY_STACK_REBOOT_REQUIRED=1
        warn "AppArmor is installed but not active in the running kernel."
        warn "Protections will not apply until the host boots with AppArmor enabled."
        if [[ -r /proc/cmdline ]]; then
            warn "Current kernel cmdline: $(< /proc/cmdline)"
        fi
        warn "Ensure your bootloader enables AppArmor (for example apparmor=1 and lsm includes apparmor), then reboot."
    fi

    if journalctl -k --no-pager -n 200 2>/dev/null | rg -q 'audit_log_(subj|object)_ctx'; then
        SECURITY_STACK_KERNEL_BUG_DETECTED=1
        warn "Detected kernel audit/AppArmor context logging errors in the kernel log."
        warn "This is usually a kernel bug, not an agent configuration problem."
        warn "Application monitoring may be noisy or unreliable until the kernel is updated."
    fi
}

github_api_get() {
    local url="$1"
    local output_path="$2"
    local -a curl_args=(
        -fsSL
        -H "Accept: application/vnd.github+json"
        -H "X-GitHub-Api-Version: 2022-11-28"
    )

    if [[ -n "${GITHUB_TOKEN:-}" ]]; then
        curl_args+=(-H "Authorization: Bearer ${GITHUB_TOKEN}")
    fi

    curl "${curl_args[@]}" "$url" -o "$output_path"
}

detect_target() {
    case "$(uname -m)" in
        x86_64|amd64)
            printf 'x86_64-unknown-linux-gnu'
            ;;
        aarch64|arm64)
            printf 'aarch64-unknown-linux-gnu'
            ;;
        *)
            die "Unsupported architecture: $(uname -m)"
            ;;
    esac
}

get_existing_config_value() {
    local key="$1"
    python3 - "$CONFIG_PATH" "$key" <<'PY'
import json
import os
import sys

path, key = sys.argv[1], sys.argv[2]
if not os.path.exists(path):
    raise SystemExit(0)

with open(path, encoding="utf-8") as handle:
    data = json.load(handle)

value = data.get(key)
if value is None:
    raise SystemExit(0)
print(value)
PY
}

write_config() {
    local server_url="$1"
    local agent_token="$2"
    local registration_token="$3"

    install -d -m 0700 -o root -g root "$CONFIG_DIR"

    python3 - "$CONFIG_PATH" "$server_url" "$agent_token" "$registration_token" <<'PY'
import json
import os
import sys

path, server_url, agent_token, registration_token = sys.argv[1:5]
data = {}

if os.path.exists(path):
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

data["server_url"] = server_url
data.setdefault("system_id", None)
data["agent_token"] = agent_token
data["registration_token"] = registration_token or None

with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY

    chown root:root "$CONFIG_PATH"
    chmod 0600 "$CONFIG_PATH"
}

write_service() {
    cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=Timekpr WebSocket Client Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Group=root
UMask=0077
ExecStart=${INSTALL_DIR}/timekpr-agent
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

    chmod 0644 "$SERVICE_PATH"
}

if [[ ${EUID} -ne 0 ]]; then
    need_cmd sudo
    exec sudo --preserve-env=TIMEKPR_SERVER_URL,TIMEKPR_AGENT_TOKEN,TIMEKPR_REGISTRATION_TOKEN,GITHUB_TOKEN bash "$0" "$@"
fi

while [[ $# -gt 0 ]]; do
    case "$1" in
        --server-url)
            [[ $# -ge 2 ]] || die "Missing value for --server-url"
            SERVER_URL="$2"
            shift 2
            ;;
        --agent-token)
            [[ $# -ge 2 ]] || die "Missing value for --agent-token"
            AGENT_TOKEN="$2"
            shift 2
            ;;
        --agent-token-file)
            [[ $# -ge 2 ]] || die "Missing value for --agent-token-file"
            AGENT_TOKEN_FILE="$2"
            shift 2
            ;;
        --registration-token)
            [[ $# -ge 2 ]] || die "Missing value for --registration-token"
            REGISTRATION_TOKEN="$2"
            shift 2
            ;;
        --registration-token-file)
            [[ $# -ge 2 ]] || die "Missing value for --registration-token-file"
            REGISTRATION_TOKEN_FILE="$2"
            shift 2
            ;;
        --repo)
            [[ $# -ge 2 ]] || die "Missing value for --repo"
            REPO="$2"
            shift 2
            ;;
        --tag)
            [[ $# -ge 2 ]] || die "Missing value for --tag"
            RELEASE_TAG="$2"
            shift 2
            ;;
        --install-dir)
            [[ $# -ge 2 ]] || die "Missing value for --install-dir"
            INSTALL_DIR="$2"
            shift 2
            ;;
        --config-dir)
            [[ $# -ge 2 ]] || die "Missing value for --config-dir"
            CONFIG_DIR="$2"
            CONFIG_PATH="${CONFIG_DIR}/config.json"
            shift 2
            ;;
        --replace-agent-token)
            REPLACE_AGENT_TOKEN=1
            shift
            ;;
        --download-only)
            DOWNLOAD_ONLY=1
            shift
            ;;
        --no-start)
            NO_START=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

need_cmd bash
need_cmd curl
need_cmd install
need_cmd python3
need_cmd tar
need_cmd uname

if [[ -n "$AGENT_TOKEN_FILE" ]]; then
    AGENT_TOKEN="$(read_secret_file "$AGENT_TOKEN_FILE")"
fi

if [[ -n "$REGISTRATION_TOKEN_FILE" ]]; then
    REGISTRATION_TOKEN="$(read_secret_file "$REGISTRATION_TOKEN_FILE")"
fi

TARGET="$(detect_target)"
ASSET_NAME="timekpr-agent-${TARGET}.tar.gz"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT
RELEASE_JSON="${TMP_DIR}/release.json"

if [[ -n "$RELEASE_TAG" ]]; then
    RELEASE_API_URL="https://api.github.com/repos/${REPO}/releases/tags/${RELEASE_TAG}"
else
    RELEASE_API_URL="https://api.github.com/repos/${REPO}/releases/latest"
fi

log "Resolving release metadata from ${REPO}"
if ! github_api_get "$RELEASE_API_URL" "$RELEASE_JSON"; then
    if [[ -n "$RELEASE_TAG" ]]; then
        die "Could not fetch release tag ${RELEASE_TAG} from ${REPO}"
    fi
    die "Could not fetch the latest release from ${REPO}. Publish a tagged GitHub release or use the manual build flow."
fi

RELEASE_TAG_RESOLVED="$(python3 - "$RELEASE_JSON" <<'PY'
import json
import sys
with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)
print(data.get("tag_name", ""))
PY
)"

DOWNLOAD_URL="$(python3 - "$RELEASE_JSON" "$ASSET_NAME" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    data = json.load(handle)

asset_name = sys.argv[2]
for asset in data.get("assets", []):
    if asset.get("name") == asset_name:
        print(asset.get("browser_download_url", ""))
        break
PY
)"

[[ -n "$RELEASE_TAG_RESOLVED" ]] || die "GitHub release metadata did not contain a tag name"
[[ -n "$DOWNLOAD_URL" ]] || die "Release ${RELEASE_TAG_RESOLVED} does not contain asset ${ASSET_NAME}"

ARCHIVE_PATH="${TMP_DIR}/${ASSET_NAME}"
EXTRACT_DIR="${TMP_DIR}/extract"
mkdir -p "$EXTRACT_DIR"

log "Downloading ${ASSET_NAME} from release ${RELEASE_TAG_RESOLVED}"
curl -fsSL "$DOWNLOAD_URL" -o "$ARCHIVE_PATH"

log "Extracting release archive"
tar -xzf "$ARCHIVE_PATH" -C "$EXTRACT_DIR"
[[ -f "${EXTRACT_DIR}/timekpr-agent" ]] || die "Archive did not contain the timekpr-agent binary"

install -d -m 0755 "$INSTALL_DIR"
install -m 0755 "${EXTRACT_DIR}/timekpr-agent" "${INSTALL_DIR}/timekpr-agent"
log "Installed binary to ${INSTALL_DIR}/timekpr-agent"

if [[ "$DOWNLOAD_ONLY" -eq 1 ]]; then
    log "Download-only mode requested; skipping config and systemd service setup"
    exit 0
fi

need_cmd systemctl

ensure_security_stack

EXISTING_SERVER_URL="$(get_existing_config_value "server_url" || true)"
EXISTING_AGENT_TOKEN="$(get_existing_config_value "agent_token" || true)"
EXISTING_REGISTRATION_TOKEN="$(get_existing_config_value "registration_token" || true)"

if [[ -z "$SERVER_URL" && -n "$EXISTING_SERVER_URL" ]]; then
    SERVER_URL="$EXISTING_SERVER_URL"
fi

if [[ -z "$SERVER_URL" ]]; then
    SERVER_URL="$(prompt 'Server WebSocket URL (prefer wss://host/ws): ')"
fi

if [[ -z "$EXISTING_AGENT_TOKEN" ]]; then
    if [[ -z "$AGENT_TOKEN" ]]; then
        AGENT_TOKEN="$(prompt_secret 'Initial agent token (matches server AGENT_TOKEN): ')"
    fi
elif [[ -n "$AGENT_TOKEN" && "$AGENT_TOKEN" != "$EXISTING_AGENT_TOKEN" && "$REPLACE_AGENT_TOKEN" -ne 1 ]]; then
    warn "Preserving the existing agent token in ${CONFIG_PATH}. Use --replace-agent-token to overwrite it."
    AGENT_TOKEN="$EXISTING_AGENT_TOKEN"
elif [[ -z "$AGENT_TOKEN" ]]; then
    AGENT_TOKEN="$EXISTING_AGENT_TOKEN"
fi

if [[ -z "$AGENT_TOKEN" ]]; then
    die "Agent token is required on first install"
fi

if [[ -z "$REGISTRATION_TOKEN" && -n "$EXISTING_REGISTRATION_TOKEN" ]]; then
    REGISTRATION_TOKEN="$EXISTING_REGISTRATION_TOKEN"
fi

log "Writing ${CONFIG_PATH} with root-only permissions"
write_config "$SERVER_URL" "$AGENT_TOKEN" "$REGISTRATION_TOKEN"

log "Writing systemd unit to ${SERVICE_PATH}"
write_service

log "Reloading systemd and enabling ${SERVICE_NAME}"
systemctl daemon-reload
systemctl enable "$SERVICE_NAME"

if [[ "$NO_START" -eq 1 ]]; then
    log "Skipping service start because --no-start was requested"
else
    log "Restarting ${SERVICE_NAME}"
    systemctl restart "$SERVICE_NAME"
fi

cat <<EOF

Installed TimeKpr agent release ${RELEASE_TAG_RESOLVED}.

Next steps:
  - Review ${CONFIG_PATH} permissions: ls -l ${CONFIG_PATH}
  - Check service status: systemctl status ${SERVICE_NAME}
  - Read recent logs: journalctl -u ${SERVICE_NAME} -n 50 --no-pager

If this is the first time the agent has run, the logs will show the generated system ID
that must be approved in the Web UI admin panel.
EOF

if [[ "$SECURITY_STACK_REBOOT_REQUIRED" -eq 1 ]]; then
    cat <<'EOF'

Important:
  - AppArmor was installed, but the running kernel does not currently have it active.
  - Reboot after enabling AppArmor in the bootloader/kernel command line, then re-run:
      aa-status
      systemctl status apparmor auditd timekpr-agent
EOF
fi

if [[ "$SECURITY_STACK_KERNEL_BUG_DETECTED" -eq 1 ]]; then
    cat <<'EOF'

Important:
  - The running kernel is logging audit/AppArmor context errors such as:
      audit: error in audit_log_subj_ctx
  - This is typically a kernel bug in audit/LSM context handling, not a bad TimeKpr install.
  - Recommended action:
      1. Update the host to a newer kernel build.
      2. Reboot.
      3. Re-check: journalctl -k | rg 'audit_log_(subj|object)_ctx'
EOF
fi
