import logging
from flask import Blueprint, render_template, request, redirect, url_for, flash, session
from src.database import db, Settings, ConsoleUser, ConsoleUserTenantMap, Tenant
from src.helpers import ADMIN_USERNAME
from src.tenant_helper import decrypt_tenant_key

_LOGGER = logging.getLogger(__name__)

ui_auth_bp = Blueprint('ui_auth', __name__)


@ui_auth_bp.route('/', methods=['GET', 'POST'])
def login():
    """Render the login page and optionally start the OIDC login flow."""
    from app import oidc_helper
    # If already logged in, go straight to dashboard or tenant switcher
    if session.get('logged_in') and session.get('user_id'):
        if session.get('active_tenant_id'):
            return redirect(url_for('ui_dashboard.dashboard'))
        return redirect(url_for('ui_auth.tenant_switcher'))

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
        
        # First, try to authenticate via the ConsoleUser table
        user = ConsoleUser.query.filter_by(username=username).first()
        if user and user.is_active and user.check_password(password):
            session['logged_in'] = True
            session['user_id'] = user.id
            flash('Login successful!', 'success')
            
            # Resolve tenant scope
            memberships = user.tenant_memberships
            if len(memberships) == 1:
                session['active_tenant_id'] = memberships[0].tenant_id
                return redirect(url_for('ui_dashboard.dashboard'))
            else:
                return redirect(url_for('ui_auth.tenant_switcher'))
                
        # Fallback to legacy single-user setting check for bootstrap/compatibility
        elif username == ADMIN_USERNAME and Settings.check_admin_password(password):
            # Check if there is already a ConsoleUser admin seeded
            user = ConsoleUser.query.filter_by(username=ADMIN_USERNAME).first()
            if not user:
                # Seed user dynamically
                user = ConsoleUser(username=ADMIN_USERNAME, email="admin@local.host", is_super_admin=True)
                user.set_password(password)
                db.session.add(user)
                db.session.commit()
                
            # Auto-map to default tenant ID 1 if they have no memberships
            if not user.tenant_memberships:
                default_tenant = db.session.get(Tenant, 1)
                if not default_tenant:
                    from src.tenant_helper import encrypt_tenant_key
                    default_tenant = Tenant(
                        id=1,
                        name="Default Workspace",
                        slug="default",
                        registration_token="admin-token",
                        encrypted_data_key=encrypt_tenant_key(b"devmasterkeydefault32byteslong!!!")
                    )
                    db.session.add(default_tenant)
                    db.session.commit()
                
                mapping = ConsoleUserTenantMap(console_user_id=user.id, tenant_id=1, role="tenant_admin")
                db.session.add(mapping)
                db.session.commit()

            session['logged_in'] = True
            session['user_id'] = user.id
            flash('Login successful!', 'success')
            
            memberships = user.tenant_memberships
            if len(memberships) == 1:
                session['active_tenant_id'] = memberships[0].tenant_id
                return redirect(url_for('ui_dashboard.dashboard'))
            else:
                return redirect(url_for('ui_auth.tenant_switcher'))

        error = 'Invalid credentials. Please try again.'
        flash(error, 'danger')
    
    return render_template('login.html', error=error)


@ui_auth_bp.route('/switch-tenant')
def tenant_switcher():
    """Render the tenant switcher console for administrators with multiple memberships."""
    if not session.get('logged_in') or not session.get('user_id'):
        return redirect(url_for('ui_auth.login'))
        
    user = ConsoleUser.query.get(session['user_id'])
    if not user or not user.is_active:
        session.clear()
        return redirect(url_for('ui_auth.login'))

    # Resolve all authorized memberships
    if user.is_super_admin:
        # Super admins can access any tenant; map them dummy memberships
        tenants = Tenant.query.all()
        memberships = [{"tenant": t} for t in tenants]
    else:
        memberships = user.tenant_memberships

    return render_template('tenant_switcher.html', memberships=memberships)


@ui_auth_bp.route('/select-tenant/<int:tenant_id>')
def select_tenant(tenant_id):
    """Set the active tenant context for the session after validation."""
    if not session.get('logged_in') or not session.get('user_id'):
        return redirect(url_for('ui_auth.login'))

    user = ConsoleUser.query.get(session['user_id'])
    if not user or not user.is_active:
        session.clear()
        return redirect(url_for('ui_auth.login'))

    # Check authorization mapping
    if not user.is_super_admin:
        membership = ConsoleUserTenantMap.query.filter_by(
            console_user_id=user.id,
            tenant_id=tenant_id
        ).first()
        if not membership:
            _LOGGER.warning("BOLA Blocked: User %s tried to switch to unmapped tenant %s", user.username, tenant_id)
            abort(403)

    tenant = Tenant.query.get(tenant_id)
    if not tenant:
        abort(404)

    session['active_tenant_id'] = tenant.id
    flash(f"Switched context to tenant: {tenant.name}", "info")
    return redirect(url_for('ui_dashboard.dashboard'))


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
        
        email = user_info.get('email')
        username = user_info.get('preferred_username') or user_info.get('sub') or 'OIDC User'
        
        # In a multi-tenant OIDC setup, we look up the user dynamically
        user = ConsoleUser.query.filter_by(email=email).first()
        if not user:
            # First OIDC login seeds local user
            user = ConsoleUser(username=username, email=email)
            db.session.add(user)
            db.session.commit()
            
        session['logged_in'] = True
        session['user_id'] = user.id
        session['user'] = {
            'username': user.username,
            'email': user.email,
            'name': user_info.get('name')
        }
        
        # Auto-map to default tenant ID 1 if they have no memberships
        if not user.tenant_memberships:
            default_tenant = db.session.get(Tenant, 1)
            if not default_tenant:
                from src.tenant_helper import encrypt_tenant_key
                default_tenant = Tenant(
                    id=1,
                    name="Default Workspace",
                    slug="default",
                    registration_token="admin-token",
                    encrypted_data_key=encrypt_tenant_key(b"devmasterkeydefault32byteslong!!!")
                )
                db.session.add(default_tenant)
                db.session.commit()
            
            mapping = ConsoleUserTenantMap(console_user_id=user.id, tenant_id=1, role="tenant_admin")
            db.session.add(mapping)
            db.session.commit()
        
        flash(f"Logged in successfully as {user.username}!", "success")
        
        # Resolve tenant switcher
        memberships = user.tenant_memberships
        if len(memberships) == 1:
            session['active_tenant_id'] = memberships[0].tenant_id
            return redirect(url_for('ui_dashboard.dashboard'))
        else:
            return redirect(url_for('ui_auth.tenant_switcher'))
            
    except (KeyError, RuntimeError, ValueError) as exc:
        _LOGGER.error("OIDC callback processing failed: %s", exc)
        flash(f"Authentication failed: {exc}", "danger")
        return redirect(url_for('ui_auth.login'))


@ui_auth_bp.route('/logout')
def logout():
    """Clear the current session and redirect back to the login page."""
    from app import oidc_helper
    session.pop('logged_in', None)
    session.pop('user_id', None)
    session.pop('active_tenant_id', None)
    session.pop('user', None)
    if oidc_helper.is_enabled:
        return redirect(url_for('ui_auth.login'))
    flash('You have been logged out', 'info')
    return redirect(url_for('ui_auth.login'))
