#!/usr/bin/env python3
"""
Database migration script for password security upgrade.

This script migrates plain text passwords to bcrypt hashes.
It's automatically handled in the Settings.check_admin_password() method,
but this script can be run manually if needed.
"""

from flask import Flask

from src.database import Settings, db

def create_app():
    """Create Flask app for migration context"""
    app = Flask(__name__)
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///timekpr.db'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    db.init_app(app)
    return app

def migrate_passwords():
    """Migrate plain text passwords to bcrypt hashes"""
    print("Starting password migration...")

    # Check if we have an old plain text password
    old_password = Settings.get_value('admin_password')
    hashed_password = Settings.get_value('admin_password_hash')

    if old_password and not hashed_password:
        print("Found plain text password, migrating to bcrypt hash...")
        Settings.set_admin_password(old_password)
        print("✅ Password migrated successfully!")
        print("🔐 Old plain text password has been removed from database")
    elif hashed_password:
        print("✅ Password already migrated to bcrypt hash")
    else:
        print("🔧 No password found, initializing with default 'admin'")
        Settings.set_admin_password('admin')
        print("✅ Default password initialized with bcrypt hash")

    print("Migration completed!")

if __name__ == '__main__':
    migration_app = create_app()

    with migration_app.app_context():
        db.create_all()
        migrate_passwords()