"""REST API for access approval requests and grants."""

import logging

from flask import Blueprint, jsonify, request, session

from src.approvals_manager import (
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
from src.database import ApprovalRequest, ManagedUserDeviceMap, PolicyApprovalGrant

_LOGGER = logging.getLogger(__name__)

api_approvals_bp = Blueprint('api_approvals', __name__)


def _require_auth():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    return None


def _get_mapping_or_404(mapping_id):
    mapping = ManagedUserDeviceMap.query.get(mapping_id)
    if mapping is None:
        return None, (jsonify({'success': False, 'message': 'Mapping not found'}), 404)
    return mapping, None


def _get_request_or_404(request_id):
    request_row = ApprovalRequest.query.get(request_id)
    if request_row is None:
        return None, (jsonify({'success': False, 'message': 'Approval request not found'}), 404)
    return request_row, None


def _get_grant_or_404(grant_id):
    grant = PolicyApprovalGrant.query.get(grant_id)
    if grant is None:
        return None, (jsonify({'success': False, 'message': 'Approval grant not found'}), 404)
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
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'message': 'Request approved',
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
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'message': 'Request denied',
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
        return jsonify({'success': False, 'message': 'Request body must be an object'}), 400

    try:
        settings = upsert_settings(
            mapping,
            app_launch_mode=body.get('app_launch_mode'),
            domain_access_mode=body.get('domain_access_mode'),
        )
    except ValueError as exc:
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'message': 'Approval settings updated',
        'settings': {
            'device_map_id': mapping.id,
            'app_launch_mode': settings.app_launch_mode,
            'domain_access_mode': settings.domain_access_mode,
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
        return jsonify({'success': False, 'message': 'Request body must be an object'}), 400

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
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'message': 'Grant created',
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
        return jsonify({'success': False, 'message': str(exc)}), 400

    return jsonify({
        'success': True,
        'message': 'Grant revoked',
        'grant': build_grant_summary(grant),
    })
