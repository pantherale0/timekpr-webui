"""add_household_multitenancy

Revision ID: 68617a488776
Revises: f1576ce83951
Create Date: 2026-06-25 22:45:12.678386

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '68617a488776'
down_revision = 'f1576ce83951'
branch_labels = None
depends_on = None


def upgrade():
    # --- New multi-tenant tables ---
    op.create_table(
        'household',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('enrollment_token', sa.String(64), unique=True, nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        'parent_account',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('oidc_sub', sa.String(255), unique=True, nullable=True),
        sa.Column('email', sa.String(255), unique=True, nullable=False),
        sa.Column('name', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    )

    op.create_table(
        'household_parent_membership',
        sa.Column('household_id', sa.Integer(),
                  sa.ForeignKey('household.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('parent_account_id', sa.Integer(),
                  sa.ForeignKey('parent_account.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permissions_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('joined_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint('household_id', 'parent_account_id',
                            name='uq_household_parent_membership'),
    )

    op.create_table(
        'household_invite',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('household_id', sa.Integer(),
                  sa.ForeignKey('household.id', ondelete='CASCADE'), nullable=False),
        sa.Column('invite_code', sa.String(32), unique=True, nullable=False),
        sa.Column('created_by_id', sa.Integer(),
                  sa.ForeignKey('parent_account.id', ondelete='SET NULL'), nullable=True,
                  index=True),
        sa.Column('permissions_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default='1'),
    )

    op.create_table(
        'managed_user_share',
        sa.Column('parent_account_id', sa.Integer(),
                  sa.ForeignKey('parent_account.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('managed_user_id', sa.Integer(),
                  sa.ForeignKey('managed_user.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('permissions_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('shared_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.create_table(
        'managed_user_share_invite',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('managed_user_id', sa.Integer(),
                  sa.ForeignKey('managed_user.id', ondelete='CASCADE'), nullable=False),
        sa.Column('invite_code', sa.String(32), unique=True, nullable=False),
        sa.Column('permissions_json', sa.JSON(), nullable=False, server_default='{}'),
        sa.Column('created_by_id', sa.Integer(),
                  sa.ForeignKey('parent_account.id', ondelete='SET NULL'), nullable=True,
                  index=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('used_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('max_uses', sa.Integer(), nullable=False, server_default='1'),
    )

    # --- Add household_id FK columns to existing tables ---
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('household_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_agent_device_household', 'household', ['household_id'], ['id'], ondelete='SET NULL')

    with op.batch_alter_table('managed_user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('household_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_managed_user_household', 'household', ['household_id'], ['id'], ondelete='CASCADE')

    # Seed default household and update existing unlinked records
    import secrets
    token = secrets.token_hex(32)
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "INSERT INTO household (name, enrollment_token) VALUES (:name, :token)"
        ).bindparams(name="Default Household", token=token)
    )
    res = bind.execute(
        sa.text("SELECT id FROM household WHERE enrollment_token = :token").bindparams(token=token)
    )
    hh_id = res.scalar() or 1

    bind.execute(
        sa.text("UPDATE agent_device SET household_id = :hh_id WHERE household_id IS NULL").bindparams(hh_id=hh_id)
    )
    bind.execute(
        sa.text("UPDATE managed_user SET household_id = :hh_id WHERE household_id IS NULL").bindparams(hh_id=hh_id)
    )

    # Seed the local legacy 'admin' parent account
    bind.execute(
        sa.text(
            "INSERT INTO parent_account (email, name, oidc_sub) VALUES (:email, :name, :oidc_sub)"
        ).bindparams(email="admin@local", name="Local Admin", oidc_sub=None)
    )
    res_parent = bind.execute(
        sa.text("SELECT id FROM parent_account WHERE email = :email").bindparams(email="admin@local")
    )
    parent_id = res_parent.scalar() or 1

    # Map the admin membership to the Default Household with owner permissions
    bind.execute(
        sa.text(
            "INSERT INTO household_parent_membership (household_id, parent_account_id, permissions_json) "
            "VALUES (:hh_id, :parent_id, :perms)"
        ).bindparams(hh_id=hh_id, parent_id=parent_id, perms='{"is_owner": true}')
    )

    with op.batch_alter_table('mapping_android_device_policy', schema=None) as batch_op:
        batch_op.create_unique_constraint('uq_mapping_android_device_policy_system_id', ['system_id'])

    with op.batch_alter_table('pending_command', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_pending_command_system_coalesce_status'))
        batch_op.drop_index(batch_op.f('ix_pending_command_system_status_created'))

    # ### end Alembic commands ###


def downgrade():
    with op.batch_alter_table('pending_command', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_pending_command_system_status_created'), ['system_id', 'status', 'created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_pending_command_system_coalesce_status'), ['system_id', 'coalesce_key', 'status'], unique=False)

    with op.batch_alter_table('mapping_android_device_policy', schema=None) as batch_op:
        batch_op.drop_constraint('uq_mapping_android_device_policy_system_id', type_='unique')

    with op.batch_alter_table('managed_user', schema=None) as batch_op:
        batch_op.drop_constraint('fk_managed_user_household', type_='foreignkey')
        batch_op.drop_column('household_id')

    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_constraint('fk_agent_device_household', type_='foreignkey')
        batch_op.drop_column('household_id')

    # Drop new multi-tenant tables in reverse dependency order
    op.drop_table('managed_user_share_invite')
    op.drop_table('managed_user_share')
    op.drop_table('household_invite')
    op.drop_table('household_parent_membership')
    op.drop_table('parent_account')
    op.drop_table('household')
