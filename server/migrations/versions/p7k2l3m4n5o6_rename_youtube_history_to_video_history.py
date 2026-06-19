"""Rename youtube_history to video_history and add platform column

Revision ID: p7k2l3m4n5o6
Revises: o6j1k2l3m4n5
Create Date: 2026-06-19 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect as sqla_inspect

revision = 'p7k2l3m4n5o6'
down_revision = 'o6j1k2l3m4n5'
branch_labels = None
depends_on = None


def _table_exists(table):
    bind = op.get_bind()
    inspector = sqla_inspect(bind)
    return table in inspector.get_table_names()


def upgrade():
    if not _table_exists('youtube_history'):
        return

    with op.batch_alter_table('youtube_history') as batch_op:
        batch_op.add_column(
            sa.Column('platform', sa.String(length=20), nullable=False, server_default='youtube')
        )
        batch_op.alter_column('video_id', existing_type=sa.String(length=20), type_=sa.String(length=25))

    op.rename_table('youtube_history', 'video_history')

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT pk_youtube_history TO pk_video_history"
        )
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT fk_youtube_history_device_id "
            "TO fk_video_history_device_id"
        )
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT fk_youtube_history_managed_user_id "
            "TO fk_video_history_managed_user_id"
        )

    op.drop_index('youtube_history_user_watched_idx', table_name='video_history')
    op.create_index(
        'video_history_user_watched_idx',
        'video_history',
        ['managed_user_id', 'watched_at'],
        unique=False,
    )
    op.create_index(
        'video_history_platform_idx',
        'video_history',
        ['platform', 'managed_user_id', 'watched_at'],
        unique=False,
    )


def downgrade():
    if not _table_exists('video_history'):
        return

    op.drop_index('video_history_platform_idx', table_name='video_history')
    op.drop_index('video_history_user_watched_idx', table_name='video_history')
    op.create_index(
        'youtube_history_user_watched_idx',
        'video_history',
        ['managed_user_id', 'watched_at'],
        unique=False,
    )

    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT pk_video_history TO pk_youtube_history"
        )
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT fk_video_history_device_id "
            "TO fk_youtube_history_device_id"
        )
        op.execute(
            "ALTER TABLE video_history RENAME CONSTRAINT fk_video_history_managed_user_id "
            "TO fk_youtube_history_managed_user_id"
        )

    with op.batch_alter_table('video_history') as batch_op:
        batch_op.alter_column('video_id', existing_type=sa.String(length=25), type_=sa.String(length=20))
        batch_op.drop_column('platform')

    op.rename_table('video_history', 'youtube_history')
