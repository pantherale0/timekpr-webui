from src.models.core import db, Settings
from src.models.device import (
    USAGE_SNAPSHOT_DATE_KEY,
    coerce_time_spent_day,
    coerce_time_left_day,
    utc_today,
    utc_date_of,
    mapping_usage_snapshot_date,
    mapping_usage_is_for_day,
    stamp_usage_snapshot,
    ensure_offline_mapping_day_snapshot,
    get_mapping_time_spent_for_day,
    get_mapping_time_left_for_day,
    AgentDevice,
    AgentAlert,
    PendingCommand,
    DeviceScreenshotSettings,
    DeviceScreenshot,
)
from src.models.user import (
    ManagedUser,
    ManagedUserDeviceMap,
    UserTimeUsage,
    UserWeeklySchedule,
    UserDailyTimeInterval,
)
from src.models.policy import (
    MappingAndroidDevicePolicy,
    AndroidForceInstalledApp,
    MappingLinuxDevicePolicy,
    AppPolicy,
    AppPolicyRule,
    ManagedUserAppPolicyAssignment,
)
from src.models.blocklist import (
    BlocklistSource,
    BlocklistDomain,
    ManagedUserBlocklistAssignment,
)
from src.models.apparmor import (
    AppArmorRule,
    AppUsageHistory,
    ApplicationIcon,
    DeviceInstalledApplication,
)
from src.models.history import (
    WebHistory,
    VideoHistory,
    YoutubeHistory,
    UserOnlineAccount,
    ChannelBlockRule,
    AiPromptLog,
    AiSessionLog,
)
from src.models.household import (
    Household,
    ParentAccount,
    HouseholdParentMembership,
    HouseholdInvite,
    ManagedUserShare,
    ManagedUserShareInvite,
)
from src.models.approval import (
    ApprovalRequest,
    PolicyApprovalGrant,
    MappingApprovalSettings,
)
