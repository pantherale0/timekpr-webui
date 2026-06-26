"""Tests for unified video history monitoring endpoints and workers."""
import json
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
import pytest
from src.models import VideoHistory, WebHistory, ManagedUser, AgentDevice, ManagedUserDeviceMap


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def yt_setup(db_session):
    user = ManagedUser(username='yt_child', system_ip='Unassigned', is_valid=True)
    db_session.add(user)
    db_session.flush()

    device = AgentDevice(
        system_id='yt-test-device',
        system_hostname='yt-test-pc',
        status='approved',
        secure_token='yt-test-token',
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
    db_session.commit()

    return user, device, mapping


def test_log_video_history_requires_token(client):
    response = client.post(
        '/api/video/log',
        data=json.dumps({
            'linux_username': 'child_os_user',
            'platform': 'youtube',
            'logs': [],
        }),
        content_type='application/json',
    )
    assert response.status_code == 401


def test_log_youtube_history_alias_success(client, yt_setup, db_session):
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
                'watched_at': '2026-06-15T12:00:00Z',
            }
        ],
    }
    response = client.post(
        '/api/youtube/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert response.status_code == 200
    res_data = response.get_json()
    assert res_data['success'] is True
    assert res_data['count'] == 1

    records = VideoHistory.query.filter_by(managed_user_id=user.id).all()
    assert len(records) == 1
    assert records[0].platform == VideoHistory.VIDEO_PLATFORM_YOUTUBE
    assert records[0].video_id == 'dQw4w9WgXcQ'
    assert records[0].category == 'Unknown'


def test_log_tiktok_history_success(client, yt_setup, db_session):
    user, device, _ = yt_setup
    payload = {
        'linux_username': 'child_os_user',
        'platform': 'tiktok',
        'logs': [
            {
                'video_id': '7123456789012345678',
                'title': 'Funny clip',
                'channel_name': 'creator',
                'channel_id': '@creator',
                'duration_seconds': 45,
                'watched_at': '2026-06-15T12:00:00Z',
            }
        ],
    }
    response = client.post(
        '/api/video/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert response.status_code == 200

    record = VideoHistory.query.filter_by(
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_TIKTOK,
    ).first()
    assert record is not None
    assert record.video_id == '7123456789012345678'
    assert record.category == 'TikTok'


def test_log_video_history_unsupported_platform(client, yt_setup):
    _, device, _ = yt_setup
    response = client.post(
        '/api/video/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps({
            'linux_username': 'child_os_user',
            'platform': 'vimeo',
            'logs': [],
        }),
        content_type='application/json',
    )
    assert response.status_code == 400


def test_get_extension_update_manifest(client):
    response = client.get('/api/extensions/update')
    assert response.status_code == 200
    assert response.mimetype == 'application/xml'
    assert 'updatecheck' in response.data.decode('utf-8')


def test_get_extension_update_manifest_uses_packaged_version(client, tmp_path, monkeypatch):
    extensions_dir = tmp_path / "extensions"
    extensions_dir.mkdir()
    (extensions_dir / "extension_version.txt").write_text("2.3.4\n", encoding="utf-8")

    monkeypatch.setattr(
        "src.blueprints.api.video.current_app.static_folder",
        str(tmp_path),
    )

    response = client.get('/api/extensions/update')
    assert response.status_code == 200
    assert "version='2.3.4'" in response.data.decode('utf-8')


@patch('src.blueprints.api.video.os.path.exists', return_value=False)
def test_download_extension_missing(mock_exists, client):
    response = client.get('/api/extensions/download')
    assert response.status_code == 404


def test_get_user_youtube_history_success(auth_client, yt_setup, db_session):
    user, device, _ = yt_setup
    h1 = VideoHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
        video_id='vid1',
        title='Title One',
        channel_name='Channel A',
        duration_seconds=120,
        category='Gaming',
        watched_at=datetime.now(timezone.utc),
    )
    h2 = VideoHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_TIKTOK,
        video_id='7123456789012345678',
        title='TikTok clip',
        channel_name='Creator B',
        duration_seconds=30,
        category='TikTok',
        watched_at=datetime.now(timezone.utc),
    )
    db_session.add_all([h1, h2])
    db_session.commit()

    response = auth_client.get(f'/api/user/{user.id}/youtube')
    assert response.status_code == 200
    data = response.get_json()['data']
    assert len(data['history']) == 1
    assert data['history'][0]['video_id'] == 'vid1'


