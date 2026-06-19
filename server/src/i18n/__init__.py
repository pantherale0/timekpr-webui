"""Server UI internationalisation."""

from src.i18n.catalog import (
    DEFAULT_LOCALE,
    SUPPORTED_LOCALES,
    api_message,
    discover_locales,
    flash_t,
    flatten_for_js,
    load_catalog,
    locale_label,
    resolve_locale,
    t,
)

__all__ = [
    'DEFAULT_LOCALE',
    'SUPPORTED_LOCALES',
    'api_message',
    'discover_locales',
    'flash_t',
    'flatten_for_js',
    'load_catalog',
    'locale_label',
    'resolve_locale',
    't',
]
