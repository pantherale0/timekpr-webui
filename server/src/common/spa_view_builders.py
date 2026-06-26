"""Build template context for SPA fragment routes."""

from flask import request, session, abort

from src.models import ParentAccount

def get_current_parent_and_household():
    """Retrieve the current parent account ID and active household ID from session, with fallback for local admin."""
    parent_id = session.get('parent_account_id')
    active_household_id = session.get('active_household_id')
    if session.get('logged_in') and not parent_id:
        # Fallback for local admin or testing environments
        parent = ParentAccount.query.filter_by(email='admin@local').first()
        if parent:
            parent_id = parent.id
            if not active_household_id and parent.memberships:
                active_household_id = parent.memberships[0].household_id
    return parent_id, active_household_id

from src.agent.helper import AgentConnectionManager
from src.alerts.manager import _build_device_alert_entries, _build_user_alert_groups
from src.policy.apparmor import _get_apparmor_usage_summary
from src.blocklist.manager import (
    _build_user_blocklist_sync_status,
    _get_blocklist_sources,
    _get_user_assigned_blocklist_source_ids,
)
from src.blueprints.api.nintendo import get_nintendo_account_summary
from src.blueprints.api.xbox import get_xbox_account_summary
from src.common.dashboard_helper import build_dashboard_snapshot
from src.models import AgentDevice, AppPolicy, ManagedUser, UserWeeklySchedule, Settings, db
from src.common.helpers import _get_device_label_map, generate_parental_access_code
from src.device.installed_apps import list_installed_apps_for_device, list_installed_apps_for_managed_user
from src.common.nintendo_sync import build_nintendo_console_view_context
from src.agent.pairing import (
    build_agent_websocket_url,
    get_server_version,
    has_uploaded_android_apk,
    pairing_payload_json,
    render_pairing_qr_data_uri,
    resolve_android_provisioning,
)
from src.common.settings import (
    _get_agent_websocket_url,
    _get_alert_retention_days,
    _get_alert_webhook_settings,
    _get_android_agent_apk_filename,
    _get_android_agent_signature_checksum,
    _get_time_sync_tolerance,
    _get_web_history_retention_days,
    _get_youtube_api_key_encrypted,
    _get_youtube_history_retention_days,
)
from src.i18n.catalog import t
from src.common.xbox_sync import build_xbox_console_view_context


def _build_device_protection_summary(
    device,
    mapped_accounts,
    blocklist_contributors,
    alert_summary,
    agent_online,
    android_device_policy=None,
    screenshot_settings=None,
    pending_command_count=0,
):
    platform = (device.platform or 'linux').strip().lower()
    is_cloud_console = platform in {'nintendo', 'xbox'}
    attention_items = []

    if not is_cloud_console and not agent_online:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_offline',
            'href': '#advanced',
            'severity': 'warning',
        })

    if pending_command_count > 0:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_pending_commands',
            'message_params': {'count': pending_command_count},
            'href': '#advanced',
            'severity': 'info',
        })

    unverified_count = sum(1 for mapping in mapped_accounts if not mapping.is_valid)
    if unverified_count:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_unverified_mappings',
            'message_params': {'count': unverified_count},
            'href': '#overview',
            'severity': 'warning',
        })

    alert_total = int((alert_summary or {}).get('total') or 0)
    if alert_total > 0:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_connection_notes',
            'message_params': {'count': alert_total},
            'href': '#advanced',
            'severity': 'info',
        })

    pending_contributors = [
        contributor for contributor in blocklist_contributors
        if contributor.get('sync_status') != 'synced'
    ]
    if pending_contributors:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_filter_sync',
            'message_params': {'count': len(pending_contributors)},
            'href': '#advanced',
            'severity': 'warning',
        })

    if android_device_policy is not None and not android_device_policy.is_synced:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_android_policy',
            'href': '#settings',
            'severity': 'warning',
        })

    if screenshot_settings is not None and not screenshot_settings.is_synced:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_screen_history',
            'href': '#settings',
            'severity': 'warning',
        })

    if not mapped_accounts:
        attention_items.append({
            'message_key': 'pages.device_detail.attention_no_profiles',
            'href': '#overview',
            'severity': 'info',
        })

    if (
        platform in {'linux', 'windows'}
        and (getattr(device, 'hardware_compliance_status', None) or '').strip().lower() == 'non_compliant'
    ):
        attention_items.append({
            'message_key': 'pages.device_detail.attention_hardware_non_compliant',
            'href': '#overview',
            'severity': 'warning',
        })

    offline_only = (
        len(attention_items) == 1
        and attention_items[0]['message_key'] == 'pages.device_detail.attention_offline'
    )
    if attention_items:
        if offline_only:
            status = 'offline'
            label_key = 'pages.device_detail.protection_offline'
        else:
            status = 'needs_attention'
            label_key = 'pages.device_detail.protection_needs_attention'
    else:
        status = 'connected'
        label_key = 'pages.device_detail.protection_connected'

    return {
        'status': status,
        'label': t(label_key),
        'attention_items': [
            {
                **item,
                'message': t(item['message_key'], **item.get('message_params', {})),
            }
            for item in attention_items
        ],
    }


