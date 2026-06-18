"""add policy preset fields to managed user

Revision ID: o6j1k2l3m4n5
Revises: n5i0j1k2l3m4
Create Date: 2026-06-18 16:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect

revision = 'o6j1k2l3m4n5'
down_revision = 'n5i0j1k2l3m4'
branch_labels = None
depends_on = None


def _column_exists(table, column):
    bind = op.get_bind()
    inspector = sqla_inspect(bind)
    columns = [c['name'] for c in inspector.get_columns(table)]
    return column in columns


def upgrade():
    if not _column_exists('managed_user', 'policy_age_bracket'):
        op.add_column('managed_user', sa.Column('policy_age_bracket', sa.String(16), nullable=True))
    if not _column_exists('managed_user', 'policy_maturity_level'):
        op.add_column('managed_user', sa.Column('policy_maturity_level', sa.String(16), nullable=True))


def downgrade():
    bind = op.get_bind()
    dialect = bind.dialect.name
    if _column_exists('managed_user', 'policy_maturity_level'):
        if dialect != 'sqlite':
            op.drop_column('managed_user', 'policy_maturity_level')
    if _column_exists('managed_user', 'policy_age_bracket'):
        if dialect != 'sqlite':
            op.drop_column('managed_user', 'policy_age_bracket')
