"""Tests for Android MDM provisioning config settings, encryption, and payload generation."""

import json
from unittest.mock import patch
import pytest

from src.database import Settings
from src.settings_manager import (
    encrypt_setting,
    decrypt_setting,
    _get_android_provisioning_skip_user_setup,
    _get_android_provisioning_leave_all_system_apps_enabled,
    _get_android_provisioning_wifi_ssid,
    _get_android_provisioning_wifi_security_type,
    _get_android_provisioning_wifi_password,
)
from src.pairing_helper import (
    build_android_provisioning_payload,
    resolve_android_provisioning,
    PROVISIONING_KEY_SKIP_USER_SETUP,
    PROVISIONING_KEY_LEAVE_ALL_SYSTEM_APPS_ENABLED,
    PROVISIONING_KEY_WIFI_SSID,
    PROVISIONING_KEY_WIFI_SECURITY_TYPE,
    PROVISIONING_KEY_WIFI_PASSWORD,
)


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


def test_encryption_decryption(app):
    with app.app_context():
        plain = "MySecretWiFiPassword123!"
        encrypted = encrypt_setting(plain)
        assert encrypted != plain
        assert encrypted != ""
        
        decrypted = decrypt_setting(encrypted)
        assert decrypted == plain


def test_encryption_decryption_empty_inputs(app):
    with app.app_context():
        assert encrypt_setting("") == ""
        assert decrypt_setting("") == ""
        assert decrypt_setting("invalid_ciphertext") == ""


def test_provisioning_settings_defaults(db_session):
    # Before any values are set
    assert _get_android_provisioning_skip_user_setup() is True
    assert _get_android_provisioning_leave_all_system_apps_enabled() is True
    assert _get_android_provisioning_wifi_ssid() == ""
    assert _get_android_provisioning_wifi_security_type() == "WPA"
    assert _get_android_provisioning_wifi_password() == ""


def test_provisioning_settings_persistence(db_session, app):
    with app.app_context():
        Settings.set_value('android_provisioning_skip_user_setup', '0')
        Settings.set_value('android_provisioning_leave_all_system_apps_enabled', '0')
        Settings.set_value('android_provisioning_wifi_ssid', 'HomeNet')
        Settings.set_value('android_provisioning_wifi_security_type', 'NONE')
        Settings.set_value('android_provisioning_wifi_password', encrypt_setting('secret123'))
        
        assert _get_android_provisioning_skip_user_setup() is False
        assert _get_android_provisioning_leave_all_system_apps_enabled() is False
        assert _get_android_provisioning_wifi_ssid() == 'HomeNet'
        assert _get_android_provisioning_wifi_security_type() == 'NONE'
        assert _get_android_provisioning_wifi_password() == 'secret123'


def test_build_android_provisioning_payload_wifi_omitted():
    payload = build_android_provisioning_payload(
        server_url='ws://localhost/ws',
        apk_url='http://localhost/apk',
        signature_checksum='checksum',
        registration_token='reg-token',
        skip_user_setup=True,
        leave_all_system_apps_enabled=True,
        wifi_ssid='',
    )
    assert payload[PROVISIONING_KEY_SKIP_USER_SETUP] is True
    assert payload[PROVISIONING_KEY_LEAVE_ALL_SYSTEM_APPS_ENABLED] is True
    assert PROVISIONING_KEY_WIFI_SSID not in payload
    assert PROVISIONING_KEY_WIFI_PASSWORD not in payload


def test_build_android_provisioning_payload_wifi_included():
    payload = build_android_provisioning_payload(
        server_url='ws://localhost/ws',
        apk_url='http://localhost/apk',
        signature_checksum='checksum',
        registration_token='reg-token',
        skip_user_setup=False,
        leave_all_system_apps_enabled=False,
        wifi_ssid='HomeNet',
        wifi_security_type='WPA',
        wifi_password='password123',
    )
    assert payload[PROVISIONING_KEY_SKIP_USER_SETUP] is False
    assert payload[PROVISIONING_KEY_LEAVE_ALL_SYSTEM_APPS_ENABLED] is False
    assert payload[PROVISIONING_KEY_WIFI_SSID] == 'HomeNet'
    assert payload[PROVISIONING_KEY_WIFI_SECURITY_TYPE] == 'WPA'
    assert payload[PROVISIONING_KEY_WIFI_PASSWORD] == 'password123'


