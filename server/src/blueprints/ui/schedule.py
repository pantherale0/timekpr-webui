import logging
from flask import Blueprint, redirect, url_for, flash, session

from src.blueprints.ui.spa import render_spa_shell
from src.database import db, ManagedUser, UserWeeklySchedule

_LOGGER = logging.getLogger(__name__)

ui_schedule_bp = Blueprint('ui_schedule', __name__)


@ui_schedule_bp.route('/weekly-schedule')
def weekly_schedule():
    """Serve weekly schedules overview for all users."""
    return render_spa_shell('weekly-schedule')


@ui_schedule_bp.route('/weekly-schedule/<int:user_id>')
def weekly_schedule_user(user_id):
    """Serve weekly schedule management page for a specific user."""
    return render_spa_shell(f'weekly-schedule/{user_id}')