def build_dashboard_context():
    from src.policy.presets import get_matrix_metadata_for_ui
    parent_id, active_hh_id = get_current_parent_and_household()

    db.session.expire_all()
    snapshot = build_dashboard_snapshot(active_household_id=active_hh_id, parent_account_id=parent_id)
    return {
        'template': 'dashboard.html',
        'users': snapshot['users'],
        'pending_adjustments': snapshot['pending_adjustments'],
        'policy_preset_matrix': get_matrix_metadata_for_ui(),
    }


def build_admin_users_context():
    from src.policy.presets import get_matrix_metadata_for_ui
    parent_id, active_hh_id = get_current_parent_and_household()

    users_query = ManagedUser.query
    devices_query = AgentDevice.query.filter_by(status='approved')
    if active_hh_id:
        users_query = users_query.filter_by(household_id=active_hh_id)
        devices_query = devices_query.filter_by(household_id=active_hh_id)

    return {
        'template': 'admin_users.html',
        'users': users_query.order_by(ManagedUser.username.asc()).all(),
        'approved_devices': devices_query.all(),
        'device_labels': _get_device_label_map(),
        'policy_preset_matrix': get_matrix_metadata_for_ui(),
    }


def build_edit_user_profile_context(user_id):
    from src.user.approvals import get_or_create_settings, grant_status_for_apps
    from src.models import MappingLinuxDevicePolicy
    from src.policy.linux import get_or_create_policy as get_or_create_linux_policy
    from src.blocklist.marketplace import load_marketplace_presets
    from src.policy.presets import get_matrix_metadata_for_ui
    from src.common.helpers import check_parent_child_access

    check_parent_child_access(user_id)

    user = ManagedUser.query.get_or_404(user_id)
    blocklist_sync_status = _build_user_blocklist_sync_status(user)
    blocklist_sources = _get_blocklist_sources(include_domains=False, enabled_only=True)
    blocklist_sources = [s for s in blocklist_sources if not s.get('is_marketplace')]
    marketplace_presets = load_marketplace_presets()
    policy_preset_matrix = get_matrix_metadata_for_ui()
    subscribed_preset_ids = [
        assignment.source.preset_id
        for assignment in user.blocklist_assignments
        if assignment.source and assignment.source.is_marketplace and assignment.source.preset_id
    ]
    app_policies = AppPolicy.query.order_by(AppPolicy.name.asc()).all()
    linux_app_policies = [policy for policy in app_policies if policy.platform == AppPolicy.PLATFORM_LINUX]
    android_app_policies = [policy for policy in app_policies if policy.platform == AppPolicy.PLATFORM_ANDROID]
    assigned_policy_ids = {assignment.policy_id for assignment in user.app_policy_assignments}
    installed_apps = list_installed_apps_for_managed_user(user.id)
    user_platforms = set()
    device_labels = _get_device_label_map()
    for mapping in user.device_mappings:
        platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
        if platform == AppPolicy.PLATFORM_ANDROID:
            user_platforms.add(AppPolicy.PLATFORM_ANDROID)
        else:
            user_platforms.add(AppPolicy.PLATFORM_LINUX)

    approval_settings_by_mapping = {}
    linux_device_policy_by_mapping = {}
    installed_apps_enriched = []
    seen_apps = set()
    for mapping in user.device_mappings:
        settings = get_or_create_settings(mapping)
        approval_settings_by_mapping[mapping.id] = {
            'app_launch_mode': settings.app_launch_mode,
            'domain_access_mode': settings.domain_access_mode,
            'registration_approval_enabled': settings.registration_approval_enabled,
            'device_label': device_labels.get(mapping.system_id, mapping.system_id),
        }
        mapping_platform = (mapping.device.platform if mapping.device else None) or AppPolicy.PLATFORM_LINUX
        if mapping_platform != AppPolicy.PLATFORM_ANDROID:
            try:
                linux_policy = get_or_create_linux_policy(mapping)
                linux_device_policy_by_mapping[mapping.id] = {
                    'device_label': device_labels.get(mapping.system_id, mapping.system_id),
                    'install_software_disabled': linux_policy.install_software_disabled,
                    'uninstall_software_disabled': linux_policy.uninstall_software_disabled,
                    'mount_removable_media_disabled': linux_policy.mount_removable_media_disabled,
                    'modify_accounts_disabled': linux_policy.modify_accounts_disabled,
                    'system_power_actions_disabled': linux_policy.system_power_actions_disabled,
                    'pkexec_elevation_disabled': linux_policy.pkexec_elevation_disabled,
                    'bluetooth_disabled': linux_policy.bluetooth_disabled,
                    'flatpak_install_disabled': linux_policy.flatpak_install_disabled,
                    'snap_install_disabled': linux_policy.snap_install_disabled,
                    'terminal_access_disabled': linux_policy.terminal_access_disabled,
                    'chrome_policies': linux_policy.chrome_policies,
                    'support_message': (
                        linux_policy.support_message
                        or MappingLinuxDevicePolicy.DEFAULT_SUPPORT_MESSAGE
                    ),
                    'is_synced': linux_policy.is_synced,
                    'last_sync_error': linux_policy.last_sync_error,
                }
            except ValueError:
                pass
        mapping_apps = list_installed_apps_for_device(
            mapping.system_id,
            linux_username=mapping.linux_username,
        )
        status_map = grant_status_for_apps(mapping, mapping_apps)
        for app in mapping_apps:
            dedupe_key = (app.system_id, app.linux_username, app.identifier, app.match_type)
            if dedupe_key in seen_apps:
                continue
            seen_apps.add(dedupe_key)
            status_entry = status_map.get(app.identifier, {'status': 'none', 'grant_id': None})
            payload = app.to_dict()
            payload['device_map_id'] = mapping.id
            payload['device_hostname'] = mapping.device.system_hostname if mapping.device else None
            payload['approval_status'] = status_entry.get('status', 'none')
            payload['grant_id'] = status_entry.get('grant_id')
            installed_apps_enriched.append(payload)

    installed_apps_enriched.sort(
        key=lambda item: (item['application_name'].lower(), item['identifier']),
    )

    return {
        'template': 'admin_user_edit.html',
        'user': user,
        'blocklist_sources': blocklist_sources,
        'blocklist_sync_status': blocklist_sync_status,
        'marketplace_presets': marketplace_presets,
        'subscribed_preset_ids': subscribed_preset_ids,
        'app_policies': app_policies,
        'linux_app_policies': linux_app_policies,
        'android_app_policies': android_app_policies,
        'assigned_policy_ids': assigned_policy_ids,
        'installed_apps': installed_apps_enriched or installed_apps,
        'user_platforms': user_platforms,
        'approval_settings_by_mapping': approval_settings_by_mapping,
        'linux_device_policy_by_mapping': linux_device_policy_by_mapping,
        'device_labels': device_labels,
        'policy_preset_matrix': policy_preset_matrix,
    }


