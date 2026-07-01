"""Tests for outbound URL SSRF protections."""

from unittest.mock import MagicMock, patch

import pytest

from src.common.url_safety import is_safe_outbound_url, validate_safe_outbound_url


def test_validate_safe_outbound_url_accepts_public_host():
    with patch('src.common.url_safety.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('93.184.216.34', 0))]):
        assert validate_safe_outbound_url('https://example.com/list.txt') == 'https://example.com/list.txt'


@pytest.mark.parametrize('url', [
    'http://127.0.0.1/blocklist.txt',
    'http://localhost/admin',
    'http://169.254.169.254/latest/meta-data',
    'http://10.0.0.5/internal',
    'ftp://example.com/file',
    'not-a-url',
])
def test_validate_safe_outbound_url_rejects_unsafe_targets(url):
    with pytest.raises(ValueError):
        validate_safe_outbound_url(url)


def test_is_safe_outbound_url_rejects_private_dns_resolution():
    with patch('src.common.url_safety.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('10.0.0.12', 0))]):
        assert not is_safe_outbound_url('https://public.example.com/list.txt')


def test_safe_requests_post_rejects_redirect_response():
    from src.common.url_safety import safe_requests_post

    redirect_response = MagicMock()
    redirect_response.status_code = 302
    redirect_response.headers = {'Location': 'https://other.example/hook'}

    with patch('src.common.url_safety.validate_safe_outbound_url', return_value='https://hooks.example/alert'):
        with patch('requests.post', return_value=redirect_response):
            with pytest.raises(ValueError, match='redirects are not allowed'):
                safe_requests_post('https://hooks.example/alert', data='{}')

