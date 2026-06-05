"""Tests for the lightweight Python debug agent protocol."""

import hashlib
import hmac

from src.debug_agent import DebugAgentProtocol, normalize_config


def _command_request(correlation_id, action, username="", args=None):
    return {
        "type": "command_request",
        "correlation_id": correlation_id,
        "action": action,
        "username": username,
        "args": args or {},
    }


def test_debug_agent_handles_pairing_and_challenge_messages():
    config = normalize_config(
        {
            "system_id": "debug-system",
            "agent_token": "secret-token",
            "agent_version": "v0.10",
        }
    )
    protocol = DebugAgentProtocol(config)

    result = protocol.handle_server_message(
        {
            "type": "challenge",
            "challenge": "challenge-value",
        }
    )
    assert not result["config_changed"]
    assert not result["reconnect"]
    assert len(result["outbound_messages"]) == 1

    register_message = result["outbound_messages"][0]
    expected_signature = hmac.new(
        b"secret-token",
        b"challenge-valuedebug-system",
        hashlib.sha256,
    ).hexdigest()
    assert register_message["type"] == "register"
    assert register_message["system_id"] == "debug-system"
    assert register_message["signature"] == expected_signature

    pairing_result = protocol.handle_server_message(
        {
            "type": "pairing_approved",
            "token": "new-device-token",
        }
    )
    assert pairing_result["config_changed"]
    assert pairing_result["reconnect"]
    assert protocol.config["agent_token"] == "new-device-token"


def test_normalize_config_seeds_fake_users_by_default():
    protocol = DebugAgentProtocol(
        {
            "system_id": "debug-system",
            "agent_version": "v0.10",
        }
    )

    assert set(protocol.config["users"]) == {"alice", "bob", "charlie"}
    assert protocol.config["users"]["alice"]["linux_uid"] == 1000
    assert protocol.config["users"]["charlie"]["time_spent_day"] == 70 * 60


def test_debug_agent_auto_creates_users_and_tracks_updates():
    protocol = DebugAgentProtocol(
        {
            "system_id": "debug-system",
            "agent_version": "v0.10",
            "seed_fake_users": False,
        }
    )

    validate_result = protocol.handle_server_message(
        _command_request("cid-1", "validate_user", username="alice")
    )
    validate_response = validate_result["outbound_messages"][0]
    assert validate_result["config_changed"]
    assert validate_response["success"] is True
    assert validate_response["data"]["config"]["LINUX_UID"] == 1000
    assert validate_response["data"]["config"]["TIME_LEFT_DAY"] == 7200

    modify_result = protocol.handle_server_message(
        _command_request(
            "cid-2",
            "modify_time_left",
            username="alice",
            args={"operation": "-", "seconds": 600},
        )
    )
    modify_response = modify_result["outbound_messages"][0]
    assert modify_result["config_changed"]
    assert modify_response["success"] is True

    hours_result = protocol.handle_server_message(
        _command_request(
            "cid-3",
            "set_allowed_hours",
            username="alice",
            args={
                "intervals": {
                    "1": {
                        "9": {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0},
                    }
                }
            },
        )
    )
    assert hours_result["outbound_messages"][0]["success"] is True
    assert protocol.config["users"]["alice"]["allowed_hours"]["1"]["9"] == {
        "STARTMIN": 0,
        "ENDMIN": 60,
        "UACC": 0,
    }

    revalidate_result = protocol.handle_server_message(
        _command_request("cid-4", "validate_user", username="alice")
    )
    revalidate_response = revalidate_result["outbound_messages"][0]
    assert revalidate_response["data"]["config"]["TIME_LEFT_DAY"] == 6600


def test_debug_agent_emits_seed_alerts_only_once():
    protocol = DebugAgentProtocol(
        {
            "system_id": "debug-system",
            "agent_version": "v0.10",
            "seed_alerts": [
                {
                    "event_type": "user_signed_in",
                    "linux_username": "alice",
                    "details": {"source": "test"},
                }
            ],
            "send_installed_apps_on_auth": False,
        }
    )

    first_result = protocol.handle_server_message(
        {
            "type": "auth_result",
            "success": True,
            "message": "Authenticated successfully",
        }
    )
    assert first_result["config_changed"]
    assert [payload["type"] for payload in first_result["outbound_messages"]] == ["alert_event"]
    assert protocol.config["seed_alerts_sent"] is True

    second_result = protocol.handle_server_message(
        {
            "type": "auth_result",
            "success": True,
            "message": "Authenticated successfully",
        }
    )
    assert not second_result["config_changed"]
    assert second_result["outbound_messages"] == []


def test_debug_agent_builds_periodic_activity_without_reconnecting():
    protocol = DebugAgentProtocol(
        {
            "system_id": "debug-system",
            "agent_version": "v0.10",
            "random_seed": 7,
            "synthetic_activity_interval_seconds": 5,
        }
    )

    protocol.handle_server_message(
        {
            "type": "auth_result",
            "success": True,
            "message": "Authenticated successfully",
        }
    )

    baseline = protocol.last_synthetic_activity_at

    payloads, changed = protocol.build_periodic_activity(now_monotonic=baseline + 3)
    assert payloads == []
    assert changed is False

    payloads, changed = protocol.build_periodic_activity(now_monotonic=baseline + 10)
    assert len(payloads) == 1
    assert payloads[0]["type"] in {"alert_event", "policy_sync_check"}
    assert isinstance(changed, bool)


def test_debug_agent_supports_incremental_domain_policy_sync():
    protocol = DebugAgentProtocol(
        {
            "system_id": "debug-system",
            "agent_version": "v0.10",
        }
    )

    begin_result = protocol.handle_server_message(
        _command_request(
            "cid-1",
            "begin_domain_policy_sync",
            args={"sync_id": "sync-1"},
        )
    )
    assert begin_result["outbound_messages"][0]["success"] is True

    chunk_result = protocol.handle_server_message(
        _command_request(
            "cid-2",
            "sync_domain_policy_chunk",
            args={
                "sync_id": "sync-1",
                "source_id": "1",
                "revision": "rev-1",
                "domains": ["dns.google", "example.com"],
            },
        )
    )
    assert chunk_result["outbound_messages"][0]["success"] is True

    manifest_result = protocol.handle_server_message(
        _command_request(
            "cid-3",
            "update_domain_policy_manifest",
            args={
                "sync_id": "sync-1",
                "policies": {
                    "1000": {
                        "linux_username": "alice",
                        "source_ids": ["1"],
                    }
                },
            },
        )
    )
    assert manifest_result["outbound_messages"][0]["success"] is True

    finalize_result = protocol.handle_server_message(
        _command_request(
            "cid-4",
            "finalize_domain_policy_sync",
            args={"sync_id": "sync-1"},
        )
    )
    assert finalize_result["outbound_messages"][0]["success"] is True
    assert protocol.config["domain_policy_state"]["source_revisions"] == {"1": "rev-1"}
    assert protocol.config["users"]["alice"]["domain_policy_source_ids"] == ["1"]

    state_result = protocol.handle_server_message(
        _command_request("cid-5", "get_domain_policy_state")
    )
    state_response = state_result["outbound_messages"][0]
    assert state_response["success"] is True
    assert state_response["data"]["source_revisions"] == {"1": "rev-1"}
