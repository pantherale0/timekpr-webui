from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import pytz
import json

import sqlite3
from sqlalchemy import event
from sqlalchemy.engine import Engine
import bcrypt
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


@event.listens_for(Engine, "connect")
def set_sqlite_pragma(dbapi_connection, connection_record):
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=60000")
        cursor.close()


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


class Settings(db.Model):
    __tablename__ = 'settings'
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(50), unique=True, nullable=False)
    value = db.Column(db.Text, nullable=False)
    
    @classmethod
    def get_value(cls, key, default=None):
        """Get a setting value by key"""
        setting = cls.query.filter_by(key=key).first()
        return setting.value if setting else default
    
    @classmethod
    def set_value(cls, key, value):
        """Set a setting value by key"""
        setting = cls.query.filter_by(key=key).first()
        if setting:
            setting.value = value
        else:
            setting = cls()
            setting.key = key
            setting.value = value
            db.session.add(setting)
        db.session.commit()
        return setting
    
    @classmethod
    def hash_password(cls, password):
        """Hash a password using bcrypt"""
        salt = bcrypt.gensalt()
        hashed = bcrypt.hashpw(password.encode('utf-8'), salt)
        return hashed.decode('utf-8')
    
    @classmethod
    def check_password(cls, password, hashed_password):
        """Check if password matches the stored hash"""
        return bcrypt.checkpw(password.encode('utf-8'), hashed_password.encode('utf-8'))
    
    @classmethod
    def set_admin_password(cls, password):
        """Set admin password with hashing"""
        hashed = cls.hash_password(password)
        cls.set_value('admin_password_hash', hashed)
        # Remove old plain text password if it exists
        old_password = cls.query.filter_by(key='admin_password').first()
        if old_password:
            db.session.delete(old_password)
            db.session.commit()
    
    @classmethod
    def check_admin_password(cls, password):
        """Check admin password against stored hash"""
        hashed_password = cls.get_value('admin_password_hash')
        if not hashed_password:
            # Check if we have old plain text password for migration
            old_password = cls.get_value('admin_password')
            if old_password:
                # Migrate old password to hashed format
                cls.set_admin_password(old_password)
                return password == old_password
            # No password set, initialize with default
            cls.set_admin_password('admin')
            return password == 'admin'
        return cls.check_password(password, hashed_password)

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

    # Relationship to per-user Linux account mappings on this device
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
        from src.nintendo_sync import get_nintendo_console_stats
        return get_nintendo_console_stats(self.system_id)

    @property
    def xbox_console_stats(self):
        """Return cached Xbox cloud console stats for this device."""
        if (self.platform or '').strip().lower() != 'xbox':
            return {}
        from src.xbox_sync import get_xbox_console_stats
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

