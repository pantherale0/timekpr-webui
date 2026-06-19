import pytest
from src.database import db, ManagedUser, BlocklistSource, BlocklistDomain, ManagedUserBlocklistAssignment
from src.marketplace_manager import (
    load_marketplace_presets,
    get_marketplace_presets_dict,
    sync_marketplace_subscriptions,
)

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

@pytest.fixture
def child_user(db_session):
    user = ManagedUser(username='test_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.commit()
    return user

def test_load_presets():
    presets = load_marketplace_presets()
    assert isinstance(presets, list)
    assert len(presets) > 0
    
    # Assert adult_explicit exists
    presets_dict = get_marketplace_presets_dict()
    assert 'adult_explicit' in presets_dict
    assert presets_dict['adult_explicit']['source_type'] == 'external_url'
    
    # Assert ai_chat exists
    assert 'ai_chat' in presets_dict
    assert presets_dict['ai_chat']['source_type'] == 'manual'
    assert len(presets_dict['ai_chat']['domains']) > 0

def test_sync_marketplace_subscriptions_manual(db_session, child_user):
    # Subscribe child to "AI Chat"
    sync_marketplace_subscriptions(child_user, ['ai_chat'])
    
    # Assert BlocklistSource created in DB
    source = BlocklistSource.query.filter_by(preset_id='ai_chat').first()
    assert source is not None
    assert source.is_marketplace is True
    assert source.source_type == 'manual'
    
    # Assert manual domains created
    domains = [d.domain for d in source.domains]
    assert 'chatgpt.com' in domains
    
    # Assert assignment created
    assignment = ManagedUserBlocklistAssignment.query.filter_by(
        managed_user_id=child_user.id,
        source_id=source.id
    ).first()
    assert assignment is not None

def test_sync_marketplace_subscriptions_external(db_session, child_user):
    # Subscribe child to "Adult & Explicit Content"
    sync_marketplace_subscriptions(child_user, ['adult_explicit'])
    
    source = BlocklistSource.query.filter_by(preset_id='adult_explicit').first()
    assert source is not None
    assert source.is_marketplace is True
    assert source.source_type == 'external_url'
    assert source.source_url is not None
    
    # Assert assignment created
    assignment = ManagedUserBlocklistAssignment.query.filter_by(
        managed_user_id=child_user.id,
        source_id=source.id
    ).first()
    assert assignment is not None

def test_reconcile_and_cleanup_orphans(db_session, child_user):
    # 1. Subscribe to both
    sync_marketplace_subscriptions(child_user, ['ai_chat', 'adult_explicit'])
    assert BlocklistSource.query.filter_by(is_marketplace=True).count() == 2
    
    # 2. Unsubscribe from one
    sync_marketplace_subscriptions(child_user, ['adult_explicit'])
    
    # Check that 'ai_chat' was cleaned up (orphan source deletion)
    assert BlocklistSource.query.filter_by(preset_id='ai_chat').first() is None
    # 'adult_explicit' should remain
    assert BlocklistSource.query.filter_by(preset_id='adult_explicit').first() is not None

def test_subscribe_user_marketplace_api(auth_client, child_user):
    # POST to subscribe user to presets
    response = auth_client.post(
        f'/managed-users/{child_user.id}/blocklists/subscribe-marketplace',
        data={'preset_ids': ['ai_chat', 'gambling']}
    )
    assert response.status_code == 302 # Redirect to profile edit
    
    # Verify DB changes
    db.session.expire_all()
    assignments = child_user.blocklist_assignments
    assert len(assignments) == 2
    pids = [a.source.preset_id for a in assignments]
    assert 'ai_chat' in pids
    assert 'gambling' in pids


def test_subscribe_user_marketplace_api_json(auth_client, child_user):
    response = auth_client.post(
        f'/managed-users/{child_user.id}/blocklists/subscribe-marketplace',
        data={'preset_ids': ['ai_chat']},
        headers={'Accept': 'application/json', 'X-Requested-With': 'XMLHttpRequest'},
    )
    assert response.status_code == 200
    assert response.get_json()['success'] is True
    db.session.expire_all()
    assert len(child_user.blocklist_assignments) == 1
    assert child_user.blocklist_assignments[0].source.preset_id == 'ai_chat'

def test_subscribe_preset_users_api(auth_client, db_session, child_user):
    user2 = ManagedUser(username='test_child_2', system_ip='Unassigned', is_valid=True)
    db_session.add(user2)
    db_session.commit()
    
    # POST to subscribe both users to 'gambling'
    response = auth_client.post(
        '/admin/marketplace/subscribe',
        data={'preset_id': 'gambling', 'user_ids': [child_user.id, user2.id]}
    )
    assert response.status_code == 302 # Redirects to restrictions
    
    # Verify both have the assignment
    db.session.expire_all()
    source = BlocklistSource.query.filter_by(preset_id='gambling').first()
    assert source is not None
    
    a1 = ManagedUserBlocklistAssignment.query.filter_by(managed_user_id=child_user.id, source_id=source.id).first()
    a2 = ManagedUserBlocklistAssignment.query.filter_by(managed_user_id=user2.id, source_id=source.id).first()
    assert a1 is not None
    assert a2 is not None

def test_create_user_with_presets_api(auth_client, db_session):
    # POST to create user and subscribe immediately
    response = auth_client.post(
        '/users/add', # Backward-compatible wrapper endpoint
        data={
            'username': 'new_child_with_presets',
            'system_id': 'approved-test-device',
            'preset_ids': ['ai_chat']
        }
    )
    # Note: approved-test-device needs to exist
    from src.database import AgentDevice
    device = AgentDevice(system_id='approved-test-device', status='approved')
    db_session.add(device)
    db_session.commit()
    
    response = auth_client.post(
        '/users/add',
        data={
            'username': 'new_child_with_presets',
            'system_id': 'approved-test-device',
            'preset_ids': ['ai_chat']
        }
    )
    assert response.status_code == 302
    
    # Assert user exists and is subscribed
    user = ManagedUser.query.filter_by(username='new_child_with_presets').first()
    assert user is not None
    
    assignments = user.blocklist_assignments
    assert len(assignments) == 1
    assert assignments[0].source.preset_id == 'ai_chat'
