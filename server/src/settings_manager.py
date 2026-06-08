import base64
import hashlib
import logging
import os
from cryptography.fernet import Fernet
from flask import current_app
from src.database import Settings

_LOGGER = logging.getLogger(__name__)


def _get_encryption_key() -> bytes:
    try:
        secret = current_app.secret_key
    except RuntimeError:
        secret = os.environ.get('FLASK_SECRET_KEY') or 'timekpr-fallback-secret-key'
    
    if isinstance(secret, str):
        secret = secret.encode('utf-8')
    elif not isinstance(secret, bytes):
        secret = str(secret).encode('utf-8')
        
    hashed = hashlib.sha256(secret).digest()
    return base64.urlsafe_b64encode(hashed)


def encrypt_setting(plain_text: str) -> str:
    if not plain_text:
        return ''
    key = _get_encryption_key()
    f = Fernet(key)
    return f.encrypt(plain_text.encode('utf-8')).decode('utf-8')


def decrypt_setting(cipher_text: str) -> str:
    if not cipher_text:
        return ''
    key = _get_encryption_key()
    f = Fernet(key)
    try:
        return f.decrypt(cipher_text.encode('utf-8')).decode('utf-8')
    except Exception as exc:
        _LOGGER.warning("Failed to decrypt setting: %s", exc)
        return ''



def _in_app_context() -> bool:
    try:
        from flask import current_app
        current_app._get_current_object()
        return True
    except RuntimeError:
        return False


def _safe_get_setting_value(key: str, default: str | None = None) -> str | None:
    if not _in_app_context():
        return default
    return Settings.get_value(key, default)


def _setting_enabled(key):
    raw_value = _safe_get_setting_value(key, None)
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_alert_webhook_settings():
    url = (_safe_get_setting_value('alert_webhook_url', '') or '').strip()
    secret = (_safe_get_setting_value('alert_webhook_secret', '') or '').strip()
    enabled = _setting_enabled('alert_webhook_enabled')
    return {
        'enabled': enabled,
        'url': url,
        'secret': secret,
        'is_active': enabled and bool(url),
    }


def _get_time_sync_tolerance():
    """Get the time sync tolerance in seconds (default: 15)."""
    raw_value = _safe_get_setting_value('time_sync_tolerance', '15')
    try:
        return max(0, int(raw_value))
    except (TypeError, ValueError):
        return 15


def _get_alert_retention_days():
    """Get the alert retention period in days (default: 30)."""
    raw_value = _safe_get_setting_value('alert_retention_days', '30')
    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return 30


def _get_agent_websocket_url():
    """Get the configured agent WebSocket URL for pairing QR codes."""
    return (_safe_get_setting_value('agent_websocket_url', '') or '').strip()


def _get_android_agent_apk_filename():
    """Get the original filename of the uploaded Android APK."""
    return (_safe_get_setting_value('android_agent_apk_filename', '') or '').strip()


def _get_android_agent_signature_checksum():
    """Get the configured Android APK signature checksum override for MDM provisioning."""
    return (_safe_get_setting_value('android_agent_signature_checksum', '') or '').strip()


def _get_android_provisioning_skip_user_setup() -> bool:
    """Get whether to skip Setup Wizard in Android MDM provisioning (default: True)."""
    raw_value = _safe_get_setting_value('android_provisioning_skip_user_setup', None)
    if raw_value is None:
        return True
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_android_provisioning_leave_all_system_apps_enabled() -> bool:
    """Get whether to leave all system apps enabled in Android MDM provisioning (default: True)."""
    raw_value = _safe_get_setting_value('android_provisioning_leave_all_system_apps_enabled', None)
    if raw_value is None:
        return True
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_android_provisioning_wifi_ssid() -> str:
    """Get the Wi-Fi SSID for Android MDM provisioning (default: empty)."""
    return (_safe_get_setting_value('android_provisioning_wifi_ssid', '') or '').strip()


def _get_android_provisioning_wifi_security_type() -> str:
    """Get the Wi-Fi security type for Android MDM provisioning (default: WPA)."""
    return (_safe_get_setting_value('android_provisioning_wifi_security_type', 'WPA') or '').strip()


def _get_android_provisioning_wifi_password_encrypted() -> str:
    """Get the raw encrypted Wi-Fi password cipher text from settings."""
    return (_safe_get_setting_value('android_provisioning_wifi_password', '') or '').strip()


def _get_android_provisioning_wifi_password() -> str:
    """Get the decrypted plain-text Wi-Fi password."""
    encrypted = _get_android_provisioning_wifi_password_encrypted()
    return decrypt_setting(encrypted)

