import threading
import time
from datetime import datetime, date
import logging
import json
import traceback
import hashlib
import hmac

import requests

from src.database import (
    AgentAlert,
    db,
    ManagedUser,
    Settings,
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


def _setting_enabled(key):
    raw_value = Settings.get_value(key, '0')
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_alert_webhook_settings():
    url = (Settings.get_value('alert_webhook_url', '') or '').strip()
    secret = (Settings.get_value('alert_webhook_secret', '') or '').strip()
    enabled = _setting_enabled('alert_webhook_enabled')
    return {
        'enabled': enabled,
        'url': url,
        'secret': secret,
        'is_active': enabled and bool(url),
    }


def _format_timestamp(value):
    return value.isoformat() + 'Z' if value else None


def _serialize_alert_for_webhook(alert):
    payload = alert.payload
    details = payload.get('details', {}) if isinstance(payload, dict) else {}
    return {
        'id': alert.id,
        'system_id': alert.system_id,
        'system_hostname': alert.device.system_hostname if alert.device else None,
        'event_type': alert.event_type,
        'linux_username': alert.linux_username,
        'occurred_at': _format_timestamp(alert.occurred_at),
        'received_at': _format_timestamp(alert.created_at),
        'details': details if isinstance(details, dict) else {},
    }


def _build_webhook_headers(alert, payload_body, secret):
    headers = {
        'Content-Type': 'application/json',
        'User-Agent': 'timekpr-webui/alert-webhook',
        'X-Timekpr-Alert-Id': str(alert.id),
    }
    if secret:
        signature = hmac.new(
            secret.encode('utf-8'),
            payload_body.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        headers['X-Timekpr-Signature'] = f'sha256={signature}'
    return headers

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
                                logger.info("Delivering alert webhooks")
                                self._deliver_pending_alerts()
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

    def _deliver_pending_alerts(self):
        webhook_settings = _get_alert_webhook_settings()
        if not webhook_settings['is_active']:
            logger.info("Alert webhook delivery disabled or missing URL")
            return

        pending_alerts = AgentAlert.query.filter(
            AgentAlert.webhook_enabled_snapshot.is_(True),
            AgentAlert.delivery_status.in_([
                AgentAlert.DELIVERY_PENDING,
                AgentAlert.DELIVERY_RETRYING,
            ]),
        ).order_by(AgentAlert.created_at.asc(), AgentAlert.id.asc()).all()

        logger.info("Found %d pending alert(s) for webhook delivery", len(pending_alerts))
        for alert in pending_alerts:
            try:
                payload = _serialize_alert_for_webhook(alert)
                payload_body = json.dumps(payload, sort_keys=True)
                headers = _build_webhook_headers(
                    alert,
                    payload_body,
                    webhook_settings['secret'],
                )

                alert.mark_delivery_attempt()
                response = requests.post(
                    webhook_settings['url'],
                    data=payload_body,
                    headers=headers,
                    timeout=5,
                )

                if 200 <= response.status_code < 300:
                    alert.mark_delivered()
                else:
                    response_text = (response.text or '').strip()
                    truncated_text = response_text[:500]
                    alert.mark_retry(
                        f'Webhook returned HTTP {response.status_code}'
                        + (f': {truncated_text}' if truncated_text else '')
                    )

                db.session.commit()
            except Exception as exc:
                logger.warning(
                    "Alert webhook delivery failed for alert %s: %s",
                    alert.id,
                    exc,
                )
                db.session.rollback()

                refreshed_alert = AgentAlert.query.get(alert.id)
                if not refreshed_alert:
                    continue

                refreshed_alert.mark_delivery_attempt()
                refreshed_alert.mark_retry(str(exc))
                db.session.commit()