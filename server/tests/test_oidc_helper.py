"""Tests for the OIDC helper wrapper around discovery and token exchange."""

# pylint: disable=protected-access

from unittest.mock import MagicMock, patch

import pytest
import requests

from src.oidc_helper import OIDCHelper

def test_oidc_disabled():
    helper = OIDCHelper()
    assert not helper.is_enabled

    # If issuer_url is missing, discovery should raise ValueError
    with pytest.raises(ValueError, match="OIDC Issuer URL is not configured"):
        helper._fetch_discovery()

def test_oidc_enabled():
    with patch.dict('os.environ', {
        'OIDC_ISSUER_URL': 'https://auth.example.com',
        'OIDC_CLIENT_ID': 'my-client-id',
        'OIDC_CLIENT_SECRET': 'my-client-secret',
        'OIDC_VERIFY_SSL': 'false'
    }):
        helper = OIDCHelper()
        assert helper.is_enabled
        assert not helper.verify_ssl

def test_fetch_discovery_cached():
    helper = OIDCHelper()
    helper._endpoints = {'authorization_endpoint': 'https://auth.com/login'}
    assert helper._fetch_discovery() == {'authorization_endpoint': 'https://auth.com/login'}

@patch('requests.get')
def test_fetch_discovery_success(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        'authorization_endpoint': 'https://auth.com/login',
        'token_endpoint': 'https://auth.com/token',
        'userinfo_endpoint': 'https://auth.com/userinfo'
    }
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    helper = OIDCHelper()
    helper.issuer_url = "https://auth.com"
    
    endpoints = helper._fetch_discovery()
    assert endpoints['authorization_endpoint'] == 'https://auth.com/login'
    mock_get.assert_called_once_with("https://auth.com/.well-known/openid-configuration", verify=True, timeout=10)

@patch('requests.get')
def test_fetch_discovery_failure(mock_get):
    mock_get.side_effect = requests.RequestException("Network Connection Refused")

    helper = OIDCHelper()
    helper.issuer_url = "https://auth.com"

    with pytest.raises(RuntimeError, match="OIDC configuration error"):
        helper._fetch_discovery()

def test_get_authorization_url():
    helper = OIDCHelper()
    helper.issuer_url = "https://auth.com"
    helper._endpoints = {'authorization_endpoint': 'https://auth.com/login'}
    helper.client_id = "test-id"

    # Dynamic redirect
    url = helper.get_authorization_url("state-xyz", "http://app.com/callback")
    assert "https://auth.com/login" in url
    assert "client_id=test-id" in url
    assert "state=state-xyz" in url
    assert "redirect_uri=http%3A%2F%2Fapp.com%2Fcallback" in url

    # Redirect override
    helper.redirect_uri_override = "http://override.com"
    url_override = helper.get_authorization_url("state-xyz", "http://app.com/callback")
    assert "redirect_uri=http%3A%2F%2Foverride.com" in url_override

    # Missing authorization endpoint
    helper._endpoints = {}
    with pytest.raises(KeyError, match="authorization_endpoint"):
        helper.get_authorization_url("state-xyz", "http://app.com/callback")

@patch('requests.post')
def test_exchange_code(mock_post):
    mock_response = MagicMock()
    mock_response.json.return_value = {'access_token': 'token-123'}
    mock_response.raise_for_status.return_value = None
    mock_post.return_value = mock_response

    helper = OIDCHelper()
    helper.issuer_url = "https://auth.com"
    helper._endpoints = {'token_endpoint': 'https://auth.com/token'}
    helper.client_id = "test-client"
    helper.client_secret = "test-secret"

    tokens = helper.exchange_code("auth-code-val", "http://app.com/callback")
    assert tokens['access_token'] == 'token-123'
    mock_post.assert_called_once()

    # Missing token endpoint
    helper._endpoints = {}
    with pytest.raises(KeyError, match="token_endpoint"):
        helper.exchange_code("code", "redirect")

    # Post failure
    helper._endpoints = {'token_endpoint': 'https://auth.com/token'}
    mock_post.side_effect = requests.RequestException("Token server offline")
    with pytest.raises(RuntimeError, match="OIDC code exchange failed"):
        helper.exchange_code("code", "redirect")

@patch('requests.get')
def test_get_user_info(mock_get):
    mock_response = MagicMock()
    mock_response.json.return_value = {'sub': 'user-123', 'name': 'John Doe'}
    mock_response.raise_for_status.return_value = None
    mock_get.return_value = mock_response

    helper = OIDCHelper()
    helper.issuer_url = "https://auth.com"
    helper._endpoints = {'userinfo_endpoint': 'https://auth.com/userinfo'}

    info = helper.get_user_info("access-token-xyz")
    assert info['name'] == 'John Doe'
    mock_get.assert_called_once_with('https://auth.com/userinfo', headers={'Authorization': 'Bearer access-token-xyz'}, verify=True, timeout=10)

    # Missing userinfo endpoint
    helper._endpoints = {}
    with pytest.raises(KeyError, match="userinfo_endpoint"):
        helper.get_user_info("access-token")

    # Get failure
    helper._endpoints = {'userinfo_endpoint': 'https://auth.com/userinfo'}
    mock_get.side_effect = requests.RequestException("Userinfo server error")
    with pytest.raises(RuntimeError, match="OIDC user info retrieval failed"):
        helper.get_user_info("access-token")

def test_generate_state():
    state1 = OIDCHelper.generate_state()
    state2 = OIDCHelper.generate_state()
    assert len(state1) > 10
    assert state1 != state2
