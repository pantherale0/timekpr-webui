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
