import secrets
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request, session
from src.models import db, ManagedUser, ManagedUserShareInvite
from src.common.helpers import parent_has_access_to_child

sharing_api_bp = Blueprint('sharing_api', __name__)

@sharing_api_bp.route('/api/profiles/<int:child_id>/generate-invite', methods=['POST'])
def generate_invite(child_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    parent_id = session.get('parent_account_id')
    
    # Policy check: Must have can_manage_policies over the child profile
    if not parent_has_access_to_child(parent_id, child_id, 'can_manage_policies'):
        return jsonify({'success': False, 'message': 'Permission denied.'}), 403

    child = ManagedUser.query.get(child_id)
    if not child:
        return jsonify({'success': False, 'message': 'Child profile not found.'}), 404

    payload = request.get_json(silent=True) or {}
    
    # Read permissions from payload
    permissions = {
        'can_view_screentime': bool(payload.get('can_view_screentime', True)),
        'can_manage_screentime': bool(payload.get('can_manage_screentime', False)),
        'can_view_monitoring': bool(payload.get('can_view_monitoring', False)),
        'can_manage_policies': bool(payload.get('can_manage_policies', False)),
    }

    # 16 bytes → 32 hex chars, matching invite_code VARCHAR(32)
    token = secrets.token_hex(16)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=48)

    invite = ManagedUserShareInvite(
        managed_user_id=child_id,
        invite_code=token,
        permissions_json=permissions,
        created_by_id=parent_id,
        created_at=datetime.now(timezone.utc),
        expires_at=expires_at,
        used_count=0,
        max_uses=1
    )

    db.session.add(invite)
    db.session.commit()

    redeem_url = f"/invite/redeem/{token}"
    return jsonify({
        'success': True,
        'invite_code': token,
        'redeem_url': redeem_url,
        'expires_at': expires_at.isoformat()
    })
