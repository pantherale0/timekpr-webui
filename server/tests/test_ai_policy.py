"""Unit and integration tests for AI policy enforcement and auditing."""
import json
from datetime import datetime, timezone, timedelta
import pytest
from src.models import (
    db,
    ManagedUser,
    AgentDevice,
    ManagedUserDeviceMap,
    MappingApprovalSettings,
    AiPromptLog,
    AiSessionLog,
    AgentAlert,
    PolicyApprovalGrant
)

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

@pytest.fixture
def ai_setup(db_session):
    user = ManagedUser(username='ai_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()

    device = AgentDevice(
        system_id='ai-test-device',
        system_hostname='ai-test-pc',
        status='approved',
        secure_token='ai-test-token',
        platform='linux',
    )
    db_session.add(device)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username='child_os_user',
    )
    db_session.add(mapping)
    db_session.flush()

    settings = MappingApprovalSettings(
        device_map_id=mapping.id,
        ai_policy_mode='off',
        ai_prompt_logging='metadata_only',
        ai_daily_time_limit=None
    )
    db_session.add(settings)
    db_session.commit()

    return user, device, mapping, settings

def test_check_policy_unauthorized(client):
    # No Auth header
    response = client.post(
        '/api/ai/check-policy',
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 401

    # Invalid Auth header
    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': 'Bearer bad-token'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 401

def test_check_policy_missing_user(client, ai_setup):
    _, device, _, _ = ai_setup
    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'domain': 'chatgpt.com'}), # Missing linux_username
        content_type='application/json'
    )
    assert response.status_code == 400

def test_check_policy_modes(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup

    # 1. Mode: Off
    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    assert res_data['allowed'] is True
    assert res_data['reason'] is None

    # 2. Mode: Block
    settings.ai_policy_mode = 'block'
    db_session.commit()
    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is False
    assert res_data['reason'] == 'blocked'

    # 3. Mode: Approve (no grant)
    settings.ai_policy_mode = 'approve'
    db_session.commit()
    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is False
    assert res_data['reason'] == 'approve_required'

    # 4. Mode: Approve (active grant exists)
    grant = PolicyApprovalGrant(
        device_map_id=mapping.id,
        grant_type=PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
        target_kind='domain',
        target_value='chatgpt.com',
        display_label='chatgpt.com',
        status=PolicyApprovalGrant.STATUS_ACTIVE,
        created_by='parent',
        created_at=datetime.now(timezone.utc)
    )
    db_session.add(grant)
    db_session.commit()

    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is True
    assert res_data['reason'] is None

def test_check_policy_limit_exceeded(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup
    settings.ai_policy_mode = 'off'
    settings.ai_daily_time_limit = 5 # 5 minutes = 300 seconds
    db_session.commit()

    # Log 200 seconds of session logs today
    log1 = AiSessionLog(
        device_map_id=mapping.id,
        domain='chatgpt.com',
        duration_seconds=200,
        logged_at=datetime.now(timezone.utc)
    )
    db_session.add(log1)
    db_session.commit()

    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is True
    assert res_data['time_left_seconds'] == 100

    # Log another 150 seconds (total 350 > 300)
    log2 = AiSessionLog(
        device_map_id=mapping.id,
        domain='claude.ai',
        duration_seconds=150,
        logged_at=datetime.now(timezone.utc)
    )
    db_session.add(log2)
    db_session.commit()

    response = client.post(
        '/api/ai/check-policy',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'child_os_user', 'domain': 'chatgpt.com'}),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is False
    assert res_data['reason'] == 'limit_exceeded'
    assert res_data['time_left_seconds'] == 0

def test_check_prompt_logging_metadata_vs_full(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup
    settings.ai_policy_mode = 'monitor'
    settings.ai_prompt_logging = 'metadata_only'
    db_session.commit()

    # Metadata only: prompt text should not be stored
    response = client.post(
        '/api/ai/check-prompt',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'service': 'chatgpt',
            'domain': 'chatgpt.com',
            'prompt_text': 'Hello AI, how are you?',
            'url': 'https://chatgpt.com/',
            'title': 'ChatGPT'
        }),
        content_type='application/json'
    )
    assert response.status_code == 200
    assert response.get_json()['allowed'] is True

    log = AiPromptLog.query.order_by(AiPromptLog.id.desc()).first()
    assert log is not None
    assert log.prompt_text is None
    assert log.prompt_length == 22
    assert log.status == 'Allowed'

    # Full text logging: prompt text should be stored
    settings.ai_prompt_logging = 'full_text'
    db_session.commit()

    response = client.post(
        '/api/ai/check-prompt',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'service': 'claude',
            'domain': 'claude.ai',
            'prompt_text': 'Please explain photosynthesis.',
            'url': 'https://claude.ai/',
            'title': 'Claude'
        }),
        content_type='application/json'
    )
    assert response.status_code == 200

    log = AiPromptLog.query.order_by(AiPromptLog.id.desc()).first()
    assert log is not None
    assert log.prompt_text == 'Please explain photosynthesis.'
    assert log.prompt_length == 30
    assert log.status == 'Allowed'

