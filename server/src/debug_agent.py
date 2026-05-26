"""Simple Python agent used to debug the server without a live TimeKpr client."""

from __future__ import annotations

import argparse
import copy
import hashlib
import hmac
import json
import logging
import os
import random
import socket
import time
import uuid
from pathlib import Path

LOGGER = logging.getLogger(__name__)

DEFAULT_SERVER_URL = "ws://127.0.0.1:5000/ws"
DEFAULT_AGENT_VERSION = os.environ.get("TIMEKPR_SERVER_VERSION", "v0.0.0-dev")
DEFAULT_CONFIG_NAME = "debug-agent.json"
DEFAULT_RECONNECT_DELAY_SECONDS = 2
DEFAULT_SOCKET_POLL_INTERVAL_SECONDS = 1
DEFAULT_SYNTHETIC_ACTIVITY_INTERVAL_SECONDS = 15
DEFAULT_TIME_LEFT_DAY = 2 * 60 * 60
DEFAULT_EMIT_STARTUP_ALERT_ON_AUTH = False
DEFAULT_SEND_POLICY_SYNC_CHECK_ON_AUTH = False
SECONDS_PER_HOUR = 60 * 60


def _json_clone(value):
    return copy.deepcopy(value)


def _full_access_day():
    return {
        str(hour): {
            "STARTMIN": 0,
            "ENDMIN": 60,
            "UACC": 0,
        }
        for hour in range(24)
    }


def _default_allowed_hours():
    return {str(day): _full_access_day() for day in range(1, 8)}


def _stable_source_revision(domains):
    normalized = [
        str(domain).strip().lower()
        for domain in list(domains or [])
        if str(domain).strip()
    ]
    digest = hashlib.sha256("\n".join(sorted(normalized)).encode("utf-8"))
    return digest.hexdigest()


def _coerce_non_negative_int(value, default):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= 0 else default


def _coerce_non_negative_float(value, default, minimum=0.0):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def _coerce_bool(value, default=False):
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _default_user_state(linux_uid, time_left_day):
    return {
        "linux_uid": linux_uid,
        "time_spent_day": 0,
        "time_left_day": time_left_day,
        "limit": time_left_day,
        "enabled": True,
        "allowed_days": ["1", "2", "3", "4", "5", "6", "7"],
        "allowed_hours": _default_allowed_hours(),
        "weekly_schedule": {},
        "domain_policy_source_ids": [],
        "apparmor_policies": [],
    }


def _default_seeded_users():
    alice = _default_user_state(1000, 5 * SECONDS_PER_HOUR)
    alice["time_spent_day"] = 45 * 60
    alice["limit"] = 6 * SECONDS_PER_HOUR
    alice["weekly_schedule"] = {
        "monday": 2.0,
        "tuesday": 2.0,
        "wednesday": 1.5,
        "thursday": 2.0,
        "friday": 3.0,
        "saturday": 4.0,
        "sunday": 4.0,
    }

    bob = _default_user_state(1001, 90 * 60)
    bob["time_spent_day"] = 30 * 60
    bob["limit"] = 2 * SECONDS_PER_HOUR
    bob["weekly_schedule"] = {
        "monday": 1.0,
        "tuesday": 1.0,
        "wednesday": 1.0,
        "thursday": 1.0,
        "friday": 1.5,
        "saturday": 2.0,
        "sunday": 2.0,
    }

    charlie = _default_user_state(1002, 40 * 60)
    charlie["time_spent_day"] = 70 * 60
    charlie["limit"] = 110 * 60
    charlie["weekly_schedule"] = {
        "monday": 1.0,
        "tuesday": 1.0,
        "wednesday": 1.0,
        "thursday": 1.0,
        "friday": 1.0,
        "saturday": 1.5,
        "sunday": 1.5,
    }

    return {
        "alice": alice,
        "bob": bob,
        "charlie": charlie,
    }


