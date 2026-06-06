#!/usr/bin/env bash
set -euo pipefail

# SHA-256 of empty input; must never be treated as a valid signing certificate checksum.
EMPTY_CHECKSUM='47DEQpj8HBSa-_TImW-5JCeuQeRkm5NMpJWZG3hSuFU'

apk="${1:-}"
if [[ -z "$apk" || ! -f "$apk" ]]; then
    cat >&2 <<'EOF'
Compute the URL-safe base64 SHA-256 checksum of an APK signing certificate.

Usage:
  android-signature-checksum.sh /path/to/app.apk

The output matches android.app.extra.PROVISIONING_DEVICE_ADMIN_SIGNATURE_CHECKSUM
used by Android Enterprise QR provisioning.

Requires apksigner (Android SDK build-tools) or keytool for legacy v1-signed APKs.
EOF
    exit 1
fi

need_cmd() {
    command -v "$1" >/dev/null 2>&1 || {
        printf 'Required command not found: %s\n' "$1" >&2
        exit 1
    }
}

need_cmd python3
need_cmd openssl

find_apksigner() {
    local candidate roots=()
    if [[ -n "${ANDROID_HOME:-}" ]]; then
        roots+=("${ANDROID_HOME}/build-tools")
    fi
    if [[ -n "${ANDROID_SDK_ROOT:-}" ]]; then
        roots+=("${ANDROID_SDK_ROOT}/build-tools")
    fi
    roots+=("${HOME}/Android/Sdk/build-tools")

    local root
    for root in "${roots[@]}"; do
        [[ -d "$root" ]] || continue
        find "$root" -maxdepth 2 -type f -name apksigner 2>/dev/null \
            | sort -V \
            | tail -n 1
    done | tail -n 1
}

checksum_from_apksigner() {
    local apksigner_bin="$1"
    python3 - "$apksigner_bin" "$apk" <<'PY'
import base64
import binascii
import subprocess
import sys

apksigner, apk_path = sys.argv[1], sys.argv[2]
result = subprocess.run(
    [apksigner, 'verify', '--print-certs', apk_path],
    capture_output=True,
    text=True,
    check=False,
)
output = '\n'.join(part for part in (result.stdout, result.stderr) if part).strip()
if result.returncode != 0 and 'certificate SHA-256 digest:' not in output:
    sys.exit(1)

hex_digest = ''
marker = 'certificate SHA-256 digest:'
for line in output.splitlines():
    if marker in line:
        hex_digest = line.split(marker, 1)[1].strip().replace(':', '')
        break
if len(hex_digest) != 64:
    sys.exit(1)

print(base64.urlsafe_b64encode(binascii.unhexlify(hex_digest)).decode().rstrip('='))
PY
}

checksum_from_keytool() {
    need_cmd keytool
    local pem

    pem="$(keytool -printcert -jarfile "$apk" -rfc 2>/dev/null)" || return 1
    if [[ "$pem" != *"BEGIN CERTIFICATE"* ]]; then
        return 1
    fi
    printf '%s' "$pem" \
        | openssl x509 -inform pem -outform der \
        | openssl dgst -sha256 -binary \
        | openssl base64 \
        | tr '+/' '-_' | tr -d '=\n'
}

checksum=""
apksigner_bin="$(find_apksigner || true)"
verify_output=""
if [[ -n "$apksigner_bin" ]]; then
    checksum="$(checksum_from_apksigner "$apksigner_bin" || true)"
    if [[ -z "$checksum" ]]; then
        verify_output="$("$apksigner_bin" verify --print-certs "$apk" 2>&1 || true)"
    fi
fi

if [[ -z "$checksum" ]]; then
    checksum="$(checksum_from_keytool || true)"
fi

if [[ -z "$checksum" || "$checksum" == "$EMPTY_CHECKSUM" ]]; then
    checksum=""
fi

if [[ -z "$checksum" ]]; then
    cat >&2 <<EOF
Failed to compute checksum for ${apk}.

Modern APKs (v2/v3 signing) require apksigner from the Android SDK build-tools.
Install build-tools and set ANDROID_HOME or ANDROID_SDK_ROOT, or ensure
~/Android/Sdk/build-tools is present.

Example:
  export ANDROID_HOME=\$HOME/Android/Sdk
  ${0} ${apk}
EOF
    if [[ -n "$apksigner_bin" ]]; then
        printf '\napksigner: %s\n' "$apksigner_bin" >&2
        if [[ -n "$verify_output" ]]; then
            printf 'apksigner verify output:\n%s\n' "$verify_output" >&2
        fi
    else
        printf '\napksigner was not found under ANDROID_HOME/ANDROID_SDK_ROOT.\n' >&2
    fi
    exit 1
fi

printf '%s\n' "$checksum"
