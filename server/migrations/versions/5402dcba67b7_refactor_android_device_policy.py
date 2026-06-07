"""refactor_android_device_policy

Revision ID: 5402dcba67b7
Revises: 6f4e9d1f9b34
Create Date: 2026-06-07 11:20:22.454346

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '5402dcba67b7'
down_revision = '6f4e9d1f9b34'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_device_owner', sa.Boolean(), nullable=False, server_default=sa.text('0')))

    with op.batch_alter_table('managed_user_device_map', schema=None) as batch_op:
        batch_op.add_column(sa.Column('android_profile_type', sa.String(length=20), nullable=True))

    op.drop_table('mapping_android_device_policy')
    op.create_table('mapping_android_device_policy',
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('screen_capture_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('camera_access', sa.String(length=40), nullable=False, server_default='CAMERA_ACCESS_UNSPECIFIED'),
        sa.Column('install_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('uninstall_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('developer_settings', sa.String(length=40), nullable=False, server_default='DEVELOPER_SETTINGS_UNSPECIFIED'),
        sa.Column('microphone_access', sa.String(length=40), nullable=False, server_default='MICROPHONE_ACCESS_UNSPECIFIED'),
        sa.Column('usb_data_access', sa.String(length=40), nullable=False, server_default='USB_DATA_ACCESS_UNSPECIFIED'),
        sa.Column('factory_reset_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('adjust_volume_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('modify_accounts_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('mount_physical_media_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('bluetooth_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('outgoing_calls_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('sms_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('short_support_message', sa.Text(), nullable=False),
        sa.Column('long_support_message', sa.Text(), nullable=False),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('block_wifi_tethering', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('block_nfc', sa.Boolean(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['system_id'], ['agent_device.system_id'], name='fk_mapping_android_device_policy_system_id'),
        sa.PrimaryKeyConstraint('system_id', name='pk_mapping_android_device_policy')
    )


def downgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_column('is_device_owner')

    with op.batch_alter_table('managed_user_device_map', schema=None) as batch_op:
        batch_op.drop_column('android_profile_type')

    op.drop_table('mapping_android_device_policy')
    op.create_table('mapping_android_device_policy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('screen_capture_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('camera_access', sa.String(length=40), nullable=False, server_default='CAMERA_ACCESS_UNSPECIFIED'),
        sa.Column('install_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('uninstall_apps_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('developer_settings', sa.String(length=40), nullable=False, server_default='DEVELOPER_SETTINGS_UNSPECIFIED'),
        sa.Column('microphone_access', sa.String(length=40), nullable=False, server_default='MICROPHONE_ACCESS_UNSPECIFIED'),
        sa.Column('usb_data_access', sa.String(length=40), nullable=False, server_default='USB_DATA_ACCESS_UNSPECIFIED'),
        sa.Column('factory_reset_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('adjust_volume_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('modify_accounts_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('mount_physical_media_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('bluetooth_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('outgoing_calls_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('sms_disabled', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('short_support_message', sa.Text(), nullable=False),
        sa.Column('long_support_message', sa.Text(), nullable=False),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('block_wifi_tethering', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('block_nfc', sa.Boolean(), nullable=False, server_default='0'),
        sa.ForeignKeyConstraint(['device_map_id'], ['managed_user_device_map.id'], name='fk_mapping_android_device_policy_device_map_id'),
        sa.PrimaryKeyConstraint('id', name='pk_mapping_android_device_policy_old'),
        sa.UniqueConstraint('device_map_id')
    )