def _default_seed_alerts():
    return [
        {
            "event_type": "system_startup",
            "details": {
                "source": "python-debug-agent",
                "note": "Synthetic startup event for server debugging",
            },
        },
        {
            "event_type": "user_signed_in",
            "linux_username": "alice",
            "details": {
                "source": "gdm-password",
                "session_id": "debug-alice-session",
            },
        },
        {
            "event_type": "app_blocked",
            "linux_username": "bob",
            "details": {
                "application_name": "Discord",
                "executable_path": "/usr/bin/discord",
                "reason": "Synthetic policy block for UI testing",
            },
        },
        {
            "event_type": "app_usage",
            "linux_username": "charlie",
            "details": {
                "application_name": "Firefox",
                "executable_path": "/usr/bin/firefox",
                "duration_seconds": 1800,
            },
        },
    ]


def _default_config():
    return {
        "server_url": DEFAULT_SERVER_URL,
        "system_id": str(uuid.uuid4()),
        "system_hostname": socket.gethostname(),
        "registration_token": None,
        "agent_token": None,
        "agent_version": DEFAULT_AGENT_VERSION,
        "strict_users": False,
        "next_linux_uid": 1000,
        "default_time_left_day": DEFAULT_TIME_LEFT_DAY,
        "reconnect_delay_seconds": DEFAULT_RECONNECT_DELAY_SECONDS,
        "socket_poll_interval_seconds": DEFAULT_SOCKET_POLL_INTERVAL_SECONDS,
        "synthetic_activity_interval_seconds": DEFAULT_SYNTHETIC_ACTIVITY_INTERVAL_SECONDS,
        "emit_startup_alert_on_auth": DEFAULT_EMIT_STARTUP_ALERT_ON_AUTH,
        "send_policy_sync_check_on_auth": DEFAULT_SEND_POLICY_SYNC_CHECK_ON_AUTH,
        "seed_fake_users": True,
        "users": {},
        "seed_alerts_on_first_auth": True,
        "seed_alerts_sent": False,
        "seed_alerts": _default_seed_alerts(),
        "random_seed": None,
        "domain_policy_state": {
            "sources": {},
            "source_revisions": {},
            "policies": {},
            "last_sync_id": None,
        },
        "domain_policy_syncs": {},
        "apparmor_state": {},
    }


