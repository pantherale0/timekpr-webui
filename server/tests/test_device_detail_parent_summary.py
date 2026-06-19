"""Parent-friendly device detail page coverage."""

import pytest

from src.database import AgentDevice, ManagedUser, ManagedUserDeviceMap
from src.spa_view_builders import _build_device_protection_summary


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as session_state:
        session_state['logged_in'] = True
    return client


@pytest.fixture
def approved_device(db_session):
    device = AgentDevice(
        system_id='test-device-summary',
        system_hostname='test-laptop',
        status='approved',
        platform='linux',
    )
    db_session.add(device)
    db_session.commit()
    return device


def _summary(device, mapped_accounts, contributors, alerts, online, android_policy=None, screenshot=None):
    return _build_device_protection_summary(
        device,
        mapped_accounts,
        contributors,
        alerts,
        online,
        android_device_policy=android_policy,
        screenshot_settings=screenshot,
    )


class _Policy:
    def __init__(self, synced):
        self.is_synced = synced


class _Screenshot:
    def __init__(self, synced):
        self.is_synced = synced


class _Mapping:
    def __init__(self, valid=True):
        self.is_valid = valid


class _Device:
    def __init__(self, platform='linux'):
        self.platform = platform


def test_protection_summary_connected_when_healthy():
    summary = _summary(_Device(), [_Mapping()], [], {'total': 0}, True)
    assert summary['status'] == 'connected'
    assert summary['attention_items'] == []


def test_protection_summary_offline_only():
    summary = _summary(_Device(), [_Mapping()], [], {'total': 0}, False)
    assert summary['status'] == 'offline'
    assert len(summary['attention_items']) == 1


def test_protection_summary_needs_attention_for_unverified_mapping():
    summary = _summary(_Device(), [_Mapping(valid=False)], [], {'total': 0}, True)
    assert summary['status'] == 'needs_attention'
    assert any(item['message_key'] == 'pages.device_detail.attention_unverified_mappings' for item in summary['attention_items'])


def test_device_detail_overview_is_parent_friendly(auth_client, approved_device):
    response = auth_client.get(f'/devices/{approved_device.system_id}')
    assert response.status_code == 200
    html = response.get_data(as_text=True)
    assert 'At a glance' in html or 'Protection status' in html
    assert 'System ID' not in html.split('Technical details')[0]


def test_device_detail_advanced_still_has_lifecycle_actions(auth_client, approved_device):
    response = auth_client.get(f'/devices/{approved_device.system_id}')
    html = response.get_data(as_text=True)
    assert 'unenroll-device-btn' in html
    assert 'advanced-tab' in html


def test_device_detail_has_mobile_tab_sub_rail(auth_client, approved_device):
    response = auth_client.get(f'/devices/{approved_device.system_id}')
    html = response.get_data(as_text=True)
    assert 'device-detail-sub-rail' in html
    assert 'device-detail-tabs--mobile' in html
    assert 'device-detail-top-nav-desktop d-none d-xl-flex' in html
    assert 'segmented-tab-btn active' not in html

def test_admin_users_mapping_links_to_child_profile(auth_client, approved_device, db_session):
    user = ManagedUser(username='summary-child', system_ip='Unassigned')
    db_session.add(user)
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=approved_device.system_id,
        linux_username='child',
        linux_uid=1001,
    )
    db_session.add(mapping)
    db_session.commit()

    response = auth_client.get('/admin/users')
    html = response.get_data(as_text=True)
    assert f'/admin/users/{user.id}?highlight_mapping={mapping.id}#profile-computer' in html
    assert f'/devices/{approved_device.system_id}' in html
