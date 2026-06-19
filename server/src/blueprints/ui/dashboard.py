import logging
from flask import Blueprint, request, redirect, url_for, flash, session
from src.database import AgentDevice, Settings
from src.agent_helper import AgentConnectionManager, refresh_installed_apps
from src.settings_manager import (
    _get_agent_websocket_url,
    _get_alert_webhook_settings,
    _get_time_sync_tolerance,
    _get_alert_retention_days,
    _get_android_agent_apk_filename,
    _get_android_agent_signature_checksum,
    encrypt_setting,
    _get_youtube_api_key_encrypted,
    _get_youtube_history_retention_days,
    _get_web_history_retention_days,
)
from src.blueprints.ui.spa import render_spa_shell
from src.pairing_helper import (
    has_uploaded_android_apk,
    normalize_agent_websocket_url,
    remove_uploaded_android_apk,
    save_uploaded_android_apk,
)

_LOGGER = logging.getLogger(__name__)

ui_dashboard_bp = Blueprint('ui_dashboard', __name__)


@ui_dashboard_bp.route('/dashboard')
def dashboard():
    """Serve the SPA shell with the family home dashboard."""
    return render_spa_shell('dashboard')


@ui_dashboard_bp.route('/admin')
def admin():
    """Redirect to the users admin page for backwards-compatibility."""
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))
    return redirect(url_for('ui_dashboard.admin_users'))


@ui_dashboard_bp.route('/admin/users')
def admin_users():
    """Serve the child profiles administration page."""
    return render_spa_shell('admin/users')


@ui_dashboard_bp.route('/admin/users/<int:user_id>')
def edit_user_profile(user_id):
    """Serve the user profile modification dashboard."""
    return render_spa_shell(f'admin/users/{user_id}')


@ui_dashboard_bp.route('/admin/approvals')
def admin_approvals():
    """Serve the access approval request queue."""
    return render_spa_shell('admin/approvals')


@ui_dashboard_bp.route('/admin/devices')
def admin_devices():
    """Serve the administration page for agent devices."""
    return render_spa_shell('admin/devices')


@ui_dashboard_bp.route('/admin/restrictions')
def admin_restrictions():
    """Serve the dedicated Internet Restrictions page."""
    return render_spa_shell('admin/restrictions')


@ui_dashboard_bp.route('/settings', methods=['GET', 'POST'])
def settings():
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    alert_webhook_settings = _get_alert_webhook_settings()
    time_sync_tolerance = _get_time_sync_tolerance()
    alert_retention_days = _get_alert_retention_days()
    agent_websocket_url = _get_agent_websocket_url()
    android_agent_apk_filename = _get_android_agent_apk_filename()
    android_agent_signature_checksum = _get_android_agent_signature_checksum()
    android_agent_apk_uploaded = has_uploaded_android_apk()

    if request.method == 'POST':
        form_name = (request.form.get('form_name') or 'password').strip()

        if form_name == 'agent_pairing':
            submitted_url = (request.form.get('agent_websocket_url') or '').strip()
            try:
                normalized_url = normalize_agent_websocket_url(submitted_url)
            except ValueError as exc:
                flash(str(exc), 'danger')
            else:
                Settings.set_value('agent_websocket_url', normalized_url)
                if normalized_url:
                    flash('Agent pairing URL updated successfully', 'success')
                else:
                    flash('Agent pairing URL reset to auto-detect', 'success')
                return redirect(url_for('ui_dashboard.settings'))

            agent_websocket_url = submitted_url
        elif form_name == 'android_provisioning':
            if request.form.get('remove_android_apk') == '1':
                remove_uploaded_android_apk()
                Settings.set_value('android_agent_apk_filename', '')
                Settings.set_value('android_agent_signature_checksum', '')
                flash('Uploaded Android APK removed', 'success')
                return redirect(url_for('ui_dashboard.settings'))

            # 1. Save settings toggles
            skip_user_setup = '1' if request.form.get('android_provisioning_skip_user_setup') == 'on' else '0'
            leave_all_system_apps_enabled = '1' if request.form.get('android_provisioning_leave_all_system_apps_enabled') == 'on' else '0'
            wifi_ssid = (request.form.get('android_provisioning_wifi_ssid') or '').strip()
            wifi_security_type = (request.form.get('android_provisioning_wifi_security_type') or 'WPA').strip()

            Settings.set_value('android_provisioning_skip_user_setup', skip_user_setup)
            Settings.set_value('android_provisioning_leave_all_system_apps_enabled', leave_all_system_apps_enabled)
            Settings.set_value('android_provisioning_wifi_ssid', wifi_ssid)
            Settings.set_value('android_provisioning_wifi_security_type', wifi_security_type)

            # 2. Handle Wi-Fi password encryption
            if not wifi_ssid:
                Settings.set_value('android_provisioning_wifi_password', '')
            else:
                wifi_password = request.form.get('android_provisioning_wifi_password') or ''
                if wifi_password:
                    encrypted_password = encrypt_setting(wifi_password)
                    Settings.set_value('android_provisioning_wifi_password', encrypted_password)

            # 3. Handle APK Upload
            uploaded_apk = request.files.get('android_agent_apk')
            apk_updated = False
            if uploaded_apk and (uploaded_apk.filename or '').strip():
                try:
                    filename, checksum = save_uploaded_android_apk(uploaded_apk)
                    Settings.set_value('android_agent_apk_filename', filename)
                    Settings.set_value('android_agent_signature_checksum', checksum)
                    apk_updated = True
                except ValueError as exc:
                    flash(str(exc), 'danger')
                    return redirect(url_for('ui_dashboard.settings'))
                except RuntimeError as exc:
                    flash(f'Failed to process uploaded APK: {exc}', 'danger')
                    return redirect(url_for('ui_dashboard.settings'))

            if apk_updated:
                flash('Android APK uploaded successfully', 'success')
            else:
                flash('MDM provisioning configuration updated successfully', 'success')
            return redirect(url_for('ui_dashboard.settings'))
        elif form_name == 'alert_webhook':
            webhook_enabled = request.form.get('alert_webhook_enabled') == 'on'
            webhook_url = (request.form.get('alert_webhook_url') or '').strip()
            webhook_secret = (request.form.get('alert_webhook_secret') or '').strip()

            if webhook_enabled and not webhook_url:
                flash('Webhook URL is required when alert delivery is enabled', 'danger')
            else:
                try:
                    if webhook_url:
                        from src.url_safety import validate_safe_outbound_url
                        webhook_url = validate_safe_outbound_url(webhook_url)
                except ValueError as exc:
                    flash(str(exc), 'danger')
                else:
                    Settings.set_value('alert_webhook_enabled', '1' if webhook_enabled else '0')
                    Settings.set_value('alert_webhook_url', webhook_url)
                    Settings.set_value('alert_webhook_secret', webhook_secret)
                    flash('Alert webhook settings updated successfully', 'success')
                    return redirect(url_for('ui_dashboard.settings'))

            alert_webhook_settings = {
                'enabled': webhook_enabled,
                'url': webhook_url,
                'secret': webhook_secret,
                'is_active': webhook_enabled and bool(webhook_url),
            }
        elif form_name == 'application_settings':
            tolerance = request.form.get('time_sync_tolerance')
            retention = request.form.get('alert_retention_days')
            try:
                tolerance_val = int(tolerance)
                retention_val = int(retention)
                
                if tolerance_val < 0:
                    flash('Tolerance must be a non-negative number', 'danger')
                elif retention_val < 1:
                    flash('Retention must be at least 1 day', 'danger')
                else:
                    Settings.set_value('time_sync_tolerance', str(tolerance_val))
                    Settings.set_value('alert_retention_days', str(retention_val))
                    flash('Application settings updated successfully', 'success')
                    return redirect(url_for('ui_dashboard.settings'))
            except (TypeError, ValueError):
                flash('Invalid setting values provided', 'danger')
        elif form_name == 'youtube_settings':
            youtube_api_key = request.form.get('youtube_api_key') or ''
            retention = request.form.get('youtube_history_retention_days')
            web_retention = request.form.get('web_history_retention_days') or '7'
            try:
                retention_val = int(retention)
                web_retention_val = int(web_retention)
                if retention_val < 0 or web_retention_val < 0:
                    flash('Retention periods must be non-negative numbers of days', 'danger')
                else:
                    Settings.set_value('youtube_history_retention_days', str(retention_val))
                    Settings.set_value('web_history_retention_days', str(web_retention_val))
                    if youtube_api_key:
                        encrypted_key = encrypt_setting(youtube_api_key)
                        Settings.set_value('youtube_api_key', encrypted_key)
                    flash('Web & Video Settings updated successfully', 'success')
                    return redirect(url_for('ui_dashboard.settings'))
            except (TypeError, ValueError):
                flash('Invalid settings values provided', 'danger')
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
                return redirect(url_for('ui_dashboard.settings'))

                return redirect(url_for('ui_dashboard.settings'))

    return render_spa_shell('settings')


