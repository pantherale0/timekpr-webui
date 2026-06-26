from datetime import datetime, timezone
from src.models.core import db


class VideoHistory(db.Model):
    __tablename__ = 'video_history'

    VIDEO_PLATFORM_YOUTUBE = 'youtube'
    VIDEO_PLATFORM_TIKTOK = 'tiktok'
    SUPPORTED_PLATFORMS = frozenset({VIDEO_PLATFORM_YOUTUBE, VIDEO_PLATFORM_TIKTOK})

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    platform = db.Column(db.String(20), nullable=False, default=VIDEO_PLATFORM_YOUTUBE)
    video_id = db.Column(db.String(25), nullable=False)
    title = db.Column(db.String(255), nullable=False)
    channel_name = db.Column(db.String(255), nullable=True)
    channel_id = db.Column(db.String(100), nullable=True)
    category = db.Column(db.String(100), nullable=False, default='Unknown')
    duration_seconds = db.Column(db.Integer, nullable=False, default=0)
    watched_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device = db.relationship(
        'AgentDevice',
        backref=db.backref('video_history', cascade='all, delete-orphan'),
    )
    managed_user = db.relationship(
        'ManagedUser',
        backref=db.backref('video_history', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.Index('video_history_user_watched_idx', 'managed_user_id', 'watched_at'),
        db.Index('video_history_platform_idx', 'platform', 'managed_user_id', 'watched_at'),
    )

    def __repr__(self):
        return (
            f'<VideoHistory {self.managed_user_id} watched {self.platform}:'
            f'{self.video_id} at {self.watched_at}>'
        )

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'managed_user_id': self.managed_user_id,
            'platform': self.platform,
            'video_id': self.video_id,
            'title': self.title,
            'channel_name': self.channel_name,
            'channel_id': self.channel_id,
            'category': self.category,
            'duration_seconds': self.duration_seconds,
            'watched_at': self.watched_at.isoformat() if self.watched_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


# Backward compatibility alias for existing imports.
YoutubeHistory = VideoHistory


class WebHistory(db.Model):
    __tablename__ = 'web_history'

    id = db.Column(db.Integer, primary_key=True)
    device_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    url = db.Column(db.Text, nullable=False)
    title = db.Column(db.String(255), nullable=True)
    domain = db.Column(db.String(255), nullable=False)
    visited_at = db.Column(db.DateTime(timezone=True), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device = db.relationship(
        'AgentDevice',
        backref=db.backref('web_history', cascade='all, delete-orphan'),
    )
    managed_user = db.relationship(
        'ManagedUser',
        backref=db.backref('web_history', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.Index('web_history_user_visited_idx', 'managed_user_id', 'visited_at'),
    )

    def __repr__(self):
        return f'<WebHistory {self.managed_user_id} visited {self.domain} at {self.visited_at}>'

    def to_dict(self):
        return {
            'id': self.id,
            'device_id': self.device_id,
            'managed_user_id': self.managed_user_id,
            'url': self.url,
            'title': self.title,
            'domain': self.domain,
            'visited_at': self.visited_at.isoformat() if self.visited_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class UserOnlineAccount(db.Model):
    __tablename__ = 'user_online_account'

    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    domain = db.Column(db.String(255), nullable=False)
    username = db.Column(db.String(255), nullable=False)
    first_seen_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_seen_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    managed_user = db.relationship('ManagedUser', backref=db.backref('online_accounts', cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('managed_user_id', 'domain', 'username', name='user_online_account_uc'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'managed_user_id': self.managed_user_id,
            'domain': self.domain,
            'username': self.username,
            'first_seen_at': self.first_seen_at.isoformat() if self.first_seen_at else None,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
        }


class ChannelBlockRule(db.Model):
    __tablename__ = 'channel_block_rule'

    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    platform = db.Column(db.String(32), nullable=False)  # 'youtube' | 'tiktok'
    channel_id = db.Column(db.String(100), nullable=True)
    channel_name = db.Column(db.String(255), nullable=False)
    is_blocked = db.Column(db.Boolean, default=True, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    managed_user = db.relationship('ManagedUser', backref=db.backref('channel_block_rules', cascade='all, delete-orphan'))

    __table_args__ = (
        db.UniqueConstraint('managed_user_id', 'platform', 'channel_name', name='user_channel_block_uc'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'managed_user_id': self.managed_user_id,
            'platform': self.platform,
            'channel_id': self.channel_id,
            'channel_name': self.channel_name,
            'is_blocked': self.is_blocked,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


class AiPromptLog(db.Model):
    __tablename__ = 'ai_prompt_log'

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    service = db.Column(db.String(64), nullable=False)
    domain = db.Column(db.String(128), nullable=False)
    prompt_text = db.Column(db.Text, nullable=True)
    prompt_length = db.Column(db.Integer, nullable=False)
    url = db.Column(db.String(1024), nullable=False)
    title = db.Column(db.String(256), nullable=False)
    status = db.Column(db.String(32), nullable=False)
    logged_at = db.Column(db.DateTime(timezone=True), nullable=False)

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('ai_prompt_logs', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.Index('ai_prompt_log_map_idx', 'device_map_id', 'logged_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'device_map_id': self.device_map_id,
            'service': self.service,
            'domain': self.domain,
            'prompt_text': self.prompt_text,
            'prompt_length': self.prompt_length,
            'url': self.url,
            'title': self.title,
            'status': self.status,
            'logged_at': self.logged_at.isoformat() if self.logged_at else None,
        }


class AiSessionLog(db.Model):
    __tablename__ = 'ai_session_log'

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    domain = db.Column(db.String(128), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    logged_at = db.Column(db.DateTime(timezone=True), nullable=False)

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('ai_session_logs', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.Index('ai_session_log_map_idx', 'device_map_id', 'logged_at'),
    )

    def to_dict(self):
        return {
            'id': self.id,
            'device_map_id': self.device_map_id,
            'domain': self.domain,
            'duration_seconds': self.duration_seconds,
            'logged_at': self.logged_at.isoformat() if self.logged_at else None,
        }
