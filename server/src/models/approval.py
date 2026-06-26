from datetime import datetime, timezone
from src.models.core import db


class ApprovalRequest(db.Model):
    __tablename__ = 'approval_request'

    REQUEST_APP_LAUNCH = 'app_launch'
    REQUEST_DOMAIN_ACCESS = 'domain_access'
    REQUEST_REGISTRATION = 'registration'
    REQUEST_DIALOGUE_FLAG = 'dialogue_flag'
    REQUEST_SENTIMENT_BREACH = 'sentiment_breach'
    VALID_REQUEST_TYPES = {
        REQUEST_APP_LAUNCH,
        REQUEST_DOMAIN_ACCESS,
        REQUEST_REGISTRATION,
        REQUEST_DIALOGUE_FLAG,
        REQUEST_SENTIMENT_BREACH,
    }

    TARGET_PACKAGE = 'package'
    TARGET_EXECUTABLE = 'executable'
    TARGET_PATH_PATTERN = 'path_pattern'
    TARGET_DOMAIN = 'domain'
    TARGET_DIALOGUE = 'dialogue'
    VALID_TARGET_KINDS = {
        TARGET_PACKAGE,
        TARGET_EXECUTABLE,
        TARGET_PATH_PATTERN,
        TARGET_DOMAIN,
        TARGET_DIALOGUE,
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
    ai_policy_mode = db.Column(
        db.String(32),
        nullable=False,
        default='off',
    )
    ai_prompt_logging = db.Column(
        db.String(32),
        nullable=False,
        default='metadata_only',
    )
    ai_daily_time_limit = db.Column(
        db.Integer,
        nullable=True,
        default=None,
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
