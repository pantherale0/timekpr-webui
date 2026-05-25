import uuid
import json
import logging
import hmac
import hashlib
import os
import re
from datetime import datetime, timezone
from queue import Queue, Empty

logger = logging.getLogger(__name__)

# Optional registration token firewall for new dynamic pairings
REGISTRATION_TOKEN = os.environ.get('REGISTRATION_TOKEN')

ALLOWED_AGENT_ALERT_TYPES = {
    'system_startup',
    'system_sleep',
    'system_resume',
    'system_restart',
    'system_shutdown',
    'user_signed_in',
    'user_signed_out',
}


def _coerce_alert_string(value, field_name, max_length, allow_empty=False):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{field_name} must be a string')

    normalized = value.strip()
    if not normalized and not allow_empty:
        raise ValueError(f'{field_name} must not be empty')
    if len(normalized) > max_length:
        raise ValueError(f'{field_name} exceeds maximum length of {max_length}')
    return normalized


def parse_agent_alert_timestamp(value):
    if not isinstance(value, str):
        raise ValueError('occurred_at must be an ISO-8601 string')

    normalized = value.strip()
    if not normalized:
        raise ValueError('occurred_at must not be empty')

    if normalized.endswith('Z'):
        normalized = normalized[:-1] + '+00:00'

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise ValueError('occurred_at must be a valid ISO-8601 timestamp') from exc

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)

    return parsed.replace(tzinfo=None)


def normalize_agent_alert_payload(system_id, payload):
    if not isinstance(payload, dict):
        raise ValueError('alert payload must be an object')

    event_type = _coerce_alert_string(payload.get('event_type'), 'event_type', 64)
    if event_type not in ALLOWED_AGENT_ALERT_TYPES:
        raise ValueError(f'Unsupported event_type: {event_type}')

    linux_username = _coerce_alert_string(
        payload.get('linux_username'),
        'linux_username',
        80,
    )
    details = payload.get('details', {})
    if details is None:
        details = {}
    if not isinstance(details, dict):
        raise ValueError('details must be an object')

    occurred_at = parse_agent_alert_timestamp(payload.get('occurred_at'))
    normalized_payload = {
        'system_id': system_id,
        'event_type': event_type,
        'occurred_at': occurred_at.isoformat() + 'Z',
        'linux_username': linux_username,
        'details': details,
    }
    return {
        'system_id': system_id,
        'event_type': event_type,
        'occurred_at': occurred_at,
        'linux_username': linux_username,
        'details': details,
        'payload_json': json.dumps(normalized_payload, sort_keys=True),
    }

class AgentConnectionManagerMeta(type):
    @property
    def REGISTRATION_TOKEN(cls):
        return REGISTRATION_TOKEN

