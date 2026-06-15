"""Background maintenance jobs for user state, alerts, and blocklist sync."""

import hashlib
import hmac
import json
import logging
import threading
import time
import traceback
import uuid
import asyncio
import aiohttp
from contextlib import contextmanager
from datetime import date, datetime, timezone, timedelta

import requests
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError

from src.database import (
    AgentAlert,
    AgentDevice,
    BlocklistDomain,
    BlocklistSource,
    db,
    ManagedUser,
    Settings,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserDailyTimeInterval,
    coerce_time_left_day,
    coerce_time_spent_day,
    get_mapping_time_spent_for_day,
    YoutubeHistory,
)
from pynintendoparental import Authenticator, NintendoParental
from pyfamilysafety import FamilySafety, Authenticator as XboxAuthenticator
from src.agent_helper import AgentClient, AgentConnectionManager
from src.nintendo_sync import (
    apply_nintendo_playtime,
    build_nintendo_console_stats,
    build_nintendo_mapping_stats,
    push_nintendo_schedule_changes,
    run_async,
    save_nintendo_console_stats,
    update_nintendo_players,
)
from src.xbox_sync import (
    apply_xbox_playtime,
    build_xbox_console_stats,
    build_xbox_mapping_stats,
    push_xbox_schedule_changes,
    save_xbox_console_stats,
    update_xbox_players,
)
from src.blocklist_helper import (
    BLOCKLIST_STREAM_CHUNK_SIZE,
    BlocklistStreamParser,
    build_source_state_map,
    compute_source_revision,
    compute_source_revision_for_source_id,
    iter_source_domain_batches,
    summarize_mapping_blocklist_sync,
    should_refresh_external_source,
)

logger = logging.getLogger(__name__)


def _safe_int(value, default=0):
    """Best-effort integer coercion for agent-provided numeric fields."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _setting_enabled(key):
    """Return whether a boolean-like setting is enabled."""
    raw_value = Settings.get_value(key, '0')
    if raw_value is None:
        return False
    return str(raw_value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_alert_webhook_settings():
    """Load the current alert webhook settings from persisted server settings."""
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
    """Serialize a datetime for webhook payloads using a UTC-style suffix."""
    return value.isoformat() + 'Z' if value else None


def _serialize_alert_for_webhook(alert):
    """Build the alert payload delivered to webhook consumers."""
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
    """Build alert webhook headers, including the optional HMAC signature."""
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


def _replace_source_domains(source, normalized_domains):
    """Replace a source's domains with a normalized in-memory list."""
    existing_by_domain = {domain.domain: domain for domain in source.domains}
    desired_domains = set(normalized_domains)

    for domain_text, domain_row in list(existing_by_domain.items()):
        if domain_text not in desired_domains:
            db.session.delete(domain_row)

    for domain_text in normalized_domains:
        if domain_text not in existing_by_domain:
            db.session.add(BlocklistDomain(source_id=source.id, domain=domain_text))

    source.content_revision = compute_source_revision(desired_domains)
    source.updated_at = datetime.now(timezone.utc)


def _assigned_source_ids_for_user(user, active_source_ids=None):
    """Return enabled blocklist source IDs assigned to a user."""
    source_ids = {
        assignment.source_id
        for assignment in getattr(user, 'blocklist_assignments', [])
        if assignment.source and assignment.source.is_enabled
    }
    if active_source_ids is not None:
        source_ids &= set(active_source_ids)
    return sorted(source_ids)


@contextmanager
def _try_lock(lock):
    """Acquire a lock without blocking and release it automatically when held."""
    acquired = lock.acquire(blocking=False)
    try:
        yield acquired
    finally:
        if acquired:
            lock.release()

