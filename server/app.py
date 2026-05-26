"""Flask application for the Timekpr web UI and agent control plane."""

import hashlib
import json
import logging
import os
import secrets
import threading
from datetime import date, datetime, timedelta

import pytz
from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify, abort
from flask_sock import Sock
from sqlalchemy import func, text
from sqlalchemy.exc import SQLAlchemyError

from src.database import (
    db,
    AgentAlert,
    ManagedUser,
    ManagedUserDeviceMap,
    ManagedUserBlocklistAssignment,
    UserTimeUsage,
    Settings,
    UserWeeklySchedule,
    UserDailyTimeInterval,
    get_mapping_time_spent_for_day,
    get_mapping_time_left_for_day,
    AgentDevice,
    BlocklistSource,
    BlocklistDomain,
    AppArmorRule,
    AppUsageHistory,
)
from src.agent_helper import (
    AgentClient,
    AgentConnectionManager,
    normalize_agent_alert_payload,
)
from src.blocklist_helper import (
    normalize_domain,
    parse_blocklist_text,
    validate_external_source_url,
    build_source_state_map,
    compute_source_revision,
    summarize_mapping_blocklist_sync,
)
from src.task_manager import BackgroundTaskManager
from src.oidc_helper import OIDCHelper

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

def _resolve_local_timezone(timezone_name):
    """Resolve the configured timezone, falling back to UTC when needed."""
    try:
        resolved_timezone = pytz.timezone(timezone_name)
        logging.info("Using timezone: %s", timezone_name)
        return resolved_timezone, timezone_name
    except pytz.exceptions.UnknownTimeZoneError:
        logging.warning("Unknown timezone '%s', falling back to UTC", timezone_name)
        return pytz.UTC, 'UTC'


# Get timezone from environment variable or default to UTC
__version__ = os.environ.get("TIMEKPR_SERVER_VERSION", "v0.0.0-dev")
TIMEZONE_STR = os.environ.get('TZ', 'UTC')
LOCAL_TIMEZONE, TIMEZONE_STR = _resolve_local_timezone(TIMEZONE_STR)

app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timekpr.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize the database
db.init_app(app)

# Initialize WebSocket support
sock = Sock(app)

# Task role flags must be evaluated before creating the task manager.
def _env_flag_enabled(key, default=False):
    raw_value = os.environ.get(key)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {'1', 'true', 'yes', 'on'}


# Initialize background task manager
task_manager = BackgroundTaskManager(
    refresh_external_blocklists=_env_flag_enabled('TIMEKPR_TASKS_REFRESH_EXTERNAL', True),
    update_user_data=_env_flag_enabled('TIMEKPR_TASKS_UPDATE_USER_DATA', True),
    sync_domain_policies=_env_flag_enabled('TIMEKPR_TASKS_SYNC_DOMAIN_POLICIES', True),
    deliver_pending_alerts=_env_flag_enabled('TIMEKPR_TASKS_DELIVER_ALERTS', True),
)
task_manager.init_app(app)
_runtime_init_lock = threading.Lock()
RUNTIME_STATE = {'initialized': False}

# Initialize OIDC helper
oidc_helper = OIDCHelper()

# Admin username remains hardcoded
ADMIN_USERNAME = 'admin'

# Make OIDC status available globally in templates
@app.context_processor
def inject_oidc_status():
    """Inject OIDC status and session user into templates"""
    return {
        'oidc_enabled': oidc_helper.is_enabled,
        'session_user': session.get('user')
    }

# Jinja2 filter to convert UTC datetime to local timezone
@app.template_filter('localtime')
def localtime_filter(dt):
    """Convert UTC datetime to local timezone"""
    if dt is None:
        return None

    # If datetime is naive (no timezone info), assume it's UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.UTC)

    # Convert to local timezone
    local_dt = dt.astimezone(LOCAL_TIMEZONE)
    return local_dt

# Make timezone string available to templates
@app.context_processor
def inject_timezone():
    """Inject timezone info into all templates"""
    return {'timezone': TIMEZONE_STR}


def _format_seconds(seconds):
    if seconds is None:
        return "Unknown"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    return f"{hours}h {minutes}m"


def _mapping_config(mapping):
    if not mapping.last_config:
        return {}
    try:
        return json.loads(mapping.last_config)
    except (TypeError, ValueError):
        return {}


def _hostname_key(hostname):
    normalized = (hostname or '').strip()
    return normalized.casefold() if normalized else None


def _build_device_label_map(devices):
    hostname_counts = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        if key:
            hostname_counts[key] = hostname_counts.get(key, 0) + 1

    label_map = {}
    for device in devices:
        key = _hostname_key(device.system_hostname)
        label_map[device.system_id] = device.format_display_name(
            include_suffix=bool(key and hostname_counts.get(key, 0) > 1)
        )
    return label_map


def _get_device_label_map():
    return _build_device_label_map(AgentDevice.query.all())


def _device_display_label(system_id, label_map=None):
    if not system_id:
        return 'Unknown device'

    labels = label_map if label_map is not None else _get_device_label_map()
    return labels.get(system_id, system_id)


def _mapping_display_label(mapping, label_map=None):
    return f"{mapping.linux_username}@{_device_display_label(mapping.system_id, label_map)}"


def _format_alert_event_label(event_type):
    if not event_type:
        return 'Unknown event'
    return event_type.replace('_', ' ').title()


def _alert_details_to_text(alert):
    payload = alert.payload
    details = payload.get('details', {}) if isinstance(payload, dict) else {}
    if not isinstance(details, dict) or not details:
        return 'No additional details'
    try:
        return json.dumps(details, sort_keys=True)
    except (TypeError, ValueError):
        return str(details)


def _build_alert_entry(alert, device_labels):
    details_text = _alert_details_to_text(alert)
    return {
        'id': alert.id,
        'event_type': alert.event_type,
        'event_label': _format_alert_event_label(alert.event_type),
        'device_label': device_labels.get(alert.system_id, alert.system_id),
        'system_id': alert.system_id,
        'linux_username': alert.linux_username,
        'scope_label': alert.linux_username or 'Device-wide',
        'is_device_wide': alert.linux_username is None,
        'occurred_at': alert.occurred_at,
        'created_at': alert.created_at,
        'delivery_status': alert.delivery_status,
        'delivery_attempts': alert.delivery_attempts,
        'last_delivery_error': alert.last_delivery_error,
        'details_text': details_text,
        'search_blob': ' '.join(
            part for part in [
                str(alert.id),
                alert.event_type or '',
                device_labels.get(alert.system_id, alert.system_id),
                alert.system_id or '',
                alert.linux_username or '',
                alert.delivery_status or '',
                details_text,
                alert.last_delivery_error or '',
            ] if part
        ).lower(),
    }


def _build_user_alert_groups(user, search_query=''):
    device_labels = _get_device_label_map()
    mapping_usernames_by_device = {}
    ordered_group_keys = []
    group_lookup = {}

    for mapping in user.device_mappings:
        mapping_usernames_by_device.setdefault(mapping.system_id, set()).add(mapping.linux_username)
        group_key = f"{mapping.system_id}:{mapping.linux_username}"
        if group_key not in group_lookup:
            group_lookup[group_key] = {
                'key': group_key,
                'title': _mapping_display_label(mapping, device_labels),
                'entries': [],
                'count': 0,
            }
            ordered_group_keys.append(group_key)

    for system_id in mapping_usernames_by_device:
        group_key = f"{system_id}:__device__"
        if group_key not in group_lookup:
            group_lookup[group_key] = {
                'key': group_key,
                'title': f"Device-wide alerts on {_device_display_label(system_id, device_labels)}",
                'entries': [],
                'count': 0,
            }
            ordered_group_keys.append(group_key)

    system_ids = list(mapping_usernames_by_device.keys())
    if not system_ids:
        return [], [], {
            'total': 0,
            'device_wide': 0,
            'account_specific': 0,
        }

    matching_entries = []
    normalized_query = (search_query or '').strip().lower()
    alerts = AgentAlert.query.filter(
        AgentAlert.system_id.in_(system_ids)
    ).order_by(AgentAlert.occurred_at.desc(), AgentAlert.id.desc()).all()

    for alert in alerts:
        allowed_usernames = mapping_usernames_by_device.get(alert.system_id, set())
        if alert.linux_username and alert.linux_username not in allowed_usernames:
            continue

        entry = _build_alert_entry(alert, device_labels)
        if normalized_query and normalized_query not in entry['search_blob']:
            continue

        matching_entries.append(entry)
        group_key = (
            f"{alert.system_id}:__device__"
            if alert.linux_username is None
            else f"{alert.system_id}:{alert.linux_username}"
        )
        group = group_lookup.get(group_key)
        if not group:
            continue
        group['entries'].append(entry)
        group['count'] += 1

    groups = [group_lookup[key] for key in ordered_group_keys if group_lookup[key]['entries']]
    summary = {
        'total': len(matching_entries),
        'device_wide': sum(1 for entry in matching_entries if entry['is_device_wide']),
        'account_specific': sum(1 for entry in matching_entries if not entry['is_device_wide']),
    }
    return groups, matching_entries, summary


def _build_device_alert_entries(device, search_query=''):
    device_labels = _get_device_label_map()
    normalized_query = (search_query or '').strip().lower()
    entries = []
    alerts = AgentAlert.query.filter_by(system_id=device.system_id).order_by(
        AgentAlert.occurred_at.desc(),
        AgentAlert.id.desc(),
    ).all()

    for alert in alerts:
        entry = _build_alert_entry(alert, device_labels)
        if normalized_query and normalized_query not in entry['search_blob']:
            continue
        entries.append(entry)

    counts_by_type = {}
    for entry in entries:
        counts_by_type.setdefault(entry['event_label'], 0)
        counts_by_type[entry['event_label']] += 1

    summary = {
        'total': len(entries),
        'device_wide': sum(1 for entry in entries if entry['is_device_wide']),
        'account_specific': sum(1 for entry in entries if not entry['is_device_wide']),
        'counts_by_type': sorted(counts_by_type.items(), key=lambda item: (-item[1], item[0])),
    }
    return entries, summary


