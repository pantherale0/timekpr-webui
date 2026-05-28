from src.blueprints.ui import (
    ui_auth_bp,
    ui_dashboard_bp,
    ui_schedule_bp,
    ui_apparmor_bp,
)
from src.blueprints.api import (
    api_devices_bp,
    api_users_bp,
    api_schedule_bp,
    api_blocklists_bp,
    api_apparmor_bp,
    api_time_bp,
    api_tasks_bp,
)
from src.blueprints.websocket import websocket_bp

__all__ = [
    'ui_auth_bp',
    'ui_dashboard_bp',
    'ui_schedule_bp',
    'ui_apparmor_bp',
    'api_devices_bp',
    'api_users_bp',
    'api_schedule_bp',
    'api_blocklists_bp',
    'api_apparmor_bp',
    'api_time_bp',
    'api_tasks_bp',
    'websocket_bp',
]
