import json
from datetime import date, datetime, timedelta, timezone
from src.models.core import db

def coerce_time_spent_day(value):
    """Coerce TIME_SPENT_DAY (timekpra output) to an integer for the time_spent column."""
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return coerce_time_spent_day(value[0]) if value else 0
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0


def coerce_time_left_day(value):
    """Coerce TIME_LEFT_DAY to an integer, or None if absent."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, (list, tuple)):
        return coerce_time_left_day(value[0]) if value else None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


USAGE_SNAPSHOT_DATE_KEY = 'USAGE_SNAPSHOT_DATE'


def utc_today() -> date:
    """Return the current calendar day in UTC."""
    return datetime.now(timezone.utc).date()


def utc_date_of(value) -> date | None:
    """Normalize a datetime to its UTC calendar date."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).date()


def mapping_usage_snapshot_date(mapping) -> date | None:
    """Return the UTC day stamped on a mapping usage snapshot, if present."""
    if mapping is None:
        return None
    raw = mapping.get_config_value(USAGE_SNAPSHOT_DATE_KEY)
    if not raw:
        return None
    try:
        return datetime.strptime(str(raw)[:10], '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None


def mapping_usage_is_for_day(mapping, day=None) -> bool:
    """Return True when mapping.last_config reflects usage for the requested UTC day."""
    if mapping is None:
        return False

    day = day or utc_today()
    snapshot_day = mapping_usage_snapshot_date(mapping)
    if snapshot_day is not None:
        return snapshot_day == day

    # Legacy snapshots without USAGE_SNAPSHOT_DATE rely on last agent contact day.
    return utc_date_of(getattr(mapping, 'last_checked', None)) == day


def stamp_usage_snapshot(config, day=None) -> dict:
    """Attach a UTC usage snapshot date to an agent/cloud config payload."""
    day = day or utc_today()
    stamped = dict(config) if isinstance(config, dict) else {}
    stamped[USAGE_SNAPSHOT_DATE_KEY] = day.isoformat()
    return stamped


def ensure_offline_mapping_day_snapshot(mapping, day=None, effective_limit_seconds=None) -> bool:
    """Reset stale offline mapping usage when the UTC day advances.

    Returns True when the mapping config was rolled forward to a new day.
    """
    if mapping is None:
        return False

    day = day or utc_today()
    if mapping_usage_is_for_day(mapping, day):
        if mapping_usage_snapshot_date(mapping) is None and mapping.last_config:
            try:
                config = json.loads(mapping.last_config)
            except (TypeError, ValueError, json.JSONDecodeError):
                config = None
            if isinstance(config, dict):
                mapping.last_config = json.dumps(stamp_usage_snapshot(config, day))
        return False

    try:
        config = json.loads(mapping.last_config) if mapping.last_config else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        config = {}
    if not isinstance(config, dict):
        config = {}

    config['TIME_SPENT_DAY'] = 0
    if effective_limit_seconds is not None:
        config['TIME_LEFT_DAY'] = effective_limit_seconds
    mapping.last_config = json.dumps(stamp_usage_snapshot(config, day))
    return True


def get_mapping_time_spent_for_day(mapping, day=None):
    """Return a mapping's last known TIME_SPENT_DAY only for the requested day."""
    if mapping is None:
        return 0

    day = day or utc_today()
    if not mapping_usage_is_for_day(mapping, day):
        return 0

    return coerce_time_spent_day(mapping.get_config_value('TIME_SPENT_DAY'))


def get_mapping_time_left_for_day(mapping, day=None):
    """Return a mapping's last known TIME_LEFT_DAY only for the requested day."""
    if mapping is None:
        return None

    day = day or utc_today()
    if not mapping_usage_is_for_day(mapping, day):
        return None

    return coerce_time_left_day(mapping.get_config_value('TIME_LEFT_DAY'))


class AgentDevice(db.Model):
    __tablename__ = 'agent_device'
    system_id = db.Column(db.String(50), primary_key=True)  # Unique Host UUID
    system_hostname = db.Column(db.String(255), nullable=True)  # Hostname used for human-readable labels
    system_ip = db.Column(db.String(50), nullable=True)     # Snapshotted connection IP
    status = db.Column(db.String(20), default='pending')    # pending, approved, rejected
    secure_token = db.Column(db.String(64), nullable=True)  # Dynamically generated token
    date_added = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_seen = db.Column(db.DateTime(timezone=True), nullable=True)
    linux_users_json = db.Column(db.Text(), nullable=True)  # JSON list of standard system users
    platform = db.Column(db.String(20), nullable=True)  # linux, android
    fcm_token = db.Column(db.String(512), nullable=True)
    fcm_token_updated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    installed_apps_report_hash = db.Column(db.String(64), nullable=True)
    installed_apps_last_reported = db.Column(db.DateTime(timezone=True), nullable=True)
    installed_apps_count = db.Column(db.Integer, nullable=True)
    pending_factory_reset = db.Column(db.Boolean, default=False, nullable=False)
    unenrolled_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_device_owner = db.Column(db.Boolean, default=False, nullable=False)
    hardware_oem = db.Column(db.String(32), nullable=True)
    hardware_oem_model = db.Column(db.String(128), nullable=True)
    hardware_compliance_status = db.Column(db.String(32), nullable=True)
    hardware_compliance_json = db.Column(db.Text(), nullable=True)
    hardware_compliance_checked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    bios_supervisor_password_escrow = db.Column(db.Text(), nullable=True)
    windows_local_admin_password_escrow = db.Column(db.Text(), nullable=True)
    windows_local_admin_rotated_at = db.Column(db.DateTime(timezone=True), nullable=True)
    windows_local_admin_rotation_id = db.Column(db.String(64), nullable=True)
    household_id = db.Column(db.Integer, db.ForeignKey('household.id', ondelete='SET NULL'), nullable=True)

    # Relationship to per-user Linux account mappings on this device
    household = db.relationship('Household', back_populates='devices')
    user_mappings = db.relationship(
        'ManagedUserDeviceMap',
        backref='device',
        lazy=True,
        cascade="all, delete-orphan",
    )
    alerts = db.relationship(
        'AgentAlert',
        backref='device',
        lazy=True,
        cascade="all, delete-orphan",
    )
    installed_applications = db.relationship(
        'DeviceInstalledApplication',
        backref='device',
        lazy=True,
        cascade="all, delete-orphan",
    )

    @property
    def has_managed_profiles(self):
        """Check if this device has at least one user mapping with a managed profile type."""
        return any(
            m.android_profile_type in ('restricted', 'standard')
            for m in self.user_mappings
        )

    @property
    def linux_users(self):
        """Parse stored JSON user list into a list of dictionaries."""
        if not self.linux_users_json:
            return []
        try:
            return json.loads(self.linux_users_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    @property
    def display_name(self):
        hostname = (self.system_hostname or '').strip()
        return hostname or self.system_id

    @property
    def system_id_suffix(self):
        system_id = (self.system_id or '').strip()
        return system_id[-2:] if len(system_id) >= 2 else system_id

    def format_display_name(self, include_suffix=False):
        if include_suffix and (self.system_hostname or '').strip():
            return f'{self.display_name} ({self.system_id_suffix})'
        return self.display_name

    @property
    def nintendo_console_stats(self):
        """Return cached Nintendo cloud console stats for this device."""
        if (self.platform or '').strip().lower() != 'nintendo':
            return {}
        from src.common.nintendo_sync import get_nintendo_console_stats
        return get_nintendo_console_stats(self.system_id)

    @property
    def xbox_console_stats(self):
        """Return cached Xbox cloud console stats for this device."""
        if (self.platform or '').strip().lower() != 'xbox':
            return {}
        from src.common.xbox_sync import get_xbox_console_stats
        return get_xbox_console_stats(self.system_id)

    def __repr__(self):
        return f'<AgentDevice {self.system_id} [{self.status}]>'


class AgentAlert(db.Model):
    __tablename__ = 'agent_alert'

    DELIVERY_PENDING = 'pending'
    DELIVERY_RETRYING = 'retrying'
    DELIVERY_DELIVERED = 'delivered'
    DELIVERY_DISABLED = 'disabled'

    id = db.Column(db.Integer, primary_key=True)
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    event_type = db.Column(db.String(64), nullable=False)
    linux_username = db.Column(db.String(80), nullable=True)
    occurred_at = db.Column(db.DateTime(timezone=True), nullable=False)
    payload_json = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    webhook_enabled_snapshot = db.Column(db.Boolean, default=False, nullable=False)
    delivery_status = db.Column(db.String(20), default=DELIVERY_PENDING, nullable=False)
    delivery_attempts = db.Column(db.Integer, default=0, nullable=False)
    last_delivery_attempt_at = db.Column(db.DateTime(timezone=True), nullable=True)
    delivered_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_delivery_error = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<AgentAlert {self.event_type} on {self.system_id}>'

    @property
    def payload(self):
        try:
            return json.loads(self.payload_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}

    @property
    def should_attempt_delivery(self):
        return (
            self.webhook_enabled_snapshot and
            self.delivery_status in {self.DELIVERY_PENDING, self.DELIVERY_RETRYING}
        )

    def mark_delivery_attempt(self):
        self.delivery_attempts += 1
        self.last_delivery_attempt_at = datetime.now(timezone.utc)

    def mark_delivered(self):
        self.delivery_status = self.DELIVERY_DELIVERED
        self.delivered_at = datetime.now(timezone.utc)
        self.last_delivery_error = None

    def mark_retry(self, error_message):
        self.delivery_status = self.DELIVERY_RETRYING
        self.last_delivery_error = error_message
        self.delivered_at = None

    def mark_delivery_disabled(self):
        self.delivery_status = self.DELIVERY_DISABLED
        self.delivered_at = None
        self.last_delivery_error = None


class PendingCommand(db.Model):
    __tablename__ = 'pending_command'

    KIND_IMPERATIVE = 'imperative'
    KIND_POLICY_SNAPSHOT = 'policy_snapshot'
    KIND_DOMAIN_RECONCILE = 'domain_reconcile'

    STATUS_PENDING = 'pending'
    STATUS_IN_FLIGHT = 'in_flight'
    STATUS_COMPLETED = 'completed'
    STATUS_FAILED = 'failed'
    STATUS_EXPIRED = 'expired'
    STATUS_SUPERSEDED = 'superseded'

    id = db.Column(db.Integer, primary_key=True)
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    action = db.Column(db.String(64), nullable=False)
    username = db.Column(db.String(80), nullable=True)
    command_kind = db.Column(db.String(32), nullable=False)
    args_json = db.Column(db.Text, nullable=True)
    coalesce_key = db.Column(db.String(128), nullable=True)
    status = db.Column(db.String(20), default=STATUS_PENDING, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    attempt_count = db.Column(db.Integer, default=0, nullable=False)
    last_error = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)

    device = db.relationship('AgentDevice', backref=db.backref('pending_commands', lazy=True))

    def __repr__(self):
        return f'<PendingCommand {self.action} on {self.system_id} [{self.status}]>'

    @property
    def args(self):
        if not self.args_json:
            return {}
        try:
            return json.loads(self.args_json)
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}


class DeviceScreenshotSettings(db.Model):
    __tablename__ = 'device_screenshot_settings'

    DEFAULT_INTERVAL_SECONDS = 300
    DEFAULT_RETENTION_HOURS = 168
    MIN_INTERVAL_SECONDS = 60
    MAX_INTERVAL_SECONDS = 3600
    MIN_RETENTION_HOURS = 24
    MAX_RETENTION_HOURS = 720

    system_id = db.Column(
        db.String(50),
        db.ForeignKey('agent_device.system_id'),
        primary_key=True,
    )
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    interval_seconds = db.Column(db.Integer, nullable=False, default=DEFAULT_INTERVAL_SECONDS)
    retention_hours = db.Column(db.Integer, nullable=False, default=DEFAULT_RETENTION_HOURS)
    revision = db.Column(db.String(64), nullable=False, default='')
    is_synced = db.Column(db.Boolean, nullable=False, default=False)
    last_synced_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_sync_error = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device = db.relationship(
        'AgentDevice',
        backref=db.backref('screenshot_settings', uselist=False, cascade='all, delete-orphan'),
    )

    def __repr__(self):
        return f'<DeviceScreenshotSettings {self.system_id} enabled={self.enabled}>'


class DeviceScreenshot(db.Model):
    __tablename__ = 'device_screenshot'

    id = db.Column(db.Integer, primary_key=True)
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    screenshot_id = db.Column(db.String(64), nullable=False)
    linux_username = db.Column(db.String(80), nullable=True)
    captured_at = db.Column(db.DateTime(timezone=True), nullable=False)
    mime_type = db.Column(db.String(64), nullable=False, default='image/jpeg')
    width = db.Column(db.Integer, nullable=True)
    height = db.Column(db.Integer, nullable=True)
    content_hash = db.Column(db.String(64), nullable=False)
    active_window_title = db.Column(db.String(255), nullable=True)
    data = db.Column(db.LargeBinary, nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    device = db.relationship(
        'AgentDevice',
        backref=db.backref('screenshots', cascade='all, delete-orphan'),
    )

    __table_args__ = (
        db.UniqueConstraint('system_id', 'screenshot_id', name='device_screenshot_uc'),
        db.Index('device_screenshot_system_captured_idx', 'system_id', 'captured_at'),
    )

    def __repr__(self):
        return f'<DeviceScreenshot {self.system_id} {self.screenshot_id}>'

    def to_summary_dict(self):
        return {
            'id': self.id,
            'screenshot_id': self.screenshot_id,
            'system_id': self.system_id,
            'linux_username': self.linux_username,
            'captured_at': self.captured_at.isoformat() if self.captured_at else None,
            'mime_type': self.mime_type,
            'width': self.width,
            'height': self.height,
            'content_hash': self.content_hash,
            'active_window_title': self.active_window_title,
        }

