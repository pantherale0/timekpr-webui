"""add android device policy extension fields

Revision ID: i0d5e6f7a8b9
Revises: h9c4d5e6f7a8
Create Date: 2026-06-05 23:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'i0d5e6f7a8b9'
down_revision = 'h9c4d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('mapping_android_device_policy', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'microphone_access',
                sa.String(length=40),
                nullable=False,
                server_default='MICROPHONE_ACCESS_UNSPECIFIED',
            ),
        )
        batch_op.add_column(
            sa.Column(
                'usb_data_access',
                sa.String(length=40),
                nullable=False,
                server_default='USB_DATA_ACCESS_UNSPECIFIED',
            ),
        )
        batch_op.add_column(
            sa.Column(
                'factory_reset_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'adjust_volume_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'modify_accounts_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'mount_physical_media_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'bluetooth_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'outgoing_calls_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column(
                'sms_disabled',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade():
    with op.batch_alter_table('mapping_android_device_policy', schema=None) as batch_op:
        batch_op.drop_column('sms_disabled')
        batch_op.drop_column('outgoing_calls_disabled')
        batch_op.drop_column('bluetooth_disabled')
        batch_op.drop_column('mount_physical_media_disabled')
        batch_op.drop_column('modify_accounts_disabled')
        batch_op.drop_column('adjust_volume_disabled')
        batch_op.drop_column('factory_reset_disabled')
        batch_op.drop_column('usb_data_access')
        batch_op.drop_column('microphone_access')
