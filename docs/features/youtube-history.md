# YouTube History Monitoring

Guardian supports logging watched YouTube videos across Android, Linux, and Windows. This feature tracks video titles, creators/channels, watch durations, categories, and timestamps, displaying them in a parent dashboard with aggregate analytics.

## Mechanics & Platform Integration

The feature runs on outbound logging directly from the client devices to the server's REST API.

### Android Agent
Enforced using a native Android **Accessibility Service** (`YoutubeAccessibilityService`):
1. Captures view layout updates inside the official native YouTube application.
2. Extracts video IDs, titles, and channel details from foreground UI text elements.
3. Automatically computes active watch durations and batches events.
4. Authorizes and uploads entries to `/api/youtube/log` using the agent's secure device token.

### Linux & Windows Agents
Enforced using a custom, lightweight **Chrome browser extension** force-installed by the agents via system policies:
1. **Linux**: Writes Chrome policy JSON to `/etc/opt/chrome/policies/managed/guardian_chrome_policies.json`.
2. **Windows**: Writes policy registry keys to `HKLM\SOFTWARE\Policies\Google\Chrome\ExtensionInstallForcelist` (using Local Machine scope to bypass non-domain Active Directory limitations on standard Windows PCs).
3. **Identity Resolution**: Agents write the current user mapping profile to the policy directory. The extension reads this mapped child identity using the secure `chrome.storage.managed` API to authenticate logs.
4. **Self-Hosting**: The extension is signed using a private RSA key and hosted directly on the Guardian server. The server serves the package as a signed `.crx` file and dynamically generates the update XML manifest.

### Building the extension

The CRX is built in CI as part of the server image workflow:

- **Release tags** (`v1.2.3`): extension version is `1.2.3` (the `v` prefix is stripped).
- **Nightly builds** (`master`): extension version is `0.0.0`.
- The signing key is supplied via the `EXTENSION_SIGNING_KEY_PEM` GitHub Actions secret (contents of `server/static/extensions/key.pem`). The key must remain stable so the Chrome extension ID does not change.

Local build:

```bash
pip install crx3 cryptography
python3 scripts/package-extension.py --version 1.2.3
```

The packaged version is written to `server/static/extensions/extension_version.txt` and served in the Chrome update manifest at `/api/extensions/update`.

---

## Server Configuration

Configure settings in **Settings**:

- **YouTube API Key**: (Optional) A Google Developer API key. If provided, a background worker asynchronously resolves category metadata (e.g., *Gaming*, *Education*, *Music*) for watched videos. If not provided, category defaults to "Unknown".
- **YouTube History Retention (Days)**: (Optional) Configures automatic pruning of logs older than the set threshold. If blank, history is kept indefinitely.

---

## REST API Reference

| Endpoint | Method | Authentication | Description |
|----------|--------|----------------|-------------|
| `/api/youtube/log` | POST | Agent Secure Token (`Bearer`) | Logs a batch of watched videos |
| `/api/extensions/update` | GET | None | Chrome update XML manifest |
| `/api/extensions/download` | GET | None | Download signed `.crx` extension |
| `/api/user/<user_id>/youtube` | GET | Session Auth | Query watched history feeds & stats |

---

## Related

- [Linux agent](../platforms/linux-agent.md)
- [Windows agent](../platforms/windows-agent.md)
- [Android agent](../platforms/android-agent.md)
- [Policy matrix](../reference/policy-matrix.md)
