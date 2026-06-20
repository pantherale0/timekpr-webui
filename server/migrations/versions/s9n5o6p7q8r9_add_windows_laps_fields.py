"""Add Windows local administrator LAPS escrow fields to agent_device

Revision ID: s9n5o6p7q8r9
Revises: q8l3m4n5o6p7
Create Date: 2026-06-20 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 's9n5o6p7q8r9'
down_revision = 'q8l3m4n5o6p7'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_device', sa.Column('windows_local_admin_password_escrow', sa.Text(), nullable=True))
    op.add_column(
        'agent_device',
        sa.Column('windows_local_admin_rotated_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column('agent_device', sa.Column('windows_local_admin_rotation_id', sa.String(length=64), nullable=True))


def downgrade():
    op.drop_column('agent_device', 'windows_local_admin_rotation_id')
    op.drop_column('agent_device', 'windows_local_admin_rotated_at')
    op.drop_column('agent_device', 'windows_local_admin_password_escrow')
