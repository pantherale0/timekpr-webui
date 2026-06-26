import logging
import re
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

from src.models import (
    db,
    AgentDevice,
    ManagedUserDeviceMap,
    MappingApprovalSettings,
    AiPromptLog,
    AiSessionLog,
    AgentAlert,
    PolicyApprovalGrant,
    utc_today,
)
from src.agent.helper import normalize_agent_alert_payload
from src.alerts.manager import _store_agent_alert

_LOGGER = logging.getLogger(__name__)

api_ai_bp = Blueprint('api_ai', __name__)

BYPASS_REGEX = re.compile(
    r'\b(vpn|proxy|unblock|circumvent|parental\s+control|disable\s+guardian|evade\s+restrictions)\b',
    re.IGNORECASE
)
CHEATING_REGEX = re.compile(
    r'\b(write\s+my\s+essay|do\s+my\s+homework|solve\s+this\s+test|write\s+an\s+essay\s+on|cheat\s+on|answer\s+this\s+question\s+for\s+my\s+test)\b',
    re.IGNORECASE
)

def _get_authenticated_mapping():
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        _LOGGER.warning("AI check rejected: Missing or invalid Authorization header.")
        return None, None, (jsonify({'success': False, 'message': 'Missing or invalid authorization header'}), 401)

    token = auth_header.split(' ')[1].strip()
    device = AgentDevice.query.filter_by(secure_token=token).first()
    if not device:
        _LOGGER.warning("AI check rejected: Invalid agent device token.")
        return None, None, (jsonify({'success': False, 'message': 'Invalid token'}), 401)

    payload = request.get_json(silent=True) or {}
    linux_username = payload.get('linux_username')
    if not linux_username:
        return None, None, (jsonify({'success': False, 'message': 'Missing linux_username'}), 400)

    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=linux_username,
    ).first()

    if not mapping:
        _LOGGER.warning(
            "AI check rejected: No mapping found for user %s on device %s",
            linux_username,
            device.system_id,
        )
        return None, None, (jsonify({'success': False, 'message': f'No user mapping for user {linux_username} on this device'}), 404)

    return device, mapping, None