@patch('src.pairing_helper.has_uploaded_android_apk', return_value=True)
def test_resolve_android_provisioning_includes_new_options(mock_uploaded, db_session, app):
    with app.app_context():
        Settings.set_value('android_provisioning_skip_user_setup', '0')
        Settings.set_value('android_provisioning_leave_all_system_apps_enabled', '1')
        Settings.set_value('android_provisioning_wifi_ssid', 'MyWiFi')
        Settings.set_value('android_provisioning_wifi_security_type', 'WPA')
        Settings.set_value('android_provisioning_wifi_password', encrypt_setting('pass123'))
        Settings.set_value('android_agent_signature_checksum', 'checksum1')

        context = resolve_android_provisioning(
            server_url='ws://localhost:5000/ws',
            version='v0.0.0-dev',
            checksum_override='checksum1',
        )
        assert context['skip_user_setup'] is False
        assert context['leave_all_system_apps_enabled'] is True
        assert context['wifi_ssid'] == 'MyWiFi'
        assert context['wifi_security_type'] == 'WPA'
        assert context['has_wifi_password'] is True
        
        payload = context['payload']
        assert payload[PROVISIONING_KEY_SKIP_USER_SETUP] is False
        assert payload[PROVISIONING_KEY_LEAVE_ALL_SYSTEM_APPS_ENABLED] is True
        assert payload[PROVISIONING_KEY_WIFI_SSID] == 'MyWiFi'
        assert payload[PROVISIONING_KEY_WIFI_PASSWORD] == 'pass123'


def test_post_android_provisioning_settings(auth_client, db_session, app):
    with app.app_context():
        response = auth_client.post(
            '/settings',
            data={
                'form_name': 'android_provisioning',
                'android_provisioning_skip_user_setup': 'on',
                # leave_all_system_apps_enabled is omitted (unchecked)
                'android_provisioning_wifi_ssid': 'NewSSID',
                'android_provisioning_wifi_security_type': 'WEP',
                'android_provisioning_wifi_password': 'new-password-321',
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        
        assert _get_android_provisioning_skip_user_setup() is True
        assert _get_android_provisioning_leave_all_system_apps_enabled() is False
        assert _get_android_provisioning_wifi_ssid() == 'NewSSID'
        assert _get_android_provisioning_wifi_security_type() == 'WEP'
        assert _get_android_provisioning_wifi_password() == 'new-password-321'


def test_post_android_provisioning_settings_preserves_password(auth_client, db_session, app):
    with app.app_context():
        Settings.set_value('android_provisioning_wifi_ssid', 'SSID')
        Settings.set_value('android_provisioning_wifi_password', encrypt_setting('original-pass'))
        
        response = auth_client.post(
            '/settings',
            data={
                'form_name': 'android_provisioning',
                'android_provisioning_wifi_ssid': 'SSID',
                'android_provisioning_wifi_security_type': 'WPA',
                'android_provisioning_wifi_password': '',  # Empty means preserve existing
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert _get_android_provisioning_wifi_password() == 'original-pass'


def test_post_android_provisioning_settings_clears_wifi(auth_client, db_session, app):
    with app.app_context():
        Settings.set_value('android_provisioning_wifi_ssid', 'SSID')
        Settings.set_value('android_provisioning_wifi_password', encrypt_setting('original-pass'))
        
        response = auth_client.post(
            '/settings',
            data={
                'form_name': 'android_provisioning',
                'android_provisioning_wifi_ssid': '',  # Empty SSID clears Wi-Fi settings
                'android_provisioning_wifi_security_type': 'WPA',
                'android_provisioning_wifi_password': 'ignored-pass',
            },
            follow_redirects=True,
        )
        assert response.status_code == 200
        assert _get_android_provisioning_wifi_ssid() == ''
        assert _get_android_provisioning_wifi_password() == ''
