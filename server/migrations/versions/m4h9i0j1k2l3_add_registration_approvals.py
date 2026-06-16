"""add registration approvals and online account audit

Revision ID: m4h9i0j1k2l3
Revises: 121e8f860952
Create Date: 2026-06-16 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect


revision = 'm4h9i0j1k2l3'
down_revision = '121e8f860952'
branch_labels = None
depends_on = None


def _column_exists(table_name, column_name):
    """Return True if *column_name* already exists in *table_name*."""
    bind = op.get_bind()
    inspector = sqla_inspect(bind)
    columns = [col['name'] for col in inspector.get_columns(table_name)]
    return column_name in columns


def _table_exists(table_name):
    """Return True if *table_name* already exists in the current database."""
    bind = op.get_bind()
    inspector = sqla_inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade():
    # Add registration_approval_enabled to mapping_approval_settings (idempotent)
    if not _column_exists('mapping_approval_settings', 'registration_approval_enabled'):
        op.add_column(
            'mapping_approval_settings',
            sa.Column(
                'registration_approval_enabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    # Create the user_online_account table for login audit trail (idempotent)
    if not _table_exists('user_online_account'):
        op.create_table(
            'user_online_account',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('managed_user_id', sa.Integer(), nullable=False),
            sa.Column('domain', sa.String(length=255), nullable=False),
            sa.Column('username', sa.String(length=255), nullable=False),
            sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
            sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(['managed_user_id'], ['managed_user.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'managed_user_id', 'domain', 'username',
                name='user_online_account_uc',
            ),
        )
        op.create_index(
            'user_online_account_user_idx',
            'user_online_account',
            ['managed_user_id'],
            unique=False,
        )


def downgrade():
    if _table_exists('user_online_account'):
        op.drop_index('user_online_account_user_idx', table_name='user_online_account')
        op.drop_table('user_online_account')
    if _column_exists('mapping_approval_settings', 'registration_approval_enabled'):
        op.drop_column('mapping_approval_settings', 'registration_approval_enabled')
