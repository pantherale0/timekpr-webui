import logging
import os
import secrets
import threading
from datetime import datetime, timezone
import pytz
from flask import Flask, url_for

# Initialize WebSocket support
from flask_sock import Sock
from flask_migrate import Migrate

# Import DB and models for registration
from src.database import db, Settings, AgentDevice
from src.task_manager import BackgroundTaskManager
from src.oidc_helper import OIDCHelper

# Import helpers for direct exposure and backwards-compatibility
from src.helpers import (
    _resolve_local_timezone,
    _env_flag_enabled,
    inject_oidc_status,
    localtime_filter,
    inject_timezone,
    TIMEZONE_STR,
    LOCAL_TIMEZONE,
    ADMIN_USERNAME,
)
from src.blocklists_manager import _get_blocklist_sources
from src.blueprints.websocket import ws_agent_handler

_LOGGER = logging.getLogger(__name__)

# Version metadata
__version__ = os.environ.get("TIMEKPR_SERVER_VERSION", "v0.0.0-dev")

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.urandom(24)
app.config['SQLALCHEMY_DATABASE_URI'] = os.environ.get('DATABASE_URL') or os.environ.get('SQLALCHEMY_DATABASE_URI') or 'sqlite:///timekpr.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Initialize extensions
db.init_app(app)
migrate = Migrate(app, db)
sock = Sock(app)

# Configure logging

formatter = logging.Formatter(
    '%(asctime)s %(levelname)s: %(message)s '
    '[in %(pathname)s:%(lineno)d]'
    )

root_logger = logging.getLogger()
root_logger.setLevel(logging.DEBUG)

if os.environ.get("DEBUG", "0") == "1":
    file_handler = logging.FileHandler(os.path.join(app.instance_path, "debug.log"))
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
stream_handler.setFormatter(formatter)
root_logger.addHandler(stream_handler)

# Initialize background task manager
task_manager = BackgroundTaskManager(
    refresh_external_blocklists=_env_flag_enabled('TIMEKPR_TASKS_REFRESH_EXTERNAL', True),
    update_user_data=_env_flag_enabled('TIMEKPR_TASKS_UPDATE_USER_DATA', True),
    sync_domain_policies=_env_flag_enabled('TIMEKPR_TASKS_SYNC_DOMAIN_POLICIES', True),
    deliver_pending_alerts=_env_flag_enabled('TIMEKPR_TASKS_DELIVER_ALERTS', True),
)
task_manager.init_app(app)
_runtime_init_lock = threading.Lock()
# Global state tracking
RUNTIME_STATE = {'initialized': False}

# Initialize OIDC helper
oidc_helper = OIDCHelper()

# Register template filters and context processors from helpers
app.context_processor(inject_oidc_status)
app.template_filter('localtime')(localtime_filter)
app.context_processor(inject_timezone)

# Import and register blueprints
from src.blueprints import (
    ui_auth_bp,
    ui_dashboard_bp,
    ui_schedule_bp,
    ui_apparmor_bp,
    api_devices_bp,
    api_users_bp,
    api_schedule_bp,
    api_blocklists_bp,
    api_apparmor_bp,
    api_time_bp,
    api_tasks_bp,
    api_alerts_bp,
    websocket_bp,
)

app.register_blueprint(ui_auth_bp)
app.register_blueprint(ui_dashboard_bp)
app.register_blueprint(ui_schedule_bp)
app.register_blueprint(ui_apparmor_bp)
app.register_blueprint(api_devices_bp)
app.register_blueprint(api_users_bp)
app.register_blueprint(api_schedule_bp)
app.register_blueprint(api_blocklists_bp)
app.register_blueprint(api_apparmor_bp)
app.register_blueprint(api_time_bp)
app.register_blueprint(api_tasks_bp)
app.register_blueprint(api_alerts_bp)
app.register_blueprint(websocket_bp)

# Register WebSocket endpoint via Flask-Sock
sock.route('/ws')(ws_agent_handler)


