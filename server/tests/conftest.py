"""Shared pytest fixtures for server-side tests."""

# pylint: disable=import-error,redefined-outer-name,unused-argument

import importlib
import os
import sys

import pytest

# Set TESTING environment variable before importing app
os.environ['TESTING'] = 'True'
os.environ['TIMEKPR_SERVER_VERSION'] = 'v0.10'

# Add workspace path to sys.path so we can import app and src.
server_root = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
workspace_root = os.path.dirname(server_root)
sys.path.insert(0, server_root)

# Cursor's bundled Python can resolve `.venv/bin/python` as the base interpreter
# without automatically activating the venv site-packages. Prepend the local venv
# packages explicitly so test collection can import Flask and other dependencies.
venv_site_packages = os.path.join(
    workspace_root,
    '.venv',
    'lib',
    f'python{sys.version_info.major}.{sys.version_info.minor}',
    'site-packages',
)
if os.path.isdir(venv_site_packages):
    sys.path.insert(0, venv_site_packages)

app_module = importlib.import_module('app')
flask_app = app_module.app
run_schema_migrations = app_module.run_schema_migrations
db = importlib.import_module('src.database').db

@pytest.fixture(scope='session')
def app():
    """Return the Flask app configured for isolated in-memory tests."""
    flask_app.config.update({
        'TESTING': True,
        'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
        'SQLALCHEMY_TRACK_MODIFICATIONS': False,
        'WTF_CSRF_ENABLED': False
    })
    return flask_app

@pytest.fixture(scope='function')
def db_session(app):
    """Provide a clean database session for each test function."""
    with app.app_context():
        db.create_all()
        run_schema_migrations()
        yield db.session
        db.session.remove()
        db.drop_all()

@pytest.fixture(scope='function')
def client(app, db_session):
    """Return a test client bound to the isolated test database."""
    return app.test_client()

@pytest.fixture(autouse=True)
def cleanup_background_tasks():
    """Stop the shared background task manager after each test."""
    yield
    app_module.task_manager.stop()