def normalize_config(config):
    normalized = _json_clone(config or {})
    defaults = _default_config()

    for key, value in defaults.items():
        normalized.setdefault(key, _json_clone(value))

    if not normalized.get("system_id"):
        normalized["system_id"] = str(uuid.uuid4())
    if not normalized.get("system_hostname"):
        normalized["system_hostname"] = socket.gethostname()
    if not normalized.get("server_url"):
        normalized["server_url"] = DEFAULT_SERVER_URL
    if not normalized.get("agent_version"):
        normalized["agent_version"] = DEFAULT_AGENT_VERSION

    normalized["strict_users"] = _coerce_bool(normalized.get("strict_users"), False)
    normalized["next_linux_uid"] = _coerce_non_negative_int(
        normalized.get("next_linux_uid"),
        1000,
    )
    normalized["default_time_left_day"] = _coerce_non_negative_int(
        normalized.get("default_time_left_day"),
        DEFAULT_TIME_LEFT_DAY,
    )
    normalized["reconnect_delay_seconds"] = _coerce_non_negative_int(
        normalized.get("reconnect_delay_seconds"),
        DEFAULT_RECONNECT_DELAY_SECONDS,
    )
    normalized["socket_poll_interval_seconds"] = _coerce_non_negative_float(
        normalized.get("socket_poll_interval_seconds"),
        DEFAULT_SOCKET_POLL_INTERVAL_SECONDS,
        minimum=0.2,
    )
    normalized["synthetic_activity_interval_seconds"] = _coerce_non_negative_float(
        normalized.get("synthetic_activity_interval_seconds"),
        DEFAULT_SYNTHETIC_ACTIVITY_INTERVAL_SECONDS,
        minimum=0.0,
    )
    normalized["emit_startup_alert_on_auth"] = _coerce_bool(
        normalized.get("emit_startup_alert_on_auth"),
        DEFAULT_EMIT_STARTUP_ALERT_ON_AUTH,
    )
    normalized["send_policy_sync_check_on_auth"] = _coerce_bool(
        normalized.get("send_policy_sync_check_on_auth"),
        DEFAULT_SEND_POLICY_SYNC_CHECK_ON_AUTH,
    )
    normalized["seed_fake_users"] = _coerce_bool(
        normalized.get("seed_fake_users"),
        True,
    )
    normalized["seed_alerts_on_first_auth"] = _coerce_bool(
        normalized.get("seed_alerts_on_first_auth"),
        True,
    )
    normalized["seed_alerts_sent"] = _coerce_bool(
        normalized.get("seed_alerts_sent"),
        False,
    )

    users = normalized.get("users")
    if not isinstance(users, dict):
        users = {}
    if not users and normalized["seed_fake_users"]:
        users = _default_seeded_users()
    normalized["users"] = users

    for username, state in list(users.items()):
        if not isinstance(state, dict):
            users[username] = _default_user_state(
                normalized["next_linux_uid"],
                normalized["default_time_left_day"],
            )
            normalized["next_linux_uid"] += 1
            continue

        state.setdefault("linux_uid", normalized["next_linux_uid"])
        state["linux_uid"] = _coerce_non_negative_int(
            state.get("linux_uid"),
            normalized["next_linux_uid"],
        )
        normalized["next_linux_uid"] = max(
            normalized["next_linux_uid"],
            state["linux_uid"] + 1,
        )
        state["time_spent_day"] = _coerce_non_negative_int(state.get("time_spent_day"), 0)
        default_time_left = normalized["default_time_left_day"]
        state["time_left_day"] = _coerce_non_negative_int(
            state.get("time_left_day"),
            default_time_left,
        )
        state["limit"] = _coerce_non_negative_int(
            state.get("limit"),
            state["time_left_day"],
        )
        state["enabled"] = _coerce_bool(state.get("enabled"), True)
        allowed_days = state.get("allowed_days")
        if not isinstance(allowed_days, list):
            allowed_days = ["1", "2", "3", "4", "5", "6", "7"]
        state["allowed_days"] = [str(day) for day in allowed_days]
        if not isinstance(state.get("weekly_schedule"), dict):
            state["weekly_schedule"] = {}
        if not isinstance(state.get("domain_policy_source_ids"), list):
            state["domain_policy_source_ids"] = []
        if not isinstance(state.get("apparmor_policies"), list):
            state["apparmor_policies"] = []
        if not isinstance(state.get("allowed_hours"), dict):
            state["allowed_hours"] = _default_allowed_hours()

    domain_policy_state = normalized.get("domain_policy_state")
    if not isinstance(domain_policy_state, dict):
        domain_policy_state = {}
    domain_policy_state.setdefault("sources", {})
    domain_policy_state.setdefault("source_revisions", {})
    domain_policy_state.setdefault("policies", {})
    domain_policy_state.setdefault("last_sync_id", None)
    normalized["domain_policy_state"] = domain_policy_state

    syncs = normalized.get("domain_policy_syncs")
    normalized["domain_policy_syncs"] = syncs if isinstance(syncs, dict) else {}

    apparmor_state = normalized.get("apparmor_state")
    normalized["apparmor_state"] = apparmor_state if isinstance(apparmor_state, dict) else {}

    seed_alerts = normalized.get("seed_alerts")
    normalized["seed_alerts"] = seed_alerts if isinstance(seed_alerts, list) else _default_seed_alerts()
    random_seed = normalized.get("random_seed")
    normalized["random_seed"] = random_seed if isinstance(random_seed, int) else None

    return normalized


def load_config(config_path):
    path = Path(config_path)
    if path.exists():
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    else:
        raw = {}
    return normalize_config(raw)


def save_config(config_path, config):
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(config, handle, indent=2, sort_keys=True)
        handle.write("\n")