class AgentConnectionManager(metaclass=AgentConnectionManagerMeta):
    # Active connections (fully approved & authenticated): system_id -> ws_object
    active_connections = {}
    
    # Pending connections (unapproved devices): system_id -> ws_object
    pending_connections = {}
    
    # Dynamic IP mapping: system_id -> remote_ip
    active_ips = {}
    
    # Thread-safe pending requests: correlation_id -> Queue
    pending_requests = {}
    
    @classmethod
    def register(cls, system_id, ws, remote_ip):
        """Register an active WebSocket connection and snapshot its current IP"""
        cls.active_connections[system_id] = ws
        cls.active_ips[remote_ip] = system_id
        cls.active_connections[system_id + "_ip"] = remote_ip
        logger.info(f"Agent registered: {system_id} from IP {remote_ip}")
        
    @classmethod
    def unregister(cls, system_id):
        """Unregister an active connection"""
        if system_id in cls.active_connections:
            ip = cls.active_connections.get(system_id + "_ip")
            if ip in cls.active_ips:
                del cls.active_ips[ip]
            if system_id + "_ip" in cls.active_connections:
                del cls.active_connections[system_id + "_ip"]
            del cls.active_connections[system_id]
            logger.info(f"Agent unregistered: {system_id}")

    @classmethod
    def register_pending(cls, system_id, ws):
        """Register an active connection in a pending state"""
        cls.pending_connections[system_id] = ws
        logger.info(f"Agent registered in PENDING state: {system_id}")

    @classmethod
    def unregister_pending(cls, system_id):
        """Unregister a pending connection"""
        if system_id in cls.pending_connections:
            del cls.pending_connections[system_id]
            logger.info(f"Agent PENDING connection removed: {system_id}")

    @classmethod
    def get_pending_connection(cls, system_id):
        """Get the active pending connection for a system_id"""
        return cls.pending_connections.get(system_id)
        
    @classmethod
    def get_connection(cls, system_id):
        """Get the active WebSocket connection for a system_id"""
        return cls.active_connections.get(system_id)
        
    @classmethod
    def is_online(cls, system_id):
        """Check if a system_id is currently online"""
        return system_id in cls.active_connections
 
    @classmethod
    def get_ip(cls, system_id):
        """Get the last snapshotted IP address for a system_id"""
        return cls.active_connections.get(system_id + "_ip", "Offline")
 
    @classmethod
    def route_response(cls, correlation_id, response_data):
        """Route a response message from client back to the waiting thread"""
        if correlation_id in cls.pending_requests:
            cls.pending_requests[correlation_id].put(response_data)
            return True
        return False

    @classmethod
    def send_command_sync(cls, system_id, action, username, args=None, timeout=15):
        """
        Send a command to the client and wait synchronously for the response.
        Thread-safe and block-based.
        """
        if not system_id:
            return False, "No system ID associated with this user", None

        ws = cls.get_connection(system_id)
        if not ws:
            return False, f"Agent for system ID '{system_id}' is offline", None

        correlation_id = str(uuid.uuid4())
        response_queue = Queue()
        cls.pending_requests[correlation_id] = response_queue

        payload = {
            "type": "command_request",
            "correlation_id": correlation_id,
            "action": action,
            "username": username,
            "args": args or {}
        }

        try:
            ws.send(json.dumps(payload))
        except Exception as e:
            if correlation_id in cls.pending_requests:
                del cls.pending_requests[correlation_id]
            return False, f"Failed to send command over WebSocket: {str(e)}", None

        try:
            # Wait for response in the queue (blocking)
            response_data = response_queue.get(timeout=timeout)
            success = response_data.get("success", False)
            message = response_data.get("message", "")
            data = response_data.get("data")
            return success, message, data
        except Empty:
            return False, f"Request timed out after {timeout} seconds", None
        finally:
            if correlation_id in cls.pending_requests:
                del cls.pending_requests[correlation_id]

    @classmethod
    def verify_signature(cls, challenge, system_id, signature_hex):
        """Verify the HMAC-SHA256 signature of challenge + system_id using the device-specific token"""
        try:
            from src.database import AgentDevice
            device = AgentDevice.query.get(system_id)
            if not device or not device.secure_token or device.status != 'approved':
                logger.warning(f"Signature verification rejected: Device {system_id} not approved or missing token")
                return False
                
            token_bytes = device.secure_token.encode('utf-8')
            msg = (challenge + system_id).encode('utf-8')
            expected = hmac.new(token_bytes, msg, hashlib.sha256).hexdigest()
            return hmac.compare_digest(expected, signature_hex)
        except Exception as e:
            logger.error(f"Error during signature verification: {e}")
            return False

