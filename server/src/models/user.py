import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import pytz

from src.models.core import db
from src.models.device import coerce_time_spent_day, coerce_time_left_day


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
    household_id = db.Column(db.Integer, db.ForeignKey('household.id', ondelete='CASCADE'), nullable=True)

    # Guardian Space overlay personalisation
    household = db.relationship('Household', back_populates='children')
    shared_parents = db.relationship('ManagedUserShare', back_populates='managed_user', lazy=True, cascade="all, delete-orphan")
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
        """Get the dynamically computed time left for today, using UTC dates."""
        today = datetime.now(timezone.utc).date()
        limit = self.get_effective_daily_limit_seconds(today)
        last_checked = self.last_checked

        if last_checked is None:
            if limit is not None:
                return limit
            return coerce_time_left_day(self.get_config_value('TIME_LEFT_DAY'))

        if last_checked.tzinfo is None:
            last_checked = last_checked.replace(tzinfo=pytz.UTC)
        last_checked_utc = last_checked.astimezone(pytz.UTC)
        if last_checked_utc.date() != today:
            if limit is not None:
                return limit
            return coerce_time_left_day(self.get_config_value('TIME_LEFT_DAY'))

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