class ManagedUser(db.Model):
    __tablename__ = 'managed_user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(50), nullable=False)
    # Legacy fields kept for compatibility during schema migration.
    # New code should use ManagedUserDeviceMap for device/account bindings.
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=True)
    system_ip = db.Column(db.String(50), nullable=False)
    is_valid = db.Column(db.Boolean, default=False)
    date_added = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_checked = db.Column(db.DateTime(timezone=True), nullable=True)
    last_config = db.Column(db.Text, nullable=True) # Store the full config JSON
    pending_time_adjustment = db.Column(db.Integer, nullable=True) # Pending time adjustment in seconds
    pending_time_operation = db.Column(db.String(1), nullable=True) # + or -
    daily_limit_adjustment_date = db.Column(db.Date, nullable=True)
    daily_limit_adjustment_seconds = db.Column(db.Integer, nullable=True)

    # Guardian Space overlay personalisation
    overlay_age_tier = db.Column(db.String(16), nullable=True)  # under8 | eight12 | teen
    overlay_parent_note = db.Column(db.Text, nullable=True)     # Message shown on the blocked overlay

    # Policy preset selection (age bracket × maturity / bypass risk)
    policy_age_bracket = db.Column(db.String(16), nullable=True)   # under7 | 8_12 | 13_15 | 16_plus
    policy_maturity_level = db.Column(db.String(16), nullable=True)  # low | medium | high

    
    # Relationship with usage data and weekly schedules
    usage_data = db.relationship('UserTimeUsage', backref='user', lazy=True, cascade="all, delete-orphan")
    weekly_schedule = db.relationship('UserWeeklySchedule', backref='user', uselist=False, cascade="all, delete-orphan")
    device_mappings = db.relationship(
        'ManagedUserDeviceMap',
        backref='managed_user',
        lazy=True,
        cascade="all, delete-orphan",
    )
    blocklist_assignments = db.relationship(
        'ManagedUserBlocklistAssignment',
        backref='managed_user',
        lazy=True,
        cascade="all, delete-orphan",
    )
    app_policy_assignments = db.relationship(
        'ManagedUserAppPolicyAssignment',
        backref='managed_user',
        lazy=True,
        cascade="all, delete-orphan",
    )
    
    def __repr__(self):
        return f'<ManagedUser {self.username}>'
    
    def get_recent_usage(self, days=7):
        """Get usage data for the last n days"""
        today = datetime.now(timezone.utc).date()
        start_date = today - timedelta(days=days-1)
        
        # Get the usage records for the specified period
        records = UserTimeUsage.query.filter_by(user_id=self.id).filter(
            UserTimeUsage.date >= start_date,
            UserTimeUsage.date <= today
        ).order_by(UserTimeUsage.date).all()
        
        # Create a dict with all days in the period
        usage_dict = {}
        for i in range(days):
            date = start_date + timedelta(days=i)
            usage_dict[date.strftime('%Y-%m-%d')] = 0
        
        # Fill in the actual data
        for record in records:
            date_str = record.date.strftime('%Y-%m-%d')
            usage_dict[date_str] = record.time_spent
        
        return usage_dict
    
    def get_usage_weekly_grouped(self, weeks=13):
        """Get usage totals grouped by week (Monday-Sunday) for the last N weeks"""
        today = datetime.now(timezone.utc).date()
        # Start from Monday of the week N-1 weeks ago
        days_since_monday = today.weekday()  # 0=Monday
        current_monday = today - timedelta(days=days_since_monday)
        start_date = current_monday - timedelta(weeks=weeks - 1)

        records = UserTimeUsage.query.filter_by(user_id=self.id).filter(
            UserTimeUsage.date >= start_date,
            UserTimeUsage.date <= today
        ).order_by(UserTimeUsage.date).all()

        result = []
        for i in range(weeks):
            week_start = start_date + timedelta(weeks=i)
            week_end = week_start + timedelta(days=6)
            total = sum(r.time_spent for r in records if week_start <= r.date <= week_end)
            result.append({
                'label': week_start.strftime('%d %b'),
                'week_start': week_start.strftime('%Y-%m-%d'),
                'total': total,
            })
        return result



    def get_usage_monthly_grouped(self, months=12):
        """Get usage totals grouped by calendar month for the last N months"""
        today = datetime.now(timezone.utc).date()

        result = []
        for i in range(months - 1, -1, -1):
            # Walk back i months from current month
            month = today.month - i
            year = today.year
            while month <= 0:
                month += 12
                year -= 1
            month_start = today.replace(year=year, month=month, day=1)
            if month == 12:
                month_end = today.replace(year=year + 1, month=1, day=1) - timedelta(days=1)
            else:
                month_end = today.replace(year=year, month=month + 1, day=1) - timedelta(days=1)

            records = UserTimeUsage.query.filter_by(user_id=self.id).filter(
                UserTimeUsage.date >= month_start,
                UserTimeUsage.date <= month_end
            ).all()
            total = sum(r.time_spent for r in records)
            result.append({
                'label': month_start.strftime('%b %Y'),
                'month': month_start.strftime('%Y-%m'),
                'total': total,
            })
        return result

    def get_all_usage_monthly(self):
        """Get all recorded usage grouped by calendar month, oldest first"""
        records = UserTimeUsage.query.filter_by(user_id=self.id).order_by(UserTimeUsage.date).all()
        if not records:
            return []

        buckets = defaultdict(int)
        for r in records:
            key = r.date.strftime('%Y-%m')
            buckets[key] += r.time_spent

        result = []
        for key in sorted(buckets):
            year, month = int(key[:4]), int(key[5:])
            label = datetime(year, month, 1).strftime('%b %Y')
            result.append({'label': label, 'month': key, 'total': buckets[key]})
        return result

    def get_config_value(self, key):
        """Extract a specific value from the stored config"""
        if not self.last_config:
            return None
        try:
            config = json.loads(self.last_config)
            return config.get(key)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def get_effective_time_left_seconds(self):
        """Get the dynamically computed time left for today, using UTC dates.

        This method uses UTC for all date comparisons, as the application operates in UTC.
        If the user was last checked on a previous day or has never been checked,
        it falls back to the effective daily limit for today (if configured) or the cached
        TIME_LEFT_DAY value.
        """
        # Use UTC date for consistency
        today = datetime.now(timezone.utc).date()
        limit = self.get_effective_daily_limit_seconds(today)
        last_checked = self.last_checked

        if last_checked is None:
            # No previous check; return limit if available, else cached value
            if limit is not None:
                return limit
            return coerce_time_left_day(self.get_config_value('TIME_LEFT_DAY'))

        # Ensure last_checked is timezone-aware UTC
        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=pytz.UTC)
        # Convert to UTC (no change) and compare dates
        last_checked_utc = last_checked.astimezone(pytz.UTC)
        if last_checked_utc.date() != today:
            if limit is not None:
                return limit
            return coerce_time_left_day(self.get_config_value('TIME_LEFT_DAY'))

        # Same day: return cached value or limit as fallback
        val = self.get_config_value('TIME_LEFT_DAY')
        if val is None:
            return limit
        return coerce_time_left_day(val)



    def get_daily_limit_adjustment_seconds(self, day=None):
        day = day or datetime.now(timezone.utc).date()
        if self.daily_limit_adjustment_date != day:
            return 0
        return int(self.daily_limit_adjustment_seconds or 0)

    def set_daily_limit_adjustment_seconds(self, seconds, day=None):
        day = day or datetime.now(timezone.utc).date()
        seconds = int(seconds or 0)
        if seconds:
            self.daily_limit_adjustment_date = day
            self.daily_limit_adjustment_seconds = seconds
        else:
            self.daily_limit_adjustment_date = None
            self.daily_limit_adjustment_seconds = None

    def apply_daily_limit_adjustment(self, operation, seconds, day=None):
        if operation not in {'+', '-'}:
            raise ValueError("operation must be '+' or '-'")
        seconds = int(seconds)
        if seconds < 0:
            raise ValueError('seconds must be non-negative')

        day = day or datetime.now(timezone.utc).date()
        current = self.get_daily_limit_adjustment_seconds(day)
        delta = seconds if operation == '+' else -seconds
        updated = current + delta
        self.set_daily_limit_adjustment_seconds(updated, day)
        return updated

    def get_effective_daily_limit_seconds(self, day=None):
        day = day or datetime.now(timezone.utc).date()
        if not self.weekly_schedule:
            return None

        base_limit = self.weekly_schedule.get_limit_seconds_for_day(day)

        if base_limit is None:
            return None

        return max(base_limit + self.get_daily_limit_adjustment_seconds(day), 0)

    def get_device_online_summary(self, online_checker):
        """Return tuple of (online_count, total_count) for mapped devices."""
        total = len(self.device_mappings)  # type: ignore
        online = 0
        for mapping in self.device_mappings:
            if online_checker(mapping.system_id):
                online += 1
        return online, total


