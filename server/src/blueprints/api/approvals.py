"""REST API for access approval requests and grants."""

from datetime import datetime, timezone
import logging

from flask import Blueprint, jsonify, request, session

from src.user.approvals import (
    approve_request,
    build_grant_summary,
    build_request_summary,
    create_grant,
    deny_request,
    get_or_create_settings,
    get_session_actor,
    list_pending_requests,
    active_grants_for_mapping,
    revoke_grant,
    upsert_settings,
)
from src.models import db, ApprovalRequest, ManagedUserDeviceMap, PolicyApprovalGrant, AgentDevice, UserOnlineAccount
from src.i18n.catalog import api_message

_LOGGER = logging.getLogger(__name__)

api_approvals_bp = Blueprint('api_approvals', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    return None


def _get_mapping_or_404(mapping_id):
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None:
        return None, (jsonify({'success': False, 'message': api_message('mapping_not_found')}), 404)
    return mapping, None


def _get_request_or_404(request_id):
    request_row = ApprovalRequest.query.get(request_id)
    if request_row is None:
        return None, (jsonify({'success': False, 'message': api_message('approval_request_not_found')}), 404)
    return request_row, None


def _get_grant_or_404(grant_id):
    grant = PolicyApprovalGrant.query.get(grant_id)
    if grant is None:
        return None, (jsonify({'success': False, 'message': api_message('approval_grant_not_found')}), 404)
    return grant, None


@api_approvals_bp.route('/api/approvals', methods=['GET'])
def list_approvals():
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    status = request.args.get('status')
    request_type = request.args.get('request_type')
    managed_user_id = request.args.get('managed_user_id')
    limit = request.args.get('limit', 50)

    parsed_user_id = None
    if managed_user_id is not None and str(managed_user_id).strip().isdigit():
        parsed_user_id = int(managed_user_id)

    rows = list_pending_requests(
        status=status,
        request_type=request_type,
        managed_user_id=parsed_user_id,
        limit=limit,
    )
    return jsonify({
        'success': True,
        'approvals': [build_request_summary(row) for row in rows],
        'count': len(rows),
    })


@api_approvals_bp.route('/api/approvals/<int:request_id>', methods=['GET'])
def get_approval(request_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    request_row, error_response = _get_request_or_404(request_id)
    if error_response is not None:
        return error_response

    payload = build_request_summary(request_row)
    if request_row.source_alert is not None:
        payload['source_alert'] = {
            'id': request_row.source_alert.id,
            'event_type': request_row.source_alert.event_type,
            'occurred_at': request_row.source_alert.occurred_at.isoformat()
            if request_row.source_alert.occurred_at else None,
        }
    return jsonify({'success': True, 'approval': payload})


@api_approvals_bp.route('/api/approvals/<int:request_id>/approve', methods=['POST'])
def approve_approval_request(request_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    actor = get_session_actor()
    try:
        request_row = approve_request(request_id, decided_by=actor)
    except ValueError as exc:
        return jsonify({'success': False, 'message': api_message('validation_error', error=str(exc))}), 400

    return jsonify({
        'success': True,
        'message': api_message('request_approved'),
        'approval': build_request_summary(request_row),
    })


@api_approvals_bp.route('/api/approvals/<int:request_id>/deny', methods=['POST'])
def deny_approval_request(request_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    actor = get_session_actor()
    body = request.get_json(silent=True) or {}
    reason = body.get('reason') if isinstance(body, dict) else None

    try:
        request_row = deny_request(request_id, decided_by=actor, reason=reason)
    except ValueError as exc:
        return jsonify({'success': False, 'message': api_message('validation_error', error=str(exc))}), 400

    return jsonify({
        'success': True,
        'message': api_message('request_denied'),
        'approval': build_request_summary(request_row),
    })


@api_approvals_bp.route('/api/mappings/<int:mapping_id>/approval-settings', methods=['GET'])
def get_mapping_approval_settings(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    settings = get_or_create_settings(mapping)
    return jsonify({
        'success': True,
        'settings': {
            'device_map_id': mapping.id,
            'app_launch_mode': settings.app_launch_mode,
            'domain_access_mode': settings.domain_access_mode,
            'ai_policy_mode': settings.ai_policy_mode,
            'ai_prompt_logging': settings.ai_prompt_logging,
            'ai_daily_time_limit': settings.ai_daily_time_limit,
        },
    })


@api_approvals_bp.route('/api/mappings/<int:mapping_id>/approval-settings', methods=['POST'])
def update_mapping_approval_settings(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'success': False, 'message': api_message('request_body_must_be_object')}), 400

    try:
        settings = upsert_settings(
            mapping,
            app_launch_mode=body.get('app_launch_mode'),
            domain_access_mode=body.get('domain_access_mode'),
            ai_policy_mode=body.get('ai_policy_mode'),
            ai_prompt_logging=body.get('ai_prompt_logging'),
            ai_daily_time_limit=body.get('ai_daily_time_limit'),
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': api_message('validation_error', error=str(exc))}), 400

    return jsonify({
        'success': True,
        'message': api_message('approval_settings_updated'),
        'settings': {
            'device_map_id': mapping.id,
            'app_launch_mode': settings.app_launch_mode,
            'domain_access_mode': settings.domain_access_mode,
            'ai_policy_mode': settings.ai_policy_mode,
            'ai_prompt_logging': settings.ai_prompt_logging,
            'ai_daily_time_limit': settings.ai_daily_time_limit,
        },
    })


@api_approvals_bp.route('/api/mappings/<int:mapping_id>/approval-grants', methods=['GET'])
def list_mapping_approval_grants(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    grants = active_grants_for_mapping(mapping)
    return jsonify({
        'success': True,
        'grants': [build_grant_summary(grant) for grant in grants],
    })


@api_approvals_bp.route('/api/mappings/<int:mapping_id>/approval-grants', methods=['POST'])
def create_mapping_approval_grant(mapping_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    mapping, error_response = _get_mapping_or_404(mapping_id)
    if error_response is not None:
        return error_response

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return jsonify({'success': False, 'message': api_message('request_body_must_be_object')}), 400

    actor = get_session_actor()
    try:
        grant = create_grant(
            mapping,
            grant_type=body.get('grant_type'),
            target_kind=body.get('target_kind'),
            target_value=body.get('target_value'),
            display_label=body.get('display_label'),
            created_by=actor,
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': api_message('validation_error', error=str(exc))}), 400

    return jsonify({
        'success': True,
        'message': api_message('grant_created'),
        'grant': build_grant_summary(grant),
    })


@api_approvals_bp.route('/api/approval-grants/<int:grant_id>/revoke', methods=['POST'])
def revoke_approval_grant(grant_id):
    auth_response = _require_auth()
    if auth_response is not None:
        return auth_response

    grant, error_response = _get_grant_or_404(grant_id)
    if error_response is not None:
        return error_response

    actor = get_session_actor()
    try:
        grant = revoke_grant(grant_id, revoked_by=actor)
    except ValueError as exc:
        return jsonify({'success': False, 'message': api_message('validation_error', error=str(exc))}), 400

    return jsonify({
        'success': True,
        'message': api_message('grant_revoked'),
        'grant': build_grant_summary(grant),
    })


def _require_agent_auth(linux_username):
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None, (jsonify({'success': False, 'message': api_message('missing_auth_header')}), 401)
    
    token = auth_header.split(' ')[1].strip()
    device = AgentDevice.query.filter_by(secure_token=token).first()
    if not device:
        return None, None, (jsonify({'success': False, 'message': api_message('invalid_token')}), 401)
        
    if not linux_username:
        return device, None, (jsonify({'success': False, 'message': api_message('missing_linux_username')}), 400)
        
    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=device.system_id,
        linux_username=linux_username
    ).first()
    if not mapping:
        return device, None, (jsonify({
            'success': False,
            'message': api_message('no_user_mapping', linux_username=linux_username),
        }), 400)
        
    return device, mapping, None


@api_approvals_bp.route('/api/registration/check', methods=['POST'])
def check_registration_status():
    body = request.get_json(silent=True) or {}
    linux_username = body.get('linux_username')
    domain = body.get('domain')
    
    if not domain:
        return jsonify({'success': False, 'message': api_message('missing_domain')}), 400
        
    device, mapping, error_response = _require_agent_auth(linux_username)
    if error_response is not None:
        return error_response
        
    settings = get_or_create_settings(mapping)
    if not settings.registration_approval_enabled:
        return jsonify({'success': True, 'allowed': True})
        
    # Check if there is an approved grant for registration on this domain
    grant = PolicyApprovalGrant.query.filter_by(
        device_map_id=mapping.id,
        grant_type='registration',
        target_value=domain,
        status='active'
    ).first()
    
    if grant:
        return jsonify({'success': True, 'allowed': True})
        
    # Check if there is a pending approval request for registration on this domain
    pending_request = ApprovalRequest.query.filter_by(
        device_map_id=mapping.id,
        request_type='registration',
        target_value=domain,
        status='pending'
    ).first()
    
    if pending_request:
        return jsonify({'success': True, 'allowed': False, 'pending': True})
        
    return jsonify({'success': True, 'allowed': False, 'pending': False})


@api_approvals_bp.route('/api/registration/request', methods=['POST'])
def request_registration_approval():
    body = request.get_json(silent=True) or {}
    linux_username = body.get('linux_username')
    domain = body.get('domain')
    
    if not domain:
        return jsonify({'success': False, 'message': api_message('missing_domain')}), 400
        
    device, mapping, error_response = _require_agent_auth(linux_username)
    if error_response is not None:
        return error_response
        
    now = datetime.now(timezone.utc)
    
    # Check if there is already a pending approval request
    existing = ApprovalRequest.query.filter_by(
        device_map_id=mapping.id,
        request_type='registration',
        target_value=domain,
        status='pending'
    ).first()
    
    if existing:
        existing.requested_at = now
        db.session.commit()
        request_row = existing
    else:
        request_row = ApprovalRequest(
            device_map_id=mapping.id,
            request_type='registration',
            target_kind='domain',
            target_value=domain,
            display_label=domain,
            status='pending',
            requested_at=now
        )
        db.session.add(request_row)
        db.session.commit()
        
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('approval_requested')
    
    return jsonify({
        'success': True,
        'message': api_message('approval_request_raised'),
        'request': build_request_summary(request_row)
    })


@api_approvals_bp.route('/api/registration/log-login', methods=['POST'])
def log_user_login():
    body = request.get_json(silent=True) or {}
    linux_username = body.get('linux_username')
    domain = body.get('domain')
    username = body.get('username')
    
    if not domain or not username:
        return jsonify({'success': False, 'message': api_message('missing_domain_or_username')}), 400
        
    device, mapping, error_response = _require_agent_auth(linux_username)
    if error_response is not None:
        return error_response
        
    now = datetime.now(timezone.utc)
    
    # Find existing or create new
    existing = UserOnlineAccount.query.filter_by(
        managed_user_id=mapping.managed_user_id,
        domain=domain,
        username=username
    ).first()
    
    if existing:
        existing.last_seen_at = now
        db.session.commit()
        account = existing
    else:
        account = UserOnlineAccount(
            managed_user_id=mapping.managed_user_id,
            domain=domain,
            username=username,
            first_seen_at=now,
            last_seen_at=now
        )
        db.session.add(account)
        db.session.commit()
        
    return jsonify({
        'success': True,
        'message': api_message('login_logged'),
        'account': account.to_dict()
    })


@api_approvals_bp.route('/api/user/<int:user_id>/online-accounts', methods=['GET'])
def get_user_online_accounts(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
        
    from src.models import ManagedUser, UserOnlineAccount
    user = ManagedUser.query.get_or_404(user_id)
    
    # Query all online accounts for this user
    accounts = UserOnlineAccount.query.filter_by(managed_user_id=user.id).order_by(UserOnlineAccount.last_seen_at.desc()).all()
    
    return jsonify({
        'success': True,
        'accounts': [acc.to_dict() for acc in accounts]
    })
