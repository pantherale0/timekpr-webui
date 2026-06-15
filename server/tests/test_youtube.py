"""Tests for YouTube History monitoring endpoints and workers."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest
from src.database import YoutubeHistory, ManagedUser, AgentDevice, ManagedUserDeviceMap

@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client

@pytest.fixture
def yt_setup(db_session):
    user = ManagedUser(username='yt_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush() # Populate ID

    device = AgentDevice(
        system_id='yt-test-device',
        system_hostname='yt-test-pc',
        status='approved',
        secure_token='yt-test-token',
        platform='linux'
    )
    db_session.add(device)
    db_session.flush()

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=device.system_id,
        linux_username='child_os_user'
    )
    db_session.add(mapping)
    db_session.commit()

    return user, device, mapping

def test_log_youtube_history_requires_token(client):
    response = client.post(
        '/api/youtube/log',
        data=json.dumps({'linux_username': 'child_os_user', 'logs': []}),
        content_type='application/json'
    )
    assert response.status_code == 401

def test_log_youtube_history_invalid_token(client):
    response = client.post(
        '/api/youtube/log',
        headers={'Authorization': 'Bearer bad-token'},
        data=json.dumps({'linux_username': 'child_os_user', 'logs': []}),
        content_type='application/json'
    )
    assert response.status_code == 401

def test_log_youtube_history_missing_username(client, yt_setup):
    _, device, _ = yt_setup
    response = client.post(
        '/api/youtube/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'logs': []}),
        content_type='application/json'
    )
    assert response.status_code == 400

def test_log_youtube_history_no_mapping(client, yt_setup):
    _, device, _ = yt_setup
    response = client.post(
        '/api/youtube/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({'linux_username': 'unknown_user', 'logs': []}),
        content_type='application/json'
    )
    assert response.status_code == 400

def test_log_youtube_history_success(client, yt_setup, db_session):
    user, device, _ = yt_setup
    payload = {
        'linux_username': 'child_os_user',
        'logs': [
            {
                'video_id': 'dQw4w9WgXcQ',
                'title': 'Never Gonna Give You Up',
                'channel_name': 'Rick Astley',
                'channel_id': 'UCuAXFUrEPWy',
                'duration_seconds': 212,
                'watched_at': '2026-06-15T12:00:00Z'
            }
        ]
    }
    response = client.post(
        '/api/youtube/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps(payload),
        content_type='application/json'
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    assert res_data['count'] == 1

    records = YoutubeHistory.query.filter_by(managed_user_id=user.id).all()
    assert len(records) == 1
    assert records[0].video_id == 'dQw4w9WgXcQ'
    assert records[0].title == 'Never Gonna Give You Up'
    assert records[0].channel_name == 'Rick Astley'
    assert records[0].category == 'Unknown'

def test_get_extension_update_manifest(client):
    response = client.get('/api/extensions/update')
    assert response.status_code == 200
    assert response.mimetype == 'application/xml'
    xml_text = response.data.decode('utf-8')
    assert 'updatecheck' in xml_text


def test_get_extension_update_manifest_uses_packaged_version(client, tmp_path, monkeypatch):
    extensions_dir = tmp_path / "extensions"
    extensions_dir.mkdir()
    (extensions_dir / "extension_version.txt").write_text("2.3.4\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.blueprints.api.youtube.current_app.static_folder",
        str(tmp_path),
    )

    response = client.get('/api/extensions/update')
    assert response.status_code == 200
    assert "version='2.3.4'" in response.data.decode('utf-8')

@patch('src.blueprints.api.youtube.os.path.exists', return_value=False)
def test_download_extension_missing(mock_exists, client):
    response = client.get('/api/extensions/download')
    assert response.status_code == 404

def test_get_user_youtube_history_unauthorized(client, yt_setup):
    user, _, _ = yt_setup
    response = client.get(f'/api/user/{user.id}/youtube')
    assert response.status_code == 401

def test_get_user_youtube_history_success(auth_client, yt_setup, db_session):
    user, device, _ = yt_setup
    h1 = YoutubeHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        video_id='vid1',
        title='Title One',
        channel_name='Channel A',
        duration_seconds=120,
        category='Gaming',
        watched_at=datetime.now(timezone.utc)
    )
    h2 = YoutubeHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        video_id='vid2',
        title='Title Two',
        channel_name='Channel B',
        duration_seconds=300,
        category='Education',
        watched_at=datetime.now(timezone.utc) - timedelta(days=1)
    )
    db_session.add_all([h1, h2])
    db_session.commit()

    response = auth_client.get(f'/api/user/{user.id}/youtube')
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    
    data = res_data['data']
    assert len(data['history']) == 2
    assert 'Gaming' in data['distinct_categories']
    assert 'Education' in data['distinct_categories']
    
    analytics = data['analytics']
    assert analytics['total_seconds'] == 420
    assert analytics['total_videos'] == 2

@patch('src.task_manager.requests.get')
def test_category_fetcher_worker_task(mock_get, yt_setup, db_session):
    user, device, _ = yt_setup
    h = YoutubeHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        video_id='target_vid',
        title='Target Title',
        category='Unknown',
        watched_at=datetime.now(timezone.utc)
    )
    db_session.add(h)
    db_session.commit()

    with patch('src.settings_manager._get_youtube_api_key', return_value='dummy-key'):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'items': [
                {
                    'id': 'target_vid',
                    'snippet': {'categoryId': '20'} # Gaming
                }
            ]
        }
        mock_get.return_value = mock_response

        from src.task_manager import BackgroundTaskManager
        manager = BackgroundTaskManager()
        manager._fetch_youtube_categories()

        db_session.expire_all()
        refreshed = YoutubeHistory.query.filter_by(video_id='target_vid').first()
        assert refreshed.category == 'Gaming'

def test_prune_youtube_history_worker_task(yt_setup, db_session):
    user, device, _ = yt_setup
    with patch('src.settings_manager._get_youtube_history_retention_days', return_value=2):
        h_recent = YoutubeHistory(
            device_id=device.system_id,
            managed_user_id=user.id,
            video_id='recent',
            title='Recent Video',
            watched_at=datetime.now(timezone.utc) - timedelta(hours=12)
        )
        h_old = YoutubeHistory(
            device_id=device.system_id,
            managed_user_id=user.id,
            video_id='old',
            title='Old Video',
            watched_at=datetime.now(timezone.utc) - timedelta(days=4)
        )
        db_session.add_all([h_recent, h_old])
        db_session.commit()

        from src.task_manager import BackgroundTaskManager
        manager = BackgroundTaskManager()
        manager._prune_youtube_history()

        db_session.expire_all()
        assert YoutubeHistory.query.filter_by(video_id='recent').first() is not None
        assert YoutubeHistory.query.filter_by(video_id='old').first() is None
