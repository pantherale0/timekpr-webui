"""API tests for pairing QR endpoints."""

import io
import zipfile
from unittest.mock import patch

import pytest

from src.models import Settings


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


def test_pairing_config_requires_auth(client):
    response = client.get('/api/pairing/config')
    assert response.status_code == 401


def test_pairing_config_authenticated(auth_client):
    response = auth_client.get('/api/pairing/config')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['payload']['type'] == 'timekpr_pairing'
    assert payload['payload']['server_url'].endswith('/ws')


def test_pairing_qr_png_authenticated(auth_client):
    response = auth_client.get('/api/pairing/qr.png')
    assert response.status_code == 200
    assert response.mimetype == 'image/png'
    assert response.data[:8] == b'\x89PNG\r\n\x1a\n'


def test_pairing_config_uses_saved_agent_websocket_url(auth_client):
    Settings.set_value('agent_websocket_url', 'wss://configured.example/ws')
    response = auth_client.get('/api/pairing/config')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['payload']['server_url'] == 'wss://configured.example/ws'


def test_provisioning_config_requires_auth(client):
    response = client.get('/api/pairing/provisioning/config')
    assert response.status_code == 401


@patch('src.agent.pairing._fetch_release_signature_checksum', return_value=None)
def test_provisioning_config_not_ready_without_overrides(mock_fetch, auth_client):
    Settings.set_value('android_agent_signature_checksum', '')
    response = auth_client.get('/api/pairing/provisioning/config')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['provisioning_ready'] is False
    assert payload['payload'] is None


@patch('src.agent.pairing.has_uploaded_android_apk', return_value=True)
def test_provisioning_config_ready_with_uploaded_apk(mock_uploaded, auth_client):
    Settings.set_value('android_agent_signature_checksum', 'test-checksum-value')
    response = auth_client.get('/api/pairing/provisioning/config')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['provisioning_ready'] is True
    assert payload['apk_url'].endswith('/api/pairing/provisioning/apk')
    assert payload['apk_source'] == 'upload'
    assert payload['signature_checksum'] == 'test-checksum-value'
    assert 'android.app.extra.PROVISIONING_DEVICE_ADMIN_COMPONENT_NAME' in payload['payload']


@patch('src.agent.pairing._fetch_release_signature_checksum', return_value=None)
def test_provisioning_qr_png_requires_ready_config(mock_fetch, auth_client):
    Settings.set_value('android_agent_signature_checksum', '')
    response = auth_client.get('/api/pairing/provisioning/qr.png')
    assert response.status_code == 400


@patch('src.agent.pairing.has_uploaded_android_apk', return_value=True)
def test_provisioning_qr_png_authenticated(mock_uploaded, auth_client):
    Settings.set_value('android_agent_signature_checksum', 'test-checksum-value')
    response = auth_client.get('/api/pairing/provisioning/qr.png')
    assert response.status_code == 200
    assert response.mimetype == 'image/png'
    assert response.data[:8] == b'\x89PNG\r\n\x1a\n'


@patch('src.blueprints.api.pairing.has_uploaded_android_apk', return_value=False)
def test_provisioning_apk_requires_upload(mock_uploaded, client):
    response = client.get('/api/pairing/provisioning/apk')
    assert response.status_code == 404


@patch('src.agent.pairing.has_uploaded_android_apk', return_value=True)
def test_provisioning_apk_serves_uploaded_file(mock_uploaded, client, tmp_path, monkeypatch):
    apk_path = tmp_path / 'android-agent.apk'
    apk_path.write_bytes(b'PK\x03\x04fake-apk')
    monkeypatch.setenv('TIMEKPR_ANDROID_APK_PATH', str(apk_path))

    response = client.get('/api/pairing/provisioning/apk')
    assert response.status_code == 200
    assert response.mimetype == 'application/vnd.android.package-archive'
    assert response.data == b'PK\x03\x04fake-apk'
