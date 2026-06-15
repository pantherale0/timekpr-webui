"""Add youtube_history table

Revision ID: e6bd8cf2e66d
Revises: k2f7g8h9i0j1
Create Date: 2026-06-15 12:41:04.762341

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6bd8cf2e66d'
down_revision = 'k2f7g8h9i0j1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'youtube_history',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('device_id', sa.String(length=50), nullable=False),
        sa.Column('managed_user_id', sa.Integer(), nullable=False),
        sa.Column('video_id', sa.String(length=20), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column('channel_name', sa.String(length=255), nullable=True),
        sa.Column('channel_id', sa.String(length=100), nullable=True),
        sa.Column('category', sa.String(length=100), nullable=False, server_default='Unknown'),
        sa.Column('duration_seconds', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('watched_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.ForeignKeyConstraint(['device_id'], ['agent_device.system_id'], name='fk_youtube_history_device_id'),
        sa.ForeignKeyConstraint(['managed_user_id'], ['managed_user.id'], name='fk_youtube_history_managed_user_id'),
        sa.PrimaryKeyConstraint('id', name='pk_youtube_history')
    )
    op.create_index('youtube_history_user_watched_idx', 'youtube_history', ['managed_user_id', 'watched_at'], unique=False)


def downgrade():
    op.drop_index('youtube_history_user_watched_idx', table_name='youtube_history')
    op.drop_table('youtube_history')
