import json
import logging
from datetime import datetime, timezone
from src.database import db, AgentAlert, AgentDevice
from src.helpers import (
    _get_device_label_map,
    _mapping_display_label,
    _device_display_label,
)
from src.settings_manager import _get_alert_webhook_settings

_LOGGER = logging.getLogger(__name__)


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
        'details': alert.payload.get('details', {}) if isinstance(alert.payload, dict) else {},
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
        AgentAlert.system_id.in_(system_ids),
        AgentAlert.event_type != 'terminal_command'
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
    alerts = AgentAlert.query.filter_by(system_id=device.system_id).filter(
        AgentAlert.event_type != 'terminal_command'
    ).order_by(
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
        device.last_seen = datetime.now(timezone.utc)

    db.session.commit()
    return alert
