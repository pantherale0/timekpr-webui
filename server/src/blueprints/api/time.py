import logging
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify, session
from src.i18n.catalog import api_message
from src.models import db, ManagedUser
from src.agent.helper import AgentConnectionManager, AgentClient
from src.common.helpers import _get_device_label_map, _mapping_display_label

_LOGGER = logging.getLogger(__name__)

api_time_bp = Blueprint('api_time', __name__)


@api_time_bp.route('/api/modify-time', methods=['POST'])
def modify_time():
    """Modify time left for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    user_id = request.form.get('user_id')
    operation = request.form.get('operation')
    seconds = request.form.get('seconds')
    
    if not user_id or not operation or not seconds:
        return jsonify({'success': False, 'message': api_message('missing_params')}), 400
    
    try:
        user_id = int(user_id)
        seconds = int(seconds)
    except ValueError:
        return jsonify({'success': False, 'message': api_message('invalid_params')}), 400
    
    if operation not in ['+', '-']:
        return jsonify({'success': False, 'message': api_message('invalid_operation')}), 400
    
    user = ManagedUser.query.get_or_404(user_id)
    today = datetime.now(timezone.utc).date()

    mappings = list(user.device_mappings)
    if not mappings:
        return jsonify({'success': False, 'message': api_message('no_mappings')}), 400

    user.apply_daily_limit_adjustment(operation, seconds, today)
    user.pending_time_adjustment = None
    user.pending_time_operation = None
    user.last_checked = datetime.now(timezone.utc)
    db.session.commit()
    from src.common.dashboard_events import notify_dashboard_changed
    notify_dashboard_changed('time_adjusted')

    online_mappings = [mapping for mapping in mappings if AgentConnectionManager.is_online(mapping.system_id)]
    device_labels = _get_device_label_map()
    if not online_mappings:
        return jsonify({
            'success': True,
            'message': api_message('devices_offline'),
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
            pending_fragments.append(api_message('pending_online_retry', count=len(failures)))
        if remaining_mappings > 0:
            pending_fragments.append(api_message('pending_offline_rebalance', count=remaining_mappings))
        return jsonify({
            'success': True,
            'message': api_message(
                'time_adjustment_partial',
                applied=len(online_mappings) - len(failures),
                total=len(online_mappings),
            ),
            'details': failures,
            'username': user.username,
            'pending': True,
            'pending_reason': '; '.join(pending_fragments),
            'refresh': True
        })

    return jsonify({
        'success': True,
        'message': api_message('adjustment_applied', count=len(online_mappings)),
        'username': user.username,
        'pending': False,
        'refresh': True
    })