class ManagedUserDeviceMap(db.Model):
    __tablename__ = 'managed_user_device_map'

    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    system_id = db.Column(db.String(50), db.ForeignKey('agent_device.system_id'), nullable=False)
    linux_username = db.Column(db.String(50), nullable=False)
    linux_uid = db.Column(db.Integer, nullable=True)
    is_valid = db.Column(db.Boolean, default=False)
    last_checked = db.Column(db.DateTime(timezone=True), nullable=True)
    last_config = db.Column(db.Text, nullable=True)
    date_added = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    last_modified = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    blocklist_policy_hash = db.Column(db.String(64), nullable=True)
    blocklist_is_synced = db.Column(db.Boolean, default=False, nullable=False)
    blocklist_last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    blocklist_last_attempted = db.Column(db.DateTime(timezone=True), nullable=True)
    blocklist_last_attempt_hash = db.Column(db.String(64), nullable=True)
    blocklist_last_error = db.Column(db.Text, nullable=True)
    android_profile_type = db.Column(db.String(20), nullable=True)

    __table_args__ = (
        db.UniqueConstraint('managed_user_id', 'system_id', name='managed_user_system_uc'),
        db.UniqueConstraint('system_id', 'linux_username', name='system_linux_username_uc'),
        db.UniqueConstraint('system_id', 'linux_uid', name='system_linux_uid_uc'),
    )

    def __repr__(self):
        return f'<ManagedUserDeviceMap user={self.managed_user_id} {self.linux_username}@{self.system_id}>'

    def get_config_value(self, key):
        """Extract a specific value from the stored mapping config."""
        if not self.last_config:
            return None
        try:
            config = json.loads(self.last_config)
            return config.get(key)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    @property
    def nintendo_player(self):
        """Return the linked Nintendo player profile for this mapping, if any."""
        device = self.device
        if not device or (device.platform or '').strip().lower() != 'nintendo':
            return None
        for player in device.linux_users:
            if player.get('username') == self.linux_username:
                return player
        return None

    @property
    def xbox_player(self):
        """Return the linked Xbox family roster profile for this mapping, if any."""
        device = self.device
        if not device or (device.platform or '').strip().lower() != 'xbox':
            return None
        for player in device.linux_users:
            if player.get('username') == self.linux_username:
                return player
        return None

    @property
    def display_linux_username(self):
        """Return a human-readable device account label for UI display."""
        if self.device and (self.device.platform or '').strip().lower() == 'xbox':
            player = self.xbox_player
            if player:
                nickname = (player.get('nickname') or '').strip()
                if nickname:
                    return nickname
        else:
            player = self.nintendo_player
            if player:
                nickname = (player.get('nickname') or '').strip()
                if nickname:
                    return nickname
        return self.linux_username

    def mark_blocklist_synced(self, policy_hash):
        self.blocklist_policy_hash = policy_hash
        self.blocklist_is_synced = True
        self.blocklist_last_synced = datetime.now(timezone.utc)
        self.blocklist_last_attempted = self.blocklist_last_synced
        self.blocklist_last_attempt_hash = policy_hash
        self.blocklist_last_error = None

    def mark_blocklist_sync_failed(self, error_message, attempt_hash=None):
        self.blocklist_is_synced = False
        self.blocklist_last_attempted = datetime.now(timezone.utc)
        self.blocklist_last_attempt_hash = attempt_hash
        self.blocklist_last_error = error_message


class BlocklistSource(db.Model):
    __tablename__ = 'blocklist_source'

    TYPE_MANUAL = 'manual'
    TYPE_EXTERNAL_URL = 'external_url'

    SYNC_NEVER = 'never'
    SYNC_OK = 'ok'
    SYNC_ERROR = 'error'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    source_type = db.Column(db.String(32), nullable=False, default=TYPE_MANUAL)
    source_url = db.Column(db.Text, nullable=True)
    is_enabled = db.Column(db.Boolean, default=True, nullable=False)
    is_marketplace = db.Column(db.Boolean, default=False, nullable=False)
    preset_id = db.Column(db.String(64), nullable=True)
    last_sync_at = db.Column(db.DateTime(timezone=True), nullable=True)
    last_sync_status = db.Column(db.String(32), default=SYNC_NEVER, nullable=False)
    last_sync_error = db.Column(db.Text, nullable=True)
    etag = db.Column(db.String(255), nullable=True)
    source_last_modified = db.Column(db.String(255), nullable=True)
    content_revision = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    domains = db.relationship(
        'BlocklistDomain',
        backref='source',
        lazy=True,
        cascade="all, delete-orphan",
        order_by='BlocklistDomain.domain.asc()',
    )
    assignments = db.relationship(
        'ManagedUserBlocklistAssignment',
        backref='source',
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f'<BlocklistSource {self.name} [{self.source_type}]>'

    @property
    def domain_count(self):
        return len(self.domains)  # type: ignore

    def mark_sync_ok(self):
        self.last_sync_at = datetime.now(timezone.utc)
        self.last_sync_status = self.SYNC_OK
        self.last_sync_error = None
        self.updated_at = datetime.now(timezone.utc)

    def mark_sync_error(self, error_message):
        self.last_sync_at = datetime.now(timezone.utc)
        self.last_sync_status = self.SYNC_ERROR
        self.last_sync_error = error_message
        self.updated_at = datetime.now(timezone.utc)


class BlocklistDomain(db.Model):
    __tablename__ = 'blocklist_domain'

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey('blocklist_source.id'), nullable=False)
    domain = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('source_id', 'domain', name='blocklist_source_domain_uc'),
    )

    def __repr__(self):
        return f'<BlocklistDomain {self.domain}>'


