"""add approvals system tables

Revision ID: e6f1a2b3c4d5
Revises: d5e8f3b2a1c4
Create Date: 2026-06-05 18:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'e6f1a2b3c4d5'
down_revision = 'd5e8f3b2a1c4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'mapping_approval_settings',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('app_launch_mode', sa.String(length=32), nullable=False, server_default='open'),
        sa.Column('domain_access_mode', sa.String(length=32), nullable=False, server_default='blocklist_only'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['device_map_id'], ['managed_user_device_map.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('device_map_id'),
    )

    op.create_table(
        'approval_request',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('request_type', sa.String(length=32), nullable=False),
        sa.Column('target_kind', sa.String(length=32), nullable=False),
        sa.Column('target_value', sa.String(length=512), nullable=False),
        sa.Column('display_label', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='pending'),
        sa.Column('requested_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('decided_by', sa.String(length=80), nullable=True),
        sa.Column('denial_reason', sa.Text(), nullable=True),
        sa.Column('source_alert_id', sa.Integer(), nullable=True),
        sa.Column('details_json', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['device_map_id'], ['managed_user_device_map.id']),
        sa.ForeignKeyConstraint(['source_alert_id'], ['agent_alert.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'approval_request_status_requested_idx',
        'approval_request',
        ['status', 'requested_at'],
        unique=False,
    )
    op.create_index(
        'approval_request_map_status_idx',
        'approval_request',
        ['device_map_id', 'status'],
        unique=False,
    )

    op.create_table(
        'policy_approval_grant',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_map_id', sa.Integer(), nullable=False),
        sa.Column('grant_type', sa.String(length=32), nullable=False),
        sa.Column('target_kind', sa.String(length=32), nullable=False),
        sa.Column('target_value', sa.String(length=512), nullable=False),
        sa.Column('display_label', sa.String(length=120), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False, server_default='active'),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_by', sa.String(length=80), nullable=True),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('revoked_by', sa.String(length=80), nullable=True),
        sa.Column('source_request_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['device_map_id'], ['managed_user_device_map.id']),
        sa.ForeignKeyConstraint(['source_request_id'], ['approval_request.id']),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'policy_approval_grant_map_status_idx',
        'policy_approval_grant',
        ['device_map_id', 'status'],
        unique=False,
    )


def downgrade():
    op.drop_index('policy_approval_grant_map_status_idx', table_name='policy_approval_grant')
    op.drop_table('policy_approval_grant')
    op.drop_index('approval_request_map_status_idx', table_name='approval_request')
    op.drop_index('approval_request_status_requested_idx', table_name='approval_request')
    op.drop_table('approval_request')
    op.drop_table('mapping_approval_settings')
