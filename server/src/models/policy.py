import json
from datetime import datetime, timezone

from src.models.core import db


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


class AppPolicy(db.Model):
    __tablename__ = 'app_policy'

    PLATFORM_LINUX = 'linux'
    PLATFORM_ANDROID = 'android'
    PLATFORM_WINDOWS = 'windows'
    VALID_PLATFORMS = {PLATFORM_LINUX, PLATFORM_ANDROID, PLATFORM_WINDOWS}

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), unique=True, nullable=False)
    platform = db.Column(db.String(20), nullable=False, default=PLATFORM_LINUX)
    household_id = db.Column(db.Integer, db.ForeignKey('household.id', ondelete='SET NULL'), nullable=True)
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