class ManagedUserBlocklistAssignment(db.Model):
    __tablename__ = 'managed_user_blocklist_assignment'

    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    source_id = db.Column(db.Integer, db.ForeignKey('blocklist_source.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('managed_user_id', 'source_id', name='managed_user_blocklist_uc'),
    )

    def __repr__(self):
        return f'<ManagedUserBlocklistAssignment user={self.managed_user_id} source={self.source_id}>'

class UserTimeUsage(db.Model):
    __tablename__ = 'user_time_usage'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    date = db.Column(db.Date, nullable=False)
    time_spent = db.Column(db.Integer, default=0) # Time spent in seconds
    
    __table_args__ = (
        db.UniqueConstraint('user_id', 'date', name='user_date_uc'),
    )
    
    def __repr__(self):
        return f'<UserTimeUsage {self.user.username} {self.date}: {self.time_spent}>'  # type: ignore

class UserWeeklySchedule(db.Model):
    __tablename__ = 'user_weekly_schedule'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    
    # Time limits per day in hours (0 = no limit/disabled)
    monday_hours = db.Column(db.Float, default=0)
    tuesday_hours = db.Column(db.Float, default=0)
    wednesday_hours = db.Column(db.Float, default=0)
    thursday_hours = db.Column(db.Float, default=0)
    friday_hours = db.Column(db.Float, default=0)
    saturday_hours = db.Column(db.Float, default=0)
    sunday_hours = db.Column(db.Float, default=0)
    
    # Sync status and timestamps
    is_synced = db.Column(db.Boolean, default=False)
    last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    last_modified = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    def __repr__(self):
        return f'<UserWeeklySchedule {self.user.username}>'  # type: ignore
    
    def get_schedule_dict(self):
        """Get schedule as a dictionary for easy template rendering"""
        return {
            'monday': self.monday_hours,
            'tuesday': self.tuesday_hours,
            'wednesday': self.wednesday_hours,
            'thursday': self.thursday_hours,
            'friday': self.friday_hours,
            'saturday': self.saturday_hours,
            'sunday': self.sunday_hours
        }

    def get_limit_hours_for_day(self, day=None):
        day = day or datetime.now(timezone.utc).date()
        day_names = (
            'monday',
            'tuesday',
            'wednesday',
            'thursday',
            'friday',
            'saturday',
            'sunday',
        )
        return self.get_schedule_dict().get(day_names[day.weekday()], 0)

    def get_limit_seconds_for_day(self, day=None):
        hours = self.get_limit_hours_for_day(day)
        if hours is None or hours <= 0:
            return None
        return int(round(hours * 3600))
    
    def set_schedule_from_dict(self, schedule_dict):
        """Set schedule from a dictionary"""
        self.monday_hours = schedule_dict.get('monday', 0)
        self.tuesday_hours = schedule_dict.get('tuesday', 0)
        self.wednesday_hours = schedule_dict.get('wednesday', 0)
        self.thursday_hours = schedule_dict.get('thursday', 0)
        self.friday_hours = schedule_dict.get('friday', 0)
        self.saturday_hours = schedule_dict.get('saturday', 0)
        self.sunday_hours = schedule_dict.get('sunday', 0)
        self.last_modified = datetime.now(timezone.utc)
        self.is_synced = False
    
    def set_weekdays_hours(self, hours):
        """Set the same hours for all weekdays (Monday to Friday)"""
        self.monday_hours = hours
        self.tuesday_hours = hours
        self.wednesday_hours = hours
        self.thursday_hours = hours
        self.friday_hours = hours
        self.last_modified = datetime.now(timezone.utc)
        self.is_synced = False
    
    def has_pending_changes(self):
        """Check if there are unsynced changes"""
        return not self.is_synced
    
    def mark_synced(self):
        """Mark the schedule as synced with the remote system"""
        self.is_synced = True
        self.last_synced = datetime.now(timezone.utc)

class UserDailyTimeInterval(db.Model):
    __tablename__ = 'user_daily_time_interval'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    
    # Day of week (1=Monday, 7=Sunday, matching ISO 8601)
    day_of_week = db.Column(db.Integer, nullable=False)  # 1-7
    sort_order = db.Column(db.Integer, nullable=False, default=0)
    
    # Time interval (24-hour format)
    start_hour = db.Column(db.Integer, nullable=False)   # 0-23
    start_minute = db.Column(db.Integer, default=0)      # 0-59
    end_hour = db.Column(db.Integer, nullable=False)     # 0-23
    end_minute = db.Column(db.Integer, default=0)        # 0-59
    
    # Whether this interval is enabled
    is_enabled = db.Column(db.Boolean, default=True)
    
    # Sync status and timestamps
    is_synced = db.Column(db.Boolean, default=False)
    last_synced = db.Column(db.DateTime(timezone=True), nullable=True)
    last_modified = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    
    # Relationship back to user
    user = db.relationship('ManagedUser', backref=db.backref('time_intervals', cascade='all, delete-orphan'))
    
    # Constraint to keep a stable per-day ordering for multiple intervals.
    __table_args__ = (
        db.UniqueConstraint('user_id', 'day_of_week', 'sort_order', name='user_day_interval_sort_order_uc'),
    )
    
    def __repr__(self):
        return f'<UserDailyTimeInterval {self.user.username} Day{self.day_of_week} {self.start_hour:02d}:{self.start_minute:02d}-{self.end_hour:02d}:{self.end_minute:02d}>'
    
    def get_time_range_string(self):
        """Get formatted time range string (e.g., '09:00-17:30')"""
        return f"{self.start_hour:02d}:{self.start_minute:02d}-{self.end_hour:02d}:{self.end_minute:02d}"
    
    def get_day_name(self):
        """Get day name from day_of_week number"""
        days = ['', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
        return days[self.day_of_week] if 1 <= self.day_of_week <= 7 else 'Unknown'

    @property
    def start_total_minutes(self):
        return self.start_hour * 60 + self.start_minute

    @property
    def end_total_minutes(self):
        return self.end_hour * 60 + self.end_minute

    def has_valid_time_components(self):
        return (
            0 <= self.start_hour <= 23 and
            0 <= self.end_hour <= 23 and
            0 <= self.start_minute <= 59 and
            0 <= self.end_minute <= 59
        )
    
    def is_valid_interval(self, step_minutes=None):
        """Check if the time interval is valid and optionally aligned to a time step."""
        if not self.has_valid_time_components():
            return False

        start_minutes = self.start_total_minutes
        end_minutes = self.end_total_minutes
        if not (start_minutes < end_minutes and 0 <= start_minutes < 1440 and 0 < end_minutes <= 1440):
            return False

        if step_minutes:
            return start_minutes % step_minutes == 0 and end_minutes % step_minutes == 0
        return True

    @staticmethod
    def sort_intervals(intervals):
        return sorted(
            intervals,
            key=lambda interval: (
                interval.start_total_minutes,
                interval.end_total_minutes,
                interval.sort_order,
                interval.id or 0,
            ),
        )

    @classmethod
    def validate_interval_collection(cls, intervals, step_minutes=None):
        """Validate a day's interval list for ordering, bounds, and overlap."""
        ordered_intervals = cls.sort_intervals(intervals)
        previous_end = None

        for interval in ordered_intervals:
            if not interval.is_valid_interval(step_minutes=step_minutes):
                return False
            if previous_end is not None and interval.start_total_minutes < previous_end:
                return False
            previous_end = interval.end_total_minutes

        return True
    
    def mark_synced(self):
        """Mark the interval as synced with the remote system"""
        self.is_synced = True
        self.last_synced = datetime.now(timezone.utc)
    
    def mark_modified(self):
        """Mark the interval as modified (needs sync)"""
        self.is_synced = False
        self.last_modified = datetime.now(timezone.utc)
    
    def to_timekpr_format(self):
        """Convert interval to timekpr hour specification format"""
        if not self.is_enabled:
            return None
        
        # If full hour intervals, just return the hour numbers
        if self.start_minute == 0 and self.end_minute == 0:
            hours = list(range(self.start_hour, self.end_hour))
            return [str(h) for h in hours]
        
        # If partial hours, include minute specifications
        result = []
        current_hour = self.start_hour
        
        # First hour (potentially partial)
        if current_hour == self.end_hour:
            # Same hour, use minute range
            result.append(f"{current_hour}[{self.start_minute}-{self.end_minute}]")
        else:
            # Multiple hours
            if self.start_minute == 0:
                result.append(str(current_hour))
            else:
                result.append(f"{current_hour}[{self.start_minute}-59]")
            
            current_hour += 1
            
            # Full hours in between
            while current_hour < self.end_hour:
                result.append(str(current_hour))
                current_hour += 1
            
            # Last hour (potentially partial)
            if self.end_minute > 0:
                result.append(f"{self.end_hour}[0-{self.end_minute}]")
        
        return result


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


class AppPolicy(db.Model):
    __tablename__ = 'app_policy'

    PLATFORM_LINUX = 'linux'
    PLATFORM_ANDROID = 'android'
    PLATFORM_WINDOWS = 'windows'
    VALID_PLATFORMS = {PLATFORM_LINUX, PLATFORM_ANDROID, PLATFORM_WINDOWS}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    platform = db.Column(db.String(20), nullable=False, default=PLATFORM_LINUX)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    rules = db.relationship(
        'AppPolicyRule',
        backref='policy',
        lazy=True,
        cascade="all, delete-orphan",
        order_by='AppPolicyRule.application_name.asc()',
    )
    assignments = db.relationship(
        'ManagedUserAppPolicyAssignment',
        backref='policy',
        lazy=True,
        cascade="all, delete-orphan",
    )

    def __repr__(self):
        return f'<AppPolicy {self.name}>'


class AppPolicyRule(db.Model):
    __tablename__ = 'app_policy_rule'

    PRESET_ALLOWED = 'allowed'
    PRESET_NO_INTERNET = 'no_internet'
    PRESET_BLOCKED = 'blocked'
    PRESET_COMPLAIN = 'complain'
    MATCH_TYPE_EXECUTABLE = 'executable'
    MATCH_TYPE_PATH_PATTERN = 'path_pattern'
    MATCH_TYPE_PACKAGE = 'package'

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, db.ForeignKey('app_policy.id'), nullable=False)
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

    __table_args__ = (
        db.UniqueConstraint(
            'policy_id',
            'executable_path',
            name='apparmor_policy_rule_exec_uc',
        ),
    )

    def __repr__(self):
        return f'<AppPolicyRule {self.application_name} [{self.preset}]>'


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


class ManagedUserAppPolicyAssignment(db.Model):
    __tablename__ = 'managed_user_app_policy_assignment'

    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id'), nullable=False)
    policy_id = db.Column(db.Integer, db.ForeignKey('app_policy.id'), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        db.UniqueConstraint('managed_user_id', 'policy_id', name='managed_user_app_policy_uc'),
    )

    def __repr__(self):
        return f'<ManagedUserAppPolicyAssignment user={self.managed_user_id} policy={self.policy_id}>'


class MappingApprovalSettings(db.Model):
    __tablename__ = 'mapping_approval_settings'

    APP_LAUNCH_OPEN = 'open'
    APP_LAUNCH_ALLOWLIST = 'allowlist'
    APP_LAUNCH_BLOCKLIST = 'blocklist'
    VALID_APP_LAUNCH_MODES = {
        APP_LAUNCH_OPEN,
        APP_LAUNCH_ALLOWLIST,
        APP_LAUNCH_BLOCKLIST,
    }

    DOMAIN_BLOCKLIST_ONLY = 'blocklist_only'
    DOMAIN_APPROVAL_ON_BLOCK = 'approval_on_block'
    VALID_DOMAIN_ACCESS_MODES = {
        DOMAIN_BLOCKLIST_ONLY,
        DOMAIN_APPROVAL_ON_BLOCK,
    }

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
        unique=True,
    )
    app_launch_mode = db.Column(
        db.String(32),
        nullable=False,
        default=APP_LAUNCH_OPEN,
    )
    domain_access_mode = db.Column(
        db.String(32),
        nullable=False,
        default=DOMAIN_BLOCKLIST_ONLY,
    )
    registration_approval_enabled = db.Column(
        db.Boolean,
        nullable=False,
        default=False,
    )
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

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('approval_settings', uselist=False, cascade='all, delete-orphan'),
    )

    def __repr__(self):
        return (
            f'<MappingApprovalSettings map={self.device_map_id} '
            f'app={self.app_launch_mode} domain={self.domain_access_mode}>'
        )


