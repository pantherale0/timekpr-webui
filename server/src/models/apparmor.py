from datetime import datetime, timezone
from src.models.core import db


class AppArmorRule(db.Model):
    __tablename__ = 'apparmor_rule'

    PRESET_ALLOWED = 'allowed'
    PRESET_NO_INTERNET = 'no_internet'
    PRESET_BLOCKED = 'blocked'
    PRESET_COMPLAIN = 'complain'
    MATCH_TYPE_EXECUTABLE = 'executable'
    MATCH_TYPE_PATH_PATTERN = 'path_pattern'
    MATCH_TYPE_PACKAGE = 'package'

    VALID_PRESETS = {PRESET_ALLOWED, PRESET_NO_INTERNET, PRESET_BLOCKED, PRESET_COMPLAIN}
    VALID_MATCH_TYPES = {MATCH_TYPE_EXECUTABLE, MATCH_TYPE_PATH_PATTERN, MATCH_TYPE_PACKAGE}

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    application_name = db.Column(db.String(120), nullable=False)
    executable_path = db.Column(db.String(512), nullable=False)
    match_type = db.Column(
        db.String(32),
        nullable=False,
        default=MATCH_TYPE_EXECUTABLE,
    )
    preset = db.Column(db.String(32), nullable=False, default=PRESET_ALLOWED)
    is_custom = db.Column(db.Boolean, default=False, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('apparmor_rules', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.UniqueConstraint(
            'device_map_id',
            'executable_path',
            name='apparmor_device_map_exec_uc',
        ),
    )

    def __repr__(self):
        return f'<AppArmorRule {self.application_name} [{self.match_type}:{self.preset}]>'

    @property
    def is_restrictive(self):
        return self.preset in {self.PRESET_NO_INTERNET, self.PRESET_BLOCKED, self.PRESET_COMPLAIN}

    @property
    def supports_network_controls(self):
        return self.match_type == self.MATCH_TYPE_EXECUTABLE

    @property
    def display_target(self):
        return self.executable_path

    def to_sync_dict(self):
        return {
            'application_name': self.application_name,
            'executable_path': self.executable_path,
            'match_type': self.match_type,
            'preset': self.preset,
        }


class AppUsageHistory(db.Model):
    __tablename__ = 'app_usage_history'

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    application_name = db.Column(db.String(120), nullable=False)
    executable_path = db.Column(db.String(255), nullable=False)
    start_time = db.Column(db.DateTime(timezone=True), nullable=False)
    end_time = db.Column(db.DateTime(timezone=True), nullable=False)
    duration_seconds = db.Column(db.Integer, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('app_usage_records', cascade='all, delete-orphan'),
    )

    def __repr__(self):
        return (
            f'<AppUsageHistory {self.application_name} '
            f'{self.duration_seconds}s>'
        )


class ApplicationIcon(db.Model):
    __tablename__ = 'application_icon'

    content_hash = db.Column(db.String(64), primary_key=True)
    mime_type = db.Column(db.String(64), nullable=False, default='image/png')
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def __repr__(self):
        return f'<ApplicationIcon {self.content_hash[:12]}...>'


class DeviceInstalledApplication(db.Model):
    __tablename__ = 'device_installed_application'

    MATCH_TYPE_EXECUTABLE = 'executable'
    MATCH_TYPE_PACKAGE = 'package'
    VALID_MATCH_TYPES = {MATCH_TYPE_EXECUTABLE, MATCH_TYPE_PACKAGE}

    PLATFORM_LINUX = 'linux'
    PLATFORM_ANDROID = 'android'
    PLATFORM_WINDOWS = 'windows'
    VALID_PLATFORMS = {PLATFORM_LINUX, PLATFORM_ANDROID, PLATFORM_WINDOWS}

    id = db.Column(db.Integer, primary_key=True)
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    linux_username = db.Column(db.String(50), nullable=False)
    application_name = db.Column(db.String(120), nullable=False)
    identifier = db.Column(db.String(512), nullable=False)
    match_type = db.Column(db.String(32), nullable=False, default=MATCH_TYPE_EXECUTABLE)
    platform = db.Column(db.String(20), nullable=False)
    version_name = db.Column(db.String(120), nullable=True)
    icon_hash = db.Column(db.String(64), nullable=True)
    first_seen_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    last_seen_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    is_present = db.Column(db.Boolean, default=True, nullable=False)

    __table_args__ = (
        db.UniqueConstraint(
            'system_id',
            'linux_username',
            'identifier',
            'match_type',
            name='device_installed_app_uc',
        ),
    )

    def __repr__(self):
        return f'<DeviceInstalledApplication {self.application_name} [{self.match_type}]>'

    def to_dict(self):
        return {
            'id': self.id,
            'system_id': self.system_id,
            'linux_username': self.linux_username,
            'application_name': self.application_name,
            'identifier': self.identifier,
            'match_type': self.match_type,
            'platform': self.platform,
            'version_name': self.version_name,
            'icon_hash': self.icon_hash,
            'first_seen_at': self.first_seen_at.isoformat() if self.first_seen_at else None,
            'last_seen_at': self.last_seen_at.isoformat() if self.last_seen_at else None,
            'is_present': self.is_present,
        }

    def to_policy_fields(self):
        return {
            'application_name': self.application_name,
            'executable_path': self.identifier,
            'match_type': self.match_type,
        }
