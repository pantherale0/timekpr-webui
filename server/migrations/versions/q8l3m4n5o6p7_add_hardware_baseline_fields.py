"""Add hardware baseline compliance fields to agent_device

Revision ID: q8l3m4n5o6p7
Revises: p7k2l3m4n5o6
Create Date: 2026-06-20 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = 'q8l3m4n5o6p7'
down_revision = 'p7k2l3m4n5o6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('agent_device', sa.Column('hardware_oem', sa.String(length=32), nullable=True))
    op.add_column('agent_device', sa.Column('hardware_oem_model', sa.String(length=128), nullable=True))
    op.add_column('agent_device', sa.Column('hardware_compliance_status', sa.String(length=32), nullable=True))
    op.add_column('agent_device', sa.Column('hardware_compliance_json', sa.Text(), nullable=True))
    op.add_column(
        'agent_device',
        sa.Column('hardware_compliance_checked_at', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column('agent_device', sa.Column('bios_supervisor_password_escrow', sa.Text(), nullable=True))


def downgrade():
    op.drop_column('agent_device', 'bios_supervisor_password_escrow')
    op.drop_column('agent_device', 'hardware_compliance_checked_at')
    op.drop_column('agent_device', 'hardware_compliance_json')
    op.drop_column('agent_device', 'hardware_compliance_status')
    op.drop_column('agent_device', 'hardware_oem_model')
    op.drop_column('agent_device', 'hardware_oem')
