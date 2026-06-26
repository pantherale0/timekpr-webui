from datetime import datetime, timezone
from src.models.core import db


class Household(db.Model):
    __tablename__ = 'household'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    enrollment_token = db.Column(db.String(64), unique=True, nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    children = db.relationship('ManagedUser', back_populates='household', lazy=True, cascade="all, delete-orphan")
    devices = db.relationship('AgentDevice', back_populates='household', lazy=True)
    memberships = db.relationship('HouseholdParentMembership', back_populates='household', lazy=True, cascade="all, delete-orphan")
    invites = db.relationship('HouseholdInvite', back_populates='household', lazy=True, cascade="all, delete-orphan")


class ParentAccount(db.Model):
    __tablename__ = 'parent_account'
    id = db.Column(db.Integer, primary_key=True)
    oidc_sub = db.Column(db.String(255), unique=True, nullable=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    name = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)

    memberships = db.relationship('HouseholdParentMembership', back_populates='parent', lazy=True, cascade="all, delete-orphan")
    shared_children_links = db.relationship('ManagedUserShare', back_populates='parent', lazy=True, cascade="all, delete-orphan")


class HouseholdParentMembership(db.Model):
    __tablename__ = 'household_parent_membership'
    household_id = db.Column(db.Integer, db.ForeignKey('household.id', ondelete='CASCADE'), primary_key=True)
    parent_account_id = db.Column(db.Integer, db.ForeignKey('parent_account.id', ondelete='CASCADE'), primary_key=True)
    permissions_json = db.Column(db.JSON, nullable=False, default=dict)
    joined_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    household = db.relationship('Household', back_populates='memberships')
    parent = db.relationship('ParentAccount', back_populates='memberships')


class HouseholdInvite(db.Model):
    __tablename__ = 'household_invite'
    id = db.Column(db.Integer, primary_key=True)
    household_id = db.Column(db.Integer, db.ForeignKey('household.id', ondelete='CASCADE'), nullable=False)
    invite_code = db.Column(db.String(32), unique=True, nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey('parent_account.id', ondelete='SET NULL'), nullable=True, index=True)
    permissions_json = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    used_count = db.Column(db.Integer, default=0, nullable=False)
    max_uses = db.Column(db.Integer, default=1, nullable=False)

    household = db.relationship('Household', back_populates='invites')


class ManagedUserShare(db.Model):
    __tablename__ = 'managed_user_share'
    parent_account_id = db.Column(db.Integer, db.ForeignKey('parent_account.id', ondelete='CASCADE'), primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id', ondelete='CASCADE'), primary_key=True)
    permissions_json = db.Column(db.JSON, nullable=False, default=dict)
    shared_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)

    parent = db.relationship('ParentAccount', back_populates='shared_children_links')
    managed_user = db.relationship('ManagedUser', back_populates='shared_parents')


class ManagedUserShareInvite(db.Model):
    __tablename__ = 'managed_user_share_invite'
    id = db.Column(db.Integer, primary_key=True)
    managed_user_id = db.Column(db.Integer, db.ForeignKey('managed_user.id', ondelete='CASCADE'), nullable=False)
    invite_code = db.Column(db.String(32), unique=True, nullable=False)
    permissions_json = db.Column(db.JSON, nullable=False, default=dict)
    created_by_id = db.Column(db.Integer, db.ForeignKey('parent_account.id', ondelete='SET NULL'), nullable=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=True)
    used_count = db.Column(db.Integer, default=0, nullable=False)
    max_uses = db.Column(db.Integer, default=1, nullable=False)
