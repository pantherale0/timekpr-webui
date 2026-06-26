from datetime import datetime, timezone
from src.models.core import db


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
