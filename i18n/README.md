# Guardian internationalisation (i18n)

Translation catalogs live under ISO 639-1 language folders:

```
i18n/
  en/
    server.yaml      # Parent console (Flask/Jinja UI)
    agent.yaml       # Agent-facing strings (future)
    extension.yaml   # Browser extension strings (Chrome _locales)
  fr/                # Example: copy en/ and translate values
    server.yaml
```

## Server UI

- **Loader:** `server/src/i18n/catalog.py`
- **Templates:** `{{ t('pages.dashboard.title') }}` or `{{ t('key', name=value) }}`
- **Python:** `from src.i18n.catalog import t, flash_t, api_message`
- **Client JS:** `window.guardianI18n('routine_no_limit')` via `#i18n-data` (flattened `js.*` keys)

### Key naming

Use stable semantic paths, not English text:

| Prefix | Use |
|--------|-----|
| `nav.*` | Sidebar and top navigation |
| `shell.*` | SPA shell, modals, onboarding wizard |
| `pages.<route>.*` | Per-page copy |
| `flash.*` | `flash_t()` messages |
| `api.*` | JSON `message` fields via `api_message()` |
| `js.*` | Inline and static JavaScript strings |
| `settings.language.*` | Language picker |

Interpolation uses Python-style placeholders: `"Hello {username}"` → `t('key', username='Jordan')`.

### Locale resolution (per request)

1. `session['locale']` (Settings language picker)
2. `Accept-Language` header
3. `Settings.default_locale` household default
4. `en` fallback

### Adding a locale

1. Copy `i18n/en/server.yaml` → `i18n/<code>/server.yaml`
2. Translate string values only; keep keys identical
3. Add `meta.locale` and `meta.label`
4. The Settings dropdown lists folders that contain `server.yaml` automatically

Missing keys log a warning and show `[missing:key]` in tests or the key path in production. English is always the fallback catalog.

## Browser extension

- **Catalog:** `i18n/<locale>/extension.yaml` → `messages:` map (Chrome format)
- **Bundle:** `python scripts/i18n/manage.py bundle --target extension` writes `extension/_locales/<lang>/messages.json` and syncs overlay assets
- **Runtime:** `chrome.i18n.getMessage()` via `extension/i18n.js` (`guardianExtI18n()`)
- **Manifest:** `default_locale: en` with `__MSG_extensionName__` placeholders

Run `bundle --target extension` before loading the extension locally or packaging the CRX (CI does this automatically).

## Management scripts

CLI entry point: [`scripts/i18n/manage.py`](../scripts/i18n/manage.py) (wrapper: `scripts/i18n/guardian-i18n`).

```bash
# Create a new locale from English templates
python scripts/i18n/manage.py add-locale fr --label "Français"

# Add a string (English); other locales get [TODO] copies automatically
python scripts/i18n/manage.py add-string server flash.auth.example "Signed in as {username}"

# Add or replace with --force; skip propagation with --no-propagate
python scripts/i18n/manage.py add-string server common.save "Save" --force

# Validate structure, key parity, and {placeholder} consistency
python scripts/i18n/manage.py validate
python scripts/i18n/manage.py validate --strict   # fail on [TODO] strings

# List locales and missing keys vs English
python scripts/i18n/manage.py list-locales
python scripts/i18n/manage.py list-missing fr --service server

# CI bundling — generate deployment artifacts
python scripts/i18n/manage.py bundle --target server     # stage i18n/ → server/i18n/ for Docker
python scripts/i18n/manage.py bundle --target agent      # Android strings + overlay JS + Rust desktop JSON
python scripts/i18n/manage.py bundle --target android    # Android res/values*/strings.xml only
python scripts/i18n/manage.py bundle --target overlay    # blockedv2.html + overlay-i18n.{locale}.js
python scripts/i18n/manage.py bundle --target rust       # agent/resources/i18n/{locale}.json
python scripts/i18n/manage.py bundle --target extension  # extension.yaml → _locales/*/messages.json
python scripts/i18n/manage.py bundle --target all        # server + agent bundles
```

### Catalog layouts by service

| File | Purpose |
|------|---------|
| `server.yaml` | Nested keys for Flask/Jinja (`pages.dashboard.title`) |
| `agent.yaml` | Flat `strings:` → Android `strings.xml`; `overlay:` + `overlay_ui:` → `overlay-i18n.{locale}.js`; `desktop:` → Rust JSON |
| `extension.yaml` | `messages:` map → Chrome `_locales/<lang>/messages.json` |

Use `{parameter}` placeholders in server strings. Android uses `%1$s` style in `agent.yaml` where required.

### CI integration

- **Server image** (`.github/workflows/server-image.yml`): `validate` + `bundle --target server` + `bundle --target extension` before Docker build and CRX packaging; staged server catalogs ship inside the image at `/app/i18n`.
- **Agent builds** (`.github/workflows/rust-agent.yml`): `validate` + `bundle --target agent` before `cargo check` / Gradle (Android `strings.xml`, overlay `overlay-i18n.*.js`, Rust `resources/i18n/*.json`).

Override catalog location at runtime with `GUARDIAN_I18N_ROOT` (server) or `GUARDIAN_LOCALE` / `LANG` (Rust desktop toasts).