def fallback_handler(error, endpoint, values):
    """Fallback handler to seamlessly map unqualified url_for to modular Blueprint endpoints."""
    if '.' not in endpoint:
        for bp_name in app.blueprints:
            bp_endpoint = f"{bp_name}.{endpoint}"
            try:
                return url_for(bp_endpoint, **values)
            except Exception:
                continue
    raise error


app.url_build_error_handlers.append(fallback_handler)


def migrate_data_sqlite_to_pg(sqlite_db_path):
    """Migrates all data from an existing SQLite database to the current PostgreSQL database."""
    from sqlalchemy import create_engine, select
    from sqlalchemy import inspect as sqla_inspect
    from src.database import (
        Settings as DbSettings,
        AgentDevice as DbAgentDevice,
        ManagedUser as DbManagedUser,
        ManagedUserDeviceMap as DbManagedUserDeviceMap,
        UserWeeklySchedule as DbUserWeeklySchedule,
        UserDailyTimeInterval as DbUserDailyTimeInterval,
        UserTimeUsage as DbUserTimeUsage,
        BlocklistSource as DbBlocklistSource,
        BlocklistDomain as DbBlocklistDomain,
        ManagedUserBlocklistAssignment as DbManagedUserBlocklistAssignment,
        AgentAlert as DbAgentAlert,
        AppArmorRule as DbAppArmorRule,
        AppUsageHistory as DbAppUsageHistory
    )
    
    _LOGGER.info(f"Starting database migration from SQLite ({sqlite_db_path}) to PostgreSQL...")
    
    sqlite_uri = f"sqlite:///{sqlite_db_path}"
    sqlite_engine = create_engine(sqlite_uri)
    
    try:
        sqlite_inspector = sqla_inspect(sqlite_engine)
        sqlite_tables = sqlite_inspector.get_table_names()
        if not sqlite_tables:
            _LOGGER.info("SQLite database is empty or has no tables. Skipping data migration.")
            sqlite_engine.dispose()
            return
            
        models_to_migrate = [
            DbSettings,
            DbAgentDevice,
            DbManagedUser,
            DbManagedUserDeviceMap,
            DbUserWeeklySchedule,
            DbUserDailyTimeInterval,
            DbUserTimeUsage,
            DbBlocklistSource,
            DbBlockDomain := DbBlocklistDomain,
            DbManagedUserBlocklistAssignment,
            DbAgentAlert,
            DbAppArmorRule,
            DbAppUsageHistory
        ]
        
        with db.session.begin():
            for model in models_to_migrate:
                table_name = model.__tablename__
                if table_name not in sqlite_tables:
                    _LOGGER.info(f"Table '{table_name}' does not exist in SQLite database. Skipping.")
                    continue
                    
                _LOGGER.info(f"Migrating table '{table_name}'...")
                
                batch_size = 10000
                total_inserted = 0
                
                with sqlite_engine.connect() as sqlite_conn:
                    columns = [c for c in model.__table__.columns]
                    query = select(*columns)
                    result = sqlite_conn.execute(query)
                    
                    while True:
                        rows_chunk = result.fetchmany(batch_size)
                        if not rows_chunk:
                            break
                        
                        rows = [dict(row._mapping) for row in rows_chunk]
                        db.session.execute(model.__table__.insert(), rows)
                        total_inserted += len(rows)
                        
                if total_inserted == 0:
                    _LOGGER.info(f"Table '{table_name}' is empty. Skipping.")
                else:
                    _LOGGER.info(f"Successfully migrated {total_inserted} rows for '{table_name}'.")
                
        _LOGGER.info("Database migration completed successfully!")
        
        try:
            sqlite_engine.dispose()
            if os.path.exists(sqlite_db_path):
                os.remove(sqlite_db_path)
                _LOGGER.info(f"Deleted old SQLite database file: {sqlite_db_path}")
        except Exception as e:
            _LOGGER.warning(f"Warning: Failed to delete old SQLite database file: {e}")
            
    except Exception as e:
        _LOGGER.error(f"CRITICAL: SQLite to PostgreSQL migration failed: {e}")
        db.session.rollback()
        sqlite_engine.dispose()
        raise e


