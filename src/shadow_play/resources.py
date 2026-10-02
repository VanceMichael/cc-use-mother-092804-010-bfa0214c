"""场地、教具库存与志愿者资质要求。

教具按"个体"管理（每套一个编号），而不是只记总数：占用、释放、损坏都落到
具体编号上，跨场次排程时同一套教具不可能被分配两次。设备损坏后退出可用池，
维修归位后才能再次占用。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .people import Volunteer


class EquipmentState(str, Enum):
    AVAILABLE = "可用"
    IN_USE = "占用中"
    DAMAGED = "已损坏"


@dataclass(frozen=True)
class EquipmentKind:
    id: str
    name: str
    per_person: bool = True   # True=每人一套（如制作包）；False=每组一套（如灯架）

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("教具类别编号与名称不能为空")


@dataclass
class EquipmentUnit:
    """一套可独立分配的教具，如"皮影制作包 #07"。"""

    id: str
    kind_id: str
    state: EquipmentState = EquipmentState.AVAILABLE
    damage_note: str = ""

    @property
    def usable(self) -> bool:
        return self.state is not EquipmentState.DAMAGED


class EquipmentPool:
    """教具库存：登记、损坏/修复、按类别统计可用量。"""

    def __init__(self) -> None:
        self._kinds: dict[str, EquipmentKind] = {}
        self._units: dict[str, EquipmentUnit] = {}

    def register_kind(self, kind: EquipmentKind) -> None:
        if kind.id in self._kinds:
            raise ValueError("教具类别重复")
        self._kinds[kind.id] = kind

    def add_unit(self, unit: EquipmentUnit) -> None:
        if unit.kind_id not in self._kinds:
            raise ValueError("教具类别未登记")
        if unit.id in self._units:
            raise ValueError("教具编号重复")
        self._units[unit.id] = unit

    def unit(self, unit_id: str) -> EquipmentUnit:
        return self._units[unit_id]

    def iter_units(self) -> list[EquipmentUnit]:
        return list(self._units.values())

    def units_of_kind(self, kind_id: str) -> list[EquipmentUnit]:
        return [u for u in self._units.values() if u.kind_id == kind_id]

    def iter_kinds(self) -> list[EquipmentKind]:
        return list(self._kinds.values())

    def kind(self, kind_id: str) -> EquipmentKind:
        return self._kinds[kind_id]

    def total(self, kind_id: str) -> int:
        return sum(1 for u in self._units.values() if u.kind_id == kind_id)

    def usable(self, kind_id: str) -> int:
        return sum(
            1
            for u in self._units.values()
            if u.kind_id == kind_id and u.usable
        )

    def available(self, kind_id: str, blocked: frozenset[str]) -> int:
        """未损坏且未被排程占用（blocked 为占用编号集合）的数量。"""
        return sum(
            1
            for u in self._units.values()
            if u.kind_id == kind_id and u.usable and u.id not in blocked
        )

    def mark_damaged(self, unit_id: str, note: str) -> None:
        unit = self._units[unit_id]
        unit.state = EquipmentState.DAMAGED
        unit.damage_note = note

    def repair(self, unit_id: str) -> None:
        unit = self._units[unit_id]
        unit.state = EquipmentState.AVAILABLE
        unit.damage_note = ""


@dataclass(frozen=True)
class Venue:
    """场地。工作坊按组开课，capacity 为单场可承载的最大总人数。"""

    id: str
    name: str
    capacity: int
    supports_workshop: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("场地编号与名称不能为空")
        if self.capacity < 1:
            raise ValueError("场地容量必须大于 0")


@dataclass(frozen=True)
class VolunteerRequirement:
    """场次对志愿者的语言、技能与人数要求。"""

    languages: frozenset[str] = frozenset()
    skills: frozenset[str] = frozenset()
    minimum: int = 1

    def satisfied_by(self, volunteers: list[Volunteer]) -> list[str]:
        """返回不达标原因清单；空清单表示满足。"""
        active = [v for v in volunteers if v.active]
        problems: list[str] = []
        if len(active) < self.minimum:
            problems.append(
                f"志愿者人数不足：需 {self.minimum} 人，实际在岗 {len(active)} 人"
            )
        qualified = [v for v in active if v.meets(self.languages, self.skills)]
        if len(qualified) < self.minimum:
            if self.languages:
                problems.append(
                    "讲解语言资质不满足：" + "、".join(sorted(self.languages))
                )
            if self.skills:
                problems.append(
                    "互动技能资质不满足：" + "、".join(sorted(self.skills))
                )
        return problems
