import logging
from flask import Blueprint, session, jsonify, redirect, request, url_for
from src.i18n.catalog import flash_t, api_message

_LOGGER = logging.getLogger(__name__)

api_tasks_bp = Blueprint('api_tasks', __name__)


@api_tasks_bp.route('/api/task-status')
def get_task_status():
    """Get the status of the background task manager"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': api_message('not_authenticated')}), 401
    
    from app import task_manager
    status = task_manager.get_status()
    return jsonify({
        'success': True,
        'status': status
    })


@api_tasks_bp.route('/restart-tasks', methods=['POST'])
def restart_tasks():
    """Restart the background task manager."""
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    from app import task_manager
    task_manager.restart()
    flash_t('flash.tasks.restarted', 'success')
    
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('ui_dashboard.dashboard'))
