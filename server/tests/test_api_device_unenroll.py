"""API tests for device unenrollment."""

import json
from unittest.mock import patch

import pytest

from src.models import AgentDevice


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def approved_device(db_session):
    device = AgentDevice(
        system_id='sys-unenroll-api',
        system_hostname='family-tablet',
        status='approved',
        secure_token='token',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


def test_unenroll_requires_auth(client):
    response = client.post(
        '/api/device/sys-unenroll-api/unenroll',
        data=json.dumps({'mode': 'unenroll'}),
        content_type='application/json',
    )
    assert response.status_code == 401


def test_unenroll_device_not_found(auth_client):
    response = auth_client.post(
        '/api/device/missing-device/unenroll',
        data=json.dumps({'mode': 'unenroll'}),
        content_type='application/json',
    )
    assert response.status_code == 404


def test_unenroll_invalid_mode(auth_client, approved_device):
    response = auth_client.post(
        f'/api/device/{approved_device.system_id}/unenroll',
        data=json.dumps({'mode': 'wipe'}),
        content_type='application/json',
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False


@patch('src.device.lifecycle.AgentConnectionManager.is_online', return_value=True)
@patch('src.device.lifecycle.AgentClient.unenroll_device', return_value=(True, 'cleared'))
def test_unenroll_success(mock_unenroll, mock_online, auth_client, approved_device, db_session):
    response = auth_client.post(
        f'/api/device/{approved_device.system_id}/unenroll',
        data=json.dumps({'mode': 'unenroll'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['server_revoked'] is True
    assert payload['delivered_to_agent'] is True

    refreshed = AgentDevice.query.get(approved_device.system_id)
    assert refreshed.status == 'rejected'
    assert refreshed.secure_token is None


@patch('src.blueprints.api.devices.lifecycle_unenroll_device')
def test_factory_reset_delegates_to_manager(mock_lifecycle, auth_client, approved_device):
    mock_lifecycle.return_value = {
        'success': True,
        'message': 'Factory reset requested',
        'delivered_to_agent': False,
        'factory_reset_requested': True,
        'pending_factory_reset': True,
        'server_revoked': True,
        'status_code': 200,
    }
    approved_device.platform = 'android'
    approved_device.fcm_token = 'fcm'

    response = auth_client.post(
        f'/api/device/{approved_device.system_id}/unenroll',
        data=json.dumps({'mode': 'factory_reset'}),
        content_type='application/json',
    )
    assert response.status_code == 200
    mock_lifecycle.assert_called_once_with(approved_device.system_id, 'factory_reset')
    payload = response.get_json()
    assert payload['factory_reset_requested'] is True