def build_admin_approvals_context():
    import json
    from src.user.approvals import build_request_summary, list_pending_requests

    parent_id, active_hh_id = get_current_parent_and_household()
    pending_rows = list_pending_requests(limit=100, active_household_id=active_hh_id, parent_account_id=parent_id)
    device_labels = _get_device_label_map()
    pending_approvals = []
    for row in pending_rows:
        summary = build_request_summary(row)
        summary['requested_at'] = row.requested_at
        try:
            summary['details'] = json.loads(row.details_json) if row.details_json else {}
        except Exception:
            summary['details'] = {}
        pending_approvals.append(summary)
    return {
        'template': 'approvals.html',
        'pending_approvals': pending_approvals,
        'pending_count': len(pending_rows),
        'device_labels': device_labels,
    }


def build_admin_devices_context():
    parent_id, active_hh_id = get_current_parent_and_household()
    devices_approved = AgentDevice.query.filter_by(status='approved')
    devices_pending = AgentDevice.query.filter_by(status='pending')
    if active_hh_id:
        devices_approved = devices_approved.filter_by(household_id=active_hh_id)
        devices_pending = devices_pending.filter_by(household_id=active_hh_id)

    return {
        'template': 'admin_devices.html',
        'approved_devices': devices_approved.all(),
        'pending_devices': devices_pending.all(),
        'device_labels': _get_device_label_map(),
        'AgentConnectionManager': AgentConnectionManager,
    }


