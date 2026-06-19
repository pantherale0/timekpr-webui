"""JSON auto-save endpoints for child profile settings."""

import pytest

from src.database import ManagedUser, AppPolicy, BlocklistSource, db


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def child_user(db_session):
    user = ManagedUser(username='autosave_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture
def blocklist_source(db_session):
    source = BlocklistSource(
        name='Test Shield',
        source_type=BlocklistSource.TYPE_MANUAL,
        is_enabled=True,
    )
    db_session.add(source)
    db_session.commit()
    return source


@pytest.fixture
def app_policy(db_session):
    policy = AppPolicy(name='Games lock', platform='linux')
    db_session.add(policy)
    db_session.commit()
    return policy


JSON_HEADERS = {
    'Accept': 'application/json',
    'X-Requested-With': 'XMLHttpRequest',
}


def test_subscribe_user_marketplace_json(auth_client, child_user):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/blocklists/subscribe-marketplace',
        data={'preset_ids': ['ai_chat']},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert 'message' in payload

    db.session.expire_all()
    assert len(child_user.blocklist_assignments) == 1


def test_update_user_blocklists_json(auth_client, child_user, blocklist_source):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/blocklists/update',
        data={'source_ids': [str(blocklist_source.id)]},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True

    db.session.expire_all()
    assert len(child_user.blocklist_assignments) == 1


def test_update_user_app_policies_json(auth_client, child_user, app_policy):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/app-policies/update',
        data={'policy_ids': [str(app_policy.id)]},
        headers=JSON_HEADERS,
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert 'sync_count' in payload
    assert 'sync_pending' in payload

    db.session.expire_all()
    assert len(child_user.app_policy_assignments) == 1
