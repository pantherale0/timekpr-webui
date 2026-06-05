"""Helpers for generating Android/Linux agent pairing payloads and QR codes."""

from __future__ import annotations

import base64
import io
import json
import os
from urllib.parse import urlparse, urlunparse

PAIRING_PAYLOAD_TYPE = 'timekpr_pairing'


def _normalize_ws_path(path: str | None) -> str:
    normalized = (path or '').strip() or '/ws'
    if not normalized.startswith('/'):
        normalized = f'/{normalized}'
    return normalized


def build_agent_websocket_url(request, explicit_url: str | None = None) -> str:
    """Build the WebSocket URL agents should connect to."""
    if explicit_url:
        candidate = explicit_url.strip()
        if candidate:
            return candidate

    configured = (os.environ.get('TIMEKPR_AGENT_WS_URL') or '').strip()
    if configured:
        return configured

    scheme = 'wss' if request.is_secure else 'ws'
    host = (request.host or 'localhost').strip()
    path = _normalize_ws_path('/ws')
    return urlunparse((scheme, host, path, '', '', ''))


def build_pairing_payload(server_url: str, registration_token: str | None = None) -> dict:
    payload = {
        'type': PAIRING_PAYLOAD_TYPE,
        'server_url': server_url.strip(),
    }
    if registration_token:
        payload['registration_token'] = registration_token.strip()
    return payload


def pairing_payload_json(server_url: str, registration_token: str | None = None) -> str:
    return json.dumps(build_pairing_payload(server_url, registration_token), sort_keys=True)


def render_pairing_qr_png(payload_json: str, box_size: int = 8, border: int = 2) -> bytes:
    """Render a QR code PNG for the pairing payload."""
    import qrcode

    qr = qrcode.QRCode(box_size=box_size, border=border)
    qr.add_data(payload_json)
    qr.make(fit=True)
    image = qr.make_image(fill_color='black', back_color='white')
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()


def render_pairing_qr_data_uri(payload_json: str) -> str:
    png_bytes = render_pairing_qr_png(payload_json)
    encoded = base64.b64encode(png_bytes).decode('ascii')
    return f'data:image/png;base64,{encoded}'
