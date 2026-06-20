"""add pending_command table

Revision ID: r9m4n5o6p7q8
Revises: q8l3m4n5o6p7
Create Date: 2026-06-20 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'r9m4n5o6p7q8'
down_revision = 'q8l3m4n5o6p7'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'pending_command',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('action', sa.String(length=64), nullable=False),
        sa.Column('username', sa.String(length=80), nullable=True),
        sa.Column('command_kind', sa.String(length=32), nullable=False),
        sa.Column('args_json', sa.Text(), nullable=True),
        sa.Column('coalesce_key', sa.String(length=128), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(['system_id'], ['agent_device.system_id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_pending_command_system_status_created',
        'pending_command',
        ['system_id', 'status', 'created_at'],
    )
    op.create_index(
        'ix_pending_command_system_coalesce_status',
        'pending_command',
        ['system_id', 'coalesce_key', 'status'],
    )


def downgrade():
    op.drop_index('ix_pending_command_system_coalesce_status', table_name='pending_command')
    op.drop_index('ix_pending_command_system_status_created', table_name='pending_command')
    op.drop_table('pending_command')