def build_admin_restrictions_context():
    from src.blocklist.marketplace import load_marketplace_presets

    parent_id, active_hh_id = get_current_parent_and_household()
    blocklist_sources = _get_blocklist_sources(include_domains=True)
    custom_sources = [s for s in blocklist_sources if not s.get('is_marketplace')]
    marketplace_presets = load_marketplace_presets()
    
    users_query = ManagedUser.query
    if active_hh_id:
        users_query = users_query.filter_by(household_id=active_hh_id)
    users = users_query.order_by(ManagedUser.username.asc()).all()

    subscribed_map = {preset['id']: [] for preset in marketplace_presets}
    for user in users:
        for assignment in user.blocklist_assignments:
            if assignment.source and assignment.source.is_marketplace and assignment.source.preset_id:
                pid = assignment.source.preset_id
                if pid in subscribed_map:
                    subscribed_map[pid].append(user.id)

    return {
        'template': 'restrictions.html',
        'blocklist_sources': custom_sources,
        'marketplace_presets': marketplace_presets,
        'users': users,
        'subscribed_map': subscribed_map,
    }


def _build_pairing_qr_context():
    server_url = build_agent_websocket_url(
        request,
        configured_url=_get_agent_websocket_url(),
    )
    parent_id, active_hh_id = get_current_parent_and_household()
    registration_token = None
    if active_hh_id:
        from src.models import Household
        hh = Household.query.get(active_hh_id)
        if hh:
            registration_token = hh.enrollment_token
            
    if not registration_token:
        registration_token = AgentConnectionManager.registration_token

    pairing_payload = pairing_payload_json(server_url, registration_token)
    pairing_qr_data_uri = render_pairing_qr_data_uri(pairing_payload)
    return {
        'pairing_server_url': server_url,
        'pairing_qr_data_uri': pairing_qr_data_uri,
        'pairing_payload': pairing_payload,
        'registration_token': registration_token,
    }


def build_settings_context():
    pairing = _build_pairing_qr_context()
    return {
        'template': 'settings.html',
        'pairing_qr_data_uri': pairing['pairing_qr_data_uri'],
        'nintendo_account': get_nintendo_account_summary(),
        'xbox_account': get_xbox_account_summary(),
    }


