"""Tests for server UI internationalisation."""

import os

import pytest
import yaml

from src.i18n.catalog import (
    DEFAULT_LOCALE,
    discover_locales,
    flatten_for_js,
    load_catalog,
    resolve_locale,
    t,
)


@pytest.fixture
def catalog_path():
    return os.path.join(
        os.path.dirname(__file__), '..', '..', 'i18n', 'en', 'server.yaml'
    )


def test_discover_locales_includes_english():
    locales = discover_locales()
    assert 'en' in locales


def test_catalog_loads_english(catalog_path):
    assert os.path.isfile(catalog_path)
    catalog = load_catalog('en')
    assert catalog.get('meta', {}).get('locale') == 'en'
    assert catalog['pages']['dashboard']['heading'] == 'Family Home'


def test_translate_known_key():
    assert t('pages.dashboard.heading', locale='en') == 'Family Home'


def test_translate_interpolation():
    result = t('flash.users.created', locale='en', username='jordan')
    assert 'jordan' in result


def test_missing_key_returns_placeholder(monkeypatch):
    monkeypatch.setenv('TESTING', '1')
    assert t('nonexistent.key.path', locale='en') == '[missing:nonexistent.key.path]'


def test_unknown_locale_falls_back_to_english():
    assert t('pages.dashboard.heading', locale='xx') == 'Family Home'


def test_resolve_locale_session_priority():
    session = {'locale': 'en'}
    assert resolve_locale(session, 'fr-FR', 'de') == 'en'


def test_resolve_locale_accept_language():
    session = {}
    assert resolve_locale(session, 'en-US,en;q=0.9', None) == 'en'


def test_resolve_locale_household_default():
    session = {}
    assert resolve_locale(session, None, 'en') == 'en'


def test_resolve_locale_fallback():
    session = {}
    assert resolve_locale(session, 'zz-unsupported', None) == DEFAULT_LOCALE


def test_flatten_for_js():
    catalog = load_catalog('en')
    flat = flatten_for_js(catalog)
    assert 'routine_no_limit' in flat
    assert flat['routine_no_limit'] == 'No limit'


def test_yaml_is_valid(catalog_path):
    with open(catalog_path, 'r', encoding='utf-8') as handle:
        data = yaml.safe_load(handle)
    assert 'pages' in data
    assert 'flash' in data
    assert 'js' in data


def test_language_form_sets_session(client):
    from src.models import Settings

    Settings.set_admin_password('admin')
    client.post('/', data={'username': 'admin', 'password': 'admin'})
    response = client.post(
        '/settings',
        data={
            'form_name': 'language',
            'locale': 'en',
        },
        follow_redirects=False,
    )
    assert response.status_code == 302
    with client.session_transaction() as sess:
        assert sess.get('locale') == 'en'
