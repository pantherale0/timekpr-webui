"""Tests for parent-friendly device linking APIs."""

import json
from unittest.mock import patch

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap, Settings


@pytest.fixture
def auth_client(client):
    Settings.set_admin_password('admin')
    client.post('/', data={'username': 'admin', 'password': 'admin'})
    return client


def test_approve_device_with_display_name(auth_client, db_session):
    device = AgentDevice(system_id='pending-aa', status='pending', system_hostname='old-host')
    db_session.add(device)
    db_session.commit()

    response = auth_client.post(
        '/api/device/approve/pending-aa',
        json={'display_name': "Jordan's Laptop"},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success']

    db_session.refresh(device)
    assert device.system_hostname == "Jordan's Laptop"
    assert device.status == 'approved'


def test_patch_device_label(auth_client, db_session):
    device = AgentDevice(system_id='approved-bb', status='approved', secure_token='tok')
    db_session.add(device)
    db_session.commit()

    response = auth_client.patch(
        '/api/device/approved-bb/label',
        json={'display_name': 'Study PC'},
    )
    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success']
    assert data['display_name'] == 'Study PC'

    db_session.refresh(device)
    assert device.system_hostname == 'Study PC'


def test_connect_user_mapping_creates_and_validates(auth_client, db_session):
    device = AgentDevice(
        system_id='laptop-1',
        status='approved',
        secure_token='tok',
        system_hostname="Jordan's Laptop",
    )
    user = ManagedUser(username='jordan', is_valid=False, system_ip='Unassigned')
    db_session.add_all([device, user])
    db_session.commit()

    with patch('src.agent_helper.AgentClient.validate_user') as mock_validate:
        mock_validate.return_value = (True, 'ok', {'LINUX_UID': 1000})
        response = auth_client.post(
            f'/api/managed-users/{user.id}/mappings/connect',
            json={
                'system_id': 'laptop-1',
                'linux_username': 'jordan',
            },
        )

    assert response.status_code == 200
    data = json.loads(response.data)
    assert data['success']
    assert data['mapping']['is_valid'] is True
    assert data['mapping']['linux_uid'] == 1000

    mapping = ManagedUserDeviceMap.query.filter_by(managed_user_id=user.id).first()
    assert mapping is not None
    assert mapping.linux_username == 'jordan'
    assert mapping.linux_uid == 1000
    assert mapping.is_valid is True


def test_connect_user_mapping_rejects_duplicate(auth_client, db_session):
    device = AgentDevice(system_id='laptop-2', status='approved', secure_token='tok')
    user = ManagedUser(username='sam', is_valid=False, system_ip='Unassigned')
    db_session.add_all([device, user])
    db_session.flush()
    db_session.add(ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='laptop-2',
        linux_username='sam',
        is_valid=True,
    ))
    db_session.commit()

    response = auth_client.post(
        f'/api/managed-users/{user.id}/mappings/connect',
        json={
            'system_id': 'laptop-2',
            'linux_username': 'sam',
        },
    )
    assert response.status_code == 409
    data = json.loads(response.data)
    assert data['success'] is False


def test_pending_devices_use_deduped_labels(auth_client, db_session):
    db_session.add_all([
        AgentDevice(system_id='pending-aa', system_hostname='family-pc', status='pending'),
        AgentDevice(system_id='pending-bb', system_hostname='family-pc', status='pending'),
    ])
    db_session.commit()

    response = auth_client.get('/api/devices/pending')
    assert response.status_code == 200
    data = json.loads(response.data)
    labels = {item['system_id']: item['display_name'] for item in data['devices']}
    assert labels['pending-aa'] == 'family-pc (aa)'
    assert labels['pending-bb'] == 'family-pc (bb)'