def build_admin_settings_context():
    alert_webhook_settings = _get_alert_webhook_settings()
    time_sync_tolerance = _get_time_sync_tolerance()
    alert_retention_days = _get_alert_retention_days()
    agent_websocket_url = _get_agent_websocket_url()
    android_agent_apk_filename = _get_android_agent_apk_filename()
    android_agent_signature_checksum = _get_android_agent_signature_checksum()
    android_agent_apk_uploaded = has_uploaded_android_apk()

    pairing = _build_pairing_qr_context()
    provisioning = resolve_android_provisioning(
        pairing['pairing_server_url'],
        get_server_version(),
        checksum_override=android_agent_signature_checksum,
        registration_token=pairing['registration_token'],
    )
    provisioning_qr_data_uri = None
    if provisioning['provisioning_ready'] and provisioning['payload_json']:
        provisioning_qr_data_uri = render_pairing_qr_data_uri(provisioning['payload_json'])

    return {
        'template': 'admin_settings.html',
        'alert_webhook_settings': alert_webhook_settings,
        'time_sync_tolerance': time_sync_tolerance,
        'alert_retention_days': alert_retention_days,
        'agent_websocket_url': agent_websocket_url,
        'pairing_server_url': pairing['pairing_server_url'],
        'pairing_qr_data_uri': pairing['pairing_qr_data_uri'],
        'pairing_payload': pairing['pairing_payload'],
        'android_agent_apk_filename': android_agent_apk_filename,
        'android_agent_apk_uploaded': android_agent_apk_uploaded,
        'android_agent_signature_checksum': android_agent_signature_checksum,
        'provisioning': provisioning,
        'provisioning_qr_data_uri': provisioning_qr_data_uri,
        'server_version': get_server_version(),
        'youtube_api_key_set': bool(_get_youtube_api_key_encrypted()),
        'youtube_history_retention_days': _get_youtube_history_retention_days(),
        'web_history_retention_days': _get_web_history_retention_days(),
        'bad_words_list': Settings.get_value('bad_words_list', ''),
    }


def build_user_stats_context(user_id):
    from src.common.helpers import check_parent_child_access
    check_parent_child_access(user_id)

    user = ManagedUser.query.get_or_404(user_id)
    alert_search = (request.args.get('alert_search') or '').strip()
    return {
        'template': 'stats.html',
        'user': user,
        'daily_30': user.get_recent_usage(days=30),
        'weekly_13': user.get_usage_weekly_grouped(weeks=13),
        'monthly_12': user.get_usage_monthly_grouped(months=12),
        'all_monthly': user.get_all_usage_monthly(),
        'alert_search': alert_search,
        **_split_user_alert_context(user, alert_search),
        'device_labels': _get_device_label_map(),
    }


def _split_user_alert_context(user, alert_search):
    alert_groups, alert_entries, alert_summary = _build_user_alert_groups(user, search_query=alert_search)
    return {
        'alert_groups': alert_groups,
        'alert_entries': alert_entries,
        'alert_summary': alert_summary,
    }


def build_device_detail_context(system_id):
    from src.common.helpers import check_parent_device_access
    check_parent_device_access(system_id)

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
        if not user:
            continue
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
    installed_apps_by_mapping = {}
    for mapping in mapped_accounts:
        usage_summaries[mapping.id] = _get_apparmor_usage_summary(mapping.id)
        installed_apps_by_mapping[mapping.id] = list_installed_apps_for_device(
            mapping.system_id,
            linux_username=mapping.linux_username,
        )

    android_device_policy = None
    parental_access_code = None
    android_recovery_ws_url = None
    nintendo_console = build_nintendo_console_view_context(device, mapped_accounts)
    xbox_console = build_xbox_console_view_context(device, mapped_accounts)
    screenshot_settings = None
    if (device.platform or 'linux').strip().lower() not in {'android', 'nintendo', 'xbox'}:
        from src.device.screenshot_settings import get_or_create_settings
        try:
            screenshot_settings = get_or_create_settings(device)
        except ValueError:
            pass
    if (device.platform or '').strip().lower() == 'android':
        from src.policy.android import get_or_create_policy as get_or_create_android_policy
        try:
            android_device_policy = get_or_create_android_policy(device)
        except ValueError:
            pass
        if device.status == 'approved' and device.secure_token:
            parental_access_code = generate_parental_access_code(device.secure_token)
            android_recovery_ws_url = build_agent_websocket_url(
                request,
                configured_url=_get_agent_websocket_url(),
            )

    agent_online = AgentConnectionManager.is_online(system_id)
    from src.agent.pending_commands import get_pending_count

    pending_command_count = get_pending_count(system_id)
    protection_summary = _build_device_protection_summary(
        device,
        mapped_accounts,
        blocklist_contributors,
        alert_summary,
        agent_online,
        android_device_policy=android_device_policy,
        screenshot_settings=screenshot_settings,
        pending_command_count=pending_command_count,
    )
    hardware_baseline = None
    if (device.platform or 'linux').strip().lower() in {'linux', 'windows'}:
        from src.device.hardware_baseline import get_hardware_baseline_status
        hardware_baseline = get_hardware_baseline_status(device)

    windows_laps = None
    if (device.platform or '').strip().lower() == 'windows':
        from src.device.windows_laps import get_windows_laps_status
        windows_laps = get_windows_laps_status(device)

    return {
        'template': 'device_detail.html',
        'device': device,
        'device_label': device_labels.get(system_id, device.display_name),
        'mapped_accounts': mapped_accounts,
        'blocklist_contributors': blocklist_contributors,
        'alert_search': alert_search,
        'alert_entries': alert_entries,
        'alert_summary': alert_summary,
        'usage_summaries': usage_summaries,
        'installed_apps_by_mapping': installed_apps_by_mapping,
        'android_device_policy': android_device_policy,
        'agent_online': agent_online,
        'protection_summary': protection_summary,
        'fcm_available': bool((device.fcm_token or '').strip()),
        'parental_access_code': parental_access_code,
        'android_recovery_ws_url': android_recovery_ws_url,
        'has_managed_profiles': device.has_managed_profiles,
        'nintendo_console': nintendo_console,
        'xbox_console': xbox_console,
        'screenshot_settings': screenshot_settings,
        'hardware_baseline': hardware_baseline,
        'windows_laps': windows_laps,
    }