class MappingAndroidDevicePolicy(db.Model):
    __tablename__ = 'mapping_android_device_policy'

    CAMERA_ACCESS_UNSPECIFIED = 'CAMERA_ACCESS_UNSPECIFIED'
    CAMERA_ACCESS_DISABLED = 'CAMERA_ACCESS_DISABLED'
    CAMERA_ACCESS_USER_CHOICE = 'CAMERA_ACCESS_USER_CHOICE'
    CAMERA_ACCESS_ENFORCED = 'CAMERA_ACCESS_ENFORCED'
    VALID_CAMERA_ACCESS = {
        CAMERA_ACCESS_UNSPECIFIED,
        CAMERA_ACCESS_DISABLED,
        CAMERA_ACCESS_USER_CHOICE,
        CAMERA_ACCESS_ENFORCED,
    }

    DEVELOPER_SETTINGS_UNSPECIFIED = 'DEVELOPER_SETTINGS_UNSPECIFIED'
    DEVELOPER_SETTINGS_DISABLED = 'DEVELOPER_SETTINGS_DISABLED'
    DEVELOPER_SETTINGS_ALLOWED = 'DEVELOPER_SETTINGS_ALLOWED'
    VALID_DEVELOPER_SETTINGS = {
        DEVELOPER_SETTINGS_UNSPECIFIED,
        DEVELOPER_SETTINGS_DISABLED,
        DEVELOPER_SETTINGS_ALLOWED,
    }

    MICROPHONE_ACCESS_UNSPECIFIED = 'MICROPHONE_ACCESS_UNSPECIFIED'
    MICROPHONE_ACCESS_DISABLED = 'MICROPHONE_ACCESS_DISABLED'
    MICROPHONE_ACCESS_USER_CHOICE = 'MICROPHONE_ACCESS_USER_CHOICE'
    MICROPHONE_ACCESS_ENFORCED = 'MICROPHONE_ACCESS_ENFORCED'
    VALID_MICROPHONE_ACCESS = {
        MICROPHONE_ACCESS_UNSPECIFIED,
        MICROPHONE_ACCESS_DISABLED,
        MICROPHONE_ACCESS_USER_CHOICE,
        MICROPHONE_ACCESS_ENFORCED,
    }

    USB_DATA_ACCESS_UNSPECIFIED = 'USB_DATA_ACCESS_UNSPECIFIED'
    USB_DATA_ACCESS_ALLOW = 'ALLOW_USB_DATA_TRANSFER'
    USB_DATA_ACCESS_DISALLOW_FILE = 'DISALLOW_USB_FILE_TRANSFER'
    USB_DATA_ACCESS_DISALLOW_ALL = 'DISALLOW_USB_DATA_TRANSFER'
    VALID_USB_DATA_ACCESS = {
        USB_DATA_ACCESS_UNSPECIFIED,
        USB_DATA_ACCESS_ALLOW,
        USB_DATA_ACCESS_DISALLOW_FILE,
        USB_DATA_ACCESS_DISALLOW_ALL,
    }

    DEFAULT_SHORT_SUPPORT_MESSAGE = (
        'This setting is managed by your parent through TimeKpr.'
    )
    DEFAULT_LONG_SUPPORT_MESSAGE = (
        'This device is protected by TimeKpr parental controls. Your parent manages '
        'screen time, apps, and websites. Ask them if you need something changed.'
    )
    MAX_SHORT_SUPPORT_MESSAGE_LENGTH = 200
    MAX_LONG_SUPPORT_MESSAGE_LENGTH = 4096

    system_id = db.Column(
        db.String(50),
        db.ForeignKey('agent_device.system_id'),
        primary_key=True,
        nullable=False,
        unique=True,
    )
    screen_capture_disabled = db.Column(db.Boolean, nullable=False, default=False)
    camera_access = db.Column(
        db.String(40),
        nullable=False,
        default=CAMERA_ACCESS_UNSPECIFIED,
    )
    install_apps_disabled = db.Column(db.Boolean, nullable=False, default=False)
    uninstall_apps_disabled = db.Column(db.Boolean, nullable=False, default=False)
    developer_settings = db.Column(
        db.String(40),
        nullable=False,
        default=DEVELOPER_SETTINGS_UNSPECIFIED,
    )
    microphone_access = db.Column(
        db.String(40),
        nullable=False,
        default=MICROPHONE_ACCESS_UNSPECIFIED,
    )
    usb_data_access = db.Column(
        db.String(40),
        nullable=False,
        default=USB_DATA_ACCESS_UNSPECIFIED,
    )
    factory_reset_disabled = db.Column(db.Boolean, nullable=False, default=False)
    adjust_volume_disabled = db.Column(db.Boolean, nullable=False, default=False)
    modify_accounts_disabled = db.Column(db.Boolean, nullable=False, default=False)
    mount_physical_media_disabled = db.Column(db.Boolean, nullable=False, default=False)
    bluetooth_disabled = db.Column(db.Boolean, nullable=False, default=False)
    outgoing_calls_disabled = db.Column(db.Boolean, nullable=False, default=False)
    sms_disabled = db.Column(db.Boolean, nullable=False, default=False)
    short_support_message = db.Column(
        db.Text,
        nullable=False,
        default=DEFAULT_SHORT_SUPPORT_MESSAGE,
    )
    long_support_message = db.Column(
        db.Text,
        nullable=False,
        default=DEFAULT_LONG_SUPPORT_MESSAGE,
    )
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
        backref=db.backref('android_device_policy', uselist=False, cascade='all, delete-orphan'),
    )

    block_wifi_tethering = db.Column(db.Boolean, nullable=False, default=False)
    block_nfc = db.Column(db.Boolean, nullable=False, default=False)

    force_installed_apps = db.relationship(
        'AndroidForceInstalledApp',
        backref='policy',
        cascade='all, delete-orphan',
        lazy=True,
    )

    def __repr__(self):
        return f'<MappingAndroidDevicePolicy device={self.system_id} revision={self.revision}>'


