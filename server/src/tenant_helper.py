import logging
import os
import base64
from functools import wraps
from flask import session, redirect, url_for, flash, g, abort
from cryptography.fernet import Fernet
from src.database import db, Tenant, ConsoleUser, ConsoleUserTenantMap

_LOGGER = logging.getLogger(__name__)


def get_master_key():
    """Resolve the server-wide Key Encryption Key (KEK) / Master Key."""
    master_key = os.environ.get('MASTER_KEY', 'devmasterkeydefault32byteslong!!!').encode('utf-8')[:32]
    # Fernet requires a base64url-encoded 32-byte key
    return base64.urlsafe_b64encode(master_key.ljust(32, b'\0')[:32])


def decrypt_tenant_key(encrypted_data_key):
    """Decrypt a tenant's local Data Encryption Key (DEK) using the Master Key (KEK)."""
    master_key = get_master_key()
    try:
        fernet = Fernet(master_key)
        decrypted = fernet.decrypt(encrypted_data_key.encode('utf-8'))
        # Return base64url encoded 32-byte key suitable for Fernet
        return base64.urlsafe_b64encode(decrypted.ljust(32, b'\0')[:32])
    except Exception as exc:
        _LOGGER.error("Failed to decrypt tenant data key: %s", exc)
        # Fallback for development environments
        return master_key


def encrypt_tenant_key(plain_data_key):
    """Encrypt a tenant's local Data Encryption Key (DEK) using the Master Key (KEK)."""
    master_key = get_master_key()
    fernet = Fernet(master_key)
    return fernet.encrypt(plain_data_key).decode('utf-8')


def tenant_required(f):
    """Decorator to enforce strict multi-tenant authorization (BOLA/IDOR shield) & context initialization."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        # 1. Verify user authentication globally
        if not session.get('logged_in') or not session.get('user_id'):
            flash('Please login first', 'warning')
            return redirect(url_for('ui_auth.login'))
            
        user = db.session.get(ConsoleUser, session['user_id'])
        if not user or not user.is_active:
            session.clear()
            flash('Your session has expired or your account is deactivated.', 'danger')
            return redirect(url_for('ui_auth.login'))
            
        # 2. Resolve Active Tenant Scope
        active_tenant_id = session.get('active_tenant_id')
        if not active_tenant_id:
            # If the user is only a member of exactly one tenant, auto-select it
            memberships = user.tenant_memberships
            if len(memberships) == 1:
                active_tenant_id = memberships[0].tenant_id
                session['active_tenant_id'] = active_tenant_id
            elif user.is_super_admin:
                # Super admins need to select a tenant or default to the first one available
                first_tenant = Tenant.query.first()
                if first_tenant:
                    active_tenant_id = first_tenant.id
                    session['active_tenant_id'] = active_tenant_id
                else:
                    flash('No tenants available in the system. Please create one.', 'warning')
                    abort(404)
            else:
                return redirect(url_for('ui_auth.tenant_switcher'))
                
        # 3. Enforce Membership Authorization Check (BOLA Shield)
        if not user.is_super_admin:
            membership = ConsoleUserTenantMap.query.filter_by(
                console_user_id=user.id, 
                tenant_id=active_tenant_id
            ).first()
            if not membership:
                _LOGGER.warning("Unauthorized Access Attempt: User %s tried to access tenant ID %s", user.username, active_tenant_id)
                abort(403) # Forbidden
                
        # 4. Context Initialization (Load Decryption Keys)
        tenant = db.session.get(Tenant, active_tenant_id)
        if not tenant:
            session.pop('active_tenant_id', None)
            flash('The active tenant no longer exists.', 'warning')
            return redirect(url_for('ui_auth.tenant_switcher'))

        g.current_tenant_id = tenant.id
        g.current_tenant_dek = decrypt_tenant_key(tenant.encrypted_data_key)
        
        return f(*args, **kwargs)
    return decorated_function