def build_user_combined_history_context(user_id):
    from src.common.helpers import check_parent_child_access
    check_parent_child_access(user_id)

    user = ManagedUser.query.get_or_404(user_id)
    return {
        'template': 'web_video_history.html',
        'user': user,
    }


def build_user_online_accounts_context(user_id):
    from src.common.helpers import check_parent_child_access
    check_parent_child_access(user_id)

    user = ManagedUser.query.get_or_404(user_id)
    return {
        'template': 'online_accounts.html',
        'user': user,
    }


def build_weekly_schedule_context():
    parent_id, active_hh_id = get_current_parent_and_household()
    users_query = ManagedUser.query
    if active_hh_id:
        users_query = users_query.filter_by(household_id=active_hh_id)
    users = users_query.order_by(ManagedUser.username.asc()).all()

    db_changed = False
    for user in users:
        if not user.weekly_schedule:
            schedule = UserWeeklySchedule(user_id=user.id)
            db.session.add(schedule)
            db_changed = True
    if db_changed:
        db.session.commit()
    return {
        'template': 'weekly_schedule.html',
        'users': users,
    }


def build_weekly_schedule_user_context(user_id):
    from src.common.helpers import check_parent_child_access
    check_parent_child_access(user_id)

    user = ManagedUser.query.get_or_404(user_id)
    if not user.weekly_schedule:
        schedule = UserWeeklySchedule(user_id=user.id)
        db.session.add(schedule)
        db.session.commit()

    return {
        'template': 'weekly_schedule_single.html',
        'user': user,
        'blocklist_sources': _get_blocklist_sources(include_domains=False, enabled_only=True),
        'blocklist_sync_status': _build_user_blocklist_sync_status(user),
        'app_policies': AppPolicy.query.order_by(AppPolicy.name.asc()).all(),
        'assigned_policy_ids': {assignment.policy_id for assignment in user.app_policy_assignments},
    }


def build_admin_app_policies_context():
    from src.policy.apparmor import CURATED_APPARMOR_APPS
    from src.device.installed_apps import list_discovered_apps_for_platform

    parent_id, active_hh_id = get_current_parent_and_household()
    policies = AppPolicy.query.order_by(AppPolicy.name.asc()).all()
    
    users_query = ManagedUser.query
    if active_hh_id:
        users_query = users_query.filter_by(household_id=active_hh_id)

    return {
        'template': 'restrictions_app.html',
        'policies': policies,
        'managed_users': users_query.order_by(ManagedUser.username.asc()).all(),
        'curated_options': CURATED_APPARMOR_APPS,
        'discovered_apps_by_policy': {
            policy.id: list_discovered_apps_for_platform(policy.platform)
            for policy in policies
        },
    }
