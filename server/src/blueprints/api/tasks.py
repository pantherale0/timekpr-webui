import logging
from flask import Blueprint, session, jsonify, flash, redirect, request, url_for

_LOGGER = logging.getLogger(__name__)

api_tasks_bp = Blueprint('api_tasks', __name__)


@api_tasks_bp.route('/api/task-status')
def get_task_status():
    """Get the status of the background task manager"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    from app import task_manager
    status = task_manager.get_status()
    return jsonify({
        'success': True,
        'status': status
    })


@api_tasks_bp.route('/restart-tasks')
def restart_tasks():
    """Restart the background task manager"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    from app import task_manager
    task_manager.restart()
    flash('Background tasks restarted', 'success')
    
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('ui_dashboard.dashboard'))
