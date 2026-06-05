"""add platform to app_policy

Revision ID: d5e8f3b2a1c4
Revises: c4d9e2a1b7f3
Create Date: 2026-06-05 14:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'd5e8f3b2a1c4'
down_revision = 'c4d9e2a1b7f3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('platform', sa.String(length=20), nullable=False, server_default='linux'),
        )
    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.alter_column('platform', server_default=None)


def downgrade():
    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.drop_column('platform')
