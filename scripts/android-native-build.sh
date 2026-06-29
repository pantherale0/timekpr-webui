#!/usr/bin/env bash
# Build the Rust guardian_agent shared library for Android and refresh UniFFI Kotlin bindings.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AGENT_DIR="${ROOT}/agent"
ANDROID_DIR="${ROOT}/android-agent"
JNI_DIR="${ANDROID_DIR}/app/src/main/jniLibs/arm64-v8a"
TARGET="aarch64-linux-android"
PROFILE="${ANDROID_NATIVE_PROFILE:-debug}"
FEATURES="${ANDROID_NATIVE_FEATURES:-}"

if ! command -v cargo >/dev/null 2>&1; then
  echo "cargo not found. Install Rust from https://rustup.rs/." >&2
  exit 1
fi

if ! command -v cargo-ndk >/dev/null 2>&1; then
  echo "cargo-ndk not found. Install with: cargo binstall cargo-ndk" >&2
  exit 1
fi

# Cursor/IDE sometimes sets a rustup proxy that breaks cargo subcommands.
unset RUSTUP_TOOLCHAIN RUSTUP_PROXY || true

rustup target add "${TARGET}" >/dev/null 2>&1 || true

cd "${AGENT_DIR}"

NDK_ARGS=(--target "${TARGET}" build --lib)
if [[ "${PROFILE}" == "release" ]]; then
  NDK_ARGS+=(--release)
fi
if [[ -n "${FEATURES}" ]]; then
  NDK_ARGS+=(--features "${FEATURES}")
fi

echo "Building libguardian_agent.so for ${TARGET} (${PROFILE})..."
cargo ndk "${NDK_ARGS[@]}"

LIB_PATH="${AGENT_DIR}/target/${TARGET}/${PROFILE}/libguardian_agent.so"
if [[ ! -f "${LIB_PATH}" ]]; then
  echo "Expected native library at ${LIB_PATH}" >&2
  exit 1
fi

mkdir -p "${JNI_DIR}"
cp "${LIB_PATH}" "${JNI_DIR}/libguardian_agent.so"
echo "Copied native library to ${JNI_DIR}/libguardian_agent.so"

echo "Regenerating UniFFI Kotlin bindings..."
cargo run --features=uniffi/cli --bin uniffi-bindgen generate \
  --language kotlin \
  --library \
  --no-format \
  --out-dir "${ANDROID_DIR}/app/src/main/java/" \
  "${LIB_PATH}"

echo "Android native artifacts are up to date."