class BackgroundTaskManager:
    """Coordinate periodic background work for the server process."""

    def __init__(
        self,
        app=None,
        *,
        refresh_external_blocklists=True,
        update_user_data=True,
        sync_domain_policies=True,
        deliver_pending_alerts=True,
    ):
        self.app = app
        self.running = False
        self.thread = None
        self.last_error = None
        self._task_lock = threading.Lock()  # Add a lock to prevent concurrent executions
        self._domain_policy_sync_lock = threading.Lock()
        self._domain_policy_sync_in_progress = set()
        self._domain_policy_sync_pending = set()
        self.refresh_external_blocklists_enabled = refresh_external_blocklists
        self.update_user_data_enabled = update_user_data
        self.sync_domain_policies_enabled = sync_domain_policies
        self.deliver_pending_alerts_enabled = deliver_pending_alerts

    def init_app(self, app):
        """Attach the Flask app used to create request-independent app contexts."""
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
                with _try_lock(self._task_lock) as acquired:
                    if not acquired:
                        logger.info("Task already running, skipping this cycle")
                    else:
                        logger.info("Starting task execution cycle")
                        # Use a fresh app context
                        if self.app:
                            with self.app.app_context():
                                try:
                                    self._run_task_cycle()
                                    logger.info("User data update cycle complete")
                                finally:
                                    db.session.remove()
                        else:
                            logger.error("App is not initialized in task manager")

                        self.last_error = None  # Clear error on successful run
            except (
                requests.RequestException,
                RuntimeError,
                TypeError,
                ValueError,
                SQLAlchemyError,
            ) as exc:
                error_msg = f"Error in background task: {exc}"
                trace = traceback.format_exc()
                logger.error("%s\n%s", error_msg, trace)
                self.last_error = {
                    'message': error_msg,
                    'trace': trace,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            except OSError as exc:
                error_msg = f"OS-level error in background task: {exc}"
                trace = traceback.format_exc()
                logger.error("%s\n%s", error_msg, trace)
                self.last_error = {
                    'message': error_msg,
                    'trace': trace,
                    'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            except AttributeError as exc:
                error_msg = f"Task manager state error: {exc}"
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

    def _run_task_cycle(self):
        if self.refresh_external_blocklists_enabled:
            logger.info("Refreshing external blocklists")
            self._refresh_external_blocklists()

        if self.update_user_data_enabled:
            logger.info("Updating user data")
            self._update_user_data()
            logger.info("Syncing Nintendo devices")
            self.sync_nintendo_devices()
            logger.info("Syncing Xbox devices")
            self.sync_xbox_devices()

        if self.sync_domain_policies_enabled:
            logger.debug("Domain policy sync is agent-initiated")

        if self.deliver_pending_alerts_enabled:
            logger.info("Delivering alert webhooks")
            self._deliver_pending_alerts()
            
        # Automatic alert pruning
        self._prune_old_alerts()

        # YouTube background tasks
        self._fetch_youtube_categories()
        self._prune_youtube_history()
    
    def _prune_old_alerts(self):
        """Automatically prune alerts older than the configured threshold."""
        try:
            from src.settings_manager import _get_alert_retention_days
            retention_days = _get_alert_retention_days()
            cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
            
            deleted_count = AgentAlert.query.filter(
                AgentAlert.occurred_at < cutoff_date
            ).delete(synchronize_session=False)
            
            if deleted_count > 0:
                db.session.commit()
                logger.info("Automatically pruned %d alerts older than %d days", deleted_count, retention_days)
        except Exception as exc:
            logger.warning("Failed to automatically prune alerts: %s", exc)
            db.session.rollback()

    def _fetch_youtube_categories(self):
        """Fetch YouTube categories/genres for newly logged videos in the background."""
        try:
            from src.settings_manager import _get_youtube_api_key
            api_key = _get_youtube_api_key()
            if not api_key:
                return

            # Find distinct video IDs that have category == 'Unknown'
            # Limit to 50 because that's the max allowed in a single YouTube API call
            pending = db.session.query(YoutubeHistory.video_id).filter_by(category='Unknown').distinct().limit(50).all()
            if not pending:
                return

            video_ids = [row.video_id for row in pending]
            logger.info("Fetching YouTube categories for %d video(s)", len(video_ids))

            # Hardcoded standard YouTube category mapping (categoryId -> human-readable name)
            youtube_category_map = {
                '1': 'Film & Animation',
                '2': 'Autos & Vehicles',
                '10': 'Music',
                '15': 'Pets & Animals',
                '17': 'Sports',
                '18': 'Short Movies',
                '19': 'Travel & Events',
                '20': 'Gaming',
                '21': 'Videoblogging',
                '22': 'People & Blogs',
                '23': 'Comedy',
                '24': 'Entertainment',
                '25': 'News & Politics',
                '26': 'Howto & Style',
                '27': 'Education',
                '28': 'Science & Technology',
                '29': 'Nonprofits & Activism',
                '30': 'Movies',
                '31': 'Anime/Animation',
                '32': 'Action/Adventure',
                '33': 'Classics',
                '34': 'Comedy',
                '35': 'Documentary',
                '36': 'Drama',
                '37': 'Family',
                '38': 'Foreign',
                '39': 'Horror',
                '40': 'Sci-Fi/Fantasy',
                '41': 'Thriller',
                '42': 'Shorts',
                '43': 'Shows',
                '44': 'Trailers'
            }

            ids_param = ','.join(video_ids)
            url = f"https://www.googleapis.com/youtube/v3/videos?part=snippet&id={ids_param}&key={api_key}"
            
            response = requests.get(url, timeout=10)
            if response.status_code != 200:
                logger.warning("YouTube API returned HTTP %d while fetching categories.", response.status_code)
                return

            data = response.json()
            items = data.get('items', [])
            
            resolved_categories = {}
            for item in items:
                v_id = item.get('id')
                snippet = item.get('snippet', {})
                cat_id = snippet.get('categoryId')
                cat_name = youtube_category_map.get(cat_id, 'Unknown')
                resolved_categories[v_id] = cat_name

            # Update database records for each video ID
            for v_id in video_ids:
                category = resolved_categories.get(v_id, 'Unavailable') # Set Unavailable if video not returned (private/deleted)
                # Bulk update all rows with this video ID
                YoutubeHistory.query.filter_by(video_id=v_id, category='Unknown').update(
                    {YoutubeHistory.category: category},
                    synchronize_session=False
                )
            
            db.session.commit()
            logger.info("Successfully updated YouTube video categories.")
        except Exception as exc:
            logger.exception("Failed to fetch YouTube categories in background task.")
            db.session.rollback()

    def _prune_youtube_history(self):
        """Automatically prune YouTube history older than the configured threshold."""
        try:
            from src.settings_manager import _get_youtube_history_retention_days
            retention_days = _get_youtube_history_retention_days()
            if retention_days > 0:
                cutoff_date = datetime.now(timezone.utc) - timedelta(days=retention_days)
                
                deleted_count = YoutubeHistory.query.filter(
                    YoutubeHistory.watched_at < cutoff_date
                ).delete(synchronize_session=False)
                
                if deleted_count > 0:
                    db.session.commit()
                    logger.info("Automatically pruned %d YouTube history entries older than %d days", deleted_count, retention_days)
        except Exception as exc:
            logger.warning("Failed to automatically prune YouTube history: %s", exc)
            db.session.rollback()


    def _update_user_data(self):
        """Update data for all managed users and their device mappings."""
        try:
            users = ManagedUser.query.all()
            logger.info("Found %d users in database", len(users))

            for user in users:
                try:
                    mappings = list(user.device_mappings)
                    logger.info("Processing managed user: %s across %d mapping(s)", user.username, len(mappings))
                    today = date.today()
                    effective_daily_limit_seconds = user.get_effective_daily_limit_seconds(today)

                    if (
                        effective_daily_limit_seconds is not None
                        and user.pending_time_adjustment is not None
                        and user.pending_time_operation is not None
                        and user.get_daily_limit_adjustment_seconds(today) == 0
                    ):
                        try:
                            user.apply_daily_limit_adjustment(
                                user.pending_time_operation,
                                user.pending_time_adjustment,
                                today,
                            )
                            effective_daily_limit_seconds = user.get_effective_daily_limit_seconds(today)
                        except ValueError:
                            logger.warning(
                                "Ignoring invalid pending adjustment for %s: %s%s",
                                user.username,
                                user.pending_time_operation,
                                user.pending_time_adjustment,
                            )

                    if not mappings:
                        user.is_valid = False
                        user.last_checked = datetime.now(timezone.utc)
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
                    time_spent_by_mapping = {
                        mapping.id: get_mapping_time_spent_for_day(mapping, today)
                        for mapping in mappings
                    }
                    shared_time_left_candidates = []
                    any_valid_mapping = False
                    all_schedule_synced = True
                    all_interval_synced = True
                    domain_policy_hint_system_ids = set()
                    validated_mappings = []

                    for mapping in mappings:
                        mapping.last_checked = datetime.now(timezone.utc)

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

                        if (
                            effective_daily_limit_seconds is None
                            and user.pending_time_adjustment is not None
                            and user.pending_time_operation is not None
                        ):
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

                        is_valid, result_message, config_dict = agent_client.validate_user(mapping.linux_username, linux_uid=mapping.linux_uid)
                        if is_valid and config_dict:
                            any_valid_mapping = True
                            previous_linux_uid = mapping.linux_uid
                            mapping.is_valid = True
                            mapping.last_config = json.dumps(config_dict)
                            mapping.linux_uid = _safe_int(config_dict.get("LINUX_UID"), mapping.linux_uid)
                            if mapping.linux_uid != previous_linux_uid:
                                domain_policy_hint_system_ids.add(mapping.system_id)
                            time_spent_by_mapping[mapping.id] = coerce_time_spent_day(
                                config_dict.get('TIME_SPENT_DAY', 0)
                            )
                            time_left = coerce_time_left_day(config_dict.get("TIME_LEFT_DAY"))
                            if time_left is not None:
                                shared_time_left_candidates.append(time_left)
                            validated_mappings.append((mapping, agent_client, config_dict))
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
                        if effective_daily_limit_seconds is not None and online_mappings > 0:
                            user.pending_time_adjustment = None
                            user.pending_time_operation = None
                        elif online_mappings > 0 and applied_pending_adjustment and not pending_adjustment_failed:
                            user.pending_time_adjustment = None
                            user.pending_time_operation = None

                    shared_time_spent = sum(time_spent_by_mapping.values())
                    shared_time_left = None
                    if effective_daily_limit_seconds is not None:
                        shared_time_left = max(effective_daily_limit_seconds - shared_time_spent, 0)
                        for mapping, agent_client, config_dict in validated_mappings:
                            current_time_left = coerce_time_left_day(config_dict.get("TIME_LEFT_DAY"))
                            if current_time_left is None:
                                continue

                            delta = shared_time_left - current_time_left
                            
                            # Use configurable tolerance to avoid notification spam
                            tolerance = _safe_int(Settings.get_value('time_sync_tolerance', '15'), 15)
                            if abs(delta) < tolerance:
                                continue

                            operation = "+" if delta > 0 else "-"
                            success, message = agent_client.modify_time_left(
                                mapping.linux_username,
                                operation,
                                abs(delta),
                            )
                            if success:
                                config_dict["TIME_LEFT_DAY"] = shared_time_left
                                mapping.last_config = json.dumps(config_dict)
                            else:
                                logger.warning(
                                    "Daily time rebalance failed for %s on %s: %s",
                                    mapping.linux_username,
                                    mapping.system_id,
                                    message,
                                )

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
                        "TIME_LEFT_DAY": (
                            shared_time_left
                            if shared_time_left is not None
                            else (min(shared_time_left_candidates) if shared_time_left_candidates else None)
                        ),
                        "MAPPING_COUNT": len(mappings),
                        "ONLINE_MAPPING_COUNT": online_mappings,
                    }
                    user.last_config = json.dumps(shared_config)
                    user.last_checked = datetime.now(timezone.utc)
                    user.is_valid = any_valid_mapping
                    db.session.commit()
                    if domain_policy_hint_system_ids:
                        self.notify_domain_policy_hint(
                            system_ids=domain_policy_hint_system_ids,
                            reason='mapping_state_changed',
                        )

                except (
                    RuntimeError,
                    TypeError,
                    ValueError,
                    SQLAlchemyError,
                ) as exc:
                    logger.error(
                        "Error updating user %s: %s\n%s",
                        user.username,
                        exc,
                        traceback.format_exc(),
                    )
                    # Continue with the next user, but make sure we commit any pending changes
                    db.session.rollback()

        except (
            RuntimeError,
            TypeError,
            ValueError,
            SQLAlchemyError,
        ) as exc:
            logger.error(
                "Error in user data update: %s\n%s",
                exc,
                traceback.format_exc(),
            )
            db.session.rollback()
        else:
            from src.dashboard_events import notify_dashboard_changed
            notify_dashboard_changed('user_data_cycle')

    def refresh_external_blocklist_source(self, source_id, force=False):
        """Refresh a single external blocklist source and persist its new revision."""
        source = BlocklistSource.query.get(source_id)
        if not source:
            return False, 'Blocklist source not found'
        if source.source_type != BlocklistSource.TYPE_EXTERNAL_URL:
            return False, f'Blocklist "{source.name}" is not an external URL source'
        if not force and not should_refresh_external_source(source):
            return True, f'Blocklist "{source.name}" is already up to date'
        previous_revision = source.content_revision

        headers = {}
        if source.etag:
            headers['If-None-Match'] = source.etag
        if source.source_last_modified:
            headers['If-Modified-Since'] = source.source_last_modified

        from src.url_safety import is_safe_outbound_url

        if not is_safe_outbound_url(source.source_url):
            message = (
                f'Blocked refresh for "{source.name}": URL resolves to a private or internal host'
            )
            source.mark_sync_error(message)
            db.session.commit()
            return False, message

        try:
            response = requests.get(
                source.source_url,
                headers=headers,
                timeout=10,
                stream=True,
            )
        except requests.RequestException as exc:
            source.mark_sync_error(str(exc))
            db.session.commit()
            return False, f'Failed to refresh "{source.name}": {exc}'

        try:
            if response.status_code == 304:
                source.mark_sync_ok()
                db.session.commit()
                return True, f'External blocklist "{source.name}" was unchanged'

            if not 200 <= response.status_code < 300:
                source.mark_sync_error(f'HTTP {response.status_code}')
                db.session.commit()
                return False, f'Failed to refresh "{source.name}": HTTP {response.status_code}'

            parser = BlocklistStreamParser()
            dialect_name = db.engine.dialect.name

            # Commit the initial deletion so it doesn't hold an exclusive lock
            # across the entire multi-million domain insert process.
            BlocklistDomain.query.filter_by(source_id=source.id).delete(synchronize_session=False)
            db.session.commit()

            inserted_in_transaction = 0
            for batch in parser.iter_domain_batches(
                response.iter_content(chunk_size=BLOCKLIST_STREAM_CHUNK_SIZE),
                encoding=response.encoding or 'utf-8',
            ):
                if dialect_name == 'sqlite':
                    db.session.execute(
                        sqlite_insert(BlocklistDomain).prefix_with('OR IGNORE'),
                        [
                            {'source_id': source.id, 'domain': domain}
                            for domain in batch
                        ],
                    )
                elif dialect_name == 'postgresql':
                    db.session.execute(
                        pg_insert(BlocklistDomain).values([
                            {'source_id': source.id, 'domain': domain}
                            for domain in batch
                        ]).on_conflict_do_nothing(constraint='blocklist_source_domain_uc')
                    )
                else:
                    db.session.bulk_insert_mappings(
                        BlocklistDomain,
                        [
                            {'source_id': source.id, 'domain': domain}
                            for domain in batch
                        ]
                    )

                inserted_in_transaction += len(batch)

                # To prevent SQLite over NFS from starving other requests/worker threads,
                # periodically commit and yield the database lock.
                if inserted_in_transaction >= 25000:
                    db.session.commit()
                    inserted_in_transaction = 0
                    if dialect_name == 'sqlite':
                        time.sleep(0.05)  # Yield lock to other processes

            if inserted_in_transaction > 0:
                db.session.commit()

            source = BlocklistSource.query.get(source.id)  # Refresh source object after commits
            source.etag = response.headers.get('ETag')
            source.source_last_modified = response.headers.get('Last-Modified')
            source.mark_sync_ok()
            source.content_revision = compute_source_revision_for_source_id(source.id)

            errors = parser.collected_errors()
            if errors:
                source.last_sync_error = '; '.join(errors)

            db.session.flush()
            domain_count = BlocklistDomain.query.filter_by(source_id=source.id).count()
            updated_revision = source.content_revision
            db.session.commit()
            if updated_revision != previous_revision:
                self.notify_domain_policy_hint(reason='blocklist_catalog_updated')
            return True, f'Refreshed "{source.name}" with {domain_count} domain(s)'
        except (requests.RequestException, SQLAlchemyError, ValueError) as exc:
            db.session.rollback()
            source = BlocklistSource.query.get(source_id)
            if source:
                source.mark_sync_error(str(exc))
                db.session.commit()
            return False, f'Failed to refresh "{source.name}": {exc}'
        finally:
            response.close()

    def _refresh_external_blocklists(self):
        external_sources = BlocklistSource.query.filter_by(
            source_type=BlocklistSource.TYPE_EXTERNAL_URL
        ).all()
        for source in external_sources:
            if not should_refresh_external_source(source):
                continue

            success, message = self.refresh_external_blocklist_source(source.id)
            if success:
                logger.info(message)
            else:
                logger.warning(message)

    def notify_domain_policy_hint(self, system_ids=None, reason='server_update'):
        """Notify online agents that domain policy state may have changed."""
        if not self.sync_domain_policies_enabled:
            return 0

        from src.agent_push import notify_policy_sync_hint

        if system_ids is None:
            online_ids = set(AgentConnectionManager.get_online_system_ids())
            from src.database import AgentDevice

            push_ids = {
                device.system_id
                for device in AgentDevice.query.filter(AgentDevice.fcm_token.isnot(None)).all()
                if (device.fcm_token or '').strip()
            }
            target_system_ids = sorted(online_ids | push_ids)
        else:
            target_system_ids = sorted(set(system_ids))

        notified = 0
        for system_id in target_system_ids:
            success, _message = notify_policy_sync_hint(system_id, reason=reason)
            if success:
                notified += 1
        return notified

    def request_domain_policy_sync(self, system_id, source_revisions=None, reason='agent_check'):
        """Queue a device-specific domain policy sync in its own worker thread."""
        if not self.sync_domain_policies_enabled:
            return False
        if not system_id or not AgentConnectionManager.is_online(system_id):
            return False
        if self.app is None:
            logger.warning("Ignoring domain policy sync request for %s because app is not initialized", system_id)
            return False

        with self._domain_policy_sync_lock:
            if system_id in self._domain_policy_sync_in_progress:
                self._domain_policy_sync_pending.add(system_id)
                return False
            self._domain_policy_sync_in_progress.add(system_id)

        sync_thread = threading.Thread(
            target=self._run_requested_domain_policy_sync,
            args=(system_id, dict(source_revisions or {}), reason),
            daemon=True,
        )
        sync_thread.start()
        return True

    def sync_nintendo_devices(self, *, force=False):
        """Sync playtime and schedules with Nintendo servers synchronously using asyncio."""
        if not self.app:
            return
        try:
            run_async(self._sync_nintendo_devices_async(force=force))
        except Exception as exc:
            logger.error("Error in sync_nintendo_devices: %s\n%s", exc, traceback.format_exc())

    async def _sync_nintendo_devices_async(self, *, force=False):
        """Asynchronously sync playtime and schedules with Nintendo servers."""
        with self.app.app_context():
            # Throttle check: Only poll Nintendo API once every 5 minutes (300 seconds)
            last_poll_str = Settings.get_value('last_nintendo_poll_at')
            now_utc = datetime.now(timezone.utc)
            if not force and last_poll_str:
                try:
                    last_poll = datetime.fromisoformat(last_poll_str)
                    if (now_utc - last_poll).total_seconds() < 300:
                        logger.debug("Skipping Nintendo sync; last poll was %s", last_poll_str)
                        return
                except (ValueError, TypeError):
                    pass

            session_token = Settings.get_value('nintendo_session_token')
            if not session_token:
                logger.debug("Skipping Nintendo sync; no linked Nintendo account")
                return

            nintendo_devices = AgentDevice.query.filter_by(platform='nintendo', status='approved').all()
            if not nintendo_devices:
                logger.debug("Skipping Nintendo sync; no approved Nintendo devices")
                return

            schedule_push_targets = []

            try:
                async with aiohttp.ClientSession() as client_session:
                    auth = Authenticator(session_token, client_session)
                    await auth.async_complete_login(use_session_token=True)
                    client = await NintendoParental.create(auth)

                    await client.update()
                    today = date.today()

                    for db_device in nintendo_devices:
                        cloud_device = client.devices.get(db_device.system_id)
                        if not cloud_device:
                            logger.warning(
                                "Nintendo cloud device %s not found for enrolled console %s",
                                db_device.system_id,
                                db_device.system_hostname,
                            )
                            continue

                        update_nintendo_players(db_device, cloud_device)
                        save_nintendo_console_stats(
                            db_device.system_id,
                            build_nintendo_console_stats(cloud_device, now_utc=now_utc),
                        )

                        global_playtime_seconds = cloud_device.today_playing_time * 60
                        primary_mapping = db_device.user_mappings[0] if db_device.user_mappings else None

                        for mapping in db_device.user_mappings:
                            user = mapping.managed_user
                            if not user:
                                continue

                            player = cloud_device.players.get(mapping.linux_username)
                            player_playtime = (player.playing_time * 60) if player else global_playtime_seconds

                            previous_playtime = 0
                            last_active_str = now_utc.isoformat()
                            if mapping.last_config:
                                try:
                                    old_stats = json.loads(mapping.last_config)
                                    previous_playtime = old_stats.get("TIME_SPENT_DAY", 0)
                                    last_active_str = old_stats.get("last_playtime_change_at", now_utc.isoformat())
                                except Exception:
                                    pass

                            if player_playtime > previous_playtime:
                                last_active_str = now_utc.isoformat()

                            apply_nintendo_playtime(mapping, player_playtime=player_playtime, today=today)
                            mapping.last_config = json.dumps(
                                build_nintendo_mapping_stats(
                                    cloud_device,
                                    player_playtime=player_playtime,
                                    global_playtime_seconds=global_playtime_seconds,
                                    last_active_str=last_active_str,
                                    now_utc=now_utc,
                                )
                            )
                            mapping.last_checked = now_utc
                            mapping.is_valid = True

                            if mapping == primary_mapping:
                                schedule_push_targets.append((cloud_device, mapping))

                    Settings.set_value('last_nintendo_poll_at', now_utc.isoformat())
                    db.session.commit()
                    logger.info("Nintendo cloud sync completed for %d device(s)", len(nintendo_devices))
            except Exception as exc:
                logger.error("Failed to sync Nintendo devices: %s\n%s", exc, traceback.format_exc())
                db.session.rollback()
                return

            for cloud_device, mapping in schedule_push_targets:
                try:
                    await push_nintendo_schedule_changes(
                        cloud_device,
                        mapping,
                        today=today,
                        now_utc=now_utc,
                    )
                    db.session.commit()
                except Exception as exc:
                    logger.warning(
                        "Failed to push Nintendo schedule for %s: %s",
                        mapping.system_id,
                        exc,
                    )
                    db.session.rollback()

    def sync_xbox_devices(self, *, force=False):
        """Sync playtime with Microsoft Family Safety servers synchronously using asyncio."""
        if not self.app:
            return
        try:
            run_async(self._sync_xbox_devices_async(force=force))
        except Exception as exc:
            logger.error("Error in sync_xbox_devices: %s\n%s", exc, traceback.format_exc())

    async def _sync_xbox_devices_async(self, *, force=False):
        """Asynchronously sync playtime with Microsoft Family Safety servers."""
        with self.app.app_context():
            # Throttle check: Only poll Xbox API once every 5 minutes (300 seconds)
            last_poll_str = Settings.get_value('last_xbox_poll_at')
            now_utc = datetime.now(timezone.utc)
            if not force and last_poll_str:
                try:
                    last_poll = datetime.fromisoformat(last_poll_str)
                    if (now_utc - last_poll).total_seconds() < 300:
                        logger.debug("Skipping Xbox sync; last poll was %s", last_poll_str)
                        return
                except (ValueError, TypeError):
                    pass

            session_token = Settings.get_value('xbox_refresh_token')
            if not session_token:
                logger.debug("Skipping Xbox sync; no linked Xbox account")
                return

            xbox_devices = AgentDevice.query.filter_by(platform='xbox', status='approved').all()
            if not xbox_devices:
                logger.debug("Skipping Xbox sync; no approved Xbox devices")
                return

            try:
                auth = await XboxAuthenticator.create(session_token, use_refresh_token=True)
                client = FamilySafety(auth)
                await client.update()

                # Update stored refresh token in settings (if rotated)
                if auth.refresh_token and auth.refresh_token != session_token:
                    Settings.set_value('xbox_refresh_token', auth.refresh_token)

                today = date.today()

                for db_device in xbox_devices:
                    cloud_device = None
                    for account in client.accounts:
                        if account.devices:
                            matched = [d for d in account.devices if d.device_id == db_device.system_id]
                            if matched:
                                cloud_device = matched[0]
                                break

                    if not cloud_device:
                        logger.warning(
                            "Xbox cloud device %s not found for enrolled console %s",
                            db_device.system_id,
                            db_device.system_hostname,
                        )
                        continue

                    update_xbox_players(db_device, client.accounts)
                    save_xbox_console_stats(
                        db_device.system_id,
                        build_xbox_console_stats(cloud_device, now_utc=now_utc),
                    )

                    for mapping in db_device.user_mappings:
                        user = mapping.managed_user
                        if not user:
                            continue

                        mapped_account = [acc for acc in client.accounts if acc.user_id == mapping.linux_username]
                        player_playtime = 0
                        if mapped_account and mapped_account[0].devices:
                            member_device = [d for d in mapped_account[0].devices if d.device_id == db_device.system_id]
                            if member_device:
                                raw_time = member_device[0].today_time_used or 0
                                if raw_time > 10000:
                                    player_playtime = int(raw_time // 1000)
                                else:
                                    player_playtime = int(raw_time)

                        previous_playtime = 0
                        last_active_str = now_utc.isoformat()
                        if mapping.last_config:
                            try:
                                old_stats = json.loads(mapping.last_config)
                                previous_playtime = old_stats.get("TIME_SPENT_DAY", 0)
                                last_active_str = old_stats.get("last_playtime_change_at", now_utc.isoformat())
                            except Exception:
                                pass

                        if player_playtime > previous_playtime:
                            last_active_str = now_utc.isoformat()

                        apply_xbox_playtime(mapping, player_playtime=player_playtime, today=today)
                        mapping.last_config = json.dumps(
                            build_xbox_mapping_stats(
                                cloud_device,
                                player_playtime=player_playtime,
                                last_active_str=last_active_str,
                                now_utc=now_utc,
                            )
                        )
                        mapping.last_checked = now_utc
                        mapping.is_valid = True

                        if mapped_account:
                            try:
                                await push_xbox_schedule_changes(
                                    mapped_account[0],
                                    mapping,
                                    today=today,
                                    now_utc=now_utc,
                                )
                            except Exception as push_exc:
                                logger.warning(
                                    "Failed to push Xbox schedule changes for %s: %s",
                                    mapping.system_id,
                                    push_exc,
                                )

                Settings.set_value('last_xbox_poll_at', now_utc.isoformat())
                db.session.commit()
                logger.info("Xbox cloud sync completed for %d device(s)", len(xbox_devices))
            except Exception as exc:
                logger.error("Failed to sync Xbox devices: %s\n%s", exc, traceback.format_exc())
                db.session.rollback()

    def _run_requested_domain_policy_sync(self, system_id, source_revisions, reason):
        try:
            with self.app.app_context():
                try:
                    success, message = self._sync_domain_policy_system(
                        system_id,
                        agent_source_revisions=source_revisions,
                    )
                    if success:
                        logger.info(
                            "Completed agent-initiated domain policy sync for %s (%s): %s",
                            system_id,
                            reason,
                            message,
                        )
                    else:
                        logger.warning(
                            "Agent-initiated domain policy sync failed for %s (%s): %s",
                            system_id,
                            reason,
                            message,
                        )
                    android_success, android_message = self._sync_android_device_policy_system(
                        system_id,
                    )
                    if android_success:
                        logger.info(
                            "Completed Android device policy sync for %s (%s): %s",
                            system_id,
                            reason,
                            android_message,
                        )
                    else:
                        logger.warning(
                            "Android device policy sync failed for %s (%s): %s",
                            system_id,
                            reason,
                            android_message,
                        )
                    linux_success, linux_message = self._sync_linux_device_policy_system(
                        system_id,
                    )
                    if linux_success:
                        logger.info(
                            "Completed Linux device policy sync for %s (%s): %s",
                            system_id,
                            reason,
                            linux_message,
                        )
                    else:
                        logger.warning(
                            "Linux device policy sync failed for %s (%s): %s",
                            system_id,
                            reason,
                            linux_message,
                        )
                    screenshot_success, screenshot_message = self._sync_screenshot_policy_system(
                        system_id,
                    )
                    if screenshot_success:
                        logger.info(
                            "Completed screenshot policy sync for %s (%s): %s",
                            system_id,
                            reason,
                            screenshot_message,
                        )
                    else:
                        logger.warning(
                            "Screenshot policy sync failed for %s (%s): %s",
                            system_id,
                            reason,
                            screenshot_message,
                        )
                finally:
                    db.session.remove()
        except (RuntimeError, TypeError, ValueError, SQLAlchemyError):
            logger.error(
                "Error running requested domain policy sync for %s\n%s",
                system_id,
                traceback.format_exc(),
            )
        finally:
            rerun = False
            with self._domain_policy_sync_lock:
                self._domain_policy_sync_in_progress.discard(system_id)
                if system_id in self._domain_policy_sync_pending:
                    self._domain_policy_sync_pending.discard(system_id)
                    rerun = True
            if rerun and AgentConnectionManager.is_online(system_id):
                self.request_domain_policy_sync(system_id, reason='queued_followup')

    def _abort_domain_policy_sync(self, agent_client, sync_id):
        try:
            agent_client.abort_domain_policy_sync(sync_id)
        except (OSError, RuntimeError, TypeError, ValueError, AttributeError):
            logger.warning(
                "Failed to abort incremental domain policy sync %s for device %s",
                sync_id,
                agent_client.system_id,
            )

    def _sync_domain_policy_device(self, system_id, mapping_state, source_state_map, source_revisions=None):
        device_policies = {}
        desired_source_ids = set()

        for mapping, assigned_source_ids, _summary in mapping_state:
            if not assigned_source_ids or mapping.linux_uid is None:
                continue
            source_ids = [str(source_id) for source_id in assigned_source_ids]
            desired_source_ids.update(source_ids)
            policy_entry = {
                'linux_username': mapping.linux_username,
                'source_ids': source_ids,
            }
            try:
                from src.approvals_manager import (
                    build_domain_allowed_domains,
                    get_or_create_settings,
                )
                from src.database import MappingApprovalSettings

                settings = get_or_create_settings(mapping)
                if settings.domain_access_mode != MappingApprovalSettings.DOMAIN_BLOCKLIST_ONLY:
                    policy_entry['domain_access_mode'] = settings.domain_access_mode
                allowed_domains = build_domain_allowed_domains(mapping)
                if allowed_domains:
                    policy_entry['allowed_domains'] = allowed_domains
            except (ImportError, RuntimeError, TypeError, ValueError):
                pass
            device_policies[str(mapping.linux_uid)] = policy_entry

        agent_client = AgentClient(system_id=system_id)
        if source_revisions is None:
            success, message, state_payload = agent_client.get_domain_policy_state()
            if not success:
                return False, message

            source_revisions = {}
            if isinstance(state_payload, dict):
                source_revisions = state_payload.get('source_revisions') or {}
        if not isinstance(source_revisions, dict):
            source_revisions = {}
        source_revisions = {
            str(source_id): str(revision or '')
            for source_id, revision in source_revisions.items()
        }

        sync_id = str(uuid.uuid4())
        success, message = agent_client.begin_domain_policy_sync(sync_id)
        if not success:
            return False, message

        try:
            stale_source_ids = sorted(set(source_revisions) - desired_source_ids)
            if stale_source_ids:
                success, message = agent_client.delete_domain_policy_sources(sync_id, stale_source_ids)
                if not success:
                    self._abort_domain_policy_sync(agent_client, sync_id)
                    return False, message

            for source_id_text in sorted(desired_source_ids, key=int):
                desired_state = source_state_map.get(source_id_text, {})
                desired_revision = desired_state.get('revision') or ''
                if source_revisions.get(source_id_text) == desired_revision:
                    continue

                source_id = int(source_id_text)
                sent_any = False
                for batch in iter_source_domain_batches(source_id):
                    sent_any = True
                    success, message = agent_client.send_domain_policy_chunk(
                        sync_id,
                        source_id_text,
                        desired_revision,
                        batch,
                    )
                    if not success:
                        self._abort_domain_policy_sync(agent_client, sync_id)
                        return False, message

                if not sent_any:
                    success, message = agent_client.send_domain_policy_chunk(
                        sync_id,
                        source_id_text,
                        desired_revision,
                        [],
                    )
                    if not success:
                        self._abort_domain_policy_sync(agent_client, sync_id)
                        return False, message

            success, message = agent_client.update_domain_policy_manifest(sync_id, device_policies)
            if not success:
                self._abort_domain_policy_sync(agent_client, sync_id)
                return False, message

            success, message = agent_client.finalize_domain_policy_sync(sync_id)
            if not success:
                self._abort_domain_policy_sync(agent_client, sync_id)
                return False, message
            return True, message
        except (OSError, RuntimeError, TypeError, ValueError):
            self._abort_domain_policy_sync(agent_client, sync_id)
            raise

    def _build_domain_policy_mapping_state(self, system_id):
        active_sources = BlocklistSource.query.filter_by(is_enabled=True).all()
        active_source_ids = {source.id for source in active_sources}
        source_state_map = build_source_state_map(active_sources)
        mappings = ManagedUserDeviceMap.query.filter_by(system_id=system_id).order_by(
            ManagedUserDeviceMap.id.asc(),
        ).all()

        mapping_state = []
        for mapping in mappings:
            assigned_source_ids = _assigned_source_ids_for_user(
                mapping.managed_user,
                active_source_ids=active_source_ids,
            )
            summary = summarize_mapping_blocklist_sync(mapping, source_state_map, assigned_source_ids)
            mapping_state.append((mapping, assigned_source_ids, summary))
        return mapping_state, source_state_map

    def _sync_android_device_policy_system(self, system_id):
        from src.android_device_policy_manager import sync_android_device_policies_for_system

        return sync_android_device_policies_for_system(system_id)

    def _sync_linux_device_policy_system(self, system_id):
        from src.linux_device_policy_manager import sync_linux_device_policies_for_system

        return sync_linux_device_policies_for_system(system_id)

    def _sync_screenshot_policy_system(self, system_id):
        from src.screenshot_settings_manager import sync_screenshot_policies_for_system

        return sync_screenshot_policies_for_system(system_id)

    def _sync_domain_policy_system(self, system_id, agent_source_revisions=None):
        mapping_state, source_state_map = self._build_domain_policy_mapping_state(system_id)
        if not mapping_state:
            return True, 'No managed-user mappings required policy sync'

        needs_sync = any(summary['needs_sync'] for _, _, summary in mapping_state)
        if not needs_sync:
            return True, 'Domain policy already up to date'

        success, message = self._sync_domain_policy_device(
            system_id,
            mapping_state,
            source_state_map,
            source_revisions=agent_source_revisions,
        )
        if success:
            for mapping, assigned_source_ids, summary in mapping_state:
                if assigned_source_ids and mapping.linux_uid is None:
                    mapping.mark_blocklist_sync_failed(
                        'Linux UID is required before domain policy can sync',
                        summary.get('retry_hash'),
                    )
                    continue

                if assigned_source_ids:
                    mapping.mark_blocklist_synced(summary['policy_hash'])
                else:
                    mapping.mark_blocklist_synced(None)
            db.session.commit()
        else:
            for mapping, _, summary in mapping_state:
                mapping.mark_blocklist_sync_failed(
                    message,
                    summary.get('retry_hash'),
                )
            db.session.commit()
        return success, message

    def _sync_domain_policies(self):
        try:
            online_system_ids = AgentConnectionManager.get_online_system_ids()
            if not online_system_ids:
                return
            for system_id in online_system_ids:
                success, message = self._sync_domain_policy_system(system_id)
                if not success:
                    logger.warning(
                        "Domain policy sync failed for device %s: %s",
                        system_id,
                        message,
                    )
        except (RuntimeError, TypeError, ValueError, SQLAlchemyError) as exc:
            logger.error(
                "Error synchronizing domain policies: %s\n%s",
                exc,
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

        from src.url_safety import is_safe_outbound_url

        if not is_safe_outbound_url(webhook_settings['url']):
            logger.warning(
                "Alert webhook delivery skipped: configured URL resolves to a private or internal host"
            )
            return

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
            except (
                requests.RequestException,
                RuntimeError,
                TypeError,
                ValueError,
                SQLAlchemyError,
            ) as exc:
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
