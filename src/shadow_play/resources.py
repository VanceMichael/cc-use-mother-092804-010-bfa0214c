"""教具、场地与志愿者的占用台账。

核心约束：

- 教具以"套"为最小单位，每套有独立编号；同一套教具在重叠时间窗内
  不能被分配给两场活动（双重分配）；
- 时间窗采用半开区间 ``[starts_at, ends_at)``，首尾相接不算冲突；
- 教具损坏后立即退出可分配池，已发生的历史占用保留，未发生的占用
  自动解除并尝试用完好套替换，替换不足时上报缺口；
- 场地同一时间只能承接一场活动；
- 志愿者/讲解按岗位分配，校验语言能力与资质，并防止同一时间被排两场。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from src.shadow_play.errors import (
    InsufficientResource,
    QualificationError,
    ResourceConflict,
    ResourceError,
)


@dataclass(frozen=True)
class TimeWindow:
    """半开时间窗。"""

    starts_at: datetime
    ends_at: datetime

    def __post_init__(self) -> None:
        if self.starts_at >= self.ends_at:
            raise ResourceError("时间窗开始时间必须早于结束时间")

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.starts_at < other.ends_at and other.starts_at < self.ends_at


class ResourceKind(str, Enum):
    KIT = "kit"
    VENUE = "venue"
    PERSON = "person"


class PersonRole(str, Enum):
    INHERITOR = "inheritor"    # 非遗传承人/艺人
    NARRATOR = "narrator"      # 讲解员
    VOLUNTEER = "volunteer"    # 志愿者
    STAFF = "staff"            # 运营人员


class AssignmentFunction(str, Enum):
    """人员在场次中的岗位。"""

    PERFORMER = "performer"                 # 演出艺人
    NARRATION = "narration"                 # 讲解（校验语言）
    WORKSHOP_FACILITATOR = "facilitator"    # 工作坊带领（校验资质）
    ONSITE_SUPPORT = "support"              # 现场协助


@dataclass(frozen=True)
class KitType:
    """教具种类，如"皮影雕刻套"。"""

    code: str
    name: str
    units_per_participant: int = 1  # 每名参与者占用套数（拆组时使用）


@dataclass
class KitUnit:
    """单个教具套。"""

    kit_type: str
    serial: str
    damaged: bool = False
    damage_note: str = ""


@dataclass(frozen=True)
class Venue:
    code: str
    name: str
    capacity: int           # 场地最大容纳人数
    supported: tuple[str, ...] = ()  # 可承接的场次类型，空表示不限


@dataclass
class Person:
    """艺人、讲解、志愿者等可排班人员。"""

    code: str
    name: str
    role: PersonRole
    languages: frozenset[str] = frozenset()
    qualifications: frozenset[str] = frozenset()  # 如 "workshop:carving"


@dataclass(frozen=True)
class Allocation:
    """一次资源占用。"""

    resource_kind: ResourceKind
    resource_id: str
    session_id: str
    window: TimeWindow
    quantity: int = 1
    note: str = ""


@dataclass(frozen=True)
class ReconcileResult:
    """按实际人数核对资源后的结果。"""

    released: tuple[str, ...]    # 释放的教具编号
    allocated: tuple[str, ...]   # 新占用的教具编号
    missing: int                 # 仍缺口的套数


class ResourceRegistry:
    """资源台账：维护教具套、场地、人员及其场次占用。"""

    def __init__(self) -> None:
        self._kit_types: dict[str, KitType] = {}
        self._kit_units: dict[str, KitUnit] = {}
        self._venues: dict[str, Venue] = {}
        self._persons: dict[str, Person] = {}
        self._allocations: list[Allocation] = []

    # ---- 注册 ----

    def register_kit_type(self, kit_type: KitType) -> None:
        if kit_type.code in self._kit_types:
            raise ResourceError(f"教具种类已存在: {kit_type.code}")
        self._kit_types[kit_type.code] = kit_type

    def register_kit_unit(self, unit: KitUnit) -> None:
        if unit.kit_type not in self._kit_types:
            raise ResourceError(f"教具种类未登记: {unit.kit_type}")
        if unit.serial in self._kit_units:
            raise ResourceError(f"教具套编号重复: {unit.serial}")
        self._kit_units[unit.serial] = unit

    def register_venue(self, venue: Venue) -> None:
        if venue.code in self._venues:
            raise ResourceError(f"场地已存在: {venue.code}")
        self._venues[venue.code] = venue

    def register_person(self, person: Person) -> None:
        if person.code in self._persons:
            raise ResourceError(f"人员已登记: {person.code}")
        self._persons[person.code] = person

    # ---- 查询 ----

    def venue(self, code: str) -> Venue:
        try:
            return self._venues[code]
        except KeyError:
            raise ResourceError(f"场地不存在: {code}") from None

    def person(self, code: str) -> Person:
        try:
            return self._persons[code]
        except KeyError:
            raise ResourceError(f"人员不存在: {code}") from None

    def allocations_for(self, session_id: str) -> tuple[Allocation, ...]:
        return tuple(a for a in self._allocations if a.session_id == session_id)

    def _busy_serials(self, kit_type: str, window: TimeWindow) -> set[str]:
        return {
            a.resource_id
            for a in self._allocations
            if a.resource_kind is ResourceKind.KIT
            and a.resource_id in self._kit_units
            and self._kit_units[a.resource_id].kit_type == kit_type
            and a.window.overlaps(window)
        }

    def available_kit_units(self, kit_type: str, window: TimeWindow) -> tuple[str, ...]:
        """给定时间窗内可占用的完好教具套。"""

        if kit_type not in self._kit_types:
            raise ResourceError(f"教具种类未登记: {kit_type}")
        busy = self._busy_serials(kit_type, window)
        return tuple(
            serial
            for serial, unit in self._kit_units.items()
            if unit.kit_type == kit_type and not unit.damaged and serial not in busy
        )

    def water_level(self, kit_type: str, window: TimeWindow) -> tuple[int, int, int]:
        """资源水位：(完好总数, 窗内已占用, 窗内可占用)。"""

        total = sum(
            1
            for unit in self._kit_units.values()
            if unit.kit_type == kit_type and not unit.damaged
        )
        busy = len(self._busy_serials(kit_type, window))
        return total, busy, total - busy

    # ---- 教具占用/释放 ----

    def allocate_kit_units(
        self,
        session_id: str,
        kit_type: str,
        quantity: int,
        window: TimeWindow,
    ) -> tuple[str, ...]:
        """为场次占用指定数量的教具套，不足时抛错且不做任何占用。"""

        if quantity < 0:
            raise ResourceError("占用数量不能为负")
        available = self.available_kit_units(kit_type, window)
        if quantity > len(available):
            total, busy, _ = self.water_level(kit_type, window)
            raise InsufficientResource(
                f"教具 {kit_type} 不足：需要 {quantity} 套，"
                f"窗内可占用 {len(available)} 套（完好 {total}，已占用 {busy}）",
                required=quantity,
                available=len(available),
            )
        chosen = available[:quantity]
        for serial in chosen:
            self._allocations.append(
                Allocation(
                    resource_kind=ResourceKind.KIT,
                    resource_id=serial,
                    session_id=session_id,
                    window=window,
                )
            )
        return tuple(chosen)

    def allocated_kit_units(
        self, session_id: str, kit_type: str, window: TimeWindow | None = None
    ) -> tuple[str, ...]:
        result = []
        for allocation in self._allocations:
            if (
                allocation.resource_kind is ResourceKind.KIT
                and allocation.session_id == session_id
                and self._kit_units[allocation.resource_id].kit_type == kit_type
                and (window is None or allocation.window.overlaps(window))
            ):
                result.append(allocation.resource_id)
        return tuple(result)

    def release_kit_units(
        self, session_id: str, kit_type: str | None = None
    ) -> tuple[str, ...]:
        """释放场次占用的教具（取消场次或人数核减时调用）。"""

        kept: list[Allocation] = []
        released: list[str] = []
        for allocation in self._allocations:
            match = (
                allocation.resource_kind is ResourceKind.KIT
                and allocation.session_id == session_id
                and (
                    kit_type is None
                    or self._kit_units[allocation.resource_id].kit_type == kit_type
                )
            )
            if match:
                released.append(allocation.resource_id)
            else:
                kept.append(allocation)
        self._allocations = kept
        return tuple(released)

    def reconcile_kit_units(
        self,
        session_id: str,
        kit_type: str,
        actual_required: int,
        window: TimeWindow,
    ) -> ReconcileResult:
        """按实际人数核对教具占用。

        - 实际需求少于当前占用：释放多余套；
        - 实际需求多于当前占用：追加占用，窗内资源不足时占用一切可占用套
          并通过 ``missing`` 上报缺口（不抛错，便于现场登记等待）；
        - 已损坏套自动从占用中剔除并优先补位。
        """

        current = set(
            serial
            for serial in self.allocated_kit_units(session_id, kit_type)
            if not self._kit_units[serial].damaged
        )
        # 剔除中途损坏的套，其占用保留为历史需另存，这里解除未发生占用
        self.release_kit_units(session_id, kit_type)

        target = max(actual_required, 0)
        keep = tuple(sorted(current))[:target]
        extra_needed = max(target - len(keep), 0)

        # 重新登记保留的占用
        for serial in keep:
            self._allocations.append(
                Allocation(
                    resource_kind=ResourceKind.KIT,
                    resource_id=serial,
                    session_id=session_id,
                    window=window,
                )
            )
        released = tuple(sorted(current - set(keep)))

        allocated: list[str] = []
        missing = 0
        if extra_needed:
            try:
                allocated = list(
                    self.allocate_kit_units(
                        session_id, kit_type, extra_needed, window
                    )
                )
            except InsufficientResource as exc:
                allocated = list(
                    self.allocate_kit_units(
                        session_id, kit_type, exc.available, window
                    )
                )
                missing = exc.required - exc.available
        return ReconcileResult(
            released=released, allocated=tuple(allocated), missing=missing
        )

    # ---- 损坏与替换 ----

    def report_damage(self, serial: str, at: datetime, *, note: str = "") -> tuple[str, TimeWindow]:
        """登记教具损坏。

        损坏套立即退出可分配池，解除其全部未结束占用并返回受影响场次。
        返回 ``(受影响场次编号, 时间窗)``；若无未结束占用返回空元组。
        补位由场次层凭实际人数调用 :meth:`reconcile_kit_units` 完成。
        """

        try:
            unit = self._kit_units[serial]
        except KeyError:
            raise ResourceError(f"教具不存在: {serial}") from None
        unit.damaged = True
        unit.damage_note = note

        affected: dict[str, TimeWindow] = {}
        kept: list[Allocation] = []
        for allocation in self._allocations:
            if (
                allocation.resource_kind is ResourceKind.KIT
                and allocation.resource_id == serial
                and allocation.window.ends_at > at
            ):
                affected.setdefault(allocation.session_id, allocation.window)
            else:
                kept.append(allocation)
        self._allocations = kept
        return tuple(affected.items())

    # ---- 场地 ----

    def allocate_venue(
        self, session_id: str, venue_code: str, window: TimeWindow
    ) -> None:
        venue = self.venue(venue_code)
        for allocation in self._allocations:
            if (
                allocation.resource_kind is ResourceKind.VENUE
                and allocation.resource_id == venue_code
                and allocation.window.overlaps(window)
            ):
                raise ResourceConflict(
                    f"场地 {venue_code} 在该时段已被场次 {allocation.session_id} 占用"
                )
        self._allocations.append(
            Allocation(
                resource_kind=ResourceKind.VENUE,
                resource_id=venue_code,
                session_id=session_id,
                window=window,
                note=venue.name,
            )
        )

    def release_session(self, session_id: str, *, now: datetime) -> tuple[Allocation, ...]:
        """释放场次全部未发生占用（取消未发生场次时使用）。

        已结束（``ends_at <= now``）的占用作为历史保留。
        """

        kept: list[Allocation] = []
        released: list[Allocation] = []
        for allocation in self._allocations:
            if allocation.session_id == session_id and allocation.window.ends_at > now:
                released.append(allocation)
            else:
                kept.append(allocation)
        self._allocations = kept
        return tuple(released)

    # ---- 人员排班 ----

    def assign_person(
        self,
        session_id: str,
        person_code: str,
        function: AssignmentFunction,
        window: TimeWindow,
        *,
        required_languages: frozenset[str] | None = None,
        required_qualification: str | None = None,
    ) -> None:
        person = self.person(person_code)
        required_languages = required_languages or frozenset()
        missing_languages = required_languages - person.languages
        if missing_languages:
            raise QualificationError(
                f"{person.name} 缺少讲解语言: {sorted(missing_languages)}"
            )
        if required_qualification and required_qualification not in person.qualifications:
            raise QualificationError(
                f"{person.name} 缺少岗位资质: {required_qualification}"
            )
        for allocation in self._allocations:
            if (
                allocation.resource_kind is ResourceKind.PERSON
                and allocation.resource_id == person_code
                and allocation.window.overlaps(window)
            ):
                raise ResourceConflict(
                    f"{person.name} 在该时段已被场次 {allocation.session_id} 排班"
                )
        self._allocations.append(
            Allocation(
                resource_kind=ResourceKind.PERSON,
                resource_id=person_code,
                session_id=session_id,
                window=window,
                note=function.value,
            )
        )

    def staff_for(self, session_id: str) -> tuple[Allocation, ...]:
        return tuple(
            a
            for a in self._allocations
            if a.session_id == session_id and a.resource_kind is ResourceKind.PERSON
        )
