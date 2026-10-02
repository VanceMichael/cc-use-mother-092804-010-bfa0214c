"""演出/展陈/工作坊场次。

场次一旦排定，就固定所依据的内容版本（content_id + 版本号）与责任人，形成
可追溯的"内容依据"。教具分配以个体编号落在场次上，供排程期做跨场次互斥。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .people import Person, Volunteer
from .resources import Venue, VolunteerRequirement


class SessionKind(str, Enum):
    PERFORMANCE = "公开演出"
    EXHIBITION = "主题展陈"
    WORKSHOP = "互动工作坊"


class SessionStatus(str, Enum):
    SCHEDULED = "未发生"
    COMPLETED = "已完成"
    CANCELLED = "已取消"


@dataclass(frozen=True)
class ContentRef:
    """场次对一个已确认内容版本的固定引用。"""

    content_id: str
    version: int
    title: str  # 快照标题，内容归档后历史场次仍可读


@dataclass
class Session:
    id: str
    kind: SessionKind
    venue: Venue
    starts_at: datetime
    language: str                       # 讲解语言
    owner: Person                       # 责任人
    program_items: tuple[ContentRef, ...]
    duration_minutes: int = 60
    groups: int = 1                     # 工作坊拆组数量
    volunteer_requirement: VolunteerRequirement | None = None
    volunteers: list[Volunteer] = field(default_factory=list)
    # 教具计划：类别编号 -> 固定数量(int) / "person"(按实际人数) / "group"(按组数)
    equipment_plan: dict[str, int | str] = field(default_factory=dict)
    requirements: dict[str, int] = field(default_factory=dict)  # 类别 -> 当前需求
    allocations: dict[str, list[str]] = field(default_factory=dict)  # 类别 -> 教具编号
    status: SessionStatus = SessionStatus.SCHEDULED

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("场次编号不能为空")
        if not self.program_items:
            raise ValueError("场次必须有内容依据")
        if self.groups < 1:
            raise ValueError("拆组数量至少为 1")
        if self.is_workshop and not self.venue.supports_workshop:
            raise ValueError("工作坊必须安排在支持互动的场地")

    @property
    def is_workshop(self) -> bool:
        return self.kind is SessionKind.WORKSHOP

    @property
    def required_capacity(self) -> int:
        return self.venue.capacity

    def allocated_units(self) -> frozenset[str]:
        units: set[str] = set()
        for ids in self.allocations.values():
            units.update(ids)
        return frozenset(units)

    def planned_count(self, kind_id: str) -> int:
        return self.requirements.get(kind_id, 0)

    def allocated_count(self, kind_id: str) -> int:
        return len(self.allocations.get(kind_id, ()))