class DebugAgentProtocol:
    """In-memory agent protocol implementation used by the CLI wrapper and tests."""

    def __init__(self, config):
        self.config = normalize_config(config)
        self.random = random.Random(self.config.get("random_seed"))
        self.authenticated = False
        self.last_synthetic_activity_at = 0.0

    def build_hello_message(self):
        return {
            "type": "hello",
            "system_id": self.config["system_id"],
            "system_hostname": self.config.get("system_hostname"),
            "registration_token": self.config.get("registration_token"),
            "agent_version": self.config["agent_version"],
        }

    def build_alert_event(self, event_type, linux_username=None, details=None, occurred_at=None):
        return {
            "type": "alert_event",
            "event_type": event_type,
            "occurred_at": occurred_at or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "linux_username": linux_username,
            "details": details or {},
        }

    def build_policy_sync_check(self):
        state = self.config["domain_policy_state"]
        return {
            "type": "policy_sync_check",
            "source_revisions": _json_clone(state.get("source_revisions", {})),
        }

    def handle_server_message(self, message):
        msg_type = message.get("type")
        result = {
            "outbound_messages": [],
            "config_changed": False,
            "reconnect": False,
        }

        if msg_type == "pairing_status":
            LOGGER.info("Pairing status: %s", message.get("status"))
            return result

        if msg_type == "pairing_approved":
            token = message.get("token")
            if not token:
                raise ValueError("pairing_approved did not include a token")
            self.config["agent_token"] = token
            result["config_changed"] = True
            result["reconnect"] = True
            LOGGER.info("Pairing approved. Stored new device token and reconnecting.")
            return result

        if msg_type == "challenge":
            result["outbound_messages"].append(self._build_register_message(message))
            return result

        if msg_type == "auth_result":
            success = bool(message.get("success"))
            if success:
                self.authenticated = True
                self.last_synthetic_activity_at = time.monotonic()
                LOGGER.info("Authenticated successfully: %s", message.get("message", ""))
                seeded_alerts = self._consume_seed_alerts()
                if seeded_alerts:
                    result["outbound_messages"].extend(seeded_alerts)
                    result["config_changed"] = True
                if self.config["emit_startup_alert_on_auth"]:
                    result["outbound_messages"].append(
                        self.build_alert_event(
                            "system_startup",
                            details={"source": "python-debug-agent"},
                        )
                    )
                if self.config["send_policy_sync_check_on_auth"]:
                    result["outbound_messages"].append(self.build_policy_sync_check())
            else:
                self.authenticated = False
                LOGGER.warning("Authentication failed: %s", message.get("message", ""))
            return result

        if msg_type == "policy_sync_hint":
            LOGGER.info("Received policy sync hint: %s", message.get("reason"))
            result["outbound_messages"].append(self.build_policy_sync_check())
            return result

        if msg_type == "command_request":
            response, changed = self._handle_command_request(message)
            result["outbound_messages"].append(response)
            result["config_changed"] = changed
            return result

        LOGGER.warning("Ignoring unsupported server message: %s", msg_type)
        return result

    def build_periodic_activity(self, now_monotonic=None):
        if not self.authenticated:
            return [], False

        interval = self.config.get("synthetic_activity_interval_seconds", 0.0)
        if interval <= 0:
            return [], False

        now = time.monotonic() if now_monotonic is None else now_monotonic
        if now - self.last_synthetic_activity_at < interval:
            return [], False

        self.last_synthetic_activity_at = now
        payload, changed = self._build_random_agent_message()
        if payload is None:
            return [], changed
        return [payload], changed

    def _consume_seed_alerts(self):
        if not self.config.get("seed_alerts_on_first_auth"):
            return []
        if self.config.get("seed_alerts_sent"):
            return []

        payloads = []
        for alert in self.config.get("seed_alerts") or []:
            if not isinstance(alert, dict):
                continue
            event_type = (alert.get("event_type") or "").strip()
            if not event_type:
                continue
            payloads.append(
                self.build_alert_event(
                    event_type,
                    linux_username=alert.get("linux_username"),
                    details=alert.get("details"),
                    occurred_at=alert.get("occurred_at"),
                )
            )
        self.config["seed_alerts_sent"] = True
        return payloads

    def _build_random_agent_message(self):
        if self.random.random() < 0.25:
            return self.build_policy_sync_check(), False

        generator = self.random.choice([
            self._random_system_event,
            self._random_session_event,
            self._random_app_blocked_event,
            self._random_app_usage_event,
        ])
        return generator()

    def _random_username(self):
        usernames = sorted(self.config["users"])
        if not usernames:
            return None
        return self.random.choice(usernames)

    def _random_system_event(self):
        event_type = self.random.choice([
            "system_sleep",
            "system_resume",
            "system_restart",
        ])
        return (
            self.build_alert_event(
                event_type,
                details={
                    "source": "python-debug-agent",
                    "note": "Periodic synthetic device event",
                },
            ),
            False,
        )

    def _random_session_event(self):
        username = self._random_username()
        if not username:
            return self._random_system_event()

        event_type = self.random.choice(["user_signed_in", "user_signed_out"])
        return (
            self.build_alert_event(
                event_type,
                linux_username=username,
                details={
                    "source": "python-debug-agent",
                    "session_id": f"{username}-{int(time.time())}",
                },
            ),
            False,
        )

    def _random_app_blocked_event(self):
        username = self._random_username()
        if not username:
            return self._random_system_event()

        application_name, executable_path = self.random.choice([
            ("Discord", "/usr/bin/discord"),
            ("Steam", "/usr/bin/steam"),
            ("Firefox", "/usr/bin/firefox"),
            ("Chromium", "/usr/bin/chromium"),
        ])
        return (
            self.build_alert_event(
                "app_blocked",
                linux_username=username,
                details={
                    "application_name": application_name,
                    "executable_path": executable_path,
                    "reason": "Periodic synthetic block event",
                },
            ),
            False,
        )

    def _random_app_usage_event(self):
        username = self._random_username()
        if not username:
            return self._random_system_event()

        user_state = self._ensure_user(username)
        if user_state is None:
            return self._random_system_event()

        application_name, executable_path = self.random.choice([
            ("Firefox", "/usr/bin/firefox"),
            ("LibreOffice Writer", "/usr/bin/libreoffice"),
            ("VLC", "/usr/bin/vlc"),
            ("Minecraft", "/usr/bin/minecraft-launcher"),
        ])
        duration_seconds = self.random.choice([120, 300, 600, 900, 1200])
        user_state["time_spent_day"] += duration_seconds
        user_state["time_left_day"] = max(user_state["time_left_day"] - duration_seconds, 0)
        end_time = time.time()
        start_time = end_time - duration_seconds
        return (
            self.build_alert_event(
                "app_usage",
                linux_username=username,
                details={
                    "application_name": application_name,
                    "executable_path": executable_path,
                    "duration_seconds": duration_seconds,
                    "start_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_time)),
                    "end_time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(end_time)),
                },
            ),
            True,
        )

    def _build_register_message(self, message):
        challenge = message.get("challenge")
        token = self.config.get("agent_token")
        if not challenge:
            raise ValueError("challenge message did not include a challenge value")
        if not token:
            raise ValueError("agent_token is required to answer an authentication challenge")

        digest = hmac.new(
            token.encode("utf-8"),
            f"{challenge}{self.config['system_id']}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "type": "register",
            "system_id": self.config["system_id"],
            "signature": digest,
        }

    def _ensure_user(self, username):
        if not username:
            return None

        users = self.config["users"]
        if username not in users:
            if self.config["strict_users"]:
                return None
            users[username] = _default_user_state(
                self.config["next_linux_uid"],
                self.config["default_time_left_day"],
            )
            self.config["next_linux_uid"] += 1
        return users[username]

    def _user_config_payload(self, username, state):
        return {
            "USERNAME": username,
            "LINUX_UID": state["linux_uid"],
            "TIME_SPENT_DAY": state["time_spent_day"],
            "TIME_LEFT_DAY": state["time_left_day"],
            "LIMIT": state["limit"],
            "ENABLED": state["enabled"],
            "ALLOWED_DAYS": list(state["allowed_days"]),
            "WEEKLY_SCHEDULE": _json_clone(state["weekly_schedule"]),
            "ALLOWED_HOURS": _json_clone(state["allowed_hours"]),
            "DOMAIN_POLICY_SOURCE_IDS": list(state["domain_policy_source_ids"]),
            "APPARMOR_POLICY_COUNT": len(state["apparmor_policies"]),
        }

    def _handle_command_request(self, message):
        correlation_id = message.get("correlation_id")
        action = message.get("action") or ""
        username = message.get("username") or ""
        args = message.get("args") or {}

        success, response_message, data, changed = self._handle_command(action, username, args)
        return (
            {
                "type": "command_response",
                "correlation_id": correlation_id,
                "success": success,
                "message": response_message,
                "data": data,
            },
            changed,
        )

    def _handle_command(self, action, username, args):
        if action == "validate_user":
            user_state = self._ensure_user(username)
            if user_state is None:
                return False, f"Unknown user '{username}'", {}, False
            return (
                True,
                "User validated successfully",
                {"config": self._user_config_payload(username, user_state)},
                True,
            )

        if action == "modify_time_left":
            user_state = self._ensure_user(username)
            if user_state is None:
                return False, f"Unknown user '{username}'", {}, False

            operation = args.get("operation", "+")
            seconds = _coerce_non_negative_int(args.get("seconds"), 0)
            if operation == "+":
                user_state["time_left_day"] += seconds
            elif operation == "-":
                user_state["time_left_day"] = max(user_state["time_left_day"] - seconds, 0)
            else:
                return False, f"Unsupported modify_time_left operation '{operation}'", {}, False

            return (
                True,
                f"Adjusted remaining time for {username} by {operation}{seconds} seconds",
                {},
                True,
            )

        if action == "set_weekly_time_limits":
            user_state = self._ensure_user(username)
            if user_state is None:
                return False, f"Unknown user '{username}'", {}, False

            schedule = args.get("schedule")
            if not isinstance(schedule, dict):
                return False, "Missing 'schedule' argument", {}, False

            user_state["weekly_schedule"] = _json_clone(schedule)
            today_name = time.strftime("%A", time.gmtime()).lower()
            current_day_hours = schedule.get(today_name)
            if current_day_hours is not None:
                limit_seconds = int(float(current_day_hours) * SECONDS_PER_HOUR)
                user_state["limit"] = max(limit_seconds, 0)
                user_state["time_left_day"] = min(
                    user_state["time_left_day"],
                    user_state["limit"],
                )
            return True, "Weekly time limits configured successfully", {}, True

        if action == "set_allowed_hours":
            user_state = self._ensure_user(username)
            if user_state is None:
                return False, f"Unknown user '{username}'", {}, False

            intervals = args.get("intervals")
            if not isinstance(intervals, dict):
                return False, "Missing 'intervals' argument", {}, False

            normalized = _default_allowed_hours()
            for day, hours in intervals.items():
                if isinstance(hours, dict):
                    normalized[str(day)] = _json_clone(hours)
            user_state["allowed_hours"] = normalized
            return True, "Allowed hours updated", {}, True

        if action == "sync_domain_policy":
            return self._sync_domain_policy_immediately(args)

        if action == "get_domain_policy_state":
            state = self.config["domain_policy_state"]
            return (
                True,
                "Fetched domain policy state",
                {
                    "source_revisions": _json_clone(state["source_revisions"]),
                    "policy_count": len(state["policies"]),
                    "source_count": len(state["sources"]),
                    "last_sync_id": state["last_sync_id"],
                },
                False,
            )

        if action == "begin_domain_policy_sync":
            sync_id = str(args.get("sync_id") or "").strip()
            if not sync_id:
                return False, "Missing 'sync_id' argument", {}, False
            current_state = self.config["domain_policy_state"]
            self.config["domain_policy_syncs"][sync_id] = {
                "sources": {
                    source_id: {
                        "revision": current_state["source_revisions"].get(source_id, ""),
                        "domains": list(domains),
                    }
                    for source_id, domains in current_state["sources"].items()
                },
                "policies": _json_clone(current_state["policies"]),
            }
            return True, f"Started domain policy sync {sync_id}", {}, True

        if action == "delete_domain_policy_sources":
            sync_id = str(args.get("sync_id") or "").strip()
            source_ids = args.get("source_ids") or []
            session = self.config["domain_policy_syncs"].get(sync_id)
            if session is None:
                return False, f"Unknown sync_id '{sync_id}'", {}, False
            for source_id in source_ids:
                session["sources"].pop(str(source_id), None)
            return True, "Deleted domain policy sources", {}, True

        if action == "sync_domain_policy_chunk":
            sync_id = str(args.get("sync_id") or "").strip()
            source_id = str(args.get("source_id") or "").strip()
            revision = str(args.get("revision") or "").strip()
            domains = args.get("domains") or []
            session = self.config["domain_policy_syncs"].get(sync_id)
            if session is None:
                return False, f"Unknown sync_id '{sync_id}'", {}, False
            if not source_id:
                return False, "Missing 'source_id' argument", {}, False

            source_entry = session["sources"].setdefault(
                source_id,
                {"revision": revision, "domains": []},
            )
            source_entry["revision"] = revision or source_entry.get("revision", "")
            source_entry["domains"].extend(
                str(domain).strip().lower()
                for domain in list(domains)
                if str(domain).strip()
            )
            source_entry["domains"] = sorted(set(source_entry["domains"]))
            return True, "Accepted domain policy chunk", {}, True

        if action == "update_domain_policy_manifest":
            sync_id = str(args.get("sync_id") or "").strip()
            policies = args.get("policies") or {}
            session = self.config["domain_policy_syncs"].get(sync_id)
            if session is None:
                return False, f"Unknown sync_id '{sync_id}'", {}, False
            if not isinstance(policies, dict):
                return False, "Missing 'policies' argument", {}, False
            session["policies"] = _json_clone(policies)
            return True, "Updated domain policy manifest", {}, True

        if action == "finalize_domain_policy_sync":
            sync_id = str(args.get("sync_id") or "").strip()
            session = self.config["domain_policy_syncs"].pop(sync_id, None)
            if session is None:
                return False, f"Unknown sync_id '{sync_id}'", {}, False

            state = self.config["domain_policy_state"]
            state["sources"] = {
                source_id: list(source_entry["domains"])
                for source_id, source_entry in session["sources"].items()
            }
            state["source_revisions"] = {
                source_id: (
                    source_entry.get("revision")
                    or _stable_source_revision(source_entry["domains"])
                )
                for source_id, source_entry in session["sources"].items()
            }
            state["policies"] = _json_clone(session["policies"])
            state["last_sync_id"] = sync_id
            self._apply_domain_policy_to_users()
            return True, "Finalized domain policy sync", {}, True

        if action == "abort_domain_policy_sync":
            sync_id = str(args.get("sync_id") or "").strip()
            self.config["domain_policy_syncs"].pop(sync_id, None)
            return True, f"Aborted domain policy sync {sync_id}", {}, True

        if action == "sync_apparmor_policy":
            policies = args.get("policies")
            if not isinstance(policies, list):
                return False, "Missing 'policies' argument", {}, False

            user_state = self._ensure_user(username)
            if user_state is None:
                return False, f"Unknown user '{username}'", {}, False

            user_state["apparmor_policies"] = _json_clone(policies)
            self.config["apparmor_state"][username] = _json_clone(policies)
            return True, f"Stored {len(policies)} AppArmor policies", {}, True

        return False, f"Unknown action '{action}'", {}, False

    def _sync_domain_policy_immediately(self, args):
        sources = args.get("sources")
        policies = args.get("policies")
        if not isinstance(sources, dict):
            return False, "Missing 'sources' argument", {}, False
        if not isinstance(policies, dict):
            return False, "Missing 'policies' argument", {}, False

        state = self.config["domain_policy_state"]
        state["sources"] = {
            str(source_id): sorted(
                set(
                    str(domain).strip().lower()
                    for domain in list(domains or [])
                    if str(domain).strip()
                )
            )
            for source_id, domains in sources.items()
        }
        state["source_revisions"] = {
            source_id: _stable_source_revision(domains)
            for source_id, domains in state["sources"].items()
        }
        state["policies"] = _json_clone(policies)
        state["last_sync_id"] = "direct-sync"
        self._apply_domain_policy_to_users()
        return True, "Domain policy synchronized", {}, True

    def _apply_domain_policy_to_users(self):
        policies = self.config["domain_policy_state"]["policies"]
        for policy in policies.values():
            if not isinstance(policy, dict):
                continue
            username = (policy.get("linux_username") or "").strip()
            if not username:
                continue
            user_state = self._ensure_user(username)
            if user_state is None:
                continue
            source_ids = policy.get("source_ids") or []
            user_state["domain_policy_source_ids"] = [str(source_id) for source_id in source_ids]


