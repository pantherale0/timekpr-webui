"""add installed apps inventory tables

Revision ID: c4d9e2a1b7f3
Revises: b3e8a1f04c2d
Create Date: 2026-06-05 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'c4d9e2a1b7f3'
down_revision = 'b3e8a1f04c2d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.add_column(sa.Column('installed_apps_report_hash', sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column('installed_apps_last_reported', sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column('installed_apps_count', sa.Integer(), nullable=True))

    with op.batch_alter_table('apparmor_rule', schema=None) as batch_op:
        batch_op.alter_column(
            'executable_path',
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=False,
        )

    with op.batch_alter_table('app_policy_rule', schema=None) as batch_op:
        batch_op.alter_column(
            'executable_path',
            existing_type=sa.String(length=255),
            type_=sa.String(length=512),
            existing_nullable=False,
        )

    op.create_table(
        'application_icon',
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('mime_type', sa.String(length=64), nullable=False),
        sa.Column('data', sa.LargeBinary(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('content_hash'),
    )

    op.create_table(
        'device_installed_application',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('system_id', sa.String(length=50), nullable=False),
        sa.Column('linux_username', sa.String(length=50), nullable=False),
        sa.Column('application_name', sa.String(length=120), nullable=False),
        sa.Column('identifier', sa.String(length=512), nullable=False),
        sa.Column('match_type', sa.String(length=32), nullable=False),
        sa.Column('platform', sa.String(length=20), nullable=False),
        sa.Column('version_name', sa.String(length=120), nullable=True),
        sa.Column('icon_hash', sa.String(length=64), nullable=True),
        sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('is_present', sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(['system_id'], ['agent_device.system_id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'system_id',
            'linux_username',
            'identifier',
            'match_type',
            name='device_installed_app_uc',
        ),
    )


def downgrade():
    op.drop_table('device_installed_application')
    op.drop_table('application_icon')

    with op.batch_alter_table('app_policy_rule', schema=None) as batch_op:
        batch_op.alter_column(
            'executable_path',
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    with op.batch_alter_table('apparmor_rule', schema=None) as batch_op:
        batch_op.alter_column(
            'executable_path',
            existing_type=sa.String(length=512),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    with op.batch_alter_table('agent_device', schema=None) as batch_op:
        batch_op.drop_column('installed_apps_count')
        batch_op.drop_column('installed_apps_last_reported')
        batch_op.drop_column('installed_apps_report_hash')
