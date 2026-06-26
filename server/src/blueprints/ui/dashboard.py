import logging
from flask import Blueprint, request, redirect, url_for, session
from src.i18n.catalog import flash_t
from src.models import AgentDevice, Settings
from src.agent.helper import AgentConnectionManager, refresh_installed_apps
from src.common.settings import encrypt_setting
from src.blueprints.ui.spa import render_spa_shell
from src.agent.pairing import (
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
        flash_t('flash.auth.login_required', 'warning')
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
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    if request.method == 'POST':
        form_name = (request.form.get('form_name') or 'password').strip()

        if form_name == 'language':
            from src.i18n.catalog import SUPPORTED_LOCALES

            chosen = (request.form.get('locale') or '').strip()
            if chosen not in SUPPORTED_LOCALES:
                flash_t('flash.common.invalid_values', 'danger')
            else:
                session['locale'] = chosen
                if request.form.get('set_household_default') == 'on':
                    Settings.set_value('default_locale', chosen)
                flash_t('flash.settings.language_saved', 'success')
            return redirect(url_for('ui_dashboard.settings'))
        else:
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_password = request.form.get('confirm_password')

            if not current_password or not new_password or not confirm_password:
                flash_t('flash.common.fields_required', 'danger')
            elif not Settings.check_admin_password(current_password):
                flash_t('flash.settings.password_wrong', 'danger')
            elif new_password != confirm_password:
                flash_t('flash.settings.password_mismatch', 'danger')
            elif len(new_password) < 4:
                flash_t('flash.settings.password_short', 'danger')
            else:
                Settings.set_admin_password(new_password)
                flash_t('flash.settings.password_updated', 'success')
                return redirect(url_for('ui_dashboard.settings'))

    return render_spa_shell('settings')


@ui_dashboard_bp.route('/admin/settings', methods=['GET', 'POST'])
def admin_settings():
    if not session.get('logged_in'):
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    if request.method == 'POST':
        form_name = (request.form.get('form_name') or '').strip()

        if form_name == 'agent_pairing':
            submitted_url = (request.form.get('agent_websocket_url') or '').strip()
            try:
                normalized_url = normalize_agent_websocket_url(submitted_url)
            except ValueError as exc:
                flash_t('flash.common.generic_error', 'danger', error=str(exc))
            else:
                Settings.set_value('agent_websocket_url', normalized_url)
                if normalized_url:
                    flash_t('flash.settings.pairing_url_updated', 'success')
                else:
                    flash_t('flash.settings.pairing_url_reset', 'success')
                return redirect(url_for('ui_dashboard.admin_settings'))
        elif form_name == 'android_provisioning':
            if request.form.get('remove_android_apk') == '1':
                remove_uploaded_android_apk()
                Settings.set_value('android_agent_apk_filename', '')
                Settings.set_value('android_agent_signature_checksum', '')
                flash_t('flash.settings.apk_removed', 'success')
                return redirect(url_for('ui_dashboard.admin_settings'))

            skip_user_setup = '1' if request.form.get('android_provisioning_skip_user_setup') == 'on' else '0'
            leave_all_system_apps_enabled = '1' if request.form.get('android_provisioning_leave_all_system_apps_enabled') == 'on' else '0'
            wifi_ssid = (request.form.get('android_provisioning_wifi_ssid') or '').strip()
            wifi_security_type = (request.form.get('android_provisioning_wifi_security_type') or 'WPA').strip()

            Settings.set_value('android_provisioning_skip_user_setup', skip_user_setup)
            Settings.set_value('android_provisioning_leave_all_system_apps_enabled', leave_all_system_apps_enabled)
            Settings.set_value('android_provisioning_wifi_ssid', wifi_ssid)
            Settings.set_value('android_provisioning_wifi_security_type', wifi_security_type)

            if not wifi_ssid:
                Settings.set_value('android_provisioning_wifi_password', '')
            else:
                wifi_password = request.form.get('android_provisioning_wifi_password') or ''
                if wifi_password:
                    encrypted_password = encrypt_setting(wifi_password)
                    Settings.set_value('android_provisioning_wifi_password', encrypted_password)

            uploaded_apk = request.files.get('android_agent_apk')
            apk_updated = False
            if uploaded_apk and (uploaded_apk.filename or '').strip():
                try:
                    filename, checksum = save_uploaded_android_apk(uploaded_apk)
                    Settings.set_value('android_agent_apk_filename', filename)
                    Settings.set_value('android_agent_signature_checksum', checksum)
                    apk_updated = True
                except ValueError as exc:
                    flash_t('flash.common.generic_error', 'danger', error=str(exc))
                    return redirect(url_for('ui_dashboard.admin_settings'))
                except RuntimeError as exc:
                    flash_t('flash.settings.apk_process_failed', 'danger', error=str(exc))
                    return redirect(url_for('ui_dashboard.admin_settings'))

            if apk_updated:
                flash_t('flash.settings.apk_uploaded', 'success')
            else:
                flash_t('flash.settings.mdm_updated', 'success')
            return redirect(url_for('ui_dashboard.admin_settings'))
        elif form_name == 'alert_webhook':
            webhook_enabled = request.form.get('alert_webhook_enabled') == 'on'
            webhook_url = (request.form.get('alert_webhook_url') or '').strip()
            webhook_secret = (request.form.get('alert_webhook_secret') or '').strip()

            if webhook_enabled and not webhook_url:
                flash_t('flash.settings.webhook_required', 'danger')
            else:
                try:
                    if webhook_url:
                        from src.common.url_safety import validate_safe_outbound_url
                        webhook_url = validate_safe_outbound_url(webhook_url)
                except ValueError as exc:
                    flash_t('flash.common.generic_error', 'danger', error=str(exc))
                else:
                    Settings.set_value('alert_webhook_enabled', '1' if webhook_enabled else '0')
                    Settings.set_value('alert_webhook_url', webhook_url)
                    Settings.set_value('alert_webhook_secret', webhook_secret)
                    flash_t('flash.settings.webhook_updated', 'success')
                    return redirect(url_for('ui_dashboard.admin_settings'))
        elif form_name == 'application_settings':
            tolerance = request.form.get('time_sync_tolerance')
            retention = request.form.get('alert_retention_days')
            try:
                tolerance_val = int(tolerance)
                retention_val = int(retention)

                if tolerance_val < 0:
                    flash_t('flash.settings.tolerance_invalid', 'danger')
                elif retention_val < 1:
                    flash_t('flash.settings.retention_min', 'danger')
                else:
                    Settings.set_value('time_sync_tolerance', str(tolerance_val))
                    Settings.set_value('alert_retention_days', str(retention_val))
                    flash_t('flash.settings.app_settings_updated', 'success')
                    return redirect(url_for('ui_dashboard.admin_settings'))
            except (TypeError, ValueError):
                flash_t('flash.common.invalid_values', 'danger')
        elif form_name == 'youtube_settings':
            youtube_api_key = request.form.get('youtube_api_key') or ''
            retention = request.form.get('youtube_history_retention_days')
            web_retention = request.form.get('web_history_retention_days') or '7'
            try:
                retention_val = int(retention)
                web_retention_val = int(web_retention)
                if retention_val < 0 or web_retention_val < 0:
                    flash_t('flash.settings.retention_invalid', 'danger')
                else:
                    Settings.set_value('video_history_retention_days', str(retention_val))
                    Settings.set_value('youtube_history_retention_days', str(retention_val))
                    Settings.set_value('web_history_retention_days', str(web_retention_val))
                    
                    bad_words = request.form.get('bad_words_list') or ''
                    Settings.set_value('bad_words_list', bad_words.strip())

                    if youtube_api_key:
                        encrypted_key = encrypt_setting(youtube_api_key)
                        Settings.set_value('youtube_api_key', encrypted_key)
                    flash_t('flash.settings.web_video_updated', 'success')
                    return redirect(url_for('ui_dashboard.admin_settings'))
            except (TypeError, ValueError):
                flash_t('flash.common.invalid_values', 'danger')

    return render_spa_shell('admin/settings')


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
        flash_t('flash.auth.login_required', 'warning')
        return redirect(url_for('ui_auth.login'))

    device = AgentDevice.query.get_or_404(system_id)
    linux_username = (request.form.get('linux_username') or '').strip()
    if not linux_username:
        mapping = device.user_mappings[0] if device.user_mappings else None
        if mapping is None:
            flash_t('flash.settings.inventory_no_account', 'warning')
            return redirect(url_for('ui_dashboard.device_detail', system_id=system_id))
        linux_username = mapping.linux_username

    if not AgentConnectionManager.is_online(system_id):
        flash_t('flash.settings.inventory_offline', 'warning')
        return redirect(url_for('ui_dashboard.device_detail', system_id=system_id))

    try:
        refresh_installed_apps(system_id, linux_username)
        flash_t('flash.settings.inventory_refresh', 'success')
    except RuntimeError as exc:
        flash_t('flash.common.generic_error', 'danger', error=str(exc))

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

