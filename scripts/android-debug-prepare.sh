#!/usr/bin/env bash
# Build, install (replace existing), launch in wait-for-debugger mode, and forward JDWP.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="${ROOT}/android-agent"
PKG="com.guardian.agent"
ACTIVITY="${PKG}/.ui.MainActivity"
JDWP_PORT="${ANDROID_JDWP_PORT:-5035}"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found. Install Android platform-tools and ensure adb is on PATH." >&2
  exit 1
fi

DEVICE_COUNT="$(adb devices | awk 'NR>1 && $2=="device" { count++ } END { print count+0 }')"
if [[ "${DEVICE_COUNT}" -lt 1 ]]; then
  echo "No adb device connected. Plug in a device or start an emulator." >&2
  exit 1
fi

cd "${AGENT_DIR}"
echo "Building and installing debug APK (replaces existing install)..."
./gradlew installDebug

echo "Stopping any running instance..."
adb shell am force-stop "${PKG}" >/dev/null 2>&1 || true

echo "Launching ${ACTIVITY} (waiting for debugger)..."
adb shell am start -D -a android.intent.action.MAIN -c android.intent.category.LAUNCHER -n "${ACTIVITY}" >/dev/null

PID=""
for _ in $(seq 1 50); do
  PID="$(adb shell pidof -s "${PKG}" 2>/dev/null | tr -d '\r\n' || true)"
  if [[ -n "${PID}" ]]; then
    break
  fi
  sleep 0.1
done

if [[ -z "${PID}" ]]; then
  echo "Timed out waiting for ${PKG} process." >&2
  exit 1
fi

adb forward "tcp:${JDWP_PORT}" "jdwp:${PID}" >/dev/null
echo "JDWP ready on localhost:${JDWP_PORT} (pid ${PID})"