def test_check_prompt_flagged_bypass_cheating(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup
    settings.ai_policy_mode = 'monitor'
    settings.ai_prompt_logging = 'full_text'
    db_session.commit()

    # Cheating attempt: solve this test
    response = client.post(
        '/api/ai/check-prompt',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'service': 'chatgpt',
            'domain': 'chatgpt.com',
            'prompt_text': 'Can you solve this test for me?',
            'url': 'https://chatgpt.com/',
            'title': 'ChatGPT'
        }),
        content_type='application/json'
    )
    assert response.status_code == 200

    log = AiPromptLog.query.order_by(AiPromptLog.id.desc()).first()
    assert log is not None
    assert log.status == 'Flagged'

    # Check alert was raised
    alert = AgentAlert.query.order_by(AgentAlert.id.desc()).first()
    assert alert is not None
    assert alert.event_type == 'ai_bypass_attempt'
    assert 'solve this test' in alert.payload['details']['matched_keywords']

    # Bypass attempt: disable guardian
    response = client.post(
        '/api/ai/check-prompt',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'service': 'claude',
            'domain': 'claude.ai',
            'prompt_text': 'How to disable guardian parental control on Linux?',
            'url': 'https://claude.ai/',
            'title': 'Claude'
        }),
        content_type='application/json'
    )
    assert response.status_code == 200

    log = AiPromptLog.query.order_by(AiPromptLog.id.desc()).first()
    assert log is not None
    assert log.status == 'Flagged'

def test_check_prompt_blocked_logging(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup
    settings.ai_policy_mode = 'block'
    db_session.commit()

    response = client.post(
        '/api/ai/check-prompt',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'service': 'chatgpt',
            'domain': 'chatgpt.com',
            'prompt_text': 'Hello AI',
            'url': 'https://chatgpt.com/',
            'title': 'ChatGPT'
        }),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['allowed'] is False

    # Blocked mode prompt should still be logged as status='Blocked'
    log = AiPromptLog.query.order_by(AiPromptLog.id.desc()).first()
    assert log is not None
    assert log.status == 'Blocked'

def test_log_session_success(client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup
    response = client.post(
        '/api/ai/log-session',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'domain': 'chatgpt.com',
            'duration_seconds': 45
        }),
        content_type='application/json'
    )
    assert response.status_code == 200
    assert response.get_json()['success'] is True

    record = AiSessionLog.query.filter_by(device_map_id=mapping.id).first()
    assert record is not None
    assert record.domain == 'chatgpt.com'
    assert record.duration_seconds == 45

def test_combined_history_endpoint(auth_client, ai_setup, db_session):
    user, device, mapping, settings = ai_setup

    prompt_log = AiPromptLog(
        device_map_id=mapping.id,
        service='chatgpt',
        domain='chatgpt.com',
        prompt_text='Hello AI test',
        prompt_length=13,
        url='https://chatgpt.com/',
        title='ChatGPT',
        status='Flagged',
        logged_at=datetime.now(timezone.utc)
    )
    session_log = AiSessionLog(
        device_map_id=mapping.id,
        domain='chatgpt.com',
        duration_seconds=120,
        logged_at=datetime.now(timezone.utc)
    )
    db_session.add_all([prompt_log, session_log])
    db_session.commit()

    response = auth_client.get(f'/api/user/{user.id}/history')
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    
    # Verify prompt exists in history
    history = res_data['data']['history']
    prompts = [item for item in history if item['type'] == 'ai_prompt']
    assert len(prompts) == 1
    assert prompts[0]['domain'] == 'chatgpt.com'
    assert prompts[0]['prompt_text'] == 'Hello AI test'
    assert prompts[0]['status'] == 'Flagged'

    # Verify analytics stats
    analytics = res_data['data']['analytics']
    assert analytics['total_ai_prompts'] == 1
    assert analytics['total_ai_seconds'] == 120
    assert len(analytics['ai_services']) == 1
    assert analytics['ai_services'][0]['service'] == 'chatgpt'
    assert analytics['ai_services'][0]['count'] == 1

def test_update_mapping_approval_settings(auth_client, ai_setup):
    user, device, mapping, settings = ai_setup
    
    response = auth_client.post(
        f'/api/mappings/{mapping.id}/approval-settings',
        data=json.dumps({
            'ai_policy_mode': 'monitor',
            'ai_prompt_logging': 'full_text',
            'ai_daily_time_limit': 15
        }),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    assert res_data['settings']['ai_policy_mode'] == 'monitor'
    assert res_data['settings']['ai_prompt_logging'] == 'full_text'
    assert res_data['settings']['ai_daily_time_limit'] == 15

    # Verify persistence
    updated = MappingApprovalSettings.query.filter_by(device_map_id=mapping.id).first()
    assert updated.ai_policy_mode == 'monitor'
    assert updated.ai_prompt_logging == 'full_text'
    assert updated.ai_daily_time_limit == 15
