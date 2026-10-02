"""非遗皮影月下剧场文化活动后台的领域模型。"""

from __future__ import annotations

from .errors import (
    CapacityExceeded,
    ContentNotPublishable,
    IllegalTransition,
    NotFound,
    QualificationMismatch,
    ResourceConflict,
    ShadowPlayError,
)
from .people import Artisan, ArtisanRole, Participant, Person, Volunteer
from .content import (
    AIGenerated,
    Artwork,
    ContentId,
    ContentKind,
    ContentVersion,
    License,
    LicenseStatus,
    Program,
    Usage,
    VersionStatus,
)
from .resources import (
    EquipmentKind,
    EquipmentPool,
    EquipmentState,
    EquipmentUnit,
    Venue,
    VolunteerRequirement,
)
from .sessions import ContentRef, Session, SessionKind, SessionStatus
from .bookings import Booking, BookingStatus, CheckIn
from .office import BackOffice, ResourceLevel, SessionOverview

__all__ = [
    "AIGenerated",
    "Artisan",
    "ArtisanRole",
    "Artwork",
    "BackOffice",
    "Booking",
    "BookingStatus",
    "CapacityExceeded",
    "CheckIn",
    "ContentId",
    "ContentKind",
    "ContentNotPublishable",
    "ContentRef",
    "ContentVersion",
    "EquipmentKind",
    "EquipmentPool",
    "EquipmentState",
    "EquipmentUnit",
    "IllegalTransition",
    "License",
    "LicenseStatus",
    "NotFound",
    "Person",
    "Program",
    "ResourceLevel",
    "Session",
    "SessionKind",
    "SessionOverview",
    "SessionStatus",
    "Usage",
    "Venue",
    "VersionStatus",
    "Volunteer",
    "VolunteerRequirement",
    "Participant",
    "ShadowPlayError",
]
