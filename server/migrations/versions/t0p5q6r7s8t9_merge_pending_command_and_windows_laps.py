"""merge pending_command and windows LAPS branches

Revision ID: t0p5q6r7s8t9
Revises: r9m4n5o6p7q8, s9n5o6p7q8r9
Create Date: 2026-06-20 23:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 't0p5q6r7s8t9'
down_revision = ('r9m4n5o6p7q8', 's9n5o6p7q8r9')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