@api_ai_bp.route('/api/ai/check-policy', methods=['POST'])
def check_policy():
    device, mapping, error_res = _get_authenticated_mapping()
    if error_res:
        return error_res

    payload = request.get_json(silent=True) or {}
    domain = payload.get('domain')

    settings = mapping.approval_settings
    ai_policy_mode = settings.ai_policy_mode if settings else 'off'
    ai_daily_time_limit = settings.ai_daily_time_limit if settings else None

    # Calculate today's time spent in seconds
    today_start = datetime.combine(utc_today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    time_spent_seconds = db.session.query(db.func.sum(AiSessionLog.duration_seconds)).filter(
        AiSessionLog.device_map_id == mapping.id,
        AiSessionLog.logged_at >= today_start
    ).scalar() or 0

    # Calculate time left
    time_left_seconds = None
    if ai_daily_time_limit is not None and ai_daily_time_limit > 0:
        limit_seconds = ai_daily_time_limit * 60
        time_left_seconds = max(0, limit_seconds - time_spent_seconds)

    allowed = True
    reason = None

    if time_left_seconds is not None and time_left_seconds <= 0:
        allowed = False
        reason = 'limit_exceeded'
    elif ai_policy_mode == 'block':
        allowed = False
        reason = 'blocked'
    elif ai_policy_mode == 'approve':
        if domain:
            grant = PolicyApprovalGrant.query.filter(
                PolicyApprovalGrant.device_map_id == mapping.id,
                PolicyApprovalGrant.grant_type == PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
                PolicyApprovalGrant.target_value == domain,
                PolicyApprovalGrant.status == PolicyApprovalGrant.STATUS_ACTIVE
            ).first()
            if grant:
                allowed = True
                reason = None
            else:
                allowed = False
                reason = 'approve_required'
        else:
            allowed = False
            reason = 'approve_required'

    return jsonify({
        'success': True,
        'allowed': allowed,
        'reason': reason,
        'time_left_seconds': time_left_seconds
    })

@api_ai_bp.route('/api/ai/check-prompt', methods=['POST'])
def check_prompt():
    device, mapping, error_res = _get_authenticated_mapping()
    if error_res:
        return error_res

    payload = request.get_json(silent=True) or {}
    service = payload.get('service')
    domain = payload.get('domain')
    prompt_text = payload.get('prompt_text') or ''
    url = payload.get('url') or ''
    title = payload.get('title') or ''

    if not service or not domain:
        return jsonify({'success': False, 'message': 'Missing service or domain'}), 400

    settings = mapping.approval_settings
    ai_policy_mode = settings.ai_policy_mode if settings else 'off'
    ai_prompt_logging = settings.ai_prompt_logging if settings else 'metadata_only'
    ai_daily_time_limit = settings.ai_daily_time_limit if settings else None

    # 1. Check Policy and Daily limits first
    today_start = datetime.combine(utc_today(), datetime.min.time()).replace(tzinfo=timezone.utc)
    time_spent_seconds = db.session.query(db.func.sum(AiSessionLog.duration_seconds)).filter(
        AiSessionLog.device_map_id == mapping.id,
        AiSessionLog.logged_at >= today_start
    ).scalar() or 0

    time_left_seconds = None
    if ai_daily_time_limit is not None and ai_daily_time_limit > 0:
        limit_seconds = ai_daily_time_limit * 60
        time_left_seconds = max(0, limit_seconds - time_spent_seconds)

    allowed = True
    reason = None

    if time_left_seconds is not None and time_left_seconds <= 0:
        allowed = False
        reason = 'limit_exceeded'
    elif ai_policy_mode == 'block':
        allowed = False
        reason = 'blocked'
    elif ai_policy_mode == 'approve':
        grant = PolicyApprovalGrant.query.filter(
            PolicyApprovalGrant.device_map_id == mapping.id,
            PolicyApprovalGrant.grant_type == PolicyApprovalGrant.GRANT_DOMAIN_ACCESS,
            PolicyApprovalGrant.target_value == domain,
            PolicyApprovalGrant.status == PolicyApprovalGrant.STATUS_ACTIVE
        ).first()
        if not grant:
            allowed = False
            reason = 'approve_required'

    # If blocked by general policy, log the block
    if not allowed:
        log_text = prompt_text if ai_prompt_logging == 'full_text' else None
        prompt_log = AiPromptLog(
            device_map_id=mapping.id,
            service=service,
            domain=domain,
            prompt_text=log_text,
            prompt_length=len(prompt_text),
            url=url,
            title=title,
            status='Blocked',
            logged_at=datetime.now(timezone.utc)
        )
        db.session.add(prompt_log)
        db.session.commit()

        return jsonify({
            'success': True,
            'allowed': False,
            'reason': reason,
            'time_left_seconds': time_left_seconds
        })

    # 2. Monitor mode keyword check
    matched_bypass = BYPASS_REGEX.findall(prompt_text)
    matched_cheating = CHEATING_REGEX.findall(prompt_text)
    matched_keywords = list(set(matched_bypass + matched_cheating))

    status = 'Allowed'
    if matched_keywords:
        status = 'Flagged'
        # Store an agent alert
        alert_payload = {
            'event_type': 'ai_bypass_attempt',
            'linux_username': mapping.linux_username,
            'occurred_at': datetime.now(timezone.utc).isoformat(),
            'details': {
                'service': service,
                'domain': domain,
                'url': url,
                'title': title,
                'matched_keywords': matched_keywords,
                'prompt_preview': prompt_text[:200]
            }
        }
        try:
            normalized_alert = normalize_agent_alert_payload(device.system_id, alert_payload)
            _store_agent_alert(device.system_id, normalized_alert)
        except Exception:
            _LOGGER.exception("Failed to generate and store agent alert for flagged AI prompt.")

    log_text = prompt_text if ai_prompt_logging == 'full_text' else None
    prompt_log = AiPromptLog(
        device_map_id=mapping.id,
        service=service,
        domain=domain,
        prompt_text=log_text,
        prompt_length=len(prompt_text),
        url=url,
        title=title,
        status=status,
        logged_at=datetime.now(timezone.utc)
    )
    db.session.add(prompt_log)
    db.session.commit()

    return jsonify({
        'success': True,
        'allowed': True,
        'reason': None,
        'time_left_seconds': time_left_seconds
    })

@api_ai_bp.route('/api/ai/log-session', methods=['POST'])
def log_session():
    device, mapping, error_res = _get_authenticated_mapping()
    if error_res:
        return error_res

    payload = request.get_json(silent=True) or {}
    domain = payload.get('domain')
    duration_seconds = payload.get('duration_seconds')

    if not domain or duration_seconds is None:
        return jsonify({'success': False, 'message': 'Missing domain or duration_seconds'}), 400

    try:
        duration_seconds = int(duration_seconds)
    except (ValueError, TypeError):
        return jsonify({'success': False, 'message': 'Invalid duration_seconds'}), 400

    session_log = AiSessionLog(
        device_map_id=mapping.id,
        domain=domain,
        duration_seconds=duration_seconds,
        logged_at=datetime.now(timezone.utc)
    )
    db.session.add(session_log)
    db.session.commit()

    return jsonify({
        'success': True
    })