def _refresh_managed_user_summary(user):
    valid_mappings = [mapping for mapping in user.device_mappings if mapping.is_valid]
    user.is_valid = bool(valid_mappings)
    today = date.today()
    effective_daily_limit_seconds = user.get_effective_daily_limit_seconds(today)

    if not valid_mappings:
        user.last_checked = datetime.utcnow()
        user.last_config = json.dumps({
            "TIME_SPENT_DAY": 0,
            "TIME_LEFT_DAY": effective_daily_limit_seconds,
            "MAPPING_COUNT": len(user.device_mappings),
            "ONLINE_MAPPING_COUNT": 0,
        })
        return

    shared_spent = 0
    time_left_values = []
    for mapping in valid_mappings:
        shared_spent += get_mapping_time_spent_for_day(mapping, today)
        time_left = get_mapping_time_left_for_day(mapping, today)
        if time_left is not None:
            time_left_values.append(time_left)

    shared_time_left = (
        max(effective_daily_limit_seconds - shared_spent, 0)
        if effective_daily_limit_seconds is not None
        else (min(time_left_values) if time_left_values else None)
    )

    user.last_checked = max(
        (mapping.last_checked for mapping in valid_mappings if mapping.last_checked),
        default=datetime.utcnow(),
    )
    user.last_config = json.dumps({
        "TIME_SPENT_DAY": shared_spent,
        "TIME_LEFT_DAY": shared_time_left,
        "MAPPING_COUNT": len(user.device_mappings),
        "ONLINE_MAPPING_COUNT": sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        ),
    })

    usage = UserTimeUsage.query.filter_by(user_id=user.id, date=today).first()
    if usage:
        usage.time_spent = shared_spent
    else:
        db.session.add(UserTimeUsage(user_id=user.id, date=today, time_spent=shared_spent))


def _serialize_blocklist_source(
    source,
    *,
    domain_count=0,
    assigned_user_count=0,
    preview_domains=None,
):
    payload = {
        'id': source.id,
        'name': source.name,
        'source_type': source.source_type,
        'source_url': source.source_url,
        'is_enabled': source.is_enabled,
        'domain_count': int(domain_count or 0),
        'assigned_user_count': int(assigned_user_count or 0),
        'last_sync_at': source.last_sync_at.strftime('%Y-%m-%d %H:%M') if source.last_sync_at else None,
        'last_sync_status': source.last_sync_status,
        'last_sync_error': source.last_sync_error,
    }
    if preview_domains is not None:
        payload['domains'] = preview_domains
    return payload


def _get_blocklist_sources(include_domains=False, enabled_only=False, preview_limit=25):
    domain_count_subquery = db.session.query(
        BlocklistDomain.source_id.label('source_id'),
        # Pylint misidentifies SQLAlchemy's dynamic func.count() as non-callable.
        # pylint: disable-next=not-callable
        func.count(BlocklistDomain.id).label('domain_count'),
    ).group_by(BlocklistDomain.source_id).subquery()

    assignment_count_subquery = db.session.query(
        ManagedUserBlocklistAssignment.source_id.label('source_id'),
        # Pylint misidentifies SQLAlchemy's dynamic func.count() as non-callable.
        # pylint: disable-next=not-callable
        func.count(ManagedUserBlocklistAssignment.id).label('assignment_count'),
    ).group_by(ManagedUserBlocklistAssignment.source_id).subquery()

    query = db.session.query(
        BlocklistSource,
        func.coalesce(domain_count_subquery.c.domain_count, 0).label('domain_count'),
        func.coalesce(assignment_count_subquery.c.assignment_count, 0).label('assignment_count'),
    ).outerjoin(
        domain_count_subquery,
        domain_count_subquery.c.source_id == BlocklistSource.id,
    ).outerjoin(
        assignment_count_subquery,
        assignment_count_subquery.c.source_id == BlocklistSource.id,
    )
    if enabled_only:
        query = query.filter(BlocklistSource.is_enabled.is_(True))

    source_rows = query.order_by(BlocklistSource.name.asc()).all()

    preview_map = {}
    if include_domains:
        manual_source_ids = [
            source.id
            for source, _, _ in source_rows
            if source.source_type == BlocklistSource.TYPE_MANUAL
        ]
        for source_id in manual_source_ids:
            preview_rows = BlocklistDomain.query.filter_by(source_id=source_id).order_by(
                BlocklistDomain.domain.asc()
            ).limit(preview_limit).all()
            preview_map[source_id] = [
                {'id': domain.id, 'domain': domain.domain}
                for domain in preview_rows
            ]

    return [
        _serialize_blocklist_source(
            source,
            domain_count=domain_count,
            assigned_user_count=assignment_count,
            preview_domains=preview_map.get(source.id) if include_domains else None,
        )
        for source, domain_count, assignment_count in source_rows
    ]


def _get_user_assigned_blocklist_source_ids(user):
    return {
        assignment.source_id
        for assignment in user.blocklist_assignments
        if assignment.source and assignment.source.is_enabled
    }


def _build_user_blocklist_sync_status(user):
    assigned_source_ids = _get_user_assigned_blocklist_source_ids(user)
    active_sources = []
    if assigned_source_ids:
        active_sources = BlocklistSource.query.filter(
            BlocklistSource.id.in_(assigned_source_ids)
        ).all()
    source_state_map = build_source_state_map(active_sources)

    mappings = []
    for mapping in sorted(
        user.device_mappings,
        key=lambda item: (
            _device_display_label(item.system_id).lower(),
            item.linux_username.lower(),
            item.id,
        ),
    ):
        summary = summarize_mapping_blocklist_sync(mapping, source_state_map, assigned_source_ids)
        mappings.append({
            'mapping_id': mapping.id,
            'system_id': mapping.system_id,
            'device_label': _device_display_label(mapping.system_id),
            'linux_username': mapping.linux_username,
            'linux_uid': mapping.linux_uid,
            'status': summary['status'],
            'needs_sync': summary['needs_sync'],
            'effective_domain_count': summary['effective_domain_count'],
            'last_synced': mapping.blocklist_last_synced.strftime('%Y-%m-%d %H:%M') if mapping.blocklist_last_synced else None,
            'last_error': mapping.blocklist_last_error,
        })

    needs_sync = any(mapping['needs_sync'] for mapping in mappings)
    synced_count = sum(1 for mapping in mappings if mapping['status'] == 'synced')
    awaiting_uid_count = sum(1 for mapping in mappings if mapping['status'] == 'awaiting_uid')

    return {
        'assigned_source_ids': sorted(assigned_source_ids),
        'assigned_source_count': len(assigned_source_ids),
        'effective_domain_count': sum(
            int(state.get('domain_count') or 0)
            for state in source_state_map.values()
        ),
        'mapping_count': len(mappings),
        'synced_mapping_count': synced_count,
        'awaiting_uid_count': awaiting_uid_count,
        'needs_sync': needs_sync,
        'mappings': mappings,
    }


INTERVAL_STEP_MINUTES = 15
INTERVAL_DAY_NAMES = {
    1: 'Monday',
    2: 'Tuesday',
    3: 'Wednesday',
    4: 'Thursday',
    5: 'Friday',
    6: 'Saturday',
    7: 'Sunday',
}


def _serialize_interval(interval):
    return {
        'id': interval.id,
        'day_name': interval.get_day_name(),
        'sort_order': interval.sort_order,
        'start_hour': interval.start_hour,
        'start_minute': interval.start_minute,
        'end_hour': interval.end_hour,
        'end_minute': interval.end_minute,
        'is_enabled': interval.is_enabled,
        'is_synced': interval.is_synced,
        'time_range': interval.get_time_range_string(),
        'last_synced': interval.last_synced.strftime('%Y-%m-%d %H:%M') if interval.last_synced else None,
    }


def _normalize_interval_entries(raw_entries):
    if raw_entries is None:
        return []
    if isinstance(raw_entries, dict):
        return [raw_entries]
    if not isinstance(raw_entries, list):
        raise ValueError('Each day must contain a list of intervals')
    return raw_entries


def _build_intervals_for_day(day_of_week, raw_entries):
    if day_of_week not in INTERVAL_DAY_NAMES:
        raise ValueError(f'Invalid day of week: {day_of_week}')

    interval_rows = []
    for raw_interval in _normalize_interval_entries(raw_entries):
        if not isinstance(raw_interval, dict):
            raise ValueError(f'Invalid interval payload for {INTERVAL_DAY_NAMES[day_of_week]}')

        if not bool(raw_interval.get('is_enabled', True)):
            continue

        interval_rows.append(UserDailyTimeInterval(
            day_of_week=day_of_week,
            sort_order=len(interval_rows),
            start_hour=int(raw_interval.get('start_hour', 9)),
            start_minute=int(raw_interval.get('start_minute', 0)),
            end_hour=int(raw_interval.get('end_hour', 17)),
            end_minute=int(raw_interval.get('end_minute', 0)),
            is_enabled=True,
        ))

    ordered_rows = UserDailyTimeInterval.sort_intervals(interval_rows)
    for index, interval in enumerate(ordered_rows):
        interval.sort_order = index

    if not UserDailyTimeInterval.validate_interval_collection(
        ordered_rows,
        step_minutes=INTERVAL_STEP_MINUTES,
    ):
        raise ValueError(
            f'Invalid time intervals for {INTERVAL_DAY_NAMES[day_of_week]}: '
            f'intervals must be ordered, non-overlapping, and use '
            f'{INTERVAL_STEP_MINUTES}-minute increments'
        )

    return ordered_rows


def _build_disabled_interval_placeholder(day_of_week):
    return UserDailyTimeInterval(
        day_of_week=day_of_week,
        sort_order=0,
        start_hour=0,
        start_minute=0,
        end_hour=0,
        end_minute=15,
        is_enabled=False,
    )


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