class AndroidForceInstalledApp(db.Model):
    __tablename__ = 'android_force_installed_app'

    id = db.Column(db.Integer, primary_key=True)
    system_id = db.Column(
        db.String(50),
        db.ForeignKey('mapping_android_device_policy.system_id'),
        nullable=False,
    )
    package_name = db.Column(db.String(255), nullable=False)
    apk_url = db.Column(db.Text, nullable=False)
    sha256_checksum = db.Column(db.String(64), nullable=True)
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

    __table_args__ = (
        db.UniqueConstraint(
            'system_id',
            'package_name',
            name='android_force_installed_app_package_uc',
        ),
    )

    def __repr__(self):
        return f'<AndroidForceInstalledApp {self.package_name} on {self.system_id}>'



class MappingLinuxDevicePolicy(db.Model):
    __tablename__ = 'mapping_linux_device_policy'

    DEFAULT_SUPPORT_MESSAGE = (
        'This setting is managed by your parent through TimeKpr.'
    )
    MAX_SUPPORT_MESSAGE_LENGTH = 500

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
        unique=True,
    )
    install_software_disabled = db.Column(db.Boolean, nullable=False, default=False)
    uninstall_software_disabled = db.Column(db.Boolean, nullable=False, default=False)
    mount_removable_media_disabled = db.Column(db.Boolean, nullable=False, default=False)
    modify_accounts_disabled = db.Column(db.Boolean, nullable=False, default=False)
    system_power_actions_disabled = db.Column(db.Boolean, nullable=False, default=False)
    pkexec_elevation_disabled = db.Column(db.Boolean, nullable=False, default=False)
    bluetooth_disabled = db.Column(db.Boolean, nullable=False, default=False)
    flatpak_install_disabled = db.Column(db.Boolean, nullable=False, default=False)
    snap_install_disabled = db.Column(db.Boolean, nullable=False, default=False)
    terminal_access_disabled = db.Column(db.Boolean, nullable=False, default=False)
    support_message = db.Column(
        db.Text,
        nullable=False,
        default=DEFAULT_SUPPORT_MESSAGE,
    )
    chrome_policies_json = db.Column(db.Text, nullable=True)
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

    DEFAULT_CHROME_POLICIES = {
        'incognito_disabled': True,
        'safesearch_enforced': True,
        'youtube_restrict': 2,            # 2 = Strict, 1 = Moderate, 0 = Off
        'block_other_extensions': False,  # Only allow our Guardian extension
        'block_genai_features': False,     # Disable browser-native Gen AI
        'allowed_extension_ids': [],      # Additional allowed extension IDs
    }

    @property
    def chrome_policies(self) -> dict:
        if not self.chrome_policies_json:
            return self.DEFAULT_CHROME_POLICIES.copy()
        try:
            val = json.loads(self.chrome_policies_json)
            if not isinstance(val, dict):
                return self.DEFAULT_CHROME_POLICIES.copy()
            # Ensure any missing keys are filled with defaults
            res = self.DEFAULT_CHROME_POLICIES.copy()
            res.update(val)
            return res
        except (TypeError, ValueError, json.JSONDecodeError):
            return self.DEFAULT_CHROME_POLICIES.copy()

    @chrome_policies.setter
    def chrome_policies(self, val: dict) -> None:
        if not isinstance(val, dict):
            val = {}
        self.chrome_policies_json = json.dumps(val)

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('linux_device_policy', uselist=False, cascade='all, delete-orphan'),
    )

    def __repr__(self):
        return f'<MappingLinuxDevicePolicy map={self.device_map_id} revision={self.revision}>'


