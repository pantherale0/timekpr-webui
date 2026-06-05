"""API tests for pairing QR endpoints."""

import pytest


@pytest.fixture
def auth_client(client):
    with client.session_transaction() as sess:
        sess['logged_in'] = True
    return client


def test_pairing_config_requires_auth(client):
    response = client.get('/api/pairing/config')
    assert response.status_code == 401


def test_pairing_config_authenticated(auth_client):
    response = auth_client.get('/api/pairing/config')
    assert response.status_code == 200
    payload = response.get_json()
    assert payload['success'] is True
    assert payload['payload']['type'] == 'timekpr_pairing'
    assert payload['payload']['server_url'].endswith('/ws')


def test_pairing_qr_png_authenticated(auth_client):
    response = auth_client.get('/api/pairing/qr.png')
    assert response.status_code == 200
    assert response.mimetype == 'image/png'
    assert response.data[:8] == b'\x89PNG\r\n\x1a\n'
