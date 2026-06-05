import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from src.database import db, ManagedUser
from src.agent_helper import AgentConnectionManager, AgentClient
from src.helpers import _get_device_label_map, _mapping_display_label

_LOGGER = logging.getLogger(__name__)

api_time_bp = Blueprint('api_time', __name__)


@api_time_bp.route('/api/modify-time', methods=['POST'])
def modify_time():
    """Modify time left for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user_id = request.form.get('user_id')
    operation = request.form.get('operation')
    seconds = request.form.get('seconds')
    
    if not user_id or not operation or not seconds:
        return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
    
    try:
        user_id = int(user_id)
        seconds = int(seconds)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid parameter format'}), 400
    
    if operation not in ['+', '-']:
        return jsonify({'success': False, 'message': "Operation must be '+' or '-'"}), 400
    
    user = ManagedUser.query.get_or_404(user_id)
    today = datetime.now(timezone.utc).date()

    mappings = list(user.device_mappings)
    if not mappings:
        return jsonify({'success': False, 'message': 'No device mappings configured for this user'}), 400

    user.apply_daily_limit_adjustment(operation, seconds, today)
    user.pending_time_adjustment = None
    user.pending_time_operation = None
    user.last_checked = datetime.now(timezone.utc)
    db.session.commit()
    from src.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('time_adjusted')

    online_mappings = [mapping for mapping in mappings if AgentConnectionManager.is_online(mapping.system_id)]
    device_labels = _get_device_label_map()
    if not online_mappings:
        return jsonify({
            'success': True,
            'message': 'All mapped devices are offline. Adjustment saved on the server and will rebalance when a mapped device reconnects.',
            'username': user.username,
            'pending': True,
            'refresh': True
        })

    failures = []
    for mapping in online_mappings:
        agent_client = AgentClient(system_id=mapping.system_id)
        success, message = agent_client.modify_time_left(mapping.linux_username, operation, seconds)
        if not success:
            failures.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    remaining_mappings = len(mappings) - len(online_mappings)
    if failures or remaining_mappings > 0:
        pending_fragments = []
        if failures:
            pending_fragments.append(f"{len(failures)} online mapping(s) need retry")
        if remaining_mappings > 0:
            pending_fragments.append(f"{remaining_mappings} offline mapping(s) will rebalance on reconnect")
        return jsonify({
            'success': True,
            'message': f"Adjustment stored on the server. Applied immediately to {len(online_mappings) - len(failures)}/{len(online_mappings)} online mapping(s).",
            'details': failures,
            'username': user.username,
            'pending': True,
            'pending_reason': '; '.join(pending_fragments),
            'refresh': True
        })

    return jsonify({
        'success': True,
        'message': f"Adjustment applied to {len(online_mappings)} mapping(s).",
        'username': user.username,
        'pending': False,
        'refresh': True
    })
