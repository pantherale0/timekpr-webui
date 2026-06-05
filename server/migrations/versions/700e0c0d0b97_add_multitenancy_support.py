"""add_multitenancy_support

Revision ID: 700e0c0d0b97
Revises: 7c66dbe66fa1
Create Date: 2026-05-29 23:02:31.276178

"""
from alembic import op
import sqlalchemy as sa
import os
import base64
from cryptography.fernet import Fernet


# revision identifiers, used by Alembic.
revision = '700e0c0d0b97'
down_revision = '7c66dbe66fa1'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Create tenant table
    op.create_table('tenant',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('slug', sa.String(length=50), nullable=False),
        sa.Column('registration_token', sa.String(length=64), nullable=False),
        sa.Column('encrypted_data_key', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
        sa.UniqueConstraint('registration_token'),
        sa.UniqueConstraint('slug')
    )

    # 2. Create tenant_settings table
    op.create_table('tenant_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('key', sa.String(length=100), nullable=False),
        sa.Column('value', sa.Text(), nullable=False),
        sa.Column('is_encrypted', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('tenant_id', 'key', name='tenant_key_uc')
    )

    # 3. Create console_user table
    op.create_table('console_user',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=False),
        sa.Column('email', sa.String(length=120), nullable=False),
        sa.Column('password_hash', sa.String(length=255), nullable=True),
        sa.Column('is_super_admin', sa.Boolean(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
        sa.UniqueConstraint('username')
    )

    # 4. Create console_user_tenant_map table
    op.create_table('console_user_tenant_map',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('console_user_id', sa.Integer(), nullable=False),
        sa.Column('tenant_id', sa.Integer(), nullable=False),
        sa.Column('role', sa.String(length=32), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['console_user_id'], ['console_user.id'], ),
        sa.ForeignKeyConstraint(['tenant_id'], ['tenant.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('console_user_id', 'tenant_id', name='user_tenant_uc')
    )

    # 5. Add tenant_id columns to existing tables
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_agent_device_tenant', 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('managed_user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_managed_user_tenant', 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_app_policy_tenant', 'tenant', ['tenant_id'], ['id'])

    with op.batch_alter_table('blocklist_source', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tenant_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_blocklist_source_tenant', 'tenant', ['tenant_id'], ['id'])

    # 6. Data Backfill: Seeding default tenant
    master_key = os.environ.get('MASTER_KEY', 'devmasterkeydefault32byteslong!!!').encode('utf-8')[:32]
    kek = base64.urlsafe_b64encode(master_key.ljust(32, b'\0')[:32])
    fernet = Fernet(kek)
    
    tenant_dek = Fernet.generate_key()
    encrypted_dek = fernet.encrypt(tenant_dek).decode('utf-8')
    reg_token = os.environ.get('REGISTRATION_TOKEN', 'admin-token')

    # Seed Default Tenant
    op.execute(
        f"INSERT INTO tenant (id, name, slug, registration_token, encrypted_data_key, created_at) "
        f"VALUES (1, 'Default Workspace', 'default', '{reg_token}', '{encrypted_dek}', CURRENT_TIMESTAMP)"
    )

    # Backfill existing entities
    op.execute("UPDATE agent_device SET tenant_id = 1")
    op.execute("UPDATE managed_user SET tenant_id = 1")
    op.execute("UPDATE app_policy SET tenant_id = 1")
    op.execute("UPDATE blocklist_source SET tenant_id = 1")


def downgrade():
    op.drop_table('console_user_tenant_map')
    op.drop_table('console_user')
    op.drop_table('tenant_settings')
    op.drop_table('tenant')

    with op.batch_alter_table('blocklist_source', schema=None) as batch_op:
        batch_op.drop_constraint('fk_blocklist_source_tenant', type_='foreignkey')
        batch_op.drop_column('tenant_id')

    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.drop_constraint('fk_app_policy_tenant', type_='foreignkey')
        batch_op.drop_column('tenant_id')

    with op.batch_alter_table('managed_user', schema=None) as batch_op:
        batch_op.drop_constraint('fk_managed_user_tenant', type_='foreignkey')
        batch_op.drop_column('tenant_id')

    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_constraint('fk_agent_device_tenant', type_='foreignkey')
        batch_op.drop_column('tenant_id')
