import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database import Settings
from src.helpers import ADMIN_USERNAME

_LOGGER = logging.getLogger(__name__)

ui_auth_bp = Blueprint('ui_auth', __name__)


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
            return redirect(url_for('ui_dashboard.dashboard'))
        error = 'Invalid credentials. Please try again.'
        flash(error, 'danger')
    
    return render_template('login.html', error=error)


@ui_auth_bp.route('/callback')
def oidc_callback():
    """Complete the OIDC callback flow and establish the admin session."""
    from app import oidc_helper
    if not oidc_helper.is_enabled:
        flash("OIDC is not enabled.", "danger")
        return redirect(url_for('ui_auth.login'))

    state_param = request.args.get('state')
    if not state_param or state_param != session.get('oidc_state'):
        flash("Authentication failed: Invalid state token (CSRF attempt prevented).", "danger")
        return redirect(url_for('ui_auth.login'))

    # Clear state after verification
    session.pop('oidc_state', None)

    code = request.args.get('code')
    if not code:
        flash("Authentication failed: No authorization code returned from provider.", "danger")
        return redirect(url_for('ui_auth.login'))

    try:
        redirect_uri = url_for('ui_auth.oidc_callback', _external=True)
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
        return redirect(url_for('ui_dashboard.dashboard'))
    except (KeyError, RuntimeError, ValueError) as exc:
        _LOGGER.error("OIDC callback processing failed: %s", exc)
        flash(f"Authentication failed: {exc}", "danger")
        return redirect(url_for('ui_auth.login'))


@ui_auth_bp.route('/logout')
def logout():
    """Clear the current session and redirect back to the login page."""
    from app import oidc_helper
    session.pop('logged_in', None)
    session.pop('user', None)
    if oidc_helper.is_enabled:
        return redirect(url_for('ui_auth.login'))
    flash('You have been logged out', 'info')
    return redirect(url_for('ui_auth.login'))
