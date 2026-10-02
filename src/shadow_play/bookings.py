"""参与者预约与签到。

签到结果以凭证形式保存：同一预约重复签到直接返回原结果，不会重复占用名额或
教具。取消只对未发生场次的预约生效；场次完成后预约作为历史保留。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .people import Participant
from .sessions import Session, SessionStatus

# 迟到宽限：开场后超过此时长仍可签到，但记为迟到
LATE_GRACE = timedelta(minutes=10)


class BookingStatus(str, Enum):
    RESERVED = "已预约"
    CHECKED_IN = "已签到"
    NO_SHOW = "未到场"
    CANCELLED = "已取消"


@dataclass(frozen=True)
class CheckIn:
    """一次签到的固定结果；重复签到返回同一对象（幂等）。"""

    booking_id: str
    at: datetime
    late: bool
    receipt: str


@dataclass
class Booking:
    id: str
    session: Session
    participant: Participant
    party_size: int = 1
    status: BookingStatus = BookingStatus.RESERVED
    check_in: CheckIn | None = None

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("预约编号不能为空")
        if self.party_size < 1:
            raise ValueError("预约人数至少为 1")

    @property
    def active(self) -> bool:
        """仍占用名额的预约（含已签到与等待到场）。"""
        return self.status in (BookingStatus.RESERVED, BookingStatus.CHECKED_IN)


def make_receipt(booking_id: str, at: datetime) -> str:
    raw = f"{booking_id}|{at.isoformat()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def is_late(session: Session, at: datetime) -> bool:
    return at > session.starts_at + LATE_GRACE