def _store_agent_alert(system_id, payload):
    webhook_config = _get_alert_webhook_settings()
    delivery_status = (
        AgentAlert.DELIVERY_PENDING
        if webhook_config['is_active']
        else AgentAlert.DELIVERY_DISABLED
    )
    alert = AgentAlert(
        system_id=system_id,
        event_type=payload['event_type'],
        linux_username=payload['linux_username'],
        occurred_at=payload['occurred_at'],
        payload_json=payload['payload_json'],
        webhook_enabled_snapshot=webhook_config['is_active'],
        delivery_status=delivery_status,
    )
    db.session.add(alert)

    device = AgentDevice.query.get(system_id)
    if device:
        device.last_seen = datetime.utcnow()

    db.session.commit()
    return alert


def _close_websocket_connection(ws, system_id, connection_label):
    """Close a websocket connection while swallowing routine disconnect errors."""
    try:
        ws.close()
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        logging.debug(
            "Ignoring %s close failure for %s",
            connection_label,
            system_id,
        )


def ws_agent_handler(ws):
    """
    WebSocket endpoint for client agents.
    Handles dynamic pairing, manual approval review, and HMAC challenge-response handshake.
    """
    remote_ip = request.remote_addr or "127.0.0.1"
    if request.headers.get("X-Forwarded-For"):
        remote_ip = request.headers.get("X-Forwarded-For").split(",")[0].strip()
        
    logging.info("WebSocket connection attempt from %s", remote_ip)
    
    # 1. Await initial "hello" registration message
    system_id = None
    try:
        try:
            hello_msg_raw = ws.receive(timeout=10)
            if not hello_msg_raw:
                logging.warning("Handshake timeout: empty hello message")
                return
                
            hello_msg = json.loads(hello_msg_raw)
            if hello_msg.get("type") != "hello":
                logging.warning(
                    "Unexpected initial message type: %s",
                    hello_msg.get('type'),
                )
                ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Expected 'hello' type"}))
                return
                
            system_id = hello_msg.get("system_id")
            system_hostname = hello_msg.get("system_hostname")
            if isinstance(system_hostname, str):
                system_hostname = system_hostname.strip() or None
            reg_token = hello_msg.get("registration_token")
            
            if not system_id:
                logging.warning("Initial hello missing system_id")
                ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Missing system_id"}))
                return
            
            agent_version = hello_msg.get("agent_version")
            stripped_server = __version__.lstrip('v')
            stripped_agent = agent_version.lstrip('v') if agent_version else ""
            if stripped_agent != stripped_server:
                logging.warning(
                    "Connection rejected: Agent version %s is incompatible with server version %s",
                    agent_version or "unknown",
                    __version__,
                )
                ws.send(json.dumps({
                    "type": "auth_result",
                    "success": False,
                    "message": f"Incompatible agent version. Please update to {__version__}.",
                    "update_required": True,
                    "target_version": __version__
                }))
                return
    
            # 2. Check and enforce Registration Token firewall
            expected_reg_token = AgentConnectionManager.registration_token
            
            with app.app_context():
                # Lookup device in database
                device = AgentDevice.query.get(system_id)
                
                if not device:
                    # If a registration token is required, verify it
                    if expected_reg_token and reg_token != expected_reg_token:
                        logging.warning(
                            "Registration rejected: Invalid registration token from %s",
                            system_id,
                        )
                        ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid registration token"}))
                        return
                    
                    # Register a new device in 'pending' state
                    device = AgentDevice(
                        system_id=system_id,
                        system_hostname=system_hostname,
                        system_ip=remote_ip,
                        status='pending',
                    )
                    db.session.add(device)
                    db.session.commit()
                    logging.info(
                        "New pending device registered: %s from %s",
                        system_id,
                        remote_ip,
                    )
                else:
                    # Existing device, update latest hostname and IP snapshot
                    if "system_hostname" in hello_msg:
                        device.system_hostname = system_hostname
                    device.system_ip = remote_ip
                    db.session.commit()
    
                # 3. Handle device pairing states
                if device.status == 'pending':
                    logging.info("Device %s is PENDING approval. Waiting...", system_id)
                    AgentConnectionManager.register_pending(system_id, ws)
                    ws.send(json.dumps({"type": "pairing_status", "status": "pending"}))
                    
                    # Keep the socket open in pending state, waiting for admin approval trigger
                    try:
                        while True:
                            msg = ws.receive()
                            if not msg:
                                break
                    except (OSError, RuntimeError, ValueError):
                        logging.debug("Pending websocket closed for %s", system_id)
                    return
    
                if device.status == 'rejected':
                    logging.warning(
                        "Connection rejected: Device %s is banned/rejected",
                        system_id,
                    )
                    ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Device rejected/banned"}))
                    return
    
                if device.status == 'approved':
                    # Device is approved! Perform secure challenge-response
                    challenge = secrets.token_hex(32)
                    ws.send(json.dumps({
                        "type": "challenge",
                        "challenge": challenge
                    }))
                    
                    # Wait for authentication signature response
                    auth_msg_raw = ws.receive(timeout=10)
                    if not auth_msg_raw:
                        logging.warning("Handshake timeout for approved device %s", system_id)
                        return
                        
                    auth_msg = json.loads(auth_msg_raw)
                    if auth_msg.get("type") != "register":
                        logging.warning(
                            "Unexpected response type from %s: %s",
                            system_id,
                            auth_msg.get('type'),
                        )
                        return
                        
                    signature = auth_msg.get("signature")
                    if not signature:
                        logging.warning("Handshake from %s missing signature", system_id)
                        return
                        
                    # Verify using device-specific secure token
                    if not AgentConnectionManager.verify_signature(challenge, system_id, signature):
                        logging.warning(
                            "Authentication signature verification failed for device %s",
                            system_id,
                        )
                        ws.send(json.dumps({"type": "auth_result", "success": False, "message": "Invalid authentication signature"}))
                        return
                        
                    # Authentication succeeded! Register active connection
                    AgentConnectionManager.register(system_id, ws, remote_ip)
                    ws.send(json.dumps({"type": "auth_result", "success": True, "message": "Authenticated successfully"}))
                    
                    device.last_seen = datetime.utcnow()
                    db.session.commit()
                    logging.info(
                        "Device %s authenticated successfully. Updated device IP snapshot to %s.",
                        system_id,
                        remote_ip,
                    )
    
        except (
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            SQLAlchemyError,
        ):
            logging.exception("Error during WebSocket handshake / loop for %s", system_id)
            return
    
        # 4. Main message listening loop for approved connections
        try:
            while True:
                msg_raw = ws.receive()
                if not msg_raw:
                    break
                    
                msg = json.loads(msg_raw)
                msg_type = msg.get("type")
                
                if msg_type == "command_response":
                    correlation_id = msg.get("correlation_id")
                    AgentConnectionManager.route_response(correlation_id, msg)
                elif msg_type == "policy_sync_check":
                    source_revisions = msg.get("source_revisions") or {}
                    if not isinstance(source_revisions, dict):
                        source_revisions = {}
                    task_manager.request_domain_policy_sync(
                        system_id,
                        source_revisions=source_revisions,
                        reason='agent_timer',
                    )
                elif msg_type == "alert_event":
                    try:
                        normalized_alert = normalize_agent_alert_payload(system_id, msg)
                        alert = _store_agent_alert(system_id, normalized_alert)
                        logging.info(
                            "Stored alert %s from agent %s as row %s",
                            alert.event_type,
                            system_id,
                            alert.id,
                        )
                        if alert.event_type == 'app_usage':
                            _store_app_usage_from_alert(system_id, normalized_alert)
                    except ValueError as exc:
                        logging.warning(
                            "Rejected invalid alert payload from %s: %s",
                            system_id,
                            exc,
                        )
                    except SQLAlchemyError as exc:
                        db.session.rollback()
                        logging.error(
                            "Failed to store alert payload from %s: %s",
                            system_id,
                            exc,
                        )
                else:
                    logging.warning(
                        "Received unexpected message type from client %s: %s",
                        system_id,
                        msg_type,
                    )
    
        except (OSError, RuntimeError, ValueError) as exc:
            logging.info("WebSocket connection closed for agent %s: %s", system_id, exc)
    finally:
        if system_id:
            AgentConnectionManager.unregister_pending(system_id)
            AgentConnectionManager.unregister(system_id)

sock.route('/ws')(ws_agent_handler)

@app.route('/', methods=['GET', 'POST'])
def login():
    """Render the login page and optionally start the OIDC login flow."""
    # If already logged in, go straight to dashboard
    if session.get('logged_in'):
        return redirect(url_for('dashboard'))

    if oidc_helper.is_enabled:
        # SSO Auto-redirect flow
        state = oidc_helper.generate_state()
        session['oidc_state'] = state
        
        # Generate redirect URI pointing to our callback endpoint
        redirect_uri = url_for('oidc_callback', _external=True)
        
        try:
            auth_url = oidc_helper.get_authorization_url(state, redirect_uri)
            return redirect(auth_url)
        except (KeyError, RuntimeError, ValueError) as exc:
            logging.error("OIDC login redirection failed: %s", exc)
            flash(
                "OIDC Login failed to initialize: OIDC provider is offline or "
                "misconfigured. Falling back to local credentials.",
                "warning",
            )
            return render_template('login.html', error="OIDC provider connection error.")

    # Fallback: Traditional form-based local login
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check admin password using hash comparison
        if username == ADMIN_USERNAME and Settings.check_admin_password(password):
            session['logged_in'] = True
            flash('Login successful!', 'success')
            return redirect(url_for('dashboard'))
        error = 'Invalid credentials. Please try again.'
        flash(error, 'danger')
    
    return render_template('login.html', error=error)

