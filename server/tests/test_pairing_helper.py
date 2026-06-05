"""Tests for agent pairing QR helpers."""

import json

import pytest

from src.pairing_helper import (
    PAIRING_PAYLOAD_TYPE,
    build_pairing_payload,
    pairing_payload_json,
    render_pairing_qr_png,
)


def test_build_pairing_payload_includes_registration_token():
    payload = build_pairing_payload('wss://example.com/ws', 'secret-token')
    assert payload['type'] == PAIRING_PAYLOAD_TYPE
    assert payload['server_url'] == 'wss://example.com/ws'
    assert payload['registration_token'] == 'secret-token'


def test_pairing_payload_json_roundtrip():
    raw = pairing_payload_json('ws://127.0.0.1:5000/ws')
    parsed = json.loads(raw)
    assert parsed['type'] == PAIRING_PAYLOAD_TYPE
    assert parsed['server_url'].startswith('ws://')


def test_render_pairing_qr_png():
    png = render_pairing_qr_png(pairing_payload_json('ws://localhost/ws'))
    assert png[:8] == b'\x89PNG\r\n\x1a\n'


def test_build_agent_websocket_url_from_request(app):
    with app.test_request_context('/', base_url='https://timekpr.example'):
        from flask import request

        from src.pairing_helper import build_agent_websocket_url

        assert build_agent_websocket_url(request) == 'wss://timekpr.example/ws'


def test_build_agent_websocket_url_explicit_override(app):
    with app.test_request_context('/'):
        from flask import request

        from src.pairing_helper import build_agent_websocket_url

        assert build_agent_websocket_url(request, explicit_url='wss://custom/ws') == 'wss://custom/ws'
