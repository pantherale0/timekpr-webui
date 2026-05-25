import json
import pytest
import hmac
import hashlib
from src.agent_helper import AgentConnectionManager, AgentClient
from src.database import AgentDevice, db

class DummyWS:
    def __init__(self):
        self.sent_messages = []
        self.closed = False
        
    def send(self, message):
        self.sent_messages.append(message)
        # Parse and auto-respond to keep sync commands fast
        try:
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            if correlation_id:
                # Route response immediately
                AgentConnectionManager.route_response(correlation_id, {
                    "success": True,
                    "message": "Mocked Command Success",
                    "data": {"stdout": "stdout_val"}
                })
        except Exception:
            pass

    def close(self):
        self.closed = True

def test_connection_registry():
    ws = DummyWS()
    system_id = "test-system-uuid"
    remote_ip = "10.0.0.5"

    # Register active
    AgentConnectionManager.register(system_id, ws, remote_ip)
    assert AgentConnectionManager.is_online(system_id)
    assert AgentConnectionManager.get_connection(system_id) == ws
    assert AgentConnectionManager.get_ip(system_id) == remote_ip

    # Register pending
    ws_pending = DummyWS()
    AgentConnectionManager.register_pending(system_id, ws_pending)
    assert AgentConnectionManager.get_pending_connection(system_id) == ws_pending

    # Unregister pending
    AgentConnectionManager.unregister_pending(system_id)
    assert AgentConnectionManager.get_pending_connection(system_id) is None

    # Unregister active
    AgentConnectionManager.unregister(system_id)
    assert not AgentConnectionManager.is_online(system_id)
    assert AgentConnectionManager.get_connection(system_id) is None
    assert AgentConnectionManager.get_ip(system_id) == "Offline"

def test_route_response():
    # If correlation_id does not exist
    assert not AgentConnectionManager.route_response("nonexistent-cid", {})

def test_send_command_sync_failures():
    # Case: system_id is None or empty
    success, msg, data = AgentConnectionManager.send_command_sync(None, "action", "john")
    assert not success
    assert "No system ID" in msg

    # Case: agent is offline
    success, msg, data = AgentConnectionManager.send_command_sync("offline-sys", "action", "john")
    assert not success
    assert "is offline" in msg

def test_send_command_sync_success():
    ws = DummyWS()
    system_id = "online-sys"
    AgentConnectionManager.register(system_id, ws, "127.0.0.1")

    # Send command (auto-responded by DummyWS)
    success, msg, data = AgentConnectionManager.send_command_sync(system_id, "test_action", "john")
    assert success
    assert msg == "Mocked Command Success"
    assert data["stdout"] == "stdout_val"

    AgentConnectionManager.unregister(system_id)

def test_send_command_sync_socket_error():
    class ErrorWS:
        def send(self, message):
            raise Exception("Websocket socket send error")
            
    system_id = "error-sys"
    AgentConnectionManager.register(system_id, ErrorWS(), "127.0.0.1")

    success, msg, data = AgentConnectionManager.send_command_sync(system_id, "action", "john")
    assert not success
    assert "Failed to send command" in msg

    AgentConnectionManager.unregister(system_id)

def test_verify_signature(db_session):
    system_id = "device-uuid"
    challenge = "challenge-string"
    token = "device-secure-pairing-token"

    # Case: device not in DB
    assert not AgentConnectionManager.verify_signature(challenge, system_id, "sig")

    # Add device to DB but unapproved
    device = AgentDevice(system_id=system_id, secure_token=token, status="pending")
    db_session.add(device)
    db_session.commit()

    assert not AgentConnectionManager.verify_signature(challenge, system_id, "sig")

    # Approve device
    device.status = "approved"
    db_session.commit()

    # Calculate valid signature
    token_bytes = token.encode('utf-8')
    msg = (challenge + system_id).encode('utf-8')
    valid_sig = hmac.new(token_bytes, msg, hashlib.sha256).hexdigest()

    assert AgentConnectionManager.verify_signature(challenge, system_id, valid_sig)
    assert not AgentConnectionManager.verify_signature(challenge, system_id, "invalid-sig")
    
    # Exception handling
    # Cause a TypeError by passing None
    assert not AgentConnectionManager.verify_signature(None, system_id, None)

