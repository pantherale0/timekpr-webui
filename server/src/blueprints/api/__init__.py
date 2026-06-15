from src.blueprints.api.devices import api_devices_bp
from src.blueprints.api.users import api_users_bp
from src.blueprints.api.schedule import api_schedule_bp
from src.blueprints.api.blocklists import api_blocklists_bp
from src.blueprints.api.time import api_time_bp
from src.blueprints.api.tasks import api_tasks_bp

from src.blueprints.api.alerts import api_alerts_bp
from src.blueprints.api.pairing import api_pairing_bp
from src.blueprints.api.dashboard import api_dashboard_bp
from src.blueprints.api.installed_apps import api_installed_apps_bp
from src.blueprints.api.approvals import api_approvals_bp
from src.blueprints.api.android_device_policy import api_android_device_policy_bp
from src.blueprints.api.linux_device_policy import api_linux_device_policy_bp
from src.blueprints.api.nintendo import api_nintendo_bp
from src.blueprints.api.xbox import api_xbox_bp
from src.blueprints.api.screenshots import api_screenshots_bp
from src.blueprints.api.youtube import api_youtube_bp

__all__ = [
    'api_devices_bp',
    'api_users_bp',
    'api_schedule_bp',
    'api_blocklists_bp',
    'api_time_bp',
    'api_tasks_bp',
    'api_alerts_bp',
    'api_pairing_bp',
    'api_dashboard_bp',
    'api_installed_apps_bp',
    'api_approvals_bp',
    'api_android_device_policy_bp',
    'api_linux_device_policy_bp',
    'api_nintendo_bp',
    'api_xbox_bp',
    'api_screenshots_bp',
    'api_youtube_bp',
]
