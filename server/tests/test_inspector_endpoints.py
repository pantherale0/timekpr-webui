import pytest
from src.database import (
    ManagedUser,
    AgentDevice,
    ManagedUserDeviceMap,
    WebHistory,
    BlocklistSource,
    BlocklistDomain,
    PolicyApprovalGrant,
)
from datetime import datetime, timezone

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

@pytest.fixture
def child_user(db_session):
    user = ManagedUser(username='inspect_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()
    return user

@pytest.fixture
def device(db_session):
    dev = AgentDevice(
        system_id='sys-inspect',
        status='approved',
        secure_token='tok',
        platform='linux',
        system_hostname='laptop-inspect'
    )
    db_session.add(dev)
    db_session.commit()
    return dev

@pytest.fixture
def mapping(db_session, child_user, device):
    map_obj = ManagedUserDeviceMap(
        managed_user_id=child_user.id,
        system_id=device.system_id,
        linux_username='linux-inspect',
        is_valid=True,
    )
    db_session.add(map_obj)
    db_session.commit()
    return map_obj

def test_get_manual_blocklists(auth_client, db_session):
    source = BlocklistSource(
        name="My Manual List",
        source_type=BlocklistSource.TYPE_MANUAL,
        is_enabled=True
    )
    db_session.add(source)
    db_session.commit()

    response = auth_client.get('/api/blocklists/sources/manual')
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    sources = res_data['sources']
    assert len(sources) >= 1
    assert any(s['name'] == "My Manual List" for s in sources)

def test_inspect_domain(auth_client, db_session, child_user, device, mapping):
    visit = WebHistory(
        managed_user_id=child_user.id,
        device_id=device.system_id,
        domain='example.com',
        title='Example Domain',
        url='http://example.com',
        visited_at=datetime.now(timezone.utc)
    )
    db_session.add(visit)
    db_session.commit()

    response = auth_client.get(f'/api/user/{child_user.id}/inspect?type=domain&value=example.com')
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    assert res_data['type'] == 'domain'
    assert res_data['value'] == 'example.com'
    assert res_data['total_visits'] == 1
    assert 'laptop-inspect' in res_data['device_distribution']
    assert res_data['whitelisted'] is False

def test_whitelist_domain(auth_client, db_session, child_user, device, mapping):
    response = auth_client.post(
        f'/api/user/{child_user.id}/whitelist',
        data={'type': 'domain', 'value': 'approved.com'}
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True

    grant = PolicyApprovalGrant.query.filter_by(
        device_map_id=mapping.id,
        target_value='approved.com'
    ).first()
    assert grant is not None
    assert grant.grant_type == PolicyApprovalGrant.GRANT_DOMAIN_ACCESS
    assert grant.status == PolicyApprovalGrant.STATUS_ACTIVE

def test_block_domain(auth_client, db_session, child_user, device, mapping):
    source = BlocklistSource(
        name="Blocked Sites",
        source_type=BlocklistSource.TYPE_MANUAL,
        is_enabled=True
    )
    db_session.add(source)
    db_session.commit()

    response = auth_client.post(
        f'/api/user/{child_user.id}/block',
        data={'type': 'domain', 'value': 'badsite.com', 'source_id': str(source.id)}
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True

    blocked = BlocklistDomain.query.filter_by(source_id=source.id, domain='badsite.com').first()
    assert blocked is not None
