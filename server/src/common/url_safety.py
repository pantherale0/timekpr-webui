"""Validate outbound HTTP(S) URLs to mitigate SSRF."""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_HOSTNAMES = frozenset({
    'localhost',
    'localhost.localdomain',
    'metadata.google.internal',
})


def _ip_is_blocked(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def is_safe_outbound_url(url: str) -> bool:
    """Return True when *url* resolves only to routable public addresses."""
    parsed = urlparse((url or '').strip())
    if parsed.scheme not in {'http', 'https'}:
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    hostname_lower = hostname.lower().rstrip('.')
    if hostname_lower in _BLOCKED_HOSTNAMES:
        return False

    try:
        if _ip_is_blocked(hostname_lower):
            return False
    except ValueError:
        pass

    try:
        port = parsed.port or (443 if parsed.scheme == 'https' else 80)
        for _, _, _, _, sockaddr in socket.getaddrinfo(hostname_lower, port):
            if _ip_is_blocked(sockaddr[0]):
                return False
    except OSError:
        return False

    return True


def validate_safe_outbound_url(url: str) -> str:
    """Normalize and validate an outbound URL, raising ValueError when unsafe."""
    normalized = (url or '').strip()
    if not normalized:
        raise ValueError('URL is required')
    if not is_safe_outbound_url(normalized):
        raise ValueError(
            'URL must resolve to a public address; private or internal hosts are not allowed'
        )
    return normalized


def safe_requests_get(
    url: str,
    *,
    max_hops: int = 5,
    timeout: int = 10,
    headers=None,
    stream: bool = False,
    **kwargs,
):
    """Perform GET with manual redirect handling and per-hop SSRF validation."""
    import requests
    from urllib.parse import urljoin

    kwargs.pop('allow_redirects', None)
    current_url = validate_safe_outbound_url(url)
    response = None
    hops = 0

    while hops < max_hops:
        response = requests.get(
            current_url,
            headers=headers,
            timeout=timeout,
            stream=stream,
            allow_redirects=False,
            **kwargs,
        )
        if response.status_code in (301, 302, 303, 307, 308):
            redirect_url = response.headers.get('Location')
            if not redirect_url:
                break
            current_url = validate_safe_outbound_url(urljoin(current_url, redirect_url))
            hops += 1
            continue
        return response

    raise ValueError('Too many redirects during outbound request')


def safe_requests_post(
    url: str,
    *,
    max_hops: int = 5,
    timeout: int = 5,
    headers=None,
    data=None,
    **kwargs,
):
    """Perform POST without following redirects (webhooks should not redirect)."""
    import requests

    kwargs.pop('allow_redirects', None)
    validated_url = validate_safe_outbound_url(url)
    response = requests.post(
        validated_url,
        headers=headers,
        data=data,
        timeout=timeout,
        allow_redirects=False,
        **kwargs,
    )
    if response.status_code in (301, 302, 303, 307, 308):
        raise ValueError('Outbound POST redirects are not allowed')
    return response
