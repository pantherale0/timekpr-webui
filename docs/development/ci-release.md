# CI and releases

GitHub Actions workflows publish server Docker images, agent binaries, and documentation.

## Workflows

| Workflow | File | Triggers |
|----------|------|----------|
| Server image | `.github/workflows/server-image.yml` | `server/**`, push `master`, tags `v*` |
| Agent CI | `.github/workflows/rust-agent.yml` | `agent/**`, `android-agent/**`, tags |
| Documentation | `.github/workflows/docs.yml` | `docs/**`, `mkdocs.yml`, push `master`, tags `v*` |

## Agent release assets (tags `v*`)

| Asset | Platform |
|-------|----------|
| `guardian-agent-<target>.tar.gz` + `.sha256` | Linux x86_64, aarch64 |
| `guardian-agent-x86_64-pc-windows-msvc.msi` + `.sha256` | Windows |
| `guardian-android-agent-<tag>.apk` + `.signature-checksum` | Android |

Set `GUARDIAN_AGENT_VERSION` / tag name at build time. Server reads `TIMEKPR_SERVER_VERSION` for handshake matching.

## Android signing secrets

Configure in GitHub repository secrets:

| Secret | Purpose |
|--------|---------|
| `ANDROID_KEYSTORE_BASE64` | Base64-encoded `.keystore` |
| `ANDROID_KEYSTORE_PASSWORD` | Keystore password |
| `ANDROID_KEY_ALIAS` | Key alias |
| `ANDROID_KEY_PASSWORD` | Key password |

Generate keystore:

```bash
keytool -genkeypair -v -keystore release.keystore -alias guardian-alias \
  -keyalg RSA -keysize 2048 -validity 10000
base64 -w 0 release.keystore > keystore_base64.txt
```

See [Android agent — keystore](../platforms/android-agent.md#generating-and-encoding-the-keystore-for-cicd).

## Server image

Published to `ghcr.io/<owner>/<repo>-server:nightly` on `master` and `:vX.Y.Z` on tags.

## Documentation (GitHub Pages)

1. **Settings → Pages → Source:** branch `gh-pages`, folder `/ (root)`
2. Push to `master` (when docs files change) → mike deploys version **`dev`** (rolling docs from `master`)
3. Push tag `v*` → mike deploys that tag, updates the **`latest`** alias, and refreshes the root redirect via `mike set-default`

Site URL configured in `mkdocs.yml` as `site_url`.

First deploy creates the `gh-pages` branch automatically.

## Related

- [Configuration reference](../getting-started/configuration.md)
- [Troubleshooting — signing](../troubleshooting/index.md#ci-android-signing-failures)
