"""add device unenrollment fields

Revision ID: h9c4d5e6f7a8
Revises: g8b3c4d5e6f7
Create Date: 2026-06-05 22:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'h9c4d5e6f7a8'
down_revision = 'g8b3c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'pending_factory_reset',
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )
        batch_op.add_column(
            sa.Column('unenrolled_at', sa.DateTime(timezone=True), nullable=True),
        )


def downgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_column('unenrolled_at')
        batch_op.drop_column('pending_factory_reset')
