"""add android push fields to agent_device

Revision ID: b3e8a1f04c2d
Revises: 7c66dbe66fa1
Create Date: 2026-06-01 22:40:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b3e8a1f04c2d'
down_revision = '7c66dbe66fa1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('platform', sa.String(length=20), nullable=True))
        batch_op.add_column(sa.Column('fcm_token', sa.String(length=512), nullable=True))
        batch_op.add_column(sa.Column('fcm_token_updated_at', sa.DateTime(timezone=True), nullable=True))


def downgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_column('fcm_token_updated_at')
        batch_op.drop_column('fcm_token')
        batch_op.drop_column('platform')
