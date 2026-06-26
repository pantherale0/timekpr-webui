"""SPA shell serving and HTML fragment routes."""

import re
from functools import wraps

from flask import Blueprint, abort, redirect, render_template, request, session, url_for

from src.common.spa_view_builders import (
    build_admin_app_policies_context,
    build_admin_approvals_context,
    build_admin_devices_context,
    build_admin_restrictions_context,
    build_admin_users_context,
    build_dashboard_context,
    build_device_detail_context,
    build_edit_user_profile_context,
    build_admin_settings_context,
    build_settings_context,
    build_user_combined_history_context,
    build_user_online_accounts_context,
    build_user_stats_context,
    build_weekly_schedule_context,
    build_weekly_schedule_user_context,
)

ui_spa_bp = Blueprint('ui_spa', __name__)

FRAGMENT_HEADER = 'X-Guardian-SPA'


def _login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get('logged_in'):
            if request.headers.get(FRAGMENT_HEADER) == 'fragment':
                abort(401)
            return redirect(url_for('ui_auth.login'))
        return view(*args, **kwargs)
    return wrapped


def _render_fragment(context):
    template = context.pop('template')
    return render_template(template, **context)


def _resolve_fragment(route_path):
    """Map a URL path to a context builder. Returns context dict or None."""
    path = '/' + route_path.lstrip('/')
    if '?' in path:
        path = path.split('?', 1)[0]

    routes = [
        (re.compile(r'^/dashboard$'), lambda: build_dashboard_context()),
        (re.compile(r'^/admin/users$'), lambda: build_admin_users_context()),
        (re.compile(r'^/admin/users/(?P<user_id>\d+)$'), lambda m: build_edit_user_profile_context(int(m.group('user_id')))),
        (re.compile(r'^/admin/approvals$'), lambda: build_admin_approvals_context()),
        (re.compile(r'^/admin/devices$'), lambda: build_admin_devices_context()),
        (re.compile(r'^/admin/restrictions$'), lambda: build_admin_restrictions_context()),
        (re.compile(r'^/admin/app-policies$'), lambda: build_admin_app_policies_context()),
        (re.compile(r'^/settings$'), lambda: build_settings_context()),
        (re.compile(r'^/admin/settings$'), lambda: build_admin_settings_context()),
        (re.compile(r'^/stats/(?P<user_id>\d+)$'), lambda m: build_user_stats_context(int(m.group('user_id')))),
        (re.compile(r'^/devices/(?P<system_id>[^/]+)$'), lambda m: build_device_detail_context(m.group('system_id'))),
        (re.compile(r'^/dashboard/user/(?P<user_id>\d+)/history$'), lambda m: build_user_combined_history_context(int(m.group('user_id')))),
        (re.compile(r'^/dashboard/user/(?P<user_id>\d+)/online-accounts$'), lambda m: build_user_online_accounts_context(int(m.group('user_id')))),
        (re.compile(r'^/dashboard/user/(?P<user_id>\d+)/youtube$'), lambda m: build_user_combined_history_context(int(m.group('user_id')))),
        (re.compile(r'^/weekly-schedule$'), lambda: build_weekly_schedule_context()),
        (re.compile(r'^/weekly-schedule/(?P<user_id>\d+)$'), lambda m: build_weekly_schedule_user_context(int(m.group('user_id')))),
    ]

    for pattern, builder in routes:
        match = pattern.match(path)
        if match:
            if match.groupdict():
                return builder(match)
            return builder()
    return None


def render_spa_shell(route_path):
    """Render the persistent SPA shell with optional server-side initial fragment."""
    if not session.get('logged_in'):
        return redirect(url_for('ui_auth.login'))

    full_path = '/' + route_path.lstrip('/')
    initial_fragment = None
    fragment_context = _resolve_fragment(route_path)
    if fragment_context:
        template_name = fragment_context.pop('template')
        initial_fragment = render_template(template_name, **fragment_context)

    return render_template(
        'spa_shell.html',
        initial_path=full_path,
        initial_fragment=initial_fragment,
    )


@ui_spa_bp.route('/ui/fragment/<path:route_path>')
@_login_required
def spa_fragment(route_path):
    """Return HTML partial for client-side navigation."""
    context = _resolve_fragment(route_path)
    if context is None:
        abort(404)
    return _render_fragment(context)


@ui_spa_bp.route('/ui/fragment')
@_login_required
def spa_fragment_root():
    return redirect(url_for('ui_spa.spa_fragment', route_path='dashboard'))
