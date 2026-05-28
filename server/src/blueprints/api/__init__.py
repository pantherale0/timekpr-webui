from src.blueprints.api.devices import api_devices_bp
from src.blueprints.api.users import api_users_bp
from src.blueprints.api.schedule import api_schedule_bp
from src.blueprints.api.blocklists import api_blocklists_bp
from src.blueprints.api.apparmor import api_apparmor_bp
from src.blueprints.api.time import api_time_bp
from src.blueprints.api.tasks import api_tasks_bp

from src.blueprints.api.alerts import api_alerts_bp

__all__ = [
    'api_devices_bp',
    'api_users_bp',
    'api_schedule_bp',
    'api_blocklists_bp',
    'api_apparmor_bp',
    'api_time_bp',
    'api_tasks_bp',
    'api_alerts_bp',
]
