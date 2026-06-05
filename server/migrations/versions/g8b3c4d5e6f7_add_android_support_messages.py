"""add android device policy support messages

Revision ID: g8b3c4d5e6f7
Revises: f7a2b3c4d5e6
Create Date: 2026-06-05 21:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'g8b3c4d5e6f7'
down_revision = 'f7a2b3c4d5e6'
branch_labels = None
depends_on = None

DEFAULT_SHORT = 'This setting is managed by your parent through TimeKpr.'
DEFAULT_LONG = (
    'This device is protected by TimeKpr parental controls. Your parent manages '
    'screen time, apps, and websites. Ask them if you need something changed.'
)


def upgrade():
    op.add_column(
        'mapping_android_device_policy',
        sa.Column('short_support_message', sa.Text(), nullable=False, server_default=DEFAULT_SHORT),
    )
    op.add_column(
        'mapping_android_device_policy',
        sa.Column('long_support_message', sa.Text(), nullable=False, server_default=DEFAULT_LONG),
    )


def downgrade():
    op.drop_column('mapping_android_device_policy', 'long_support_message')
    op.drop_column('mapping_android_device_policy', 'short_support_message')