def _get_websocket_module():
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError(
            "websocket-client is required to run the Python debug agent. "
            "Install it with 'pip install -r server/requirements.txt'."
        ) from exc
    return websocket


def _create_connection(websocket_module, server_url, timeout_seconds):
    return websocket_module.create_connection(server_url, timeout=timeout_seconds)


def _recv_json(ws):
    raw_message = ws.recv()
    if not raw_message:
        raise RuntimeError("websocket closed")
    return json.loads(raw_message)


def _send_json(ws, payload):
    ws.send(json.dumps(payload))


def _config_path_from_default():
    return Path(__file__).resolve().parent.parent / DEFAULT_CONFIG_NAME


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Run a lightweight Python agent that mimics the TimeKpr client "
            "protocol so the server can be debugged without a live machine."
        )
    )
    parser.add_argument(
        "--config",
        default=str(_config_path_from_default()),
        help="Path to the JSON config/state file to create or update.",
    )
    parser.add_argument("--server-url", help="Override the configured WebSocket URL.")
    parser.add_argument("--system-id", help="Override the configured system_id.")
    parser.add_argument("--system-hostname", help="Override the reported hostname.")
    parser.add_argument("--registration-token", help="Override the registration token.")
    parser.add_argument("--agent-token", help="Override the pairing token.")
    parser.add_argument(
        "--agent-version",
        help="Version string reported to the server. It must match the server version.",
    )
    parser.add_argument(
        "--strict-users",
        action="store_true",
        help="Require users to be predeclared in the config instead of auto-creating them.",
    )
    parser.add_argument(
        "--emit-startup-alert",
        action="store_true",
        help="Send a system_startup alert after a successful authentication.",
    )
    parser.add_argument(
        "--policy-sync-check-on-auth",
        action="store_true",
        help="Send a policy_sync_check message immediately after authentication.",
    )
    parser.add_argument(
        "--activity-interval",
        type=float,
        help="Seconds between periodic synthetic agent messages. Set to 0 to disable.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Exit after the first disconnect instead of reconnecting.",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        help="Python log level, for example DEBUG or INFO.",
    )
    return parser.parse_args(argv)


