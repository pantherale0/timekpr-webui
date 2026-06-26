"""API tests for Android device restriction policies."""

import io
import struct
import zipfile

import pytest

from src.models import AgentDevice, ManagedUser, ManagedUserDeviceMap


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


@pytest.fixture
def android_policy_fixture(db_session):
    device = AgentDevice(
        system_id='sys-api-android-policy',
        status='approved',
        secure_token='token',
        platform='android',
    )
    user = ManagedUser(username='api-android-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-android-policy',
        linux_username='android',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()
    return {'user': user, 'mapping': mapping}


def test_get_android_device_policy_requires_auth(client, android_policy_fixture):
    mapping_id = android_policy_fixture['mapping'].id
    response = client.get(f'/api/mappings/{mapping_id}/android-device-policy')
    assert response.status_code == 401


def test_get_android_device_policy_defaults(auth_client, android_policy_fixture):
    system_id = android_policy_fixture['mapping'].system_id
    response = auth_client.get(f'/api/devices/{system_id}/android-device-policy')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['camera_access'] == 'CAMERA_ACCESS_UNSPECIFIED'
    assert payload['policy']['device_policy']['screenCaptureDisabled'] is False


def test_put_android_device_policy(auth_client, android_policy_fixture, monkeypatch):
    system_id = android_policy_fixture['mapping'].system_id
    monkeypatch.setattr(
        'src.policy.android.push_device_policy',
        lambda device: (False, 'Agent offline'),
    )
    response = auth_client.put(
        f'/api/devices/{system_id}/android-device-policy',
        json={
            'screen_capture_disabled': True,
            'camera_access': 'CAMERA_ACCESS_DISABLED',
            'install_apps_disabled': True,
            'developer_settings': 'DEVELOPER_SETTINGS_DISABLED',
        },
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['policy']['screen_capture_disabled'] is True
    assert payload['policy']['is_synced'] is False
    assert 'sync pending' in payload['message'].lower()


def test_put_rejects_linux_mapping(auth_client, db_session):
    device = AgentDevice(system_id='sys-api-linux-policy', status='approved', secure_token='token')
    user = ManagedUser(username='api-linux-child', system_ip='Unassigned', is_valid=True)
    db_session.add_all([device, user])
    db_session.flush()
    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id='sys-api-linux-policy',
        linux_username='child',
        is_valid=True,
    )
    db_session.add(mapping)
    db_session.commit()

    response = auth_client.put(
        f'/api/mappings/{mapping.id}/android-device-policy',
        json={'screen_capture_disabled': True},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False


def create_dummy_axml(package_name: str) -> bytes:
    strings = ["manifest", "package", package_name]
    str_data = b""
    offsets = []
    for s in strings:
        offsets.append(len(str_data))
        s_bytes = s.encode('utf-8')
        length_char = len(s)
        length_bytes = len(s_bytes)
        str_data += bytes([length_char, length_bytes]) + s_bytes + b"\x00"
        
    string_count = len(strings)
    string_offset = 28 + string_count * 4
    string_pool_size = string_offset + len(str_data)
    
    if string_pool_size % 4 != 0:
        padding = 4 - (string_pool_size % 4)
        str_data += b"\x00" * padding
        string_pool_size += padding
        
    string_pool_chunk = struct.pack(
        '<IIIII', string_count, 0, 256, string_offset, 0
    )
    for off in offsets:
        string_pool_chunk += struct.pack('<I', off)
    string_pool_chunk += str_data
    
    manifest_chunk = struct.pack(
        '<IIIIIIHHHHHH',
        0x00100102, 56, 1, 0xFFFFFFFF, 0xFFFFFFFF, 0,
        20, 20, 1, 0, 0, 0
    )
    manifest_chunk += struct.pack(
        '<iiiii',
        -1, 1, 2, 0, 0
    )
    
    file_size = 8 + 8 + len(string_pool_chunk) + len(manifest_chunk)
    header = struct.pack('<II', 0x00080003, file_size)
    
    return header + struct.pack('<II', 0x001C0001, 8 + len(string_pool_chunk)) + string_pool_chunk + manifest_chunk


def create_mock_apk(package_name: str) -> bytes:
    axml_bytes = create_dummy_axml(package_name)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        z.writestr("AndroidManifest.xml", axml_bytes)
    return out.getvalue()


def test_validate_apk_url_success(auth_client, android_policy_fixture, requests_mock):
    system_id = android_policy_fixture['mapping'].system_id
    apk_content = create_mock_apk("com.example.testapp")
    requests_mock.get("https://example.com/app.apk", content=apk_content)
    
    response = auth_client.post(
        f'/api/devices/{system_id}/validate-apk-url',
        json={'apk_url': 'https://example.com/app.apk'},
    )
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['package_name'] == "com.example.testapp"
    assert len(payload['sha256_checksum']) == 64


def test_validate_apk_url_requires_auth(client, android_policy_fixture):
    system_id = android_policy_fixture['mapping'].system_id
    response = client.post(
        f'/api/devices/{system_id}/validate-apk-url',
        json={'apk_url': 'https://example.com/app.apk'},
    )
    assert response.status_code == 401


def test_validate_apk_url_unsafe_url(auth_client, android_policy_fixture):
    system_id = android_policy_fixture['mapping'].system_id
    response = auth_client.post(
        f'/api/devices/{system_id}/validate-apk-url',
        json={'apk_url': 'https://127.0.0.1/app.apk'},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert "private" in payload['message'] or "internal" in payload['message']


def test_validate_apk_url_too_large(auth_client, android_policy_fixture, requests_mock):
    system_id = android_policy_fixture['mapping'].system_id
    requests_mock.get(
        "https://example.com/huge.apk",
        headers={"Content-Length": str(101 * 1024 * 1024)},
        content=b"some bytes"
    )
    response = auth_client.post(
        f'/api/devices/{system_id}/validate-apk-url',
        json={'apk_url': 'https://example.com/huge.apk'},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert "exceeds" in payload['message']


def test_validate_apk_url_invalid_zip(auth_client, android_policy_fixture, requests_mock):
    system_id = android_policy_fixture['mapping'].system_id
    requests_mock.get("https://example.com/corrupt.apk", content=b"invalid zip file bytes")
    response = auth_client.post(
        f'/api/devices/{system_id}/validate-apk-url',
        json={'apk_url': 'https://example.com/corrupt.apk'},
    )
    assert response.status_code == 400
    payload = response.get_json()
    assert payload['success'] is False
    assert "not a valid ZIP" in payload['message'] or "BadZipFile" in payload['message'] or "Invalid APK" in payload['message']
