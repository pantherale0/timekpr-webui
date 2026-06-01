import json
import logging
import secrets
from datetime import datetime, timezone
from flask import Blueprint, request
from sqlalchemy.exc import SQLAlchemyError
from src.database import db, AgentDevice
from src.agent_helper import AgentConnectionManager, normalize_agent_alert_payload
from src.agent_push import update_device_push_metadata
from src.alerts_manager import _store_agent_alert
from src.apparmor_manager import _store_app_usage_from_alert

_LOGGER = logging.getLogger(__name__)

websocket_bp = Blueprint('websocket', __name__)


def _close_websocket_connection(ws, system_id, connection_label):
    """Close a websocket connection while swallowing routine disconnect errors."""
    try:
        ws.close()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        _LOGGER.debug(
            "Ignoring %s close failure for %s",
            connection_label,
            system_id,
        )


def ws_agent_handler(ws):
    """
    WebSocket endpoint for client agents.
    Handles dynamic pairing, manual approval review, and HMAC challenge-response handshake.
    """
    from app import __version__, app, task_manager

    remote_ip = request.remote_addr or "127.0.0.1"
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        remote_ip = x_forwarded_for.split(",")[0].strip()
        
    _LOGGER.info("WebSocket connection attempt from %s", remote_ip)
    
    # 1. Await initial "hello" registration message
    system_id = None
    try:
        try:
            hello_msg_raw = ws.receive(timeout=10)
            if not hello_msg_raw:
                _LOGGER.warning("Handshake timeout: empty hello message")
                return
                
            hello_msg = json.loads(hello_msg_raw)
            if hello_msg.get("type") != "hello":
                _LOGGER.warning(
                    "Unexpected initial message type: %s",
                    hello_msg.get('type'),
                )
                ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Expected 'hello' type"}))
                return
                
            system_id = hello_msg.get("system_id")
            system_hostname = hello_msg.get("system_hostname")
            if isinstance(system_hostname, str):
                system_hostname = system_hostname.strip() or None
            reg_token = hello_msg.get("registration_token")
            
            if not system_id:
                _LOGGER.warning("Initial hello missing system_id")
                ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Missing system_id"}))
                return
            
            agent_version = hello_msg.get("agent_version")
            stripped_server = __version__.lstrip('v')
            stripped_agent = agent_version.lstrip('v') if agent_version else ""
            if stripped_agent != stripped_server:
                _LOGGER.warning(
                    "Connection rejected: Agent version %s is incompatible with server version %s",
                    agent_version or "unknown",
                    __version__,
                )
                ws.send(json.dumps({
                    "type": "auth_result",
                    "success": False,
                    "message": f"Incompatible agent version. Please update to {__version__}.",
                    "update_required": True,
                    "target_version": __version__
                }))
                return
    
            expected_reg_token = AgentConnectionManager.registration_token
            
            with app.app_context():
                device = AgentDevice.query.get(system_id)
                
                if not device:
                    if expected_reg_token and reg_token != expected_reg_token:
                        _LOGGER.warning(
                            "Registration rejected: Invalid registration token from %s",
                            system_id,
                        )
                        ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid registration token"}))
                        return
                    
                    device = AgentDevice(
                        system_id=system_id,
                        system_hostname=system_hostname,
                        system_ip=remote_ip,
                        status='pending',
                    )
                    db.session.add(device)
                    _LOGGER.info(
                        "New pending device registered: %s from %s",
                        system_id,
                        remote_ip,
                    )
                else:
                    if "system_hostname" in hello_msg:
                        device.system_hostname = system_hostname
                    device.system_ip = remote_ip

                # Update synced linux users list for all connections
                linux_users = hello_msg.get("linux_users")
                if linux_users is not None:
                    device.linux_users_json = json.dumps(linux_users)

                update_device_push_metadata(device, hello_msg)
                
                db.session.commit()
    
                if device.status == 'pending':
                    _LOGGER.info("Device %s is PENDING approval. Waiting...", system_id)
                    AgentConnectionManager.register_pending(system_id, ws)
                    ws.send(json.dumps({"type": "pairing_status", "status": "pending"}))
                    
                    try:
                        while True:
                            msg = ws.receive()
                            if not msg:
                                break
                    except (OSError, RuntimeError, ValueError):
                        _LOGGER.debug("Pending websocket closed for %s", system_id)
                    return
    
                if device.status == 'rejected':
                    _LOGGER.warning(
                        "Connection rejected: Device %s is banned/rejected",
                        system_id,
                    )
                    ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Device rejected/banned"}))
                    return
    
                if device.status == 'approved':
                    challenge = secrets.token_hex(32)
                    ws.send(json.dumps({
                        "type": "challenge",
                        "challenge": challenge
                    }))
                    
                    auth_msg_raw = ws.receive(timeout=10)
                    if not auth_msg_raw:
                        _LOGGER.warning("Handshake timeout for approved device %s", system_id)
                        return
                        
                    auth_msg = json.loads(auth_msg_raw)
                    if auth_msg.get("type") != "register":
                        _LOGGER.warning(
                            "Unexpected response type from %s: %s",
                            system_id,
                            auth_msg.get('type'),
                        )
                        return
                        
                    signature = auth_msg.get("signature")
                    if not signature:
                        _LOGGER.warning("Handshake from %s missing signature", system_id)
                        return
                        
                    if not AgentConnectionManager.verify_signature(challenge, system_id, signature):
                        _LOGGER.warning(
                            "Authentication signature verification failed for device %s",
                            system_id,
                        )
                        ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid authentication signature"}))
                        return
                        
                    AgentConnectionManager.register(system_id, ws, remote_ip)
                    ws.send(json.dumps({"type": "auth_result", "success": True, "message": "Authenticated successfully"}))
                    
                    device.last_seen = datetime.now(timezone.utc)
                    db.session.commit()
                    _LOGGER.info(
                        "Device %s authenticated successfully. Updated device IP snapshot to %s.",
                        system_id,
                        remote_ip,
                    )
    
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            SQLAlchemyError,
        ):
            _LOGGER.exception("Error during WebSocket handshake / loop for %s", system_id)
            return
    
        try:
            while True:
                msg_raw = ws.receive()
                if not msg_raw:
                    break
                    
                msg = json.loads(msg_raw)
                msg_type = msg.get("type")
                
                if msg_type == "command_response":
                    correlation_id = msg.get("correlation_id")
                    AgentConnectionManager.route_response(correlation_id, msg)
                elif msg_type == "policy_sync_check":
                    source_revisions = msg.get("source_revisions") or {}
                    if not isinstance(source_revisions, dict):
                        source_revisions = {}
                    task_manager.request_domain_policy_sync(
                        system_id,
                        source_revisions=source_revisions,
                        reason='agent_timer',
                    )
                elif msg_type == "alert_event":
                    try:
                        normalized_alert = normalize_agent_alert_payload(system_id, msg)
                        alert = _store_agent_alert(system_id, normalized_alert)
                        _LOGGER.info(
                            "Stored alert %s from agent %s as row %s",
                            alert.event_type,
                            system_id,
                            alert.id,
                        )
                        if alert.event_type == 'app_usage':
                            _store_app_usage_from_alert(system_id, normalized_alert)
                    except ValueError as exc:
                        _LOGGER.warning(
                            "Rejected invalid alert payload from %s: %s",
                            system_id,
                            exc,
                        )
                    except SQLAlchemyError as exc:
                        db.session.rollback()
                        _LOGGER.error(
                            "Failed to store alert payload from %s: %s",
                            system_id,
                            exc,
                        )
                else:
                    _LOGGER.warning(
                        "Received unexpected message type from client %s: %s",
                        system_id,
                        msg_type,
                    )
    
        except (OSError, RuntimeError, ValueError) as exc:
            _LOGGER.info("WebSocket connection closed for agent %s: %s", system_id, exc)
    finally:
        if system_id:
            AgentConnectionManager.unregister_pending(system_id)
            AgentConnectionManager.unregister(system_id)
