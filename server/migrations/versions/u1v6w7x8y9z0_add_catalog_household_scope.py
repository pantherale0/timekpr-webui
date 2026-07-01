"""add household scope to blocklist and app policy catalogs

Revision ID: u1v6w7x8y9z0
Revises: 68617a488776
Create Date: 2026-07-01 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'u1v6w7x8y9z0'
down_revision = '68617a488776'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('blocklist_source', schema=None) as batch_op:
        batch_op.add_column(sa.Column('household_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_blocklist_source_household',
            'household',
            ['household_id'],
            ['id'],
            ondelete='SET NULL',
        )

    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.add_column(sa.Column('household_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_app_policy_household',
            'household',
            ['household_id'],
            ['id'],
            ondelete='SET NULL',
        )

    conn = op.get_bind()
    first_household = conn.execute(sa.text('SELECT id FROM household ORDER BY id LIMIT 1')).scalar()
    if first_household is not None:
        conn.execute(
            sa.text('UPDATE blocklist_source SET household_id = :hh_id WHERE household_id IS NULL'),
            {'hh_id': first_household},
        )
        conn.execute(
            sa.text('UPDATE app_policy SET household_id = :hh_id WHERE household_id IS NULL'),
            {'hh_id': first_household},
        )


def downgrade():
    with op.batch_alter_table('app_policy', schema=None) as batch_op:
        batch_op.drop_constraint('fk_app_policy_household', type_='foreignkey')
        batch_op.drop_column('household_id')

    with op.batch_alter_table('blocklist_source', schema=None) as batch_op:
        batch_op.drop_constraint('fk_blocklist_source_household', type_='foreignkey')
        batch_op.drop_column('household_id')
