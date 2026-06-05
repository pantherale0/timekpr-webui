"""add mapping android device policy table

Revision ID: f7a2b3c4d5e6
Revises: e6f1a2b3c4d5
Create Date: 2026-06-05 20:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'f7a2b3c4d5e6'
down_revision = 'e6f1a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mapping_android_device_policy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('screen_capture_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('camera_access', sa.String(length=40), nullable=False, server_default='CAMERA_ACCESS_UNSPECIFIED'),
        sa.Column('install_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('uninstall_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('developer_settings', sa.String(length=40), nullable=False, server_default='DEVELOPER_SETTINGS_UNSPECIFIED'),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_map_id'], ['managed_user_device_map.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_map_id'),
    )


def downgrade():
    op.drop_table('mapping_android_device_policy')
