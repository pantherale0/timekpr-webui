"""Tests for Windows LAPS escrow and Safe Mode lockdown APIs."""

import pytest

from src.database import AgentDevice, db
from src.settings_manager import encrypt_setting


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def windows_device(db_session):
    device = AgentDevice(
        system_id='sys-windows-laps',
        system_hostname='WIN-TEST',
        status='approved',
        platform='windows',
        secure_token='test-token',
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_windows_laps_status_requires_auth(client, windows_device):
    response = client.get(f'/api/devices/{windows_device.system_id}/windows-laps')
    assert response.status_code == 401


def test_windows_laps_status_defaults(auth_client, windows_device):
    response = auth_client.get(f'/api/devices/{windows_device.system_id}/windows-laps')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['status']['supported'] is True
    assert payload['status']['has_escrowed_password'] is False


def test_persist_credential_escrow(auth_client, windows_device, app):
    from src.windows_laps_manager import get_windows_laps_status, persist_credential_escrow

    with app.app_context():
        persist_credential_escrow(
            windows_device.system_id,
            'windows_local_admin',
            'rotation-123',
            'S3cure-Passw0rd!',
        )
        device = AgentDevice.query.get(windows_device.system_id)
        status = get_windows_laps_status(device, reveal_password=True)
        assert status['has_escrowed_password'] is True
        assert status['rotation_id'] == 'rotation-123'
        assert status['escrow_password'] == 'S3cure-Passw0rd!'


def test_reveal_escrowed_password(auth_client, windows_device):
    windows_device.windows_local_admin_password_escrow = encrypt_setting('Another-Pass!')
    db.session.commit()

    response = auth_client.post(
        f'/api/devices/{windows_device.system_id}/windows-laps/reveal-password',
        json={},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['password'] == 'Another-Pass!'


def test_clear_safe_mode_lockdown_offline(auth_client, windows_device, monkeypatch):
    monkeypatch.setattr(
        'src.windows_laps_manager.AgentConnectionManager.is_online',
        lambda _system_id: False,
    )
    response = auth_client.post(
        f'/api/devices/{windows_device.system_id}/windows-laps/clear-safe-mode-lockdown',
        json={},
    )
    assert response.status_code == 409
