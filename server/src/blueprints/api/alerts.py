import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify, session
from src.i18n.catalog import api_message
from sqlalchemy import or_, desc, asc
from src.models import db, AgentAlert, ManagedUser, AgentDevice
from src.alerts.manager import (
    _format_alert_event_label,
    _alert_details_to_text,
    _build_alert_entry,
)
from src.common.helpers import _get_device_label_map

_LOGGER = logging.getLogger(__name__)

api_alerts_bp = Blueprint('api_alerts', __name__)

@api_alerts_bp.route('/api/alerts', methods=['GET'])
def get_alerts():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    system_id = request.args.get('system_id')
    managed_user_id = request.args.get('managed_user_id')
    search = request.args.get('search', '').strip()
    event_type = request.args.get('event_type')
    sort_by = request.args.get('sort_by', 'date')
    sort_dir = request.args.get('sort_dir', 'desc')
    page = int(request.args.get('page', 1))
    per_page = int(request.args.get('per_page', 50))
    start_date_str = request.args.get('start_date')
    end_date_str = request.args.get('end_date')
    include_commands = request.args.get('include_commands', 'false').lower() == 'true'

    from src.common.helpers import resolve_session_parent_id, scope_alerts_query_for_parent

    parent_id = resolve_session_parent_id()
    query = AgentAlert.query
    query = scope_alerts_query_for_parent(query, parent_id)

    if start_date_str:
        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
            query = query.filter(AgentAlert.occurred_at >= start_date)
        except ValueError:
            pass

    if end_date_str:
        try:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
            query = query.filter(AgentAlert.occurred_at <= end_date)
        except ValueError:
            pass

    if system_id:
        query = query.filter(AgentAlert.system_id == system_id)
    
    if managed_user_id:
        user = ManagedUser.query.get(managed_user_id)
        if user:
            # Alerts for a user are those where linux_username matches one of their mappings
            usernames = [m.linux_username for m in user.device_mappings]
            # Also include device-wide alerts if they are linked to the user's devices
            # But the user only cares about alerts on THEIR mapped devices
            system_ids = [m.system_id for m in user.device_mappings]
            
            query = query.filter(
                AgentAlert.system_id.in_(system_ids),
                or_(
                    AgentAlert.linux_username.in_(usernames),
                    AgentAlert.linux_username.is_(None)
                )
            )

    if event_type:
        query = query.filter(AgentAlert.event_type == event_type)
    elif not include_commands:
        query = query.filter(AgentAlert.event_type != 'terminal_command')

    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            or_(
                AgentAlert.event_type.ilike(search_filter),
                AgentAlert.linux_username.ilike(search_filter),
                AgentAlert.payload_json.ilike(search_filter),
                AgentAlert.last_delivery_error.ilike(search_filter)
            )
        )

    # Sorting
    sort_col = AgentAlert.occurred_at
    if sort_by == 'type':
        sort_col = AgentAlert.event_type
    
    if sort_dir == 'asc':
        query = query.order_by(asc(sort_col), asc(AgentAlert.id))
    else:
        query = query.order_by(desc(sort_col), desc(AgentAlert.id))

    # Pagination
    pagination = query.paginate(page=page, per_page=per_page, error_out=False)
    
    device_labels = _get_device_label_map()
    
    alerts_data = []
    for alert in pagination.items:
        alerts_data.append(_build_alert_entry(alert, device_labels))

    # Get summary stats for the current filter (total, counts by type)
    # We can use the base query (without pagination) for this
    # But for performance, maybe we only do this if requested or once
    
    # Types for filtering dropdown
    all_types = db.session.query(AgentAlert.event_type).filter(AgentAlert.event_type != 'terminal_command').distinct().all()
    event_types = sorted([t[0] for t in all_types if t[0]])

    return jsonify({
        'success': True,
        'data': {
            'alerts': alerts_data,
            'pagination': {
                'page': pagination.page,
                'per_page': pagination.per_page,
                'total_items': pagination.total,
                'total_pages': pagination.pages,
                'has_next': pagination.has_next,
                'has_prev': pagination.has_prev,
            },
            'filters': {
                'event_types': [
                    {'value': et, 'label': _format_alert_event_label(et)}
                    for et in event_types
                ]
            }
        }
    })

@api_alerts_bp.route('/api/alerts/prune', methods=['POST'])
def prune_alerts():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401

    payload = request.get_json() or {}
    older_than_days = int(payload.get('older_than_days', 30))
    system_id = payload.get('system_id')
    managed_user_id = payload.get('managed_user_id')

    from src.common.helpers import resolve_session_parent_id, scope_alerts_query_for_parent

    parent_id = resolve_session_parent_id()
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    
    query = AgentAlert.query.filter(AgentAlert.occurred_at < cutoff_date)
    query = scope_alerts_query_for_parent(query, parent_id)
    
    if system_id:
        query = query.filter(AgentAlert.system_id == system_id)
    
    if managed_user_id:
        user = ManagedUser.query.get(managed_user_id)
        if user:
            usernames = [m.linux_username for m in user.device_mappings]
            system_ids = [m.system_id for m in user.device_mappings]
            query = query.filter(
                AgentAlert.system_id.in_(system_ids),
                or_(
                    AgentAlert.linux_username.in_(usernames),
                    AgentAlert.linux_username.is_(None)
                )
            )

    deleted_count = query.delete(synchronize_session=False)
    db.session.commit()

    _LOGGER.info("Pruned %d alerts older than %d days.", deleted_count, older_than_days)
    return jsonify({
        'success': True, 
        'message': api_message('alerts_pruned', count=deleted_count),
        'deleted_count': deleted_count
    })