@app.route('/callback')
def oidc_callback():
    """Complete the OIDC callback flow and establish the admin session."""
    if not oidc_helper.is_enabled:
        flash("OIDC is not enabled.", "danger")
        return redirect(url_for('login'))

    state_param = request.args.get('state')
    if not state_param or state_param != session.get('oidc_state'):
        flash("Authentication failed: Invalid state token (CSRF attempt prevented).", "danger")
        return redirect(url_for('login'))

    # Clear state after verification
    session.pop('oidc_state', None)

    code = request.args.get('code')
    if not code:
        flash("Authentication failed: No authorization code returned from provider.", "danger")
        return redirect(url_for('login'))

    try:
        redirect_uri = url_for('oidc_callback', _external=True)
        # Exchange code for tokens
        tokens = oidc_helper.exchange_code(code, redirect_uri)
        access_token = tokens.get('access_token')
        
        # Get user details from userinfo endpoint
        user_info = oidc_helper.get_user_info(access_token)
        
        # Extract details and log in
        session['logged_in'] = True
        session['user'] = {
            'username': user_info.get('preferred_username') or user_info.get('sub') or 'OIDC User',
            'email': user_info.get('email'),
            'name': user_info.get('name')
        }
        
        flash(f"Logged in successfully as {session['user']['username']}!", "success")
        return redirect(url_for('dashboard'))
    except (KeyError, RuntimeError, ValueError) as exc:
        logging.error("OIDC callback processing failed: %s", exc)
        flash(f"Authentication failed: {exc}", "danger")
        return redirect(url_for('login'))

@app.route('/dashboard')
def dashboard():
    """Render the main dashboard with user status and recent usage data."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get all valid users - make sure we're getting fresh data by expiring SQLAlchemy's cache
    db.session.expire_all()
    users = ManagedUser.query.all()
    
    # Track users with pending time adjustments
    pending_adjustments = {}
    
    # Prepare user data for the dashboard
    user_data = []
    for user in users:
        # Get usage data for charts
        usage_data = user.get_recent_usage(days=7)
        mapping_count = len(user.device_mappings)
        online_mapping_count = sum(
            1 for mapping in user.device_mappings if AgentConnectionManager.is_online(mapping.system_id)
        )
        valid_mapping_count = sum(1 for mapping in user.device_mappings if mapping.is_valid)
        time_left_formatted = _format_seconds(user.get_config_value('TIME_LEFT_DAY'))
        
        # Check for pending time adjustments
        if user.pending_time_adjustment is not None and user.pending_time_operation is not None:
            minutes = user.pending_time_adjustment // 60
            operation = user.pending_time_operation
            pending_adjustments[str(user.id)] = f"{operation}{minutes} minutes"
        
        user_data.append({
            'id': user.id,
            'username': user.username,
            'is_online': online_mapping_count > 0,
            'mapping_count': mapping_count,
            'online_mapping_count': online_mapping_count,
            'valid_mapping_count': valid_mapping_count,
            'last_checked': user.last_checked,
            'usage_data': usage_data,
            'time_left': time_left_formatted,
            'weekly_schedule': user.weekly_schedule
        })

    users_sorted = sorted(user_data, key=lambda item: item['username'].lower())
    return render_template('dashboard.html', users=users_sorted, pending_adjustments=pending_adjustments)

@app.route('/admin')
def admin():
    """Render the administration page for users and agent devices."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get all managed users
    users = ManagedUser.query.order_by(ManagedUser.username.asc()).all()
    device_labels = _get_device_label_map()
    approved_devices = AgentDevice.query.filter_by(status='approved').all()
    pending_devices = AgentDevice.query.filter_by(status='pending').all()
    return render_template(
        'admin.html',
        users=users,
        approved_devices=approved_devices,
        pending_devices=pending_devices,
        device_labels=device_labels,
    )

@app.route('/api/device/approve/<system_id>', methods=['POST'])
def approve_device(system_id):
    """Approve a pending device and send its pairing token if connected."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
    if device.status != 'pending':
        return jsonify({'success': False, 'message': f'Device is not pending (status: {device.status})'}), 400
        
    # Generate 64-character token (secrets.token_hex(32))
    secure_token = secrets.token_hex(32)
    device.secure_token = secure_token
    device.status = 'approved'
    db.session.commit()
    device_label = _device_display_label(system_id)
    
    # Check if there is an active pending connection
    ws = AgentConnectionManager.get_pending_connection(system_id)
    if ws:
        try:
            ws.send(json.dumps({
                "type": "pairing_approved",
                "token": secure_token
            }))
            # Clean up pending connections
            AgentConnectionManager.unregister_pending(system_id)
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            logging.error(
                "Failed to send pairing_approved to device %s: %s",
                system_id,
                exc,
            )

    logging.info("Approved device %s and generated secure token.", system_id)
    return jsonify({'success': True, 'message': f'Device {device_label} approved successfully.'})

@app.route('/api/device/reject/<system_id>', methods=['POST'])
def reject_device(system_id):
    """Reject a device and close any pending or active websocket sessions."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
        
    device = AgentDevice.query.get(system_id)
    if not device:
        return jsonify({'success': False, 'message': 'Device not found'}), 404
        
    device.status = 'rejected'
    device.secure_token = None
    db.session.commit()
    device_label = _device_display_label(system_id)
    
    # Close any active or pending connection
    ws_pending = AgentConnectionManager.get_pending_connection(system_id)
    if ws_pending:
        _close_websocket_connection(ws_pending, system_id, 'pending connection')
        AgentConnectionManager.unregister_pending(system_id)
        
    ws_active = AgentConnectionManager.get_connection(system_id)
    if ws_active:
        _close_websocket_connection(ws_active, system_id, 'active connection')
        AgentConnectionManager.unregister(system_id)
        
    logging.info("Rejected device %s and closed connections.", system_id)
    return jsonify({'success': True, 'message': f'Device {device_label} rejected successfully.'})

@app.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))

    alert_webhook_settings = _get_alert_webhook_settings()
    blocklist_sources = _get_blocklist_sources(include_domains=True)

    if request.method == 'POST':
        form_name = (request.form.get('form_name') or 'password').strip()

        if form_name == 'alert_webhook':
            webhook_enabled = request.form.get('alert_webhook_enabled') == 'on'
            webhook_url = (request.form.get('alert_webhook_url') or '').strip()
            webhook_secret = (request.form.get('alert_webhook_secret') or '').strip()

            if webhook_enabled and not webhook_url:
                flash('Webhook URL is required when alert delivery is enabled', 'danger')
            else:
                Settings.set_value('alert_webhook_enabled', '1' if webhook_enabled else '0')
                Settings.set_value('alert_webhook_url', webhook_url)
                Settings.set_value('alert_webhook_secret', webhook_secret)
                flash('Alert webhook settings updated successfully', 'success')
                return redirect(url_for('settings'))

            alert_webhook_settings = {
                'enabled': webhook_enabled,
                'url': webhook_url,
                'secret': webhook_secret,
                'is_active': webhook_enabled and bool(webhook_url),
            }
        else:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password or not new_password or not confirm_password:
                flash('All fields are required', 'danger')
            elif not Settings.check_admin_password(current_password):
                flash('Current password is incorrect', 'danger')
            elif new_password != confirm_password:
                flash('New passwords do not match', 'danger')
            elif len(new_password) < 4:
                flash('New password must be at least 4 characters long', 'danger')
            else:
                Settings.set_admin_password(new_password)
                flash('Password updated successfully', 'success')
                return redirect(url_for('settings'))

    return render_template(
        'settings.html',
        alert_webhook_settings=alert_webhook_settings,
        blocklist_sources=blocklist_sources,
    )


@app.route('/blocklists/sources/add', methods=['POST'])
def create_blocklist_source():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    name = (request.form.get('name') or '').strip()
    source_type = (request.form.get('source_type') or BlocklistSource.TYPE_MANUAL).strip()
    source_url = (request.form.get('source_url') or '').strip()
    manual_domains_raw = request.form.get('manual_domains') or ''

    if not name:
        flash('Blocklist name is required', 'danger')
        return redirect(url_for('settings'))

    existing = BlocklistSource.query.filter_by(name=name).first()
    if existing:
        flash(f'Blocklist "{name}" already exists', 'warning')
        return redirect(url_for('settings'))

    if source_type not in {BlocklistSource.TYPE_MANUAL, BlocklistSource.TYPE_EXTERNAL_URL}:
        flash('Unsupported blocklist source type', 'danger')
        return redirect(url_for('settings'))

    validated_url = None
    domains = []
    try:
        if source_type == BlocklistSource.TYPE_EXTERNAL_URL:
            validated_url = validate_external_source_url(source_url)
        else:
            domains, _ = parse_blocklist_text(manual_domains_raw, strict=True)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('settings'))

    source = BlocklistSource(
        name=name,
        source_type=source_type,
        source_url=validated_url,
        is_enabled=True,
        content_revision=compute_source_revision(domains),
    )
    db.session.add(source)
    db.session.flush()

    for domain in domains:
        db.session.add(BlocklistDomain(source_id=source.id, domain=domain))

    db.session.commit()

    if source.source_type == BlocklistSource.TYPE_EXTERNAL_URL:
        success, message = task_manager.refresh_external_blocklist_source(source.id)
        flash(message, 'success' if success else 'warning')
    else:
        task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
        flash(f'Blocklist "{source.name}" created with {len(domains)} domain(s)', 'success')

    return redirect(url_for('settings'))


@app.route('/blocklists/sources/<int:source_id>/delete', methods=['POST'])
def delete_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source_row = db.session.query(
        BlocklistSource.id,
        BlocklistSource.name,
    ).filter_by(id=source_id).first()
    if source_row is None:
        abort(404)

    source_name = source_row.name
    ManagedUserBlocklistAssignment.query.filter_by(source_id=source_id).delete(
        synchronize_session=False
    )
    BlocklistDomain.query.filter_by(source_id=source_id).delete(
        synchronize_session=False
    )
    BlocklistSource.query.filter_by(id=source_id).delete(
        synchronize_session=False
    )
    db.session.commit()
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Blocklist "{source_name}" deleted', 'success')
    return redirect(url_for('settings'))


