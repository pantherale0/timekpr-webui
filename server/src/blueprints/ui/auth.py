import logging
import time
from flask import Blueprint, render_template, request, redirect, url_for, session
from src.database import Settings
from src.helpers import ADMIN_USERNAME
from src.i18n.catalog import flash_t, t

_LOGGER = logging.getLogger(__name__)

ui_auth_bp = Blueprint('ui_auth', __name__)

_OIDC_DENIAL_MESSAGE_KEYS = {
    'Missing user information from identity provider': 'flash.auth.oidc_denied_missing_user',
    (
        'OIDC admin access is not configured. Set ALLOWED_OIDC_ADMINS, '
        'ALLOWED_OIDC_ADMIN_DOMAINS, ALLOWED_OIDC_ADMIN_ROLES, or ALLOWED_OIDC_ADMIN_GROUPS.'
    ): 'flash.auth.oidc_denied_not_configured',
    'You are not authorized to access the admin console.': 'flash.auth.oidc_denied_unauthorized',
}


def _flash_oidc_denial(denial_message: str) -> None:
    key = _OIDC_DENIAL_MESSAGE_KEYS.get(denial_message)
    if key:
        flash_t(key, 'danger')
    else:
        flash_t('flash.auth.oidc_denied_generic', 'danger', reason=denial_message)


@ui_auth_bp.route('/', methods=['GET', 'POST'])
def login():
    """Render the login page and optionally start the OIDC login flow."""
    from app import oidc_helper
    # If already logged in, go straight to dashboard
    if session.get('logged_in'):
        return redirect(url_for('ui_dashboard.dashboard'))

    if oidc_helper.is_enabled:
        # SSO Auto-redirect flow
        state = oidc_helper.generate_state()
        session['oidc_state'] = state
        
        # Generate redirect URI pointing to our callback endpoint
        redirect_uri = url_for('ui_auth.oidc_callback', _external=True)
        
        try:
            auth_url = oidc_helper.get_authorization_url(state, redirect_uri)
            return redirect(auth_url)
        except (KeyError, RuntimeError, ValueError) as exc:
            _LOGGER.error("OIDC login redirection failed: %s", exc)
            flash_t('flash.auth.oidc_init_failed', 'warning')
            return render_template('login.html', error="OIDC provider connection error.")

    # Fallback: Traditional form-based local login
    error = None
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        # Check admin password using hash comparison
        if username == ADMIN_USERNAME and Settings.check_admin_password(password):
            session['logged_in'] = True
            flash_t('flash.auth.login_success', 'success')
            return redirect(url_for('ui_dashboard.dashboard'))
        error = t('flash.auth.invalid_credentials')
        flash_t('flash.auth.invalid_credentials', 'danger')
    
    return render_template('login.html', error=error)


@ui_auth_bp.route('/callback')
def oidc_callback():
    """Complete the OIDC callback flow and establish the admin session."""
    from app import oidc_helper
    if not oidc_helper.is_enabled:
        flash_t('flash.auth.oidc_disabled', 'danger')
        return redirect(url_for('ui_auth.login'))

    state_param = request.args.get('state')
    if not state_param or state_param != session.get('oidc_state'):
        flash_t('flash.auth.oidc_csrf', 'danger')
        return redirect(url_for('ui_auth.login'))

    # Clear state after verification
    session.pop('oidc_state', None)

    code = request.args.get('code')
    if not code:
        flash_t('flash.auth.oidc_no_code', 'danger')
        return redirect(url_for('ui_auth.login'))

    try:
        redirect_uri = url_for('ui_auth.oidc_callback', _external=True)
        # Exchange code for tokens
        tokens = oidc_helper.exchange_code(code, redirect_uri)
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token')
        expires_in = tokens.get('expires_in', 3600)
        
        # Get user details from userinfo endpoint
        user_info = oidc_helper.get_user_info(access_token)

        authorized, denial_message = oidc_helper.is_authorized_admin(user_info)
        if not authorized:
            _LOGGER.warning(
                "OIDC login denied for identity %s: %s",
                user_info.get('email') or user_info.get('preferred_username') or user_info.get('sub'),
                denial_message,
            )
            _flash_oidc_denial(denial_message)
            return redirect(url_for('ui_auth.login'))

        # Extract details and log in
        session['logged_in'] = True
        session['user'] = {
            'username': user_info.get('preferred_username') or user_info.get('sub') or 'OIDC User',
            'email': user_info.get('email'),
            'name': user_info.get('name')
        }
        
        # Store token information and configure permanent session for OIDC
        session['oidc_access_token'] = access_token
        if refresh_token:
            session['oidc_refresh_token'] = refresh_token
        session['oidc_token_expires_at'] = time.time() + expires_in
        session.permanent = True

        flash_t(
            'flash.auth.oidc_success',
            'success',
            username=session['user']['username'],
        )
        return redirect(url_for('ui_dashboard.dashboard'))
    except (KeyError, RuntimeError, ValueError) as exc:
        _LOGGER.error("OIDC callback processing failed: %s", exc)
        flash_t('flash.auth.oidc_failed', 'danger', error=str(exc))
        return redirect(url_for('ui_auth.login'))


@ui_auth_bp.route('/logout')
def logout():
    """Clear the current session and redirect back to the login page."""
    from app import oidc_helper
    session.pop('logged_in', None)
    session.pop('user', None)
    session.pop('oidc_access_token', None)
    session.pop('oidc_refresh_token', None)
    session.pop('oidc_token_expires_at', None)
    if oidc_helper.is_enabled:
        return redirect(url_for('ui_auth.login'))
    flash_t('flash.auth.logout', 'info')
    return redirect(url_for('ui_auth.login'))
