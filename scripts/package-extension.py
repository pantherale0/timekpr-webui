#!/usr/bin/env python3
"""Package the Guardian Chrome extension as a signed CRX3 bundle."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import zipfile

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXTENSION_SRC_DIR = os.path.join(ROOT_DIR, "extension")
EXTENSIONS_DIST_DIR = os.path.join(ROOT_DIR, "server", "static", "extensions")
KEY_PATH = os.path.join(EXTENSIONS_DIST_DIR, "key.pem")
CRX_PATH = os.path.join(EXTENSIONS_DIST_DIR, "youtube_monitor.crx")
ID_PATH = os.path.join(EXTENSIONS_DIST_DIR, "extension_id.txt")
VERSION_PATH = os.path.join(EXTENSIONS_DIST_DIR, "extension_version.txt")
MANIFEST_PATH = os.path.join(EXTENSION_SRC_DIR, "manifest.json")

_CHROME_VERSION_RE = re.compile(
    r"^(?P<major>\d+)(?:\.(?P<minor>\d+))?(?:\.(?P<patch>\d+))?(?:\.(?P<build>\d+))?$"
)


def normalize_extension_version(raw: str) -> str:
    """Normalize a git tag or version string to Chrome's dotted-integer format."""
    candidate = (raw or "").strip().lstrip("v")
    if not candidate:
        raise ValueError("Extension version must not be empty")

    match = _CHROME_VERSION_RE.match(candidate)
    if not match:
        raise ValueError(
            f"Invalid extension version {raw!r}; expected 1-4 dot-separated integers "
            "(for example 1.2.3)."
        )

    parts = [
        match.group("major"),
        match.group("minor") or "0",
        match.group("patch") or "0",
    ]
    if match.group("build") is not None:
        parts.append(match.group("build"))
    return ".".join(parts)


def get_extension_id(private_key_path: str) -> str:
    """Calculate the Chrome Extension ID from the private key (DER public key hash)."""
    with open(private_key_path, "rb") as key_file:
        private_key = serialization.load_pem_private_key(
            key_file.read(),
            password=None,
            backend=default_backend(),
        )

    public_key = private_key.public_key()
    der_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    sha256_hash = hashlib.sha256(der_bytes).hexdigest()
    half_hash = sha256_hash[:32]
    mapping = {hex(i)[2:]: chr(97 + i) for i in range(16)}
    return "".join(mapping[c] for c in half_hash)


def ensure_signing_key(require_existing: bool) -> None:
    os.makedirs(EXTENSIONS_DIST_DIR, exist_ok=True)

    if os.path.exists(KEY_PATH):
        return

    if require_existing:
        raise SystemExit(
            f"Signing key not found at {KEY_PATH}. "
            "Provide EXTENSION_SIGNING_KEY_PEM or run locally without --require-existing-key."
        )

    print(f"Generating new private key at {KEY_PATH}...")
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    with open(KEY_PATH, "wb") as key_file:
        key_file.write(pem)


def resolve_version(explicit_version: str | None) -> str:
    for candidate in (explicit_version, os.environ.get("EXTENSION_VERSION")):
        if candidate:
            return normalize_extension_version(candidate)

    with open(MANIFEST_PATH, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)
    return normalize_extension_version(str(manifest.get("version", "0.0.0")))


def write_manifest_version(version: str) -> None:
    with open(MANIFEST_PATH, encoding="utf-8") as manifest_file:
        manifest = json.load(manifest_file)

    manifest["version"] = version

    with open(MANIFEST_PATH, "w", encoding="utf-8") as manifest_file:
        json.dump(manifest, manifest_file, indent=2)
        manifest_file.write("\n")


def verify_crx_version(crx_path: str, expected_version: str) -> None:
    with open(crx_path, "rb") as crx_file:
        data = crx_file.read()

    zip_offset = data.find(b"PK\x03\x04")
    if zip_offset < 0:
        raise SystemExit(f"Could not locate ZIP payload inside {crx_path}")

    with zipfile.ZipFile(io.BytesIO(data[zip_offset:])) as archive:
        manifest = json.loads(archive.read("manifest.json"))

    packaged_version = normalize_extension_version(str(manifest.get("version", "")))
    if packaged_version != expected_version:
        raise SystemExit(
            f"Packaged CRX version mismatch: expected {expected_version}, got {packaged_version}"
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--version",
        help="Chrome extension version to embed (for example 1.2.3 or v1.2.3). "
        "Defaults to EXTENSION_VERSION or manifest.json.",
    )
    parser.add_argument(
        "--require-existing-key",
        action="store_true",
        help="Fail if the signing key is missing instead of generating a new one.",
    )
    return parser.parse_args()


def main() -> None:
    try:
        import crx3.creator
    except ImportError:
        print("Error: 'crx3' package is not installed. Please run: pip install crx3")
        sys.exit(1)

    args = parse_args()
    version = resolve_version(args.version)
    ensure_signing_key(args.require_existing_key)

    ext_id = get_extension_id(KEY_PATH)
    print(f"Calculated Chrome Extension ID: {ext_id}")
    print(f"Packaging extension version: {version}")

    write_manifest_version(version)

    print(f"Packaging extension from '{EXTENSION_SRC_DIR}' into '{CRX_PATH}'...")
    try:
        crx3.creator.create_crx_file(EXTENSION_SRC_DIR, KEY_PATH, CRX_PATH)
    except Exception as exc:
        print(f"Error during packaging: {exc}")
        sys.exit(1)

    verify_crx_version(CRX_PATH, version)

    with open(ID_PATH, "w", encoding="utf-8") as id_file:
        id_file.write(ext_id)

    with open(VERSION_PATH, "w", encoding="utf-8") as version_file:
        version_file.write(version)

    print("Success! Extension packaged and signed successfully.")


if __name__ == "__main__":
    main()