def test_agent_client(db_session):
    ws = DummyWS()
    system_id = "agent-client-sys"
    AgentConnectionManager.register(system_id, ws, "127.0.0.1")

    client = AgentClient(system_id=system_id)

    # Test validate_user
    # When agent helper fails, validate_user should return False
    # Let's temporarily unregister it to cause failure
    AgentConnectionManager.unregister(system_id)
    is_valid, msg, config = client.validate_user("john")
    assert not is_valid
    assert "offline" in msg

    # Re-register
    AgentConnectionManager.register(system_id, ws, "127.0.0.1")

    # Mock full timekpr output format
    # DummyWS send will trigger immediate routing of this stdout output
    stdout_output = "TIME_SPENT_DAY: 1200\nLIMIT: 3600\nENABLED: true\nLIST: a;b;c\n"
    # Customize DummyWS for this test
    class CustomWS:
        def send(self, message):
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            AgentConnectionManager.route_response(correlation_id, {
                "success": True,
                "message": "Success",
                "data": {"stdout": stdout_output}
            })
    
    AgentConnectionManager.register(system_id, CustomWS(), "127.0.0.1")
    is_valid, msg, config = client.validate_user("john")
    assert is_valid
    assert config["TIME_SPENT_DAY"] == 1200
    assert config["LIMIT"] == 3600
    assert config["ENABLED"] is True
    assert config["LIST"] == ["a", "b", "c"]

    # Test modify_time_left
    success, msg = client.modify_time_left("john", "+", 60)
    assert success

    # Test set_weekly_time_limits
    success, msg = client.set_weekly_time_limits("john", {"monday": 2.0})
    assert success

    # Test set_allowed_hours
    class MockInterval:
        def __init__(self, is_enabled, is_valid, format_val):
            self.is_enabled = is_enabled
            self.is_valid = is_valid
            self.format_val = format_val
            
        def is_valid_interval(self):
            return self.is_valid
            
        def to_timekpr_format(self):
            return self.format_val

    intervals = {
        1: MockInterval(True, True, ["9", "10"]),
        2: MockInterval(True, False, None),
        3: MockInterval(False, True, None)
    }
    success, msg = client.set_allowed_hours("john", intervals)
    assert success

    AgentConnectionManager.unregister(system_id)

def test_send_command_sync_timeout():
    ws = DummyWS()
    system_id = "timeout-sys"
    AgentConnectionManager.register(system_id, ws, "127.0.0.1")
    
    from unittest.mock import patch
    from queue import Empty
    with patch('queue.Queue.get', side_effect=Empty):
        success, msg, data = AgentConnectionManager.send_command_sync(system_id, "action", "john", timeout=0.01)
        assert not success
        assert "timed out" in msg

    AgentConnectionManager.unregister(system_id)

def test_agent_client_parser_missing_lines():
    ws = DummyWS()
    system_id = "parser-sys"
    client = AgentClient(system_id=system_id)

    stdout_output = "DIGITS: 1;2;3\nBOOL_F: false\n"
    class ParserWS:
        def send(self, message):
            payload = json.loads(message)
            correlation_id = payload.get("correlation_id")
            AgentConnectionManager.route_response(correlation_id, {
                "success": True,
                "message": "Success",
                "data": {"stdout": stdout_output}
            })
    AgentConnectionManager.register(system_id, ParserWS(), "127.0.0.1")
    is_valid, msg, config = client.validate_user("john")
    assert is_valid
    assert config["DIGITS"] == [1, 2, 3]
    assert config["BOOL_F"] is False

    class MockInterval:
        def __init__(self, is_enabled, is_valid, format_val):
            self.is_enabled = is_enabled
            self.is_valid = is_valid
            self.format_val = format_val
            
        def is_valid_interval(self):
            return self.is_valid
            
        def to_timekpr_format(self):
            return self.format_val

    intervals = {
        1: MockInterval(True, True, []),
    }
    success, msg = client.set_allowed_hours("john", intervals)
    assert success

    AgentConnectionManager.unregister(system_id)
