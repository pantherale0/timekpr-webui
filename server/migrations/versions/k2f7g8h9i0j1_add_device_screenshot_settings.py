"""add device screenshot settings and screenshot storage

Revision ID: k2f7g8h9i0j1
Revises: c1aa5a072532
Create Date: 2026-06-13 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'k2f7g8h9i0j1'
down_revision = 'c1aa5a072532'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'device_screenshot_settings',
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('interval_seconds', sa.Integer(), nullable=False, server_default='300'),
        sa.Column('retention_hours', sa.Integer(), nullable=False, server_default='168'),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['system_id'], ['agent_device.system_id']),
        sa.PrimaryKeyConstraint('system_id'),
    )

    op.create_table(
        'device_screenshot',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('screenshot_id', sa.String(length=64), nullable=False),
        sa.Column('linux_username', sa.String(length=80), nullable=True),
        sa.Column('captured_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('mime_type', sa.String(length=64), nullable=False),
        sa.Column('width', sa.Integer(), nullable=True),
        sa.Column('height', sa.Integer(), nullable=True),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('active_window_title', sa.String(length=255), nullable=True),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['system_id'], ['agent_device.system_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('system_id', 'screenshot_id', name='device_screenshot_uc'),
    )
    op.create_index(
        'device_screenshot_system_captured_idx',
        'device_screenshot',
        ['system_id', 'captured_at'],
        unique=False,
    )


def downgrade():
    op.drop_index('device_screenshot_system_captured_idx', table_name='device_screenshot')
    op.drop_table('device_screenshot')
    op.drop_table('device_screenshot_settings')
