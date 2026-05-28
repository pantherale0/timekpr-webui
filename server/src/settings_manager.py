import logging
from src.database import Settings

_LOGGER = logging.getLogger(__name__)


def _setting_enabled(key):
    raw_value = Settings.get_value(key, None)
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_alert_webhook_settings():
    url = (Settings.get_value('alert_webhook_url', '') or '').strip()
    secret = (Settings.get_value('alert_webhook_secret', '') or '').strip()
    enabled = _setting_enabled('alert_webhook_enabled')
    return {
        'enabled': enabled,
        'url': url,
        'secret': secret,
        'is_active': enabled and bool(url),
    }
