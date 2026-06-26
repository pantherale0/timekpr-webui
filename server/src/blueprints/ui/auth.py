import logging
import time
import secrets
from datetime import datetime, timezone
from flask import Blueprint, render_template, request, redirect, url_for, session
from src.database import db, Settings, ParentAccount, Household, HouseholdParentMembership, HouseholdInvite, ManagedUserShare, ManagedUserShareInvite
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
            parent = ParentAccount.query.filter_by(email='admin@local').first()
            if parent:
                session['parent_account_id'] = parent.id
                memberships = [m.household_id for m in parent.memberships]
                if memberships:
                    session['active_household_id'] = memberships[0]
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

        # Look up or create ParentAccount
        email = user_info.get('email')
        oidc_sub = user_info.get('sub')
        name = user_info.get('name')
        
        if not email:
            email = f"{session['user']['username']}@oidc.local"
            
        parent = ParentAccount.query.filter((ParentAccount.oidc_sub == oidc_sub) | (ParentAccount.email == email)).first()
        if not parent:
            parent = ParentAccount(
                oidc_sub=oidc_sub,
                email=email,
                name=name
            )
            db.session.add(parent)
            db.session.commit()
            _LOGGER.info("Created new ParentAccount for OIDC user: %s", email)
        else:
            if not parent.oidc_sub:
                parent.oidc_sub = oidc_sub
            if name and not parent.name:
                parent.name = name
            parent.last_login = datetime.now(timezone.utc)
            db.session.commit()
            
        session['parent_account_id'] = parent.id
        
        # Load user's associated households and shares
        memberships = [m.household_id for m in parent.memberships]
        has_shares = ManagedUserShare.query.filter_by(parent_account_id=parent.id).first() is not None
        
        flash_t(
            'flash.auth.oidc_success',
            'success',
            username=session['user']['username'],
        )

        if not memberships and not has_shares:
            _LOGGER.info("OIDC user %s has no households/shares, redirecting to onboarding", email)
            return redirect(url_for('ui_auth.onboarding'))

        if memberships:
            session['active_household_id'] = memberships[0]
        else:
            session['active_household_id'] = None

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


@ui_auth_bp.route('/onboarding', methods=['GET'])
def onboarding():
    if not session.get('logged_in'):
        return redirect(url_for('ui_auth.login'))
    return render_template('onboarding.html')


@ui_auth_bp.route('/onboarding/create', methods=['POST'])
def onboarding_create():
    if not session.get('logged_in') or not session.get('parent_account_id'):
        return redirect(url_for('ui_auth.login'))
        
    parent_id = session['parent_account_id']
    household_name = request.form.get('household_name', '').strip()
    if not household_name:
        flash_t('flash.auth.household_name_required', 'danger')
        return redirect(url_for('ui_auth.onboarding'))
        
    try:
        new_hh = Household(name=household_name, enrollment_token=secrets.token_hex(32))
        db.session.add(new_hh)
        db.session.flush()
        
        membership = HouseholdParentMembership(
            household_id=new_hh.id,
            parent_account_id=parent_id,
            permissions_json={"is_owner": True}
        )
        db.session.add(membership)
        db.session.commit()
        
        session['active_household_id'] = new_hh.id
        flash_t('flash.auth.household_created', 'success', name=household_name)
        return redirect(url_for('ui_dashboard.dashboard'))
    except Exception as exc:
        db.session.rollback()
        _LOGGER.error("Failed to create household: %s", exc)
        flash_t('flash.auth.household_create_failed', 'danger')
        return redirect(url_for('ui_auth.onboarding'))