@ui_dashboard_bp.route('/stats/<int:user_id>')
def user_stats(user_id):
    """Serve extended usage history for a single user."""
    return render_spa_shell(f'stats/{user_id}')


@ui_dashboard_bp.route('/devices/<system_id>')
def device_detail(system_id):
    return render_spa_shell(f'devices/{system_id}')


@ui_dashboard_bp.route('/devices/<system_id>/installed-apps/refresh', methods=['POST'])
def refresh_device_installed_apps_ui(system_id):
    if not session.get('logged_in'):
        flash('Please login first', 'warning')
        return redirect(url_for('ui_auth.login'))

    device = AgentDevice.query.get_or_404(system_id)
    linux_username = (request.form.get('linux_username') or '').strip()
    if not linux_username:
        mapping = device.user_mappings[0] if device.user_mappings else None
        if mapping is None:
            flash('No linked account available to refresh application inventory', 'warning')
            return redirect(url_for('ui_dashboard.device_detail', system_id=system_id))
        linux_username = mapping.linux_username

    if not AgentConnectionManager.is_online(system_id):
        flash('Device is offline. Inventory will refresh when the agent reconnects.', 'warning')
        return redirect(url_for('ui_dashboard.device_detail', system_id=system_id))

    try:
        refresh_installed_apps(system_id, linux_username)
        flash('Requested installed application inventory refresh from agent', 'success')
    except RuntimeError as exc:
        flash(str(exc), 'danger')

    return redirect(url_for('ui_dashboard.device_detail', system_id=system_id))


@ui_dashboard_bp.route('/dashboard/user/<int:user_id>/youtube')
def user_youtube_history(user_id):
    """Legacy redirect to the combined Web & Video history view."""
    return redirect(url_for('ui_dashboard.user_combined_history', user_id=user_id))


@ui_dashboard_bp.route('/dashboard/user/<int:user_id>/history')
def user_combined_history(user_id):
    """Serve the unified Web & Video history dashboard for a user."""
    return render_spa_shell(f'dashboard/user/{user_id}/history')


@ui_dashboard_bp.route('/dashboard/user/<int:user_id>/online-accounts')
def user_online_accounts(user_id):
    """Serve the online accounts report dashboard for a user."""
    return render_spa_shell(f'dashboard/user/{user_id}/online-accounts')

