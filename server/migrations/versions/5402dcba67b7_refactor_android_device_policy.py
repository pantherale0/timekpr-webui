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

_DEFAULT_SHORT_SUPPORT_MESSAGE = (
    'This setting is managed by your parent through TimeKpr.'
)
_DEFAULT_LONG_SUPPORT_MESSAGE = (
    'This device is protected by TimeKpr parental controls. Your parent manages '
    'screen time, apps, and websites. Ask them if you need something changed.'
)


def _mapping_android_device_policy_columns():
    return [
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('screen_capture_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('camera_access', sa.String(length=40), nullable=False, server_default='CAMERA_ACCESS_UNSPECIFIED'),
        sa.Column('install_apps_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('uninstall_apps_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('developer_settings', sa.String(length=40), nullable=False, server_default='DEVELOPER_SETTINGS_UNSPECIFIED'),
        sa.Column('microphone_access', sa.String(length=40), nullable=False, server_default='MICROPHONE_ACCESS_UNSPECIFIED'),
        sa.Column('usb_data_access', sa.String(length=40), nullable=False, server_default='USB_DATA_ACCESS_UNSPECIFIED'),
        sa.Column('factory_reset_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('adjust_volume_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('modify_accounts_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('mount_physical_media_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('bluetooth_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('outgoing_calls_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sms_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            'short_support_message',
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_SHORT_SUPPORT_MESSAGE,
        ),
        sa.Column(
            'long_support_message',
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_LONG_SUPPORT_MESSAGE,
        ),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('block_wifi_tethering', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('block_nfc', sa.Boolean(), nullable=False, server_default=sa.false()),
    ]


def upgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'is_device_owner',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            )
        )

    with op.batch_alter_table('managed_user_device_map', schema=None) as batch_op:
        batch_op.add_column(sa.Column('android_profile_type', sa.String(length=20), nullable=True))

    op.rename_table('mapping_android_device_policy', 'mapping_android_device_policy_old')
    op.create_table(
        'mapping_android_device_policy',
        *_mapping_android_device_policy_columns(),
        sa.ForeignKeyConstraint(
            ['system_id'],
            ['agent_device.system_id'],
            name='fk_mapping_android_device_policy_system_id',
        ),
        sa.PrimaryKeyConstraint('system_id', name='pk_mapping_android_device_policy'),
    )

    op.execute(sa.text("""
        INSERT INTO mapping_android_device_policy (
            system_id,
            screen_capture_disabled,
            camera_access,
            install_apps_disabled,
            uninstall_apps_disabled,
            developer_settings,
            microphone_access,
            usb_data_access,
            factory_reset_disabled,
            adjust_volume_disabled,
            modify_accounts_disabled,
            mount_physical_media_disabled,
            bluetooth_disabled,
            outgoing_calls_disabled,
            sms_disabled,
            short_support_message,
            long_support_message,
            revision,
            is_synced,
            last_synced_at,
            last_sync_error,
            created_at,
            updated_at,
            block_wifi_tethering,
            block_nfc
        )
        SELECT
            mudm.system_id,
            old.screen_capture_disabled,
            old.camera_access,
            old.install_apps_disabled,
            old.uninstall_apps_disabled,
            old.developer_settings,
            old.microphone_access,
            old.usb_data_access,
            old.factory_reset_disabled,
            old.adjust_volume_disabled,
            old.modify_accounts_disabled,
            old.mount_physical_media_disabled,
            old.bluetooth_disabled,
            old.outgoing_calls_disabled,
            old.sms_disabled,
            old.short_support_message,
            old.long_support_message,
            old.revision,
            old.is_synced,
            old.last_synced_at,
            old.last_sync_error,
            old.created_at,
            old.updated_at,
            old.block_wifi_tethering,
            old.block_nfc
        FROM mapping_android_device_policy_old old
        INNER JOIN managed_user_device_map mudm ON old.device_map_id = mudm.id
        WHERE old.id IN (
            SELECT MAX(old2.id)
            FROM mapping_android_device_policy_old old2
            INNER JOIN managed_user_device_map mudm2 ON old2.device_map_id = mudm2.id
            GROUP BY mudm2.system_id
        )
    """))

    op.drop_table('mapping_android_device_policy_old')


def downgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_column('is_device_owner')

    with op.batch_alter_table('managed_user_device_map', schema=None) as batch_op:
        batch_op.drop_column('android_profile_type')

    op.rename_table('mapping_android_device_policy', 'mapping_android_device_policy_new')
    op.create_table(
        'mapping_android_device_policy',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('screen_capture_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('camera_access', sa.String(length=40), nullable=False, server_default='CAMERA_ACCESS_UNSPECIFIED'),
        sa.Column('install_apps_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('uninstall_apps_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('developer_settings', sa.String(length=40), nullable=False, server_default='DEVELOPER_SETTINGS_UNSPECIFIED'),
        sa.Column('microphone_access', sa.String(length=40), nullable=False, server_default='MICROPHONE_ACCESS_UNSPECIFIED'),
        sa.Column('usb_data_access', sa.String(length=40), nullable=False, server_default='USB_DATA_ACCESS_UNSPECIFIED'),
        sa.Column('factory_reset_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('adjust_volume_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('modify_accounts_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('mount_physical_media_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('bluetooth_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('outgoing_calls_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('sms_disabled', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            'short_support_message',
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_SHORT_SUPPORT_MESSAGE,
        ),
        sa.Column(
            'long_support_message',
            sa.Text(),
            nullable=False,
            server_default=_DEFAULT_LONG_SUPPORT_MESSAGE,
        ),
        sa.Column('revision', sa.String(length=64), nullable=False, server_default=''),
        sa.Column('is_synced', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('last_synced_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('last_sync_error', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('block_wifi_tethering', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column('block_nfc', sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.ForeignKeyConstraint(
            ['device_map_id'],
            ['managed_user_device_map.id'],
            name='fk_mapping_android_device_policy_device_map_id',
        ),
        sa.PrimaryKeyConstraint('id', name='pk_mapping_android_device_policy_old'),
        sa.UniqueConstraint('device_map_id'),
    )

    op.execute(sa.text("""
        INSERT INTO mapping_android_device_policy (
            device_map_id,
            screen_capture_disabled,
            camera_access,
            install_apps_disabled,
            uninstall_apps_disabled,
            developer_settings,
            microphone_access,
            usb_data_access,
            factory_reset_disabled,
            adjust_volume_disabled,
            modify_accounts_disabled,
            mount_physical_media_disabled,
            bluetooth_disabled,
            outgoing_calls_disabled,
            sms_disabled,
            short_support_message,
            long_support_message,
            revision,
            is_synced,
            last_synced_at,
            last_sync_error,
            created_at,
            updated_at,
            block_wifi_tethering,
            block_nfc
        )
        SELECT
            mudm.id,
            new.screen_capture_disabled,
            new.camera_access,
            new.install_apps_disabled,
            new.uninstall_apps_disabled,
            new.developer_settings,
            new.microphone_access,
            new.usb_data_access,
            new.factory_reset_disabled,
            new.adjust_volume_disabled,
            new.modify_accounts_disabled,
            new.mount_physical_media_disabled,
            new.bluetooth_disabled,
            new.outgoing_calls_disabled,
            new.sms_disabled,
            new.short_support_message,
            new.long_support_message,
            new.revision,
            new.is_synced,
            new.last_synced_at,
            new.last_sync_error,
            new.created_at,
            new.updated_at,
            new.block_wifi_tethering,
            new.block_nfc
        FROM mapping_android_device_policy_new new
        INNER JOIN managed_user_device_map mudm ON new.system_id = mudm.system_id
        WHERE mudm.id IN (
            SELECT MIN(mudm2.id)
            FROM managed_user_device_map mudm2
            GROUP BY mudm2.system_id
        )
    """))

    op.drop_table('mapping_android_device_policy_new')