@app.route('/blocklists/sources/<int:source_id>/refresh', methods=['POST'])
def refresh_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    success, message = task_manager.refresh_external_blocklist_source(source_id, force=True)
    flash(message, 'success' if success else 'warning')
    return redirect(url_for('settings'))


@app.route('/blocklists/sources/<int:source_id>/toggle', methods=['POST'])
def toggle_blocklist_source(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    source.is_enabled = request.form.get('is_enabled') == 'on'
    source.updated_at = datetime.utcnow()
    db.session.commit()
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Blocklist "{source.name}" {"enabled" if source.is_enabled else "disabled"}', 'success')
    return redirect(url_for('settings'))


@app.route('/blocklists/sources/<int:source_id>/domains/add', methods=['POST'])
def add_blocklist_domain(source_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    if source.source_type != BlocklistSource.TYPE_MANUAL:
        flash('Only manual blocklists support direct domain editing', 'warning')
        return redirect(url_for('settings'))

    raw_domain = request.form.get('domain')
    try:
        domain = normalize_domain(raw_domain)
    except ValueError as exc:
        flash(str(exc), 'danger')
        return redirect(url_for('settings'))

    existing = BlocklistDomain.query.filter_by(source_id=source.id, domain=domain).first()
    if existing:
        flash(f'{domain} is already present in "{source.name}"', 'warning')
        return redirect(url_for('settings'))

    db.session.add(BlocklistDomain(source_id=source.id, domain=domain))
    source.content_revision = compute_source_revision(
        row.domain
        for row in BlocklistDomain.query.with_entities(BlocklistDomain.domain).filter_by(
            source_id=source.id
        )
    )
    source.updated_at = datetime.utcnow()
    db.session.commit()
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Added {domain} to "{source.name}"', 'success')
    return redirect(url_for('settings'))


@app.route('/blocklists/sources/<int:source_id>/domains/<int:domain_id>/delete', methods=['POST'])
def delete_blocklist_domain(source_id, domain_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    source = BlocklistSource.query.get_or_404(source_id)
    domain = BlocklistDomain.query.filter_by(id=domain_id, source_id=source.id).first_or_404()
    domain_text = domain.domain
    db.session.delete(domain)
    db.session.flush()
    source.content_revision = compute_source_revision(
        row.domain
        for row in BlocklistDomain.query.with_entities(BlocklistDomain.domain).filter_by(
            source_id=source.id
        )
    )
    source.updated_at = datetime.utcnow()
    db.session.commit()
    task_manager.notify_domain_policy_hint(reason='blocklist_catalog_updated')
    flash(f'Removed {domain_text} from "{source.name}"', 'success')
    return redirect(url_for('settings'))


@app.route('/managed-users/<int:user_id>/blocklists/update', methods=['POST'])
def update_user_blocklists(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    selected_ids = {
        int(raw_id)
        for raw_id in request.form.getlist('source_ids')
        if str(raw_id).strip().isdigit()
    }

    valid_sources = {
        source.id: source
        for source in BlocklistSource.query.filter(
            BlocklistSource.id.in_(selected_ids),
            BlocklistSource.is_enabled.is_(True),
        ).all()
    } if selected_ids else {}

    if selected_ids and len(valid_sources) != len(selected_ids):
        flash('One or more selected blocklists no longer exist', 'danger')
        return redirect(url_for('weekly_schedule_user', user_id=user.id))

    current_ids = {assignment.source_id for assignment in user.blocklist_assignments}
    for assignment in list(user.blocklist_assignments):
        if assignment.source_id not in selected_ids:
            db.session.delete(assignment)

    for source_id in sorted(selected_ids - current_ids):
        db.session.add(ManagedUserBlocklistAssignment(managed_user_id=user.id, source_id=source_id))

    db.session.commit()
    task_manager.notify_domain_policy_hint(reason='blocklist_assignment_updated')
    flash(f'Updated blocklist assignments for {user.username}', 'success')
    return redirect(url_for('weekly_schedule_user', user_id=user.id))

@app.route('/api/task-status')
def get_task_status():
    """Get the status of the background task manager"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    status = task_manager.get_status()
    return jsonify({
        'success': True,
        'status': status
    })

@app.route('/restart-tasks')
def restart_tasks():
    """Restart the background task manager"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    task_manager.restart()
    flash('Background tasks restarted', 'success')
    
    # Redirect back to the referring page
    referrer = request.referrer
    if referrer:
        return redirect(referrer)
    return redirect(url_for('dashboard'))

@app.route('/logout')
def logout():
    """Clear the current session and redirect back to the login page."""
    session.pop('logged_in', None)
    session.pop('user', None)
    if oidc_helper.is_enabled:
        return redirect(url_for('login'))
    flash('You have been logged out', 'info')
    return redirect(url_for('login'))

@app.route('/managed-users/add', methods=['POST'])
def create_managed_user():
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    if not username:
        flash('Managed user name is required', 'danger')
        return redirect(url_for('admin'))

    existing_user = ManagedUser.query.filter_by(username=username).first()
    if existing_user:
        flash(f'Managed user {username} already exists', 'warning')
        return redirect(url_for('admin'))

    managed_user = ManagedUser(
        username=username,
        is_valid=False,
        system_ip='Unassigned',
    )
    db.session.add(managed_user)
    db.session.commit()

    flash(f'Managed user {username} created', 'success')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/add', methods=['POST'])
def add_user_mapping(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    system_id = (request.form.get('system_id') or '').strip()
    linux_username = (request.form.get('linux_username') or '').strip()
    linux_uid_raw = (request.form.get('linux_uid') or '').strip()

    if not system_id or not linux_username:
        flash('Device and Linux username are required', 'danger')
        return redirect(url_for('admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('admin'))

    device_label = _device_display_label(system_id)
    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        flash(f'{user.username} is already linked to {device_label}', 'warning')
        return redirect(url_for('admin'))

    linux_uid = None
    if linux_uid_raw:
        try:
            linux_uid = int(linux_uid_raw)
        except ValueError:
            flash('Linux UID must be numeric', 'danger')
            return redirect(url_for('admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=linux_username,
        linux_uid=linux_uid,
        is_valid=False,
    )
    db.session.add(mapping)
    db.session.commit()
    task_manager.notify_domain_policy_hint(system_ids={system_id}, reason='mapping_updated')

    flash(f'Mapping added: {user.username} -> {linux_username}@{device_label}', 'success')
    return redirect(url_for('admin'))


@app.route('/users/add', methods=['GET', 'POST'])
def add_user():
    """
    Backward-compatible endpoint that creates a managed user and one mapping.
    """
    if request.method == 'GET':
        return redirect(url_for('admin'))
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    username = (request.form.get('username') or '').strip()
    system_id = (request.form.get('system_id') or '').strip()

    if not username or not system_id:
        flash('Both username and device are required', 'danger')
        return redirect(url_for('admin'))

    device = AgentDevice.query.get(system_id)
    if not device or device.status != 'approved':
        flash(f'Device {_device_display_label(system_id)} is not registered or approved', 'danger')
        return redirect(url_for('admin'))

    device_label = _device_display_label(system_id)
    user = ManagedUser.query.filter_by(username=username).first()
    if not user:
        user = ManagedUser(username=username, is_valid=False, system_ip='Unassigned')
        db.session.add(user)
        db.session.flush()

    existing_mapping = ManagedUserDeviceMap.query.filter_by(
        managed_user_id=user.id,
        system_id=system_id,
    ).first()
    if existing_mapping:
        db.session.rollback()
        flash(f'User {username} on {device_label} already exists', 'warning')
        return redirect(url_for('admin'))

    mapping = ManagedUserDeviceMap(
        managed_user_id=user.id,
        system_id=system_id,
        linux_username=username,
    )
    db.session.add(mapping)
    db.session.commit()
    task_manager.notify_domain_policy_hint(system_ids={system_id}, reason='mapping_updated')
    flash(f'Managed user {username} and mapping added', 'success')
    return redirect(url_for('admin'))


@app.route('/users/validate/<int:user_id>')
def validate_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mappings = list(user.device_mappings)
    if not mappings:
        flash('No device mappings configured for this managed user', 'warning')
        return redirect(url_for('admin'))

    total_valid = 0
    messages = []
    device_labels = _get_device_label_map()
    policy_hint_system_ids = set()
    for mapping in mappings:
        previous_linux_uid = mapping.linux_uid
        agent_client = AgentClient(system_id=mapping.system_id)
        is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)
        mapping.last_checked = datetime.utcnow()
        mapping.is_valid = is_valid
        if is_valid and config_dict:
            mapping.last_config = json.dumps(config_dict)
            if config_dict.get("LINUX_UID") is not None:
                try:
                    mapping.linux_uid = int(config_dict.get("LINUX_UID"))
                except (TypeError, ValueError):
                    pass
            if mapping.linux_uid != previous_linux_uid:
                policy_hint_system_ids.add(mapping.system_id)
            total_valid += 1
        else:
            messages.append(f"{_mapping_display_label(mapping, device_labels)}: {message}")

    _refresh_managed_user_summary(user)

    db.session.commit()
    if policy_hint_system_ids:
        task_manager.notify_domain_policy_hint(
            system_ids=policy_hint_system_ids,
            reason='mapping_updated',
        )
    if total_valid:
        flash(f'Validated {total_valid}/{len(mappings)} mapping(s) for {user.username}', 'success')
    else:
        flash(f'User validation failed: {"; ".join(messages) if messages else "No mappings validated"}', 'danger')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/validate')
def validate_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    agent_client = AgentClient(system_id=mapping.system_id)
    is_valid, message, config_dict = agent_client.validate_user(mapping.linux_username)

    previous_linux_uid = mapping.linux_uid
    mapping.last_checked = datetime.utcnow()
    mapping.is_valid = is_valid
    if is_valid and config_dict:
        mapping.last_config = json.dumps(config_dict)
        if config_dict.get("LINUX_UID") is not None:
            try:
                mapping.linux_uid = int(config_dict.get("LINUX_UID"))
            except (TypeError, ValueError):
                pass

    _refresh_managed_user_summary(user)
    db.session.commit()
    if mapping.linux_uid != previous_linux_uid:
        task_manager.notify_domain_policy_hint(
            system_ids={mapping.system_id},
            reason='mapping_updated',
        )
    device_labels = _get_device_label_map()

    if is_valid:
        flash(f'Mapping validated: {_mapping_display_label(mapping, device_labels)}', 'success')
    else:
        flash(f'Mapping validation failed: {message}', 'danger')
    return redirect(url_for('admin'))


@app.route('/managed-users/<int:user_id>/mappings/<int:mapping_id>/delete', methods=['POST'])
def delete_mapping(user_id, mapping_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    mapping = ManagedUserDeviceMap.query.filter_by(id=mapping_id, managed_user_id=user.id).first_or_404()
    mapping_label = _mapping_display_label(mapping)
    affected_system_id = mapping.system_id
    db.session.delete(mapping)
    db.session.flush()
    _refresh_managed_user_summary(user)
    db.session.commit()
    task_manager.notify_domain_policy_hint(system_ids={affected_system_id}, reason='mapping_updated')
    flash(f'Mapping removed: {mapping_label}', 'success')
    return redirect(url_for('admin'))

@app.route('/users/delete/<int:user_id>', methods=['POST'])
def delete_user(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    username = user.username
    affected_system_ids = {mapping.system_id for mapping in user.device_mappings}
    
    db.session.delete(user)
    db.session.commit()
    if affected_system_ids:
        task_manager.notify_domain_policy_hint(system_ids=affected_system_ids, reason='mapping_updated')
    
    flash(f'User {username} removed successfully', 'success')
    return redirect(url_for('admin'))

@app.route('/api/user/<int:user_id>/usage')
def get_user_usage(user_id):
    """API endpoint to get user usage data"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    days = request.args.get('days', 7, type=int)
    
    usage_data = user.get_recent_usage(days=days)
    
    # Format for chart.js
    labels = list(usage_data.keys())
    values = list(usage_data.values())
    
    # Convert seconds to hours for better readability
    values_hours = [round(v / 3600, 1) for v in values]
    
    return jsonify({
        'success': True,
        'labels': labels,
        'values': values_hours,
        'username': user.username
    })

@app.route('/weekly-schedule/<int:user_id>')
def weekly_schedule_user(user_id):
    """Display weekly schedule management page for a specific user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))
    
    # Get the specific user
    user = ManagedUser.query.get_or_404(user_id)
    
    # Ensure the user has a weekly schedule record
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.commit()
    
    blocklist_sync_status = _build_user_blocklist_sync_status(user)
    return render_template(
        'weekly_schedule_single.html',
        user=user,
        blocklist_sources=_get_blocklist_sources(include_domains=False, enabled_only=True),
        blocklist_sync_status=blocklist_sync_status,
    )

@app.route('/weekly-schedule/update', methods=['POST'])
def update_weekly_schedule():
    """Update weekly schedule for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user_id = request.form.get('user_id')
    
    if not user_id:
        flash('User ID is required', 'danger')
        return redirect(url_for('admin'))
    
    try:
        user_id = int(user_id)
    except ValueError:
        flash('Invalid user ID', 'danger')
        return redirect(url_for('admin'))
    
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
        schedule_data[day] = hours  # Store as float hours to support fractional hours
    
    # Get or create weekly schedule
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.flush()  # Get the ID
        user.weekly_schedule = schedule
    else:
        schedule = user.weekly_schedule
    
    # Update the schedule
    schedule.set_schedule_from_dict(schedule_data)
    
    try:
        db.session.commit()
        flash(f'Weekly schedule updated for {user.username}', 'success')
    except SQLAlchemyError as exc:
        db.session.rollback()
        flash(f'Error updating schedule: {exc}', 'danger')
    
    return redirect(url_for('weekly_schedule_user', user_id=user.id))

@app.route('/api/user/<int:user_id>/intervals')
def get_user_intervals(user_id):
    """API endpoint to get user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get all intervals for this user
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

@app.route('/api/user/<int:user_id>/intervals/update', methods=['POST'])
def update_user_intervals(user_id):
    """API endpoint to update user time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    try:
        # Get interval data from request
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

@app.route('/api/user/<int:user_id>/intervals/sync-status')
def get_intervals_sync_status(user_id):
    """Get sync status of user's time intervals"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    user = ManagedUser.query.get_or_404(user_id)
    
    # Get all intervals for this user
    intervals = UserDailyTimeInterval.query.filter_by(user_id=user.id).all()
    
    # Check if any intervals need sync
    needs_sync = any(not interval.is_synced for interval in intervals)
    
    # Get last sync time (most recent among all intervals)
    last_synced = None
    if intervals:
        synced_intervals = [i for i in intervals if i.last_synced]
        if synced_intervals:
            last_synced = max(i.last_synced for i in synced_intervals)
            last_synced = last_synced.strftime('%Y-%m-%d %H:%M')
    
    # Count enabled vs total intervals
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


@app.route('/api/user/<int:user_id>/blocklists/sync-status')
def get_blocklist_sync_status(user_id):
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    user = ManagedUser.query.get_or_404(user_id)
    status = _build_user_blocklist_sync_status(user)
    return jsonify({
        'success': True,
        'needs_sync': status['needs_sync'],
        'assigned_source_count': status['assigned_source_count'],
        'effective_domain_count': status['effective_domain_count'],
        'mapping_count': status['mapping_count'],
        'synced_mapping_count': status['synced_mapping_count'],
        'awaiting_uid_count': status['awaiting_uid_count'],
        'mappings': status['mappings'],
    })

@app.route('/api/schedule-sync-status/<int:user_id>')
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
        'is_synced': True,  # No schedule means no sync needed
        'schedule': None,
        'last_synced': None,
        'last_modified': None
    })

@app.route('/stats/<int:user_id>')
def user_stats(user_id):
    """Display extended usage history for a single user"""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))

    user = ManagedUser.query.get_or_404(user_id)

    daily_30   = user.get_recent_usage(days=30)
    weekly_13  = user.get_usage_weekly_grouped(weeks=13)
    monthly_12 = user.get_usage_monthly_grouped(months=12)
    all_monthly = user.get_all_usage_monthly()
    alert_search = (request.args.get('alert_search') or '').strip()
    alert_groups, alert_entries, alert_summary = _build_user_alert_groups(user, search_query=alert_search)
    device_labels = _get_device_label_map()

    return render_template('stats.html',
        user=user,
        daily_30=daily_30,
        weekly_13=weekly_13,
        monthly_12=monthly_12,
        all_monthly=all_monthly,
        alert_search=alert_search,
        alert_groups=alert_groups,
        alert_entries=alert_entries,
        alert_summary=alert_summary,
        device_labels=device_labels,
    )


@app.route('/devices/<system_id>')
def device_detail(system_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))

    device = AgentDevice.query.get_or_404(system_id)
    alert_search = (request.args.get('alert_search') or '').strip()
    alert_entries, alert_summary = _build_device_alert_entries(device, search_query=alert_search)
    device_labels = _get_device_label_map()
    mapped_accounts = sorted(
        device.user_mappings,
        key=lambda mapping: (
            mapping.managed_user.username.lower() if mapping.managed_user else '',
            mapping.linux_username.lower(),
            mapping.id,
        ),
    )
    blocklist_contributors = []
    for mapping in mapped_accounts:
        user = mapping.managed_user
        assigned_source_ids = _get_user_assigned_blocklist_source_ids(user)
        if not assigned_source_ids:
            continue
        status = _build_user_blocklist_sync_status(user)
        blocklist_contributors.append({
            'managed_user': user.username,
            'linux_username': mapping.linux_username,
            'linux_uid': mapping.linux_uid,
            'assigned_source_count': status['assigned_source_count'],
            'effective_domain_count': status['effective_domain_count'],
            'sync_status': next(
                (
                    item['status']
                    for item in status['mappings']
                    if item['mapping_id'] == mapping.id
                ),
                'pending',
            ),
        })

    usage_summaries = {}
    for mapping in mapped_accounts:
        usage_summaries[mapping.id] = _get_apparmor_usage_summary(mapping.id)

    return render_template(
        'device_detail.html',
        device=device,
        device_label=device_labels.get(system_id, device.display_name),
        mapped_accounts=mapped_accounts,
        blocklist_contributors=blocklist_contributors,
        alert_search=alert_search,
        alert_entries=alert_entries,
        alert_summary=alert_summary,
        usage_summaries=usage_summaries,
    )

@app.route('/api/modify-time', methods=['POST'])
def modify_time():
    """Modify time left for a user"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    # Get parameters from request
    user_id = request.form.get('user_id')
    operation = request.form.get('operation')
    seconds = request.form.get('seconds')
    
    if not user_id or not operation or not seconds:
        return jsonify({'success': False, 'message': 'Missing required parameters'}), 400
    
    try:
        user_id = int(user_id)
        seconds = int(seconds)
    except ValueError:
        return jsonify({'success': False, 'message': 'Invalid parameter format'}), 400
    
    # Validate operation
    if operation not in ['+', '-']:
        return jsonify({'success': False, 'message': "Operation must be '+' or '-'"}), 400
    
    # Get user from database
    user = ManagedUser.query.get_or_404(user_id)
    today = date.today()
    effective_daily_limit_before_adjustment = user.get_effective_daily_limit_seconds(today)
    
    mappings = list(user.device_mappings)
    if not mappings:
        return jsonify({'success': False, 'message': 'No device mappings configured for this user'}), 400

    if effective_daily_limit_before_adjustment is not None:
        user.apply_daily_limit_adjustment(operation, seconds, today)
        user.pending_time_adjustment = None
        user.pending_time_operation = None
        user.last_checked = datetime.utcnow()
        db.session.commit()

        online_mappings = [mapping for mapping in mappings if AgentConnectionManager.is_online(mapping.system_id)]
        device_labels = _get_device_label_map()
        if not online_mappings:
            return jsonify({
                'success': True,
                'message': 'Adjustment saved on the server and will rebalance when a mapped device reconnects.',
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
                pending_fragments.append(f"{len(failures)} online mapping(s) need retry")
            if remaining_mappings > 0:
                pending_fragments.append(f"{remaining_mappings} offline mapping(s) will rebalance on reconnect")
            return jsonify({
                'success': True,
                'message': f"Adjustment stored on the server. Applied immediately to {len(online_mappings) - len(failures)}/{len(online_mappings)} online mapping(s).",
                'details': failures,
                'username': user.username,
                'pending': True,
                'pending_reason': '; '.join(pending_fragments),
                'refresh': True
            })

        return jsonify({
            'success': True,
            'message': f"Adjustment applied to {len(online_mappings)} mapping(s).",
            'username': user.username,
            'pending': False,
            'refresh': True
        })

    online_mappings = [mapping for mapping in mappings if AgentConnectionManager.is_online(mapping.system_id)]
    device_labels = _get_device_label_map()
    if not online_mappings:
        user.pending_time_adjustment = seconds
        user.pending_time_operation = operation
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f"All mapped devices are offline. Adjustment {operation}{seconds}s queued.",
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

    if failures:
        user.pending_time_adjustment = seconds
        user.pending_time_operation = operation
        db.session.commit()
        return jsonify({
            'success': True,
            'message': f"Applied to {len(online_mappings) - len(failures)}/{len(online_mappings)} online mapping(s). Remaining queued.",
            'details': failures,
            'username': user.username,
            'pending': True,
            'refresh': True
        })

    user.pending_time_adjustment = None
    user.pending_time_operation = None
    user.last_checked = datetime.utcnow()
    db.session.commit()
    return jsonify({
        'success': True,
        'message': f"Adjustment applied to {len(online_mappings)} mapping(s).",
        'username': user.username,
        'refresh': True
    })


# ── AppArmor Policy Management ──────────────────────────────────────────

CURATED_APPARMOR_APPS = [
    {'name': 'Firefox',            'path': '/usr/bin/firefox',            'icon': '🦊'},
    {'name': 'Google Chrome',      'path': '/usr/bin/google-chrome',      'icon': '🌐'},
    {'name': 'Steam',              'path': '/usr/bin/steam',              'icon': '🎮'},
    {'name': 'Discord',            'path': '/usr/bin/discord',            'icon': '💬'},
    {'name': 'Minecraft',          'path': '/usr/bin/minecraft-launcher', 'icon': '⛏️'},
    {'name': 'Spotify',            'path': '/usr/bin/spotify',            'icon': '🎵'},
    {'name': 'VLC',                'path': '/usr/bin/vlc',                'icon': '🎬'},
]

CURATED_APPARMOR_PATHS = {app['path'] for app in CURATED_APPARMOR_APPS}


def _store_app_usage_from_alert(system_id, normalized_alert):
    """Persist a structured AppUsageHistory row from an app_usage alert event."""
    details = normalized_alert.get('details', {})
    if not isinstance(details, dict):
        return

    linux_username = normalized_alert.get('linux_username')
    executable_path = (details.get('executable_path') or '').strip()
    application_name = (details.get('application_name') or '').strip() or executable_path
    duration_seconds = details.get('duration_seconds')

    if not linux_username or not executable_path or not isinstance(duration_seconds, (int, float)):
        return

    duration_seconds = max(0, int(duration_seconds))
    mapping = ManagedUserDeviceMap.query.filter_by(
        system_id=system_id,
        linux_username=linux_username,
    ).first()
    if not mapping:
        return

    start_iso = (details.get('start_time') or '').strip()
    end_iso = (details.get('end_time') or '').strip()
    try:
        start_time = datetime.fromisoformat(start_iso.replace('Z', '+00:00')).replace(tzinfo=None)
        end_time = datetime.fromisoformat(end_iso.replace('Z', '+00:00')).replace(tzinfo=None)
    except (TypeError, ValueError):
        end_time = datetime.utcnow()
        from datetime import timedelta as td
        start_time = end_time - td(seconds=duration_seconds)

    record = AppUsageHistory(
        device_map_id=mapping.id,
        application_name=application_name,
        executable_path=executable_path,
        start_time=start_time,
        end_time=end_time,
        duration_seconds=duration_seconds,
    )
    db.session.add(record)
    db.session.commit()
    logging.info(
        "Stored app_usage record for %s@%s: %s (%ds)",
        linux_username, system_id, application_name, duration_seconds,
    )


def _get_apparmor_usage_summary(mapping_id, days=7):
    """Build an aggregate app-usage summary for a mapping over the last N days."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    records = AppUsageHistory.query.filter(
        AppUsageHistory.device_map_id == mapping_id,
        AppUsageHistory.start_time >= cutoff,
    ).all()

    aggregate = {}
    for record in records:
        key = record.executable_path
        entry = aggregate.setdefault(key, {
            'application_name': record.application_name,
            'executable_path': record.executable_path,
            'total_seconds': 0,
            'session_count': 0,
        })
        entry['total_seconds'] += record.duration_seconds
        entry['session_count'] += 1

    result = sorted(aggregate.values(), key=lambda item: -item['total_seconds'])
    for item in result:
        secs = item['total_seconds']
        hours = secs // 3600
        minutes = (secs % 3600) // 60
        if hours > 0:
            item['formatted'] = f"{hours}h {minutes}m"
        else:
            item['formatted'] = f"{minutes}m"
    return result


@app.route('/apparmor/policy/<int:mapping_id>', methods=['GET', 'POST'])
def apparmor_policy(mapping_id):
    """Visual AppArmor policy management for a single device mapping."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('login'))

    mapping = ManagedUserDeviceMap.query.get_or_404(mapping_id)
    user = mapping.managed_user
    device_labels = _get_device_label_map()
    device_label = device_labels.get(mapping.system_id, mapping.system_id)

    if request.method == 'POST':
        # Process preset changes for curated apps
        for app_template in CURATED_APPARMOR_APPS:
            preset = request.form.get(f"preset_{app_template['path']}", 'allowed').strip()
            if preset not in AppArmorRule.VALID_PRESETS:
                preset = AppArmorRule.PRESET_ALLOWED

            existing = AppArmorRule.query.filter_by(
                device_map_id=mapping.id,
                executable_path=app_template['path'],
            ).first()
            if existing:
                existing.preset = preset
                existing.application_name = app_template['name']
                existing.is_custom = False
            else:
                db.session.add(AppArmorRule(
                    device_map_id=mapping.id,
                    application_name=app_template['name'],
                    executable_path=app_template['path'],
                    preset=preset,
                    is_custom=False,
                ))

        # Process custom app additions
        custom_name = (request.form.get('custom_app_name') or '').strip()
        custom_path = (request.form.get('custom_app_path') or '').strip()
        custom_preset = (request.form.get('custom_app_preset') or 'allowed').strip()
        if custom_name and custom_path:
            if custom_preset not in AppArmorRule.VALID_PRESETS:
                custom_preset = AppArmorRule.PRESET_ALLOWED
            existing = AppArmorRule.query.filter_by(
                device_map_id=mapping.id,
                executable_path=custom_path,
            ).first()
            if existing:
                existing.preset = custom_preset
                existing.application_name = custom_name
            else:
                db.session.add(AppArmorRule(
                    device_map_id=mapping.id,
                    application_name=custom_name,
                    executable_path=custom_path,
                    preset=custom_preset,
                    is_custom=True,
                ))

        # Process custom rule presets from the existing list
        custom_rules = AppArmorRule.query.filter_by(
            device_map_id=mapping.id,
            is_custom=True,
        ).all()
        for rule in custom_rules:
            form_key = f"preset_{rule.executable_path}"
            if form_key in request.form:
                new_preset = request.form[form_key].strip()
                if new_preset in AppArmorRule.VALID_PRESETS:
                    rule.preset = new_preset

        db.session.commit()

        # Push the policy to the agent if it is online
        all_rules = AppArmorRule.query.filter_by(device_map_id=mapping.id).all()
        policies_list = [rule.to_sync_dict() for rule in all_rules if rule.is_restrictive]
        if AgentConnectionManager.is_online(mapping.system_id):
            agent = AgentClient(system_id=mapping.system_id)
            success, sync_msg = agent.sync_apparmor_policy(
                mapping.linux_username,
                policies_list,
            )
            if success:
                flash(f'AppArmor policy saved and synced to {device_label}', 'success')
            else:
                flash(f'Policy saved but sync failed: {sync_msg}', 'warning')
        else:
            flash('Policy saved. Will sync when the device reconnects.', 'success')

        return redirect(url_for('apparmor_policy', mapping_id=mapping.id))

    # GET: Build template data
    existing_rules = {
        rule.executable_path: rule
        for rule in AppArmorRule.query.filter_by(device_map_id=mapping.id).all()
    }

    curated_apps = []
    for app_template in CURATED_APPARMOR_APPS:
        rule = existing_rules.get(app_template['path'])
        curated_apps.append({
            'name': app_template['name'],
            'path': app_template['path'],
            'icon': app_template['icon'],
            'preset': rule.preset if rule else AppArmorRule.PRESET_ALLOWED,
        })

    custom_rules = [
        {
            'name': rule.application_name,
            'path': rule.executable_path,
            'preset': rule.preset,
            'id': rule.id,
        }
        for rule in sorted(
            existing_rules.values(),
            key=lambda r: (r.application_name.lower(), r.id),
        )
        if rule.executable_path not in CURATED_APPARMOR_PATHS
    ]

    usage_summary = _get_apparmor_usage_summary(mapping.id)
    is_online = AgentConnectionManager.is_online(mapping.system_id)
    restrictive_count = sum(1 for app in curated_apps if app['preset'] != 'allowed') + \
        sum(1 for rule in custom_rules if rule['preset'] != 'allowed')

    return render_template(
        'apparmor_policy.html',
        mapping=mapping,
        user=user,
        device_label=device_label,
        curated_apps=curated_apps,
        custom_rules=custom_rules,
        usage_summary=usage_summary,
        is_online=is_online,
        restrictive_count=restrictive_count,
    )


@app.route('/apparmor/rule/<int:rule_id>/delete', methods=['POST'])
def delete_apparmor_rule(rule_id):
    """Delete a custom AppArmor rule."""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401

    rule = AppArmorRule.query.get_or_404(rule_id)
    mapping_id = rule.device_map_id
    db.session.delete(rule)
    db.session.commit()
    flash(f'Removed AppArmor rule for {rule.application_name}', 'success')
    return redirect(url_for('apparmor_policy', mapping_id=mapping_id))


def run_schema_migrations():
    """Run lightweight SQLite migrations and backfill mapping table."""
    agent_device_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(agent_device)")).fetchall()
    }
    if agent_device_columns and 'system_hostname' not in agent_device_columns:
        db.session.execute(text("""
            ALTER TABLE agent_device
            ADD COLUMN system_hostname VARCHAR(255) NULL
        """))
        db.session.commit()

    managed_user_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(managed_user)")).fetchall()
    }
    if managed_user_columns and 'daily_limit_adjustment_date' not in managed_user_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user
            ADD COLUMN daily_limit_adjustment_date DATE NULL
        """))
    if managed_user_columns and 'daily_limit_adjustment_seconds' not in managed_user_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user
            ADD COLUMN daily_limit_adjustment_seconds INTEGER NULL
        """))
    db.session.commit()

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS managed_user_device_map (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_user_id INTEGER NOT NULL,
            system_id VARCHAR(50) NOT NULL,
            linux_username VARCHAR(50) NOT NULL,
            linux_uid INTEGER NULL,
            is_valid BOOLEAN DEFAULT 0,
            last_checked DATETIME NULL,
            last_config TEXT NULL,
            date_added DATETIME NULL,
            last_modified DATETIME NULL,
            blocklist_policy_hash VARCHAR(64) NULL,
            blocklist_is_synced BOOLEAN NOT NULL DEFAULT 0,
            blocklist_last_synced DATETIME NULL,
            blocklist_last_attempted DATETIME NULL,
            blocklist_last_attempt_hash VARCHAR(64) NULL,
            blocklist_last_error TEXT NULL,
            FOREIGN KEY(managed_user_id) REFERENCES managed_user(id),
            FOREIGN KEY(system_id) REFERENCES agent_device(system_id),
            UNIQUE(managed_user_id, system_id),
            UNIQUE(system_id, linux_username),
            UNIQUE(system_id, linux_uid)
        )
    """))
    db.session.commit()

    mapping_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(managed_user_device_map)")).fetchall()
    }
    if mapping_columns and 'blocklist_policy_hash' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_policy_hash VARCHAR(64) NULL
        """))
    if mapping_columns and 'blocklist_is_synced' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_is_synced BOOLEAN NOT NULL DEFAULT 0
        """))
    if mapping_columns and 'blocklist_last_synced' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_last_synced DATETIME NULL
        """))
    if mapping_columns and 'blocklist_last_attempted' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_last_attempted DATETIME NULL
        """))
    if mapping_columns and 'blocklist_last_attempt_hash' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_last_attempt_hash VARCHAR(64) NULL
        """))
    if mapping_columns and 'blocklist_last_error' not in mapping_columns:
        db.session.execute(text("""
            ALTER TABLE managed_user_device_map
            ADD COLUMN blocklist_last_error TEXT NULL
        """))
    db.session.commit()

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS blocklist_source (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name VARCHAR(120) NOT NULL UNIQUE,
            source_type VARCHAR(32) NOT NULL,
            source_url TEXT NULL,
            is_enabled BOOLEAN NOT NULL DEFAULT 1,
            last_sync_at DATETIME NULL,
            last_sync_status VARCHAR(32) NOT NULL DEFAULT 'never',
            last_sync_error TEXT NULL,
            etag VARCHAR(255) NULL,
            source_last_modified VARCHAR(255) NULL,
            content_revision VARCHAR(64) NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS blocklist_domain (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            domain VARCHAR(255) NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(source_id) REFERENCES blocklist_source(id),
            UNIQUE(source_id, domain)
        )
    """))
    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS managed_user_blocklist_assignment (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            managed_user_id INTEGER NOT NULL,
            source_id INTEGER NOT NULL,
            created_at DATETIME NOT NULL,
            FOREIGN KEY(managed_user_id) REFERENCES managed_user(id),
            FOREIGN KEY(source_id) REFERENCES blocklist_source(id),
            UNIQUE(managed_user_id, source_id)
        )
    """))
    db.session.commit()

    blocklist_source_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(blocklist_source)")).fetchall()
    }
    if blocklist_source_columns and 'content_revision' not in blocklist_source_columns:
        db.session.execute(text("""
            ALTER TABLE blocklist_source
            ADD COLUMN content_revision VARCHAR(64) NULL
        """))
        db.session.commit()

    legacy_sources = BlocklistSource.query.filter(BlocklistSource.content_revision.is_(None)).all()
    for source in legacy_sources:
        basis = source.updated_at or source.created_at or datetime.utcnow()
        source.content_revision = hashlib.sha256(
            f'legacy:{source.id}:{basis.isoformat()}'.encode('utf-8')
        ).hexdigest()
    if legacy_sources:
        db.session.commit()

    users = ManagedUser.query.filter(ManagedUser.system_id.isnot(None)).all()
    for user in users:
        if not user.system_id:
            continue
        existing = ManagedUserDeviceMap.query.filter_by(
            managed_user_id=user.id,
            system_id=user.system_id,
        ).first()
        if existing:
            continue

        linux_uid = None
        if user.last_config:
            try:
                parsed = json.loads(user.last_config)
                if parsed.get("LINUX_UID") is not None:
                    linux_uid = int(parsed.get("LINUX_UID"))
            except (TypeError, ValueError):
                linux_uid = None

        mapping = ManagedUserDeviceMap(
            managed_user_id=user.id,
            system_id=user.system_id,
            linux_username=user.username,
            linux_uid=linux_uid,
            is_valid=user.is_valid,
            last_checked=user.last_checked,
            last_config=user.last_config,
        )
        db.session.add(mapping)
    db.session.commit()

    interval_columns = {
        row[1]
        for row in db.session.execute(text("PRAGMA table_info(user_daily_time_interval)")).fetchall()
    }
    if interval_columns and 'sort_order' not in interval_columns:
        db.session.execute(text("""
            CREATE TABLE user_daily_time_interval_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                day_of_week INTEGER NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                start_hour INTEGER NOT NULL,
                start_minute INTEGER DEFAULT 0,
                end_hour INTEGER NOT NULL,
                end_minute INTEGER DEFAULT 0,
                is_enabled BOOLEAN DEFAULT 1,
                is_synced BOOLEAN DEFAULT 0,
                last_synced DATETIME NULL,
                last_modified DATETIME NULL,
                FOREIGN KEY(user_id) REFERENCES managed_user(id),
                UNIQUE(user_id, day_of_week, sort_order)
            )
        """))
        db.session.execute(text("""
            INSERT INTO user_daily_time_interval_new (
                id,
                user_id,
                day_of_week,
                sort_order,
                start_hour,
                start_minute,
                end_hour,
                end_minute,
                is_enabled,
                is_synced,
                last_synced,
                last_modified
            )
            SELECT
                id,
                user_id,
                day_of_week,
                0,
                start_hour,
                start_minute,
                end_hour,
                end_minute,
                1,
                is_synced,
                last_synced,
                last_modified
            FROM user_daily_time_interval
            WHERE COALESCE(is_enabled, 1) = 1
        """))
        db.session.execute(text("DROP TABLE user_daily_time_interval"))
        db.session.execute(text("ALTER TABLE user_daily_time_interval_new RENAME TO user_daily_time_interval"))
        db.session.commit()

    db.session.execute(text("""
        CREATE TABLE IF NOT EXISTS agent_alert (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            system_id VARCHAR(50) NOT NULL,
            event_type VARCHAR(64) NOT NULL,
            linux_username VARCHAR(80) NULL,
            occurred_at DATETIME NOT NULL,
            payload_json TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            webhook_enabled_snapshot BOOLEAN NOT NULL DEFAULT 0,
            delivery_status VARCHAR(20) NOT NULL DEFAULT 'pending',
            delivery_attempts INTEGER NOT NULL DEFAULT 0,
            last_delivery_attempt_at DATETIME NULL,
            delivered_at DATETIME NULL,
            last_delivery_error TEXT NULL,
            FOREIGN KEY(system_id) REFERENCES agent_device(system_id)
        )
    """))
    db.session.commit()


def initialize_runtime(start_background_tasks=False):
    """Initialize the runtime database state and optional background workers."""

    if os.environ.get('TESTING'):
        return

    with _runtime_init_lock:
        if not RUNTIME_STATE['initialized']:
            with app.app_context():
                db.create_all()
                run_schema_migrations()
                print("Database tables verified")

                # Initialize admin password if it doesn't exist
                if not Settings.get_value('admin_password_hash', None) and not Settings.get_value('admin_password', None):
                    Settings.set_admin_password('admin')
                    print("Admin password initialized")

            RUNTIME_STATE['initialized'] = True

    if start_background_tasks:
        task_manager.start()
        print("Background tasks started automatically")


if not os.environ.get('TESTING'):
    initialize_runtime(start_background_tasks=_env_flag_enabled('TIMEKPR_ENABLE_BACKGROUND_TASKS'))

if __name__ == '__main__':
    initialize_runtime(start_background_tasks=True)
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)