class AgentClient:
    def __init__(self, system_id):
        self.system_id = system_id

    def _parse_timekpr_output(self, output):
        """Parse the legacy CLI output format into a dictionary"""
        config_dict = {}
        
        # Regular expression to match key-value pairs
        pattern = r'([A-Z_]+):\s*(.*)'
        
        for line in output.split('\n'):
            match = re.search(pattern, line)
            if match:
                key = match.group(1)
                value = match.group(2).strip()
                
                # Convert numeric values
                if value.isdigit():
                    value = int(value)
                elif ';' in value:
                    # Handle semicolon-separated lists
                    value = value.split(';')
                    # Convert to integers if possible
                    if all(item.isdigit() for item in value):
                        value = [int(item) for item in value]
                elif value.lower() == 'true':
                    value = True
                elif value.lower() == 'false':
                    value = False
                
                config_dict[key] = value
                
        return config_dict

    def _full_access_day_hours(self):
        return {
            str(hour): {
                "STARTMIN": 0,
                "ENDMIN": 60,
                "UACC": 0,
            }
            for hour in range(24)
        }

    def _interval_to_dbus_hours(self, interval):
        if not interval or not interval.is_enabled or not interval.is_valid_interval():
            return {}

        if interval.start_minute == 0 and interval.end_minute == 0:
            return {
                str(hour): {"STARTMIN": 0, "ENDMIN": 60, "UACC": 0}
                for hour in range(interval.start_hour, interval.end_hour)
            }

        result = {}
        current_hour = interval.start_hour

        if current_hour == interval.end_hour:
            result[str(current_hour)] = {
                "STARTMIN": interval.start_minute,
                "ENDMIN": interval.end_minute,
                "UACC": 0,
            }
            return result

        result[str(current_hour)] = {
            "STARTMIN": interval.start_minute,
            "ENDMIN": 60,
            "UACC": 0,
        }
        current_hour += 1

        while current_hour < interval.end_hour:
            result[str(current_hour)] = {
                "STARTMIN": 0,
                "ENDMIN": 60,
                "UACC": 0,
            }
            current_hour += 1

        if interval.end_minute > 0:
            result[str(interval.end_hour)] = {
                "STARTMIN": 0,
                "ENDMIN": interval.end_minute,
                "UACC": 0,
            }

        return result

    def _build_dbus_day_hours(self, day_num, day_intervals):
        if not isinstance(day_intervals, list):
            day_intervals = [day_intervals]

        ordered_intervals = sorted(
            [
                interval for interval in day_intervals
                if interval and interval.is_enabled and interval.is_valid_interval()
            ],
            key=lambda interval: (
                interval.start_hour * 60 + interval.start_minute,
                interval.end_hour * 60 + interval.end_minute,
                getattr(interval, 'sort_order', 0),
            ),
        )

        if not ordered_intervals:
            return self._full_access_day_hours()

        day_hours = {}
        for interval in ordered_intervals:
            for hour_key, hour_spec in self._interval_to_dbus_hours(interval).items():
                existing = day_hours.get(hour_key)
                if existing is not None and existing != hour_spec:
                    raise ValueError(
                        f"Day {day_num} contains multiple disjoint intervals within hour {hour_key}, "
                        "which the TimeKpr D-Bus API cannot represent"
                    )
                day_hours[hour_key] = hour_spec

        return day_hours

    def validate_user(self, username):
        """
        Check if a user exists by querying the agent for normalized user config
        Returns: (is_valid, message, config_dict)
        """
        success, message, data = AgentConnectionManager.send_command_sync(
            self.system_id, "validate_user", username
        )
        if not success:
            return False, message, None

        config_dict = None
        if isinstance(data, dict):
            config_payload = data.get("config")
            if isinstance(config_payload, dict):
                config_dict = config_payload

        if config_dict is None:
            stdout = data.get("stdout", "") if isinstance(data, dict) else ""
            config_dict = self._parse_timekpr_output(stdout)

        return True, message, config_dict

    def modify_time_left(self, username, operation, seconds):
        """
        Modify time left for a user through the agent
        Returns: (success, message)
        """
        success, message, _ = AgentConnectionManager.send_command_sync(
            self.system_id, "modify_time_left", username, {
                "operation": operation,
                "seconds": seconds
            }
        )
        return success, message

    def set_weekly_time_limits(self, username, schedule_dict):
        """
        Set daily time limits for a user through the agent
        Returns: (success, message)
        """
        success, message, _ = AgentConnectionManager.send_command_sync(
            self.system_id, "set_weekly_time_limits", username, {
                "schedule": schedule_dict
            }
        )
        return success, message

    def set_allowed_hours(self, username, intervals_dict):
        """
        Set allowed hours for a user through the agent
        Returns: (success, message)
        """
        day_order = [1, 2, 3, 4, 5, 6, 7]  # Monday to Sunday

        intervals_serial = {}
        for day_num in day_order:
            day_intervals = intervals_dict.get(day_num) or []
            try:
                intervals_serial[str(day_num)] = self._build_dbus_day_hours(day_num, day_intervals)
            except ValueError as exc:
                return False, str(exc)

        success, message, _ = AgentConnectionManager.send_command_sync(
            self.system_id, "set_allowed_hours", username, {
                "intervals": intervals_serial
            }
        )
        return success, message