def initialize_runtime(start_background_tasks=False):
    """Initialize the database and start background tasks."""
    from flask_migrate import stamp, upgrade
    _LOGGER.info("Runtime initialization started")
    if os.environ.get('TESTING'):
        return

    with _runtime_init_lock:
        if not RUNTIME_STATE['initialized']:
            with app.app_context():
                db_uri = app.config.get('SQLALCHEMY_DATABASE_URI', '')
                is_pg = db_uri.startswith('postgresql://') or db_uri.startswith('postgresql+psycopg2://')
                
                # Use absolute path for migrations directory to avoid CWD issues
                migrations_dir = os.path.join(app.root_path, 'migrations')
                migrations_exist = os.path.isdir(migrations_dir)
                
                sqlite_migrated = False
                if is_pg:
                    possible_sqlite_paths = [
                        os.path.join(app.instance_path, 'timekpr.db'),
                        os.path.join(app.root_path, 'timekpr.db'),
                        'instance/timekpr.db',
                        'timekpr.db'
                    ]
                    for path in possible_sqlite_paths:
                        if os.path.exists(path):
                            _LOGGER.info(f"Found SQLite DB at {path}. Initiating migration...")
                            db.create_all()
                            try:
                                migrate_data_sqlite_to_pg(path)
                                sqlite_migrated = True
                                if migrations_exist:
                                    try:
                                        stamp(directory=migrations_dir)
                                        _LOGGER.info("Stamped PostgreSQL database migration state as head.")
                                    except Exception as stamp_err:
                                        _LOGGER.warning(f"Warning: Failed to stamp PostgreSQL: {stamp_err}")
                            except Exception as mig_err:
                                _LOGGER.error(f"Error during SQLite to PostgreSQL migration: {mig_err}")
                            break

                if not sqlite_migrated:
                    if migrations_exist:
                        _LOGGER.info(f"Ensuring database is up to date (dir: {migrations_dir})...")
                        try:
                            # Try to upgrade first (handles existing databases with migrations)
                            upgrade(directory=migrations_dir)
                            _LOGGER.info("Database migrations applied successfully")
                        except Exception as e:
                            _LOGGER.info(f"Database upgrade() failed or database is new: {e}")
                            # Fallback: ensure tables exist and stamp as head
                            try:
                                db.create_all()
                                try:
                                    stamp(directory=migrations_dir)
                                    _LOGGER.info("Database stamped as head revision after create_all()")
                                except Exception as stamp_err:
                                    _LOGGER.warning(f"Could not stamp database: {stamp_err}")
                            except Exception as create_err:
                                _LOGGER.error(f"Failure during db.create_all() fallback: {create_err}")
                    else:
                        _LOGGER.info("Migrations directory missing. Creating database tables directly...")
                        db.create_all()

                # Final safety measure: Ensure all tables defined in models exist.
                # This catches cases where migrations are 'current' but tables are missing.
                db.create_all()

                try:
                    if not Settings.get_value('admin_password_hash', None) and not Settings.get_value('admin_password', None):
                        Settings.set_admin_password('admin')
                        _LOGGER.info("Admin password initialized")
                except Exception as e:
                    _LOGGER.warning(f"Warning: Could not initialize admin password: {e}")

            RUNTIME_STATE['initialized'] = True
            _LOGGER.info("Runtime initialization completed")

    if start_background_tasks:
        task_manager.start()
        _LOGGER.info("Background tasks started automatically")


if not os.environ.get('TESTING'):
    initialize_runtime(start_background_tasks=_env_flag_enabled('TIMEKPR_ENABLE_BACKGROUND_TASKS'))

if __name__ == '__main__':
    initialize_runtime(start_background_tasks=True)
    debug = bool(int(os.environ.get("DEBUG", "0")))
    app.run(host='0.0.0.0', port=5000, debug=debug, use_reloader=debug)
