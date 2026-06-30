"""Tests for WebSocket server configuration."""


def test_disable_permessage_deflate_handshake_is_idempotent():
    import simple_websocket.ws as sw_ws

    from src.common.websocket import disable_permessage_deflate_handshake

    disable_permessage_deflate_handshake()
    first = sw_ws.Server._handle_events
    disable_permessage_deflate_handshake()
    assert sw_ws.Server._handle_events is first
    assert sw_ws.Server._guardian_no_deflate is True
