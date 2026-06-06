"""add mapping linux device policy table

Revision ID: j1e6f7a8b9c0
Revises: i0d5e6f7a8b9
Create Date: 2026-06-06 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'j1e6f7a8b9c0'
down_revision = 'i0d5e6f7a8b9'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mapping_linux_device_policy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('install_software_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('uninstall_software_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('mount_removable_media_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('modify_accounts_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('system_power_actions_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('pkexec_elevation_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('bluetooth_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('flatpak_install_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('snap_install_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('terminal_access_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column(
            'support_message',
            sa.Text(),
            nullable=False,
            server_default='This setting is managed by your parent through TimeKpr.',
        ),
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
    op.drop_table('mapping_linux_device_policy')
