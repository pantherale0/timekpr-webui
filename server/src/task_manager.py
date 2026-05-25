import threading
import time
from datetime import datetime, date
import logging
import json
import traceback

from src.database import (
    db,
    ManagedUser,
    UserTimeUsage,
    UserDailyTimeInterval,
    coerce_time_spent_day,
)
from src.agent_helper import AgentClient, AgentConnectionManager

logger = logging.getLogger(__name__)


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

class BackgroundTaskManager:
    def __init__(self, app=None):
        self.app = app
        self.running = False
        self.thread = None
        self.last_error = None
        self._task_lock = threading.Lock()  # Add a lock to prevent concurrent executions
    
    def init_app(self, app):
        self.app = app
    
    def start(self):
        """Start the background task manager"""
        if self.running:
            logger.info("Task manager already running, not starting again")
            return
            
        self.running = True
        self.thread = threading.Thread(target=self._run_tasks, daemon=True)
        self.thread.start()
        logger.info("Background task manager started with thread ID: %s", self.thread.ident)
    
    def stop(self):
        """Stop the background task manager"""
        logger.info("Stopping background task manager...")
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                logger.warning("Thread did not stop gracefully within timeout")
            else:
                logger.info("Thread stopped successfully")
        logger.info("Background task manager stopped")
    
    def restart(self):
        """Restart the background task manager"""
        logger.info("Restarting background task manager...")
        self.stop()
        time.sleep(1)  # Give it a moment to fully stop
        self.start()
        logger.info("Background task manager restarted")
        
    def get_status(self):
        """Get the status of the background task manager"""
        status = {
            'running': self.running,
            'thread_alive': self.thread.is_alive() if self.thread else False,
            'last_error': self.last_error,
            'thread_id': self.thread.ident if self.thread else None
        }
        logger.info("Task manager status: %s", status)
        return status
    
    def _run_tasks(self):
        """Main task loop"""
        logger.info("Task loop started in thread ID: %s", threading.current_thread().ident)
        while self.running:
            try:
                # Only process tasks if we can acquire the lock
                if self._task_lock.acquire(blocking=False):
                    try:
                        logger.info("Starting task execution cycle")
                        # Use a fresh app context
                        if self.app:
                            with self.app.app_context():
                                logger.info("Updating user data")
                                self._update_user_data()
                                logger.info("User data update cycle complete")
                        else:
                            logger.error("App is not initialized in task manager")
                        
                        self.last_error = None  # Clear error on successful run
                    finally:
                        self._task_lock.release()
                else:
                    logger.info("Task already running, skipping this cycle")
            except Exception as e:
                if self._task_lock.locked():
                    self._task_lock.release()
                error_msg = f"Error in background task: {str(e)}"
                trace = traceback.format_exc()
                logger.error("%s\n%s", error_msg, trace)
                self.last_error = {
                    'message': error_msg,
                    'trace': trace,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            
            # Sleep for 10 seconds before next run
            logger.info("Task cycle finished, sleeping for 10 seconds")
            for _ in range(10):
                if not self.running:
                    logger.info("Task loop stopping during sleep")
                    break
                time.sleep(1)
    
    def _update_user_data(self):
        """Update data for all managed users and their device mappings."""
        try:
            users = ManagedUser.query.all()
            logger.info("Found %d users in database", len(users))

            for user in users:
                try:
                    mappings = list(user.device_mappings)
                    logger.info("Processing managed user: %s across %d mapping(s)", user.username, len(mappings))

                    if not mappings:
                        user.is_valid = False
                        user.last_checked = datetime.utcnow()
                        db.session.commit()
                        continue

                    unsynced_intervals = UserDailyTimeInterval.query.filter_by(
                        user_id=user.id,
                        is_synced=False
                    ).all()

                    intervals_dict = {day: [] for day in range(1, 8)}
                    for interval in sorted(
                        user.time_intervals,
                        key=lambda item: (
                            item.day_of_week,
                            item.sort_order,
                            item.start_total_minutes,
                            item.id or 0,
                        ),
                    ):
                        intervals_dict.setdefault(interval.day_of_week, []).append(interval)
                    schedule_dict = user.weekly_schedule.get_schedule_dict() if user.weekly_schedule else None
                    has_positive_limits = False
                    if schedule_dict:
                        week_days = (
                            'monday', 'tuesday', 'wednesday', 'thursday',
                            'friday', 'saturday', 'sunday',
                        )
                        has_positive_limits = any((schedule_dict.get(day, 0) or 0) > 0 for day in week_days)

                    pending_adjustment_failed = False
                    applied_pending_adjustment = False
                    online_mappings = 0
                    shared_time_spent = 0
                    shared_time_left_candidates = []
                    any_valid_mapping = False
                    all_schedule_synced = True
                    all_interval_synced = True

                    for mapping in mappings:
                        mapping.last_checked = datetime.utcnow()

                        if not AgentConnectionManager.is_online(mapping.system_id):
                            logger.info(
                                "Mapping offline for %s on %s",
                                mapping.linux_username,
                                mapping.system_id,
                            )
                            all_schedule_synced = False
                            all_interval_synced = False
                            continue

                        online_mappings += 1
                        agent_client = AgentClient(system_id=mapping.system_id)

                        if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
                            success, message = agent_client.modify_time_left(
                                mapping.linux_username,
                                user.pending_time_operation,
                                user.pending_time_adjustment
                            )
                            if success:
                                applied_pending_adjustment = True
                            else:
                                pending_adjustment_failed = True
                                logger.warning(
                                    "Pending adjustment failed for mapping %s on %s: %s",
                                    mapping.linux_username,
                                    mapping.system_id,
                                    message,
                                )

                        if schedule_dict and not user.weekly_schedule.is_synced:
                            if not has_positive_limits:
                                logger.info(
                                    "No positive limits for %s; marking schedule synced locally",
                                    user.username,
                                )
                            else:
                                success, message = agent_client.set_weekly_time_limits(
                                    mapping.linux_username,
                                    schedule_dict
                                )
                                if not success:
                                    all_schedule_synced = False
                                    logger.warning(
                                        "Schedule sync failed for %s on %s: %s",
                                        mapping.linux_username,
                                        mapping.system_id,
                                        message,
                                    )

                        if unsynced_intervals:
                            success, message = agent_client.set_allowed_hours(
                                mapping.linux_username,
                                intervals_dict
                            )
                            if not success:
                                all_interval_synced = False
                                logger.warning(
                                    "Interval sync failed for %s on %s: %s",
                                    mapping.linux_username,
                                    mapping.system_id,
                                    message,
                                )

                        is_valid, result_message, config_dict = agent_client.validate_user(mapping.linux_username)
                        if is_valid and config_dict:
                            any_valid_mapping = True
                            mapping.is_valid = True
                            mapping.last_config = json.dumps(config_dict)
                            mapping.linux_uid = _safe_int(config_dict.get("LINUX_UID"), mapping.linux_uid)
                            shared_time_spent += coerce_time_spent_day(config_dict.get('TIME_SPENT_DAY', 0))
                            time_left = config_dict.get("TIME_LEFT_DAY")
                            if isinstance(time_left, int):
                                shared_time_left_candidates.append(time_left)
                        else:
                            mapping.is_valid = False
                            logger.warning(
                                "Validation failed for mapping %s on %s: %s",
                                mapping.linux_username,
                                mapping.system_id,
                                result_message,
                            )

                    if user.weekly_schedule and not user.weekly_schedule.is_synced and (has_positive_limits is False or all_schedule_synced):
                        user.weekly_schedule.mark_synced()

                    if unsynced_intervals and all_interval_synced:
                        for interval in unsynced_intervals:
                            interval.mark_synced()

                    if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
                        if online_mappings > 0 and applied_pending_adjustment and not pending_adjustment_failed:
                            user.pending_time_adjustment = None
                            user.pending_time_operation = None

                    today = date.today()
                    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
                    if usage:
                        usage.time_spent = shared_time_spent
                    else:
                        db.session.add(UserTimeUsage(
                            user_id=user.id,
                            date=today,
                            time_spent=shared_time_spent
                        ))

                    shared_config = {
                        "TIME_SPENT_DAY": shared_time_spent,
                        "TIME_LEFT_DAY": min(shared_time_left_candidates) if shared_time_left_candidates else None,
                        "MAPPING_COUNT": len(mappings),
                        "ONLINE_MAPPING_COUNT": online_mappings,
                    }
                    user.last_config = json.dumps(shared_config)
                    user.last_checked = datetime.utcnow()
                    user.is_valid = any_valid_mapping
                    db.session.commit()

                except Exception as e:
                    logger.error(
                        "Error updating user %s: %s\n%s",
                        user.username,
                        str(e),
                        traceback.format_exc(),
                    )
                    # Continue with the next user, but make sure we commit any pending changes
                    db.session.rollback()
                    
        except Exception as e:
            logger.error(
                "Error in user data update: %s\n%s",
                str(e),
                traceback.format_exc(),
            )
            db.session.rollback()