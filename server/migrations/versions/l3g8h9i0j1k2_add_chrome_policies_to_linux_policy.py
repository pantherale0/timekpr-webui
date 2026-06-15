"""add chrome policies to linux policy

Revision ID: l3g8h9i0j1k2
Revises: e6bd8cf2e66d
Create Date: 2026-06-15 17:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'l3g8h9i0j1k2'
down_revision = 'e6bd8cf2e66d'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('mapping_linux_device_policy', sa.Column('chrome_policies_json', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('mapping_linux_device_policy', 'chrome_policies_json')
