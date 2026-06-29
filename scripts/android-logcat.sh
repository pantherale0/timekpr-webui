#!/usr/bin/env bash
# Stream logcat for the Guardian Android agent.
set -euo pipefail

PKG="com.guardian.agent"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not found. Install Android platform-tools and ensure adb is on PATH." >&2
  exit 1
fi

DEVICE_COUNT="$(adb devices | awk 'NR>1 && $2=="device" { count++ } END { print count+0 }')"
if [[ "${DEVICE_COUNT}" -lt 1 ]]; then
  echo "No adb device connected." >&2
  exit 1
fi

trim() {
  tr -d '\r\n'
}

read_pid() {
  adb shell pidof -s "${PKG}" 2>/dev/null | trim || true
}

read_uid() {
  # pm list packages -U → "package:com.guardian.agent uid:10192"
  local line
  line="$(adb shell pm list packages -U "${PKG}" 2>/dev/null | trim || true)"
  if [[ "${line}" =~ uid:([0-9]+) ]]; then
    echo "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

PID="$(read_pid)"
if [[ -n "${PID}" ]]; then
  echo "Logcat for ${PKG} (pid ${PID}). Press Ctrl+C to stop."
  exec adb logcat -v color -v time --pid="${PID}"
fi

UID="$(read_uid || true)"
if [[ -n "${UID}" ]]; then
  echo "App not running — logcat for ${PKG} (uid ${UID}). Press Ctrl+C to stop."
  # --uid requires Android 7+ on device; supported by modern adb.
  exec adb logcat -v color -v time --uid="${UID}"
fi

echo "Could not resolve pid/uid for ${PKG}; using Guardian tag filter. Press Ctrl+C to stop."
# Explicit allow-list: *:S silences everything else.
exec adb logcat -v color -v time \
  AndroidRuntime:E \
  System.err:W \
  GuardianApplication:D \
  AgentSessionCoordinator:D \
  DeviceOwnerProvisioner:D \
  SecondaryUserProvisioner:D \
  ProvisioningBootstrap:D \
  DomainBlockVpn:D \
  VpnNetworkCapture:D \
  UsageMonitorService:D \
  EnforcementController:D \
  PolicyPayloadReceiver:D \
  PolicyStorePayloadPush:D \
  PolicyIpcServer:D \
  UserSwitchedReceiver:D \
  UserUnlockedReceiver:D \
  SecondaryUserInit:D \
  OtpLockActivity:D \
  GuardianOverlayActivity:D \
  BlockedDomainOverlay:D \
  TimeExhaustedOverlay:D \
  AgentUpdater:D \
  ClockIntegrityMonitor:D \
  '*:S'
