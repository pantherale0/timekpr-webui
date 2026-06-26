import sqlite3
import bcrypt
from sqlalchemy import event
from sqlalchemy.engine import Engine
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
