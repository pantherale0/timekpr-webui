import logging
from flask import Blueprint, session, flash, redirect, url_for, render_template
from src.database import db, ManagedUser, UserWeeklySchedule, AppPolicy
from src.blocklists_manager import _build_user_blocklist_sync_status, _get_blocklist_sources

_LOGGER = logging.getLogger(__name__)

ui_schedule_bp = Blueprint('ui_schedule', __name__)


@ui_schedule_bp.route('/weekly-schedule')
def weekly_schedule():
    """Display weekly schedules overview for all users"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    
    db_changed = False
    for user in users:
        if not user.weekly_schedule:
            schedule = UserWeeklySchedule(user_id=user.id)
            db.session.add(schedule)
            db_changed = True
    if db_changed:
        db.session.commit()
        
    return render_template('weekly_schedule.html', users=users)


@ui_schedule_bp.route('/weekly-schedule/<int:user_id>')
def weekly_schedule_user(user_id):
    """Display weekly schedule management page for a specific user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    
    user = ManagedUser.query.get_or_404(user_id)
    
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.commit()
    
    blocklist_sync_status = _build_user_blocklist_sync_status(user)
    app_policies = AppPolicy.query.order_by(AppPolicy.name.asc()).all()
    assigned_policy_ids = {assignment.policy_id for assignment in user.app_policy_assignments}

    return render_template(
        'weekly_schedule_single.html',
        user=user,
        blocklist_sources=_get_blocklist_sources(include_domains=False, enabled_only=True),
        blocklist_sync_status=blocklist_sync_status,
        app_policies=app_policies,
        assigned_policy_ids=assigned_policy_ids,
    )
