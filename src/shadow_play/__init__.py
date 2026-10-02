"""月下剧场活动后台领域模型。

模块划分：

- errors：领域错误
- content：演绎内容版本与非遗传承人授权
- resources：教具、场地、志愿者的占用台账
- sessions：场次生命周期、预约、签到与现场资源调整
- views：负责人场次视图
"""

from src.shadow_play.content import (
    AiProvenance,
    Authorization,
    ContentKind,
    ContentRegistry,
    ContentVersion,
    GrantStatus,
    RightHolder,
    VersionStatus,
)
from src.shadow_play.resources import (
    Allocation,
    AssignmentFunction,
    KitType,
    KitUnit,
    Person,
    PersonRole,
    ReconcileResult,
    ResourceKind,
    ResourceRegistry,
    TimeWindow,
    Venue,
)
from src.shadow_play.sessions import (
    Booking,
    BookingStatus,
    CheckInResult,
    ProgramSystem,
    Session,
    SessionStatus,
    SessionType,
)
from src.shadow_play.views import SessionView, SessionViewBuilder

__all__ = [
    "AiProvenance",
    "Allocation",
    "AssignmentFunction",
    "Authorization",
    "Booking",
    "BookingStatus",
    "CheckInResult",
    "ContentKind",
    "ContentRegistry",
    "ContentVersion",
    "GrantStatus",
    "KitType",
    "KitUnit",
    "Person",
    "PersonRole",
    "ProgramSystem",
    "ReconcileResult",
    "ResourceKind",
    "ResourceRegistry",
    "RightHolder",
    "Session",
    "SessionStatus",
    "SessionType",
    "SessionView",
    "SessionViewBuilder",
    "TimeWindow",
    "Venue",
    "VersionStatus",
]