@ui_auth_bp.route('/onboarding/join', methods=['POST'])
def onboarding_join():
    if not session.get('logged_in') or not session.get('parent_account_id'):
        return redirect(url_for('ui_auth.login'))
        
    parent_id = session['parent_account_id']
    invite_code = request.form.get('invite_code', '').strip()
    if not invite_code:
        flash_t('flash.auth.invite_code_required', 'danger')
        return redirect(url_for('ui_auth.onboarding'))
        
    try:
        now = datetime.now(timezone.utc)
        invite = HouseholdInvite.query.filter_by(invite_code=invite_code).first()
        if not invite or (invite.expires_at and invite.expires_at < now) or invite.used_count >= invite.max_uses:
            flash_t('flash.auth.invalid_invite_code', 'danger')
            return redirect(url_for('ui_auth.onboarding'))
            
        existing = HouseholdParentMembership.query.filter_by(
            household_id=invite.household_id,
            parent_account_id=parent_id
        ).first()
        if existing:
            session['active_household_id'] = invite.household_id
            flash_t('flash.auth.already_member', 'info')
            return redirect(url_for('ui_dashboard.dashboard'))
            
        membership = HouseholdParentMembership(
            household_id=invite.household_id,
            parent_account_id=parent_id,
            permissions_json=invite.permissions_json
        )
        db.session.add(membership)
        invite.used_count += 1
        db.session.commit()
        
        session['active_household_id'] = invite.household_id
        flash_t('flash.auth.household_joined', 'success')
        return redirect(url_for('ui_dashboard.dashboard'))
    except Exception as exc:
        db.session.rollback()
        _LOGGER.error("Failed to join household: %s", exc)
        flash_t('flash.auth.household_join_failed', 'danger')
        return redirect(url_for('ui_auth.onboarding'))


@ui_auth_bp.route('/onboarding/redeem_share', methods=['POST'])
def onboarding_redeem_share():
    if not session.get('logged_in') or not session.get('parent_account_id'):
        return redirect(url_for('ui_auth.login'))
        
    parent_id = session['parent_account_id']
    share_code = request.form.get('share_code', '').strip()
    if not share_code:
        flash_t('flash.auth.share_code_required', 'danger')
        return redirect(url_for('ui_auth.onboarding'))
        
    try:
        now = datetime.now(timezone.utc)
        invite = ManagedUserShareInvite.query.filter_by(invite_code=share_code).first()
        if not invite or (invite.expires_at and invite.expires_at < now) or invite.used_count >= invite.max_uses:
            flash_t('flash.auth.invalid_share_code', 'danger')
            return redirect(url_for('ui_auth.onboarding'))
            
        existing = ManagedUserShare.query.filter_by(
            managed_user_id=invite.managed_user_id,
            parent_account_id=parent_id
        ).first()
        if existing:
            flash_t('flash.auth.already_shared', 'info')
            return redirect(url_for('ui_dashboard.dashboard'))
            
        share = ManagedUserShare(
            parent_account_id=parent_id,
            managed_user_id=invite.managed_user_id,
            permissions_json=invite.permissions_json
        )
        db.session.add(share)
        invite.used_count += 1
        db.session.commit()
        
        flash_t('flash.auth.child_share_redeemed', 'success')
        return redirect(url_for('ui_dashboard.dashboard'))
    except Exception as exc:
        db.session.rollback()
        _LOGGER.error("Failed to redeem child share: %s", exc)
        flash_t('flash.auth.child_share_failed', 'danger')
        return redirect(url_for('ui_auth.onboarding'))


@ui_auth_bp.route('/auth/switch_household/<int:household_id>', methods=['POST'])
def switch_household(household_id):
    if not session.get('logged_in'):
        return redirect(url_for('ui_auth.login'))
        
    parent_id = session.get('parent_account_id')
    if not parent_id:
        return redirect(url_for('ui_auth.login'))
        
    from src.database import HouseholdParentMembership
    membership = HouseholdParentMembership.query.filter_by(
        household_id=household_id,
        parent_account_id=parent_id
    ).first()
    
    if not membership:
        flash_t('flash.auth.unauthorized_household', 'danger')
        return redirect(url_for('ui_dashboard.dashboard'))
        
    session['active_household_id'] = household_id
    flash_t('flash.auth.household_switched', 'success')
    return redirect(url_for('ui_dashboard.dashboard'))