def apply_cli_overrides(config, args):
    overridden = _json_clone(config)
    if args.server_url:
        overridden["server_url"] = args.server_url
    if args.system_id:
        overridden["system_id"] = args.system_id
    if args.system_hostname:
        overridden["system_hostname"] = args.system_hostname
    if args.registration_token is not None:
        overridden["registration_token"] = args.registration_token
    if args.agent_token is not None:
        overridden["agent_token"] = args.agent_token
    if args.agent_version:
        overridden["agent_version"] = args.agent_version
    if args.strict_users:
        overridden["strict_users"] = True
    if args.emit_startup_alert:
        overridden["emit_startup_alert_on_auth"] = True
    if args.policy_sync_check_on_auth:
        overridden["send_policy_sync_check_on_auth"] = True
    if args.activity_interval is not None:
        overridden["synthetic_activity_interval_seconds"] = args.activity_interval
    return normalize_config(overridden)


def run(argv=None):
    args = parse_args(argv)
    websocket_module = _get_websocket_module()
    logging.basicConfig(
        level=getattr(logging, str(args.log_level).upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    config_path = Path(args.config)
    config = apply_cli_overrides(load_config(config_path), args)
    save_config(config_path, config)
    LOGGER.info(
        "Starting Python debug agent for %s at %s",
        config["system_id"],
        config["server_url"],
    )

    while True:
        protocol = DebugAgentProtocol(load_config(config_path))
        ws = None
        try:
            ws = _create_connection(
                websocket_module,
                protocol.config["server_url"],
                timeout_seconds=protocol.config["socket_poll_interval_seconds"],
            )
            _send_json(ws, protocol.build_hello_message())
            LOGGER.info("Connected. Waiting for server messages.")

            while True:
                try:
                    message = _recv_json(ws)
                    result = protocol.handle_server_message(message)
                    if result["config_changed"]:
                        save_config(config_path, protocol.config)
                    for payload in result["outbound_messages"]:
                        _send_json(ws, payload)
                    if result["reconnect"]:
                        break
                except websocket_module.WebSocketTimeoutException:
                    payloads, changed = protocol.build_periodic_activity()
                    if changed:
                        save_config(config_path, protocol.config)
                    for payload in payloads:
                        _send_json(ws, payload)
        except KeyboardInterrupt:
            LOGGER.info("Stopping debug agent.")
            return 0
        except Exception as exc:  # pylint: disable=broad-except
            LOGGER.warning("Debug agent connection loop ended: %s", exc)
        finally:
            if ws is not None:
                try:
                    ws.close()
                except Exception:  # pylint: disable=broad-except
                    LOGGER.debug("Failed to close websocket cleanly", exc_info=True)

        if args.once:
            return 0

        delay = max(protocol.config.get("reconnect_delay_seconds", 0), 0)
        LOGGER.info("Reconnecting in %s second(s).", delay)
        time.sleep(delay)


def main():
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
