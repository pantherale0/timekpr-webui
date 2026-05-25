import uuid
import json
import logging
import hmac
import hashlib
import os
import re
from queue import Queue, Empty

logger = logging.getLogger(__name__)

# Optional registration token firewall for new dynamic pairings
REGISTRATION_TOKEN = os.environ.get('REGISTRATION_TOKEN')

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
        """Parse the output of timekpra --userinfo command into a dictionary"""
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

    def validate_user(self, username):
        """
        Check if a user exists by running the timekpra --userinfo command via Agent
        Returns: (is_valid, message, config_dict)
        """
        success, message, data = AgentConnectionManager.send_command_sync(
            self.system_id, "validate_user", username
        )
        if not success:
            return False, message, None
        
        stdout = ""
        if isinstance(data, dict):
            stdout = data.get("stdout", "")
            
        config_dict = self._parse_timekpr_output(stdout)
        return True, message, config_dict

    def modify_time_left(self, username, operation, seconds):
        """
        Modify time left for a user using timekpra --settimeleft command via Agent
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
        Set daily time limits for a user using timekpra commands via Agent
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
        Set allowed hours for a user using timekpra --setallowedhours command via Agent
        Returns: (success, message)
        """
        intervals_serial = {}
        day_order = [1, 2, 3, 4, 5, 6, 7]  # Monday to Sunday
        
        for day_num in day_order:
            interval = intervals_dict.get(day_num)
            if interval and interval.is_enabled and interval.is_valid_interval():
                hour_specs = interval.to_timekpr_format()
                if hour_specs:
                    intervals_serial[str(day_num)] = ';'.join(hour_specs)
                else:
                    intervals_serial[str(day_num)] = ';'.join([str(h) for h in range(24)])
            else:
                intervals_serial[str(day_num)] = ';'.join([str(h) for h in range(24)])

        success, message, _ = AgentConnectionManager.send_command_sync(
            self.system_id, "set_allowed_hours", username, {
                "intervals": intervals_serial
            }
        )
        return success, message
