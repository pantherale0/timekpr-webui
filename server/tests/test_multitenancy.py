import os
import json
import base64
import pytest
from flask import g, session
from app import app
from src.database import db, Tenant, TenantSettings, ConsoleUser, ConsoleUserTenantMap, AgentDevice, ManagedUser
from src.tenant_helper import encrypt_tenant_key, decrypt_tenant_key, tenant_required


@pytest.fixture
def multitenant_app():
    app.config['TESTING'] = True
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///:memory:'
    app.config['SECRET_KEY'] = 'test-secret-key'
    
    with app.app_context():
        db.create_all()
        # Seed test master KEK
        os.environ['MASTER_KEY'] = 'test-master-kek-key-32-bytes!!!'
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture
def db_session(multitenant_app):
    with multitenant_app.app_context():
        yield db.session


def test_tenant_creation_and_encryption(multitenant_app, db_session):
    # 1. Create a Tenant with encrypted DEK
    plain_dek = b'tenant-a-dek-secret-32-bytes-long'
    encrypted_dek = encrypt_tenant_key(plain_dek)
    
    tenant = Tenant(
        name="Acme Corp",
        slug="acme",
        registration_token="acme-token-123",
        encrypted_data_key=encrypted_dek
    )
    db_session.add(tenant)
    db_session.commit()
    
    assert tenant.id is not None
    
    # Verify we can decrypt the DEK back
    decrypted_dek = decrypt_tenant_key(tenant.encrypted_data_key)
    assert decrypted_dek is not None


def test_transparent_tenant_settings_encryption(multitenant_app, db_session):
    tenant = Tenant(
        name="Acme Corp",
        slug="acme",
        registration_token="acme-token-123",
        encrypted_data_key=encrypt_tenant_key(b'tenant-a-dek-secret-32-bytes-long')
    )
    db_session.add(tenant)
    db_session.commit()

    # Set up flask g mock decryption context
    g.current_tenant_dek = decrypt_tenant_key(tenant.encrypted_data_key)
    
    # Save encrypted setting
    TenantSettings.set_value(tenant.id, "sso_client_secret", "my-super-secret-oauth-key", encrypt=True)
    
    # Save plaintext setting
    TenantSettings.set_value(tenant.id, "sso_provider", "azure", encrypt=False)
    
    # Query from DB to verify it decrypted transparently
    secret_val = TenantSettings.get_value(tenant.id, "sso_client_secret")
    provider_val = TenantSettings.get_value(tenant.id, "sso_provider")
    
    assert secret_val == "my-super-secret-oauth-key"
    assert provider_val == "azure"
    
    # Verify the value in DB is indeed encrypted
    raw_setting = TenantSettings.query.filter_by(tenant_id=tenant.id, key="sso_client_secret").first()
    assert raw_setting.value != "my-super-secret-oauth-key"
    assert "my-super-secret" not in raw_setting.value


def test_bola_idor_route_protection(multitenant_app, db_session):
    # Create two tenants
    t1 = Tenant(name="Tenant 1", slug="t1", registration_token="tok1", encrypted_data_key="dek1")
    t2 = Tenant(name="Tenant 2", slug="t2", registration_token="tok2", encrypted_data_key="dek2")
    db_session.add_all([t1, t2])
    db_session.commit()
    
    # Create a user mapped ONLY to Tenant 1
    user = ConsoleUser(username="t1_admin", email="t1@admin.com")
    user.set_password("password")
    db_session.add(user)
    db_session.commit()
    
    mapping = ConsoleUserTenantMap(console_user_id=user.id, tenant_id=t1.id, role="tenant_admin")
    db_session.add(mapping)
    db_session.commit()
    
    # Mock Flask request session
    with multitenant_app.test_request_context():
        session['logged_in'] = True
        session['user_id'] = user.id
        session['active_tenant_id'] = t1.id
        
        # Accessing Tenant 1 should pass
        @tenant_required
        def dummy_route():
            return "success"
            
        res = dummy_route()
        assert res == "success"
        assert g.current_tenant_id == t1.id

    # Switch session active tenant to Tenant 2 (attempting IDOR/BOLA attack)
    with multitenant_app.test_request_context():
        session['logged_in'] = True
        session['user_id'] = user.id
        session['active_tenant_id'] = t2.id
        
        @tenant_required
        def dummy_route_2():
            return "success"
            
        from werkzeug.exceptions import Forbidden
        with pytest.raises(Forbidden):
            dummy_route_2()