class ApprovalRequest(db.Model):
    __tablename__ = 'approval_request'

    REQUEST_APP_LAUNCH = 'app_launch'
    REQUEST_DOMAIN_ACCESS = 'domain_access'
    REQUEST_REGISTRATION = 'registration'
    VALID_REQUEST_TYPES = {REQUEST_APP_LAUNCH, REQUEST_DOMAIN_ACCESS, REQUEST_REGISTRATION}

    TARGET_PACKAGE = 'package'
    TARGET_EXECUTABLE = 'executable'
    TARGET_PATH_PATTERN = 'path_pattern'
    TARGET_DOMAIN = 'domain'
    VALID_TARGET_KINDS = {
        TARGET_PACKAGE,
        TARGET_EXECUTABLE,
        TARGET_PATH_PATTERN,
        TARGET_DOMAIN,
    }

    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_DENIED = 'denied'
    STATUS_SUPERSEDED = 'superseded'
    VALID_STATUSES = {STATUS_PENDING, STATUS_APPROVED, STATUS_DENIED, STATUS_SUPERSEDED}

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    request_type = db.Column(db.String(32), nullable=False)
    target_kind = db.Column(db.String(32), nullable=False)
    target_value = db.Column(db.String(512), nullable=False)
    display_label = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(32), nullable=False, default=STATUS_PENDING)
    requested_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    decided_at = db.Column(db.DateTime(timezone=True), nullable=True)
    decided_by = db.Column(db.String(80), nullable=True)
    denial_reason = db.Column(db.Text, nullable=True)
    source_alert_id = db.Column(
        db.Integer,
        db.ForeignKey('agent_alert.id'),
        nullable=True,
    )
    details_json = db.Column(db.Text, nullable=True)

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('approval_requests', cascade='all, delete-orphan'),
    )
    source_alert = db.relationship('AgentAlert', foreign_keys=[source_alert_id])

    __table_args__ = (
        db.Index('approval_request_status_requested_idx', 'status', 'requested_at'),
        db.Index('approval_request_map_status_idx', 'device_map_id', 'status'),
    )

    def __repr__(self):
        return f'<ApprovalRequest {self.request_type}:{self.target_value} [{self.status}]>'


