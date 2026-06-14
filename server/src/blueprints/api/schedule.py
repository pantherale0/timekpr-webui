import logging
from datetime import timezone
from flask import Blueprint, session, request, jsonify, flash, redirect, url_for
from sqlalchemy.exc import SQLAlchemyError
from src.database import db, ManagedUser, UserWeeklySchedule, UserDailyTimeInterval
from src.schedule_manager import (
    INTERVAL_STEP_MINUTES,
    _serialize_interval,
    _build_intervals_for_day,
    _build_disabled_interval_placeholder,
)

_LOGGER = logging.getLogger(__name__)

api_schedule_bp = Blueprint('api_schedule', __name__)


@api_schedule_bp.route('/weekly-schedule/update', methods=['POST'])
def update_weekly_schedule():
    """Update weekly schedule for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user_id = request.form.get('user_id')
    
    if not user_id:
        flash('User ID is required', 'danger')
        return redirect(url_for('ui_dashboard.admin'))
    
    try:
        user_id = int(user_id)
    except ValueError:
        flash('Invalid user ID', 'danger')
        return redirect(url_for('ui_dashboard.admin'))
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get schedule data from form
    schedule_data = {}
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
    
    for day in days:
        hours = request.form.get(day, '0')
        try:
            hours = float(hours)
            if hours < 0:
                hours = 0
            elif hours > 24:
                hours = 24
        except (ValueError, TypeError):
            hours = 0
        schedule_data[day] = hours
    
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.flush()
        user.weekly_schedule = schedule
    else:
        schedule = user.weekly_schedule
    
    schedule.set_schedule_from_dict(schedule_data)
    
    try:
        db.session.commit()
        flash(f'Weekly schedule updated for {user.username}', 'success')
    except SQLAlchemyError as exc:
        db.session.rollback()
        flash(f'Error updating schedule: {exc}', 'danger')
    
    return redirect(url_for('ui_schedule.weekly_schedule_user', user_id=user.id))


@api_schedule_bp.route('/api/user/<int:user_id>/intervals')
def get_user_intervals(user_id):
    """API endpoint to get user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).order_by(
        UserDailyTimeInterval.day_of_week,
        UserDailyTimeInterval.sort_order,
        UserDailyTimeInterval.id,
    ).all()

    intervals_dict = {str(day): [] for day in range(1, 8)}
    for interval in intervals:
        if interval.is_enabled:
            intervals_dict[str(interval.day_of_week)].append(_serialize_interval(interval))

    return jsonify({
        'success': True,
        'intervals': intervals_dict,
        'username': user.username,
        'step_minutes': INTERVAL_STEP_MINUTES,
    })


@api_schedule_bp.route('/api/user/<int:user_id>/intervals/update', methods=['POST'])
def update_user_intervals(user_id):
    """API endpoint to update user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        intervals_data = data.get('intervals')
        if not isinstance(intervals_data, dict):
            return jsonify({'success': False, 'message': 'Intervals payload must be an object'}), 400

        replacement_map = {}
        for day_str, raw_entries in intervals_data.items():
            try:
                day_of_week = int(day_str)
            except (TypeError, ValueError):
                return jsonify({
                    'success': False,
                    'message': f'Invalid day value: {day_str}'
                }), 400

            try:
                replacement_map[day_of_week] = _build_intervals_for_day(day_of_week, raw_entries)
            except (ValueError, TypeError) as e:
                return jsonify({
                    'success': False,
                    'message': str(e)
                }), 400

        for day_of_week, new_intervals in replacement_map.items():
            existing_intervals = UserDailyTimeInterval.query.filter_by(
                user_id=user.id,
                day_of_week=day_of_week,
            ).all()
            for interval in existing_intervals:
                db.session.delete(interval)
            db.session.flush()

            persisted_intervals = new_intervals or [_build_disabled_interval_placeholder(day_of_week)]
            for interval in persisted_intervals:
                interval.user_id = user.id
                interval.mark_modified()
                db.session.add(interval)
        
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': f'Time intervals updated for {user.username}',
            'username': user.username
        })
        
    except SQLAlchemyError as exc:
        db.session.rollback()
        return jsonify({
            'success': False,
            'message': f'Error updating intervals: {exc}'
        }), 500


@api_schedule_bp.route('/api/user/<int:user_id>/intervals/sync-status')
def get_intervals_sync_status(user_id):
    """Get sync status of user's time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).all()
    needs_sync = any(not interval.is_synced for interval in intervals)
    
    last_synced = None
    if intervals:
        synced_intervals = [i for i in intervals if i.last_synced]
        if synced_intervals:
            last_synced = max(
                i.last_synced if i.last_synced.tzinfo is not None
                else i.last_synced.replace(tzinfo=timezone.utc)
                for i in synced_intervals
            )
            last_synced = last_synced.strftime('%Y-%m-%d %H:%M')
    
    enabled_count = sum(1 for i in intervals if i.is_enabled)
    total_count = enabled_count
    
    return jsonify({
        'success': True,
        'needs_sync': needs_sync,
        'last_synced': last_synced,
        'enabled_intervals': enabled_count,
        'total_intervals': total_count,
        'username': user.username
    })


@api_schedule_bp.route('/api/schedule-sync-status/<int:user_id>')
def get_schedule_sync_status(user_id):
    """Get the sync status of a user's weekly schedule"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    if user.weekly_schedule:
        schedule_dict = user.weekly_schedule.get_schedule_dict()
        last_synced = None
        if user.weekly_schedule.last_synced:
            last_synced = user.weekly_schedule.last_synced.strftime('%Y-%m-%d %H:%M')
        
        return jsonify({
            'success': True,
            'is_synced': user.weekly_schedule.is_synced,
            'schedule': schedule_dict,
            'last_synced': last_synced,
            'last_modified': user.weekly_schedule.last_modified.strftime('%Y-%m-%d %H:%M') if user.weekly_schedule.last_modified else None
        })
    return jsonify({
        'success': True,
        'is_synced': True,
        'schedule': None,
        'last_synced': None,
        'last_modified': None
    })