@patch('src.common.tasks.requests.get')
def test_category_fetcher_worker_task(mock_get, yt_setup, db_session):
    user, device, _ = yt_setup
    h = VideoHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
        video_id='target_vid',
        title='Target Title',
        category='Unknown',
        watched_at=datetime.now(timezone.utc),
    )
    db_session.add(h)
    db_session.commit()

    with patch('src.common.settings._get_youtube_api_key', return_value='dummy-key'):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            'items': [
                {
                    'id': 'target_vid',
                    'snippet': {'categoryId': '20'},
                }
            ]
        }
        mock_get.return_value = mock_response

        from src.common.tasks import BackgroundTaskManager
        manager = BackgroundTaskManager()
        manager._fetch_youtube_categories()

        db_session.expire_all()
        refreshed = VideoHistory.query.filter_by(video_id='target_vid').first()
        assert refreshed.category == 'Gaming'


def test_prune_video_history_worker_task(yt_setup, db_session):
    user, device, _ = yt_setup
    with patch('src.common.settings._get_video_history_retention_days', return_value=2):
        h_recent = VideoHistory(
            device_id=device.system_id,
            managed_user_id=user.id,
            platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
            video_id='recent',
            title='Recent Video',
            watched_at=datetime.now(timezone.utc) - timedelta(hours=12),
        )
        h_old = VideoHistory(
            device_id=device.system_id,
            managed_user_id=user.id,
            platform=VideoHistory.VIDEO_PLATFORM_TIKTOK,
            video_id='7123456789012345678',
            title='Old TikTok',
            watched_at=datetime.now(timezone.utc) - timedelta(days=4),
        )
        db_session.add_all([h_recent, h_old])
        db_session.commit()

        from src.common.tasks import BackgroundTaskManager
        manager = BackgroundTaskManager()
        manager._prune_video_history()

        db_session.expire_all()
        assert VideoHistory.query.filter_by(video_id='recent').first() is not None
        assert VideoHistory.query.filter_by(video_id='7123456789012345678').first() is None


def test_get_user_combined_history_success(auth_client, yt_setup, db_session):
    user, device, _ = yt_setup

    h1 = VideoHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_YOUTUBE,
        video_id='vid1',
        title='Youtube Video Title',
        channel_name='Channel A',
        duration_seconds=120,
        category='Gaming',
        watched_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    h2 = VideoHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        platform=VideoHistory.VIDEO_PLATFORM_TIKTOK,
        video_id='7123456789012345678',
        title='TikTok Video Title',
        channel_name='Creator C',
        duration_seconds=20,
        category='TikTok',
        watched_at=datetime.now(timezone.utc) - timedelta(minutes=30),
    )
    w1 = WebHistory(
        device_id=device.system_id,
        managed_user_id=user.id,
        url='https://wikipedia.org/wiki/Special',
        title='Wikipedia Page',
        domain='wikipedia.org',
        visited_at=datetime.now(timezone.utc),
    )

    db_session.add_all([h1, h2, w1])
    db_session.commit()

    response = auth_client.get(f'/api/user/{user.id}/history')
    assert response.status_code == 200
    data = response.get_json()['data']
    assert len(data['history']) == 3
    assert data['history'][0]['type'] == 'web'

    response_tiktok = auth_client.get(f'/api/user/{user.id}/history?type=tiktok')
    assert response_tiktok.status_code == 200
    tiktok_history = response_tiktok.get_json()['data']['history']
    assert len(tiktok_history) == 1
    assert tiktok_history[0]['type'] == 'tiktok'
    assert tiktok_history[0]['platform'] == 'tiktok'

    analytics = data['analytics']
    assert analytics['total_video_count'] == 2
    assert analytics['total_web_visits'] == 1


def test_log_web_history_success(client, yt_setup, db_session):
    user, device, _ = yt_setup
    payload = {
        'linux_username': 'child_os_user',
        'logs': [
            {
                'url': 'https://google.com/search?q=test',
                'title': 'Google Search',
                'domain': 'google.com',
                'visited_at': '2026-06-15T12:00:00Z',
            }
        ],
    }
    response = client.post(
        '/api/browser/log',
        headers={'Authorization': f'Bearer {device.secure_token}'},
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert response.status_code == 200
    records = WebHistory.query.filter_by(managed_user_id=user.id).all()
    assert len(records) == 1