class PolicyApprovalGrant(db.Model):
    __tablename__ = 'policy_approval_grant'

    GRANT_APP_LAUNCH = 'app_launch'
    GRANT_DOMAIN_ACCESS = 'domain_access'
    GRANT_REGISTRATION = 'registration'
    VALID_GRANT_TYPES = {GRANT_APP_LAUNCH, GRANT_DOMAIN_ACCESS, GRANT_REGISTRATION}

    TARGET_PACKAGE = 'package'
    TARGET_EXECUTABLE = 'executable'
    TARGET_PATH_PATTERN = 'path_pattern'
    TARGET_DOMAIN = 'domain'
    VALID_TARGET_KINDS = {
        TARGET_PACKAGE,
        TARGET_EXECUTABLE,
        TARGET_PATH_PATTERN,
        TARGET_DOMAIN,
    }

    STATUS_ACTIVE = 'active'
    STATUS_REVOKED = 'revoked'
    VALID_STATUSES = {STATUS_ACTIVE, STATUS_REVOKED}

    id = db.Column(db.Integer, primary_key=True)
    device_map_id = db.Column(
        db.Integer,
        db.ForeignKey('managed_user_device_map.id'),
        nullable=False,
    )
    grant_type = db.Column(db.String(32), nullable=False)
    target_kind = db.Column(db.String(32), nullable=False)
    target_value = db.Column(db.String(512), nullable=False)
    display_label = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(32), nullable=False, default=STATUS_ACTIVE)
    created_at = db.Column(
        db.DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    created_by = db.Column(db.String(80), nullable=True)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)
    revoked_by = db.Column(db.String(80), nullable=True)
    source_request_id = db.Column(
        db.Integer,
        db.ForeignKey('approval_request.id'),
        nullable=True,
    )

    device_map = db.relationship(
        'ManagedUserDeviceMap',
        backref=db.backref('approval_grants', cascade='all, delete-orphan'),
    )
    source_request = db.relationship('ApprovalRequest', foreign_keys=[source_request_id])

    __table_args__ = (
        db.Index('policy_approval_grant_map_status_idx', 'device_map_id', 'status'),
    )

    def __repr__(self):
        return f'<PolicyApprovalGrant {self.grant_type}:{self.target_value} [{self.status}]>'


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