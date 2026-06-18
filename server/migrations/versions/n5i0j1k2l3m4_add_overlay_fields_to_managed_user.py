"""add overlay fields to managed user

Revision ID: n5i0j1k2l3m4
Revises: 66fd98cac85c
Create Date: 2026-06-18 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect

revision = 'n5i0j1k2l3m4'
down_revision = '66fd98cac85c'
branch_labels = None
depends_on = None


def _column_exists(table, column):
    bind = op.get_bind()
    inspector = sqla_inspect(bind)
    columns = [c['name'] for c in inspector.get_columns(table)]
    return column in columns


def upgrade():
    if not _column_exists('managed_user', 'overlay_age_tier'):
        op.add_column('managed_user', sa.Column('overlay_age_tier', sa.String(16), nullable=True))
    if not _column_exists('managed_user', 'overlay_parent_note'):
        op.add_column('managed_user', sa.Column('overlay_parent_note', sa.Text(), nullable=True))


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if _column_exists('managed_user', 'overlay_parent_note'):
        if dialect != 'sqlite':
            op.drop_column('managed_user', 'overlay_parent_note')
    if _column_exists('managed_user', 'overlay_age_tier'):
        if dialect != 'sqlite':
            op.drop_column('managed_user', 'overlay_age_tier')
