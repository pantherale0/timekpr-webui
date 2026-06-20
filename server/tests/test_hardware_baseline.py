"""API tests for hardware BIOS baseline management."""

import json

import pytest

from src.database import AgentDevice, db
from src.settings_manager import decrypt_setting, encrypt_setting


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def hardware_device(db_session):
    device = AgentDevice(
        system_id='sys-hardware-baseline',
        status='approved',
        secure_token='hardware-token',
        platform='linux',
        hardware_oem='dell',
        hardware_oem_model='Latitude 5420',
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_hardware_baseline_status_requires_auth(client, hardware_device):
    response = client.get(f'/api/devices/{hardware_device.system_id}/hardware-baseline/status')
    assert response.status_code == 401


def test_hardware_baseline_status_defaults(auth_client, hardware_device):
    response = auth_client.get(f'/api/devices/{hardware_device.system_id}/hardware-baseline/status')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['status']['hardware_oem'] == 'dell'
    assert payload['status']['supported'] is True


def test_apply_hardware_baseline_offline(auth_client, hardware_device, monkeypatch):
    monkeypatch.setattr(
        'src.hardware_baseline_manager.AgentConnectionManager.is_online',
        lambda _system_id: False,
    )
    response = auth_client.post(f'/api/devices/{hardware_device.system_id}/hardware-baseline/apply', json={})
    assert response.status_code == 409


def test_apply_hardware_baseline_persists_receipt(auth_client, hardware_device, monkeypatch):
    receipt = {
        'platform': 'linux',
        'oem': 'dell',
        'overall': 'non_compliant',
        'settings': {
            'supervisor_password': {'applied': True},
            'usb_boot_disabled': {'applied': False},
            'secure_boot_enabled': {'applied': True},
        },
    }

    def fake_apply(self, username='', force_reset_password=False):
        return True, 'applied', {
            'receipt': receipt,
            'escrow_password': 'TestBiosPass123',
        }

    monkeypatch.setattr('src.hardware_baseline_manager.AgentConnectionManager.is_online', lambda _system_id: True)
    monkeypatch.setattr('src.agent_helper.AgentClient.apply_hardware_baseline', fake_apply)

    response = auth_client.post(f'/api/devices/{hardware_device.system_id}/hardware-baseline/apply', json={})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    refreshed = AgentDevice.query.get(hardware_device.system_id)
    assert refreshed.hardware_compliance_status == 'non_compliant'
    assert decrypt_setting(refreshed.bios_supervisor_password_escrow) == 'TestBiosPass123'


def test_reveal_escrowed_password(auth_client, hardware_device):
    hardware_device.bios_supervisor_password_escrow = encrypt_setting('RevealMeNow')
    db.session.commit()

    response = auth_client.post(f'/api/devices/{hardware_device.system_id}/hardware-baseline/reveal-password')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['escrow_password'] == 'RevealMeNow'


def test_audit_hardware_baseline_persists_receipt(auth_client, hardware_device, monkeypatch):
    receipt = {
        'platform': 'linux',
        'oem': 'lenovo',
        'overall': 'compliant',
        'settings': {},
    }

    def fake_audit(self, username=''):
        return True, 'audited', {'receipt': receipt}

    monkeypatch.setattr('src.hardware_baseline_manager.AgentConnectionManager.is_online', lambda _system_id: True)
    monkeypatch.setattr('src.agent_helper.AgentClient.audit_hardware_baseline', fake_audit)

    response = auth_client.post(f'/api/devices/{hardware_device.system_id}/hardware-baseline/audit', json={})
    assert response.status_code == 200
    refreshed = AgentDevice.query.get(hardware_device.system_id)
    assert refreshed.hardware_compliance_status == 'compliant'
    stored = json.loads(refreshed.hardware_compliance_json)
    assert stored['oem'] == 'lenovo'
