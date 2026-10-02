"""文化活动后台总控：把内容授权、场次排程、教具库存、预约签到串起来。

关键规则的实现位置：
- 公开闸门 `schedule_session`：只接受已确认版本 + 当日有效授权 + AI 来源/范围
  合规 + 志愿者资质达标，未获许可素材在此被挡住；
- 教具唯一分配 `_reconcile_resources`：以教具个体编号做跨场次互斥，按实际
  人数（拆组、迟到）重新计算占用与释放，任何一套教具都不会双重分配；
- 设备损坏 `report_damage`：损坏个体退出并立即触发重算，不足即暴露缺口；
- 临时加场 `schedule_session`：同样走闸门与互斥校验；
- 重复签到 `check_in`：返回首次的原凭证；
- 取消 `cancel_session`：只影响未发生场次，已完成演出与授权历史原样保留。
"""

from __future__ import annotations

from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .bookings import (
    Booking,
    BookingStatus,
    CheckIn,
    is_late,
    make_receipt,
)
from .content import (
    ContentVersion,
    Program,
    Usage,
)
from .errors import (
    CapacityExceeded,
    IllegalTransition,
    NotFound,
    QualificationMismatch,
    ResourceConflict,
)
from .people import Person, Volunteer
from .resources import (
    EquipmentPool,
    EquipmentState,
    Venue,
    VolunteerRequirement,
)
from .sessions import (
    ContentRef,
    Session,
    SessionKind,
    SessionStatus,
)


class ResourceLevel(str, Enum):
    """负责人看到的资源水位。"""

    FULL = "充足"
    TIGHT = "偏紧"        # 已占用达到库存可用量的 80%
    SHORTAGE = "有缺口"   # 计划需求无法满足


# 库存占用达到该比例即提示"偏紧"
TIGHT_RATIO = 0.8


@dataclass
class _KindWater:
    kind_id: str
    name: str
    planned: int
    allocated: int
    usable_total: int
    available_now: int
    level: ResourceLevel


@dataclass
class SessionOverview:
    """负责人查看场次时一次看到的四件事 + 场次状态。"""

    session: Session
    content_basis: list[str]            # 内容依据：版本、状态、AI 标注
    pending_licenses: list[str]         # 待补/失效授权
    water: list[_KindWater]             # 资源水位
    owner: str                          # 责任人
    volunteer_issues: list[str]

    def as_dict(self) -> dict:
        return {
            "场次": self.session.id,
            "类型": self.session.kind.value,
            "状态": self.session.status.value,
            "讲解语言": self.session.language,
            "责任人": self.owner,
            "内容依据": self.content_basis,
            "待补授权": self.pending_licenses,
            "资源水位": [
                {
                    "教具": w.name,
                    "需要": w.planned,
                    "已分配": w.allocated,
                    "库存可用": w.usable_total,
                    "当前空闲": w.available_now,
                    "水位": w.level.value,
                }
                for w in self.water
            ],
            "志愿者问题": self.volunteer_issues,
        }


class BackOffice:
    def __init__(self, program: Program, pool: EquipmentPool) -> None:
        self.program = program
        self.pool = pool
        self.sessions: dict[str, Session] = {}
        self.bookings: dict[str, Booking] = {}
        self._session_bookings: dict[str, list[Booking]] = {}
        self.owners: dict[str, Person] = {}
        self.volunteers: dict[str, Volunteer] = {}

    # ------------------------------------------------------------------ #
    # 人员登记
    # ------------------------------------------------------------------ #
    def register_volunteer(self, volunteer: Volunteer) -> None:
        if volunteer.id in self.volunteers:
            raise ValueError("志愿者已登记")
        self.volunteers[volunteer.id] = volunteer

    # ------------------------------------------------------------------ #
    # 排程：公开闸门
    # ------------------------------------------------------------------ #
    def schedule_session(
        self,
        session_id: str,
        kind: SessionKind,
        venue: Venue,
        starts_at: datetime,
        language: str,
        owner: Person,
        content_refs: list[tuple[str, int | None]],
        *,
        duration_minutes: int = 60,
        groups: int = 1,
        volunteer_requirement: VolunteerRequirement | None = None,
        volunteers: list[Volunteer] | None = None,
        equipment_plan: dict[str, int | str] | None = None,
    ) -> Session:
        """新建场次。

        content_refs 为 (内容编号, 版本号|None=最新已确认)；
        equipment_plan 为 类别编号 -> 固定数量 / ``"person"``（按实际人数）/
        ``"group"``（按拆组数）。
        """
        if session_id in self.sessions:
            raise ValueError("场次编号重复")

        day = starts_at.date()
        usage = _usage_for(kind)
        refs: list[ContentRef] = []
        for content_id, number in content_refs:
            version = self._resolve_version(content_id, number)
            # 闸门一：版本确认、授权有效、AI 来源与适用范围
            self.program.ensure_publishable(version, usage, day)
            refs.append(
                ContentRef(
                    content_id=content_id,
                    version=version.version,
                    title=version.title,
                )
            )

        chosen = list(volunteers or [])
        for v in chosen:
            if v.id not in self.volunteers:
                self.register_volunteer(v)
        requirement = volunteer_requirement or VolunteerRequirement(minimum=1)
        # 闸门二：志愿者资质（语言/技能/人数）
        problems = requirement.satisfied_by(chosen)
        if problems:
            raise QualificationMismatch(problems)

        # 闸门三：场地与志愿者在时间重叠场次上的使用边界
        candidate = _CandidateSession(venue=venue, starts_at=starts_at,
                                      duration_minutes=duration_minutes)
        for other in self.sessions.values():
            if other.status is not SessionStatus.SCHEDULED:
                continue
            if self._ranges_overlap(candidate, other):
                if other.venue.id == venue.id:
                    raise ResourceConflict(
                        f"场地 {venue.name} 在 {other.id} 场次已被占用，时间重叠"
                    )
                clash = sorted(
                    {v.id for v in chosen} & {v.id for v in other.volunteers}
                )
                if clash:
                    raise ResourceConflict(
                        f"志愿者 {', '.join(clash)} 已分配给重叠场次 {other.id}"
                    )

        plan = dict(equipment_plan or {})
        if kind is not SessionKind.WORKSHOP:
            bad = [k for k, s in plan.items() if isinstance(s, str)]
            if bad:
                raise ValueError("按人/按组的教具计划只能用于互动工作坊")
        for kind_id, spec in plan.items():
            try:
                self.pool.kind(kind_id)
            except KeyError:
                raise NotFound(f"教具类别未登记: {kind_id}") from None
            if isinstance(spec, str) and spec not in ("person", "group"):
                raise ValueError('教具计划取值只能是整数、"person" 或 "group"')
            if isinstance(spec, bool) or (isinstance(spec, int) and spec < 0):
                raise ValueError("教具固定数量不能为负")

        session = Session(
            id=session_id,
            kind=kind,
            venue=venue,
            starts_at=starts_at,
            duration_minutes=duration_minutes,
            language=language,
            owner=owner,
            program_items=tuple(refs),
            groups=groups,
            volunteer_requirement=requirement,
            volunteers=chosen,
            equipment_plan=plan,
        )
        self.sessions[session_id] = session
        self._session_bookings[session_id] = []
        self.owners[owner.id] = owner
        self._reconcile_resources(session)
        return session

    def _resolve_version(
        self, content_id: str, number: int | None
    ) -> ContentVersion:
        try:
            artwork = self.program.artwork(content_id)
        except KeyError:
            raise NotFound(f"内容未登记: {content_id}") from None
        if number is None:
            version = artwork.latest_confirmed()
            if version is None:
                raise NotFound(f"内容尚无已确认版本: {content_id}")
            return version
        try:
            return artwork.version(number)
        except KeyError:
            raise NotFound(f"内容版本不存在: {content_id} v{number}") from None

    # ------------------------------------------------------------------ #
    # 预约：容量
    # ------------------------------------------------------------------ #
    def reserve(
        self,
        booking_id: str,
        session_id: str,
        participant,
        party_size: int = 1,
    ) -> Booking:
        session = self._session(session_id)
        if session.status is not SessionStatus.SCHEDULED:
            raise IllegalTransition("场次已取消或已结束，不能再预约")
        if booking_id in self.bookings:
            raise ValueError("预约编号重复")
        if self._headcount(session) + party_size > session.venue.capacity:
            raise CapacityExceeded(
                f"{session.venue.name} 容量 {session.venue.capacity}，"
                f"已约 {self._headcount(session)}，无法再加 {party_size} 人"
            )
        booking = Booking(
            id=booking_id,
            session=session,
            participant=participant,
            party_size=party_size,
        )
        self.bookings[booking_id] = booking
        self._session_bookings[session_id].append(booking)
        # 预约即预占资源，避免临近开场才发现教具不够；失败则回滚预约
        try:
            self._reconcile_resources(session)
        except Exception:
            self._session_bookings[session_id].remove(booking)
            del self.bookings[booking_id]
            with suppress(Exception):
                self._reconcile_resources(session)
            raise
        return booking

    def mark_no_show(self, booking_id: str) -> None:
        """开场后确认未到（迟到超时）：释放名额与教具；之后补到可再签到。"""
        booking = self._booking(booking_id)
        session = booking.session
        if session.status is not SessionStatus.SCHEDULED:
            raise IllegalTransition("只有未发生场次可以登记缺席")
        if booking.status is BookingStatus.CHECKED_IN:
            raise IllegalTransition("已签到者不能登记缺席")
        if booking.status is BookingStatus.NO_SHOW:
            return
        booking.status = BookingStatus.NO_SHOW
        self._reconcile_resources(session)

    def cancel_booking(self, booking_id: str) -> None:
        booking = self._booking(booking_id)
        if booking.session.status is SessionStatus.COMPLETED:
            raise IllegalTransition("场次已完成，预约作为历史保留")
        if booking.status is BookingStatus.CHECKED_IN:
            raise IllegalTransition("已签到的参与者已在场，不能取消预约")
        if booking.status is BookingStatus.CANCELLED:
            return
        booking.status = BookingStatus.CANCELLED
        self._reconcile_resources(booking.session)

    # ------------------------------------------------------------------ #
    # 签到：幂等 + 迟到
    # ------------------------------------------------------------------ #
    def check_in(self, booking_id: str, at: datetime | None = None) -> CheckIn:
        booking = self._booking(booking_id)
        if booking.session.status is not SessionStatus.SCHEDULED:
            raise IllegalTransition("只有未发生场次可以签到")
        if booking.status is BookingStatus.CANCELLED:
            raise IllegalTransition("预约已取消，不能签到")
        # 重复签到返回原结果
        if booking.check_in is not None:
            return booking.check_in

        moment = at or datetime.now()
        receipt = CheckIn(
            booking_id=booking.id,
            at=moment,
            late=is_late(booking.session, moment),
            receipt=make_receipt(booking.id, moment),
        )
        booking.status = BookingStatus.CHECKED_IN
        booking.check_in = receipt
        # 迟到者仍占用其名额与教具（不退资源），重算保证数量仍自洽
        self._reconcile_resources(booking.session)
        return receipt

    # ------------------------------------------------------------------ #
    # 现场变动：拆组、加场、损坏
    # ------------------------------------------------------------------ #
    def regroup(self, session_id: str, groups: int) -> None:
        """工作坊现场拆/并组：组数变化，按实际人数重算每组共用教具。"""
        session = self._session(session_id)
        if not session.is_workshop:
            raise IllegalTransition("只有工作坊可以拆组")
        if session.status is not SessionStatus.SCHEDULED:
            raise IllegalTransition("场次已结束或已取消")
        if groups < 1:
            raise ValueError("组数至少为 1")
        previous = session.groups
        previous_plan = dict(session.requirements)
        previous_alloc = {k: list(v) for k, v in session.allocations.items()}
        session.groups = groups
        try:
            self._reconcile_resources(session)
        except Exception:
            # 教具不足以支撑新组数：恢复原分组与原占用
            session.groups = previous
            session.requirements = previous_plan
            session.allocations = previous_alloc
            raise

    def report_damage(self, unit_id: str, note: str) -> None:
        """教具损坏：退出可用池，并重算所有未发生场次的占用。

        多个场次可能同时暴露缺口，汇总为一个 ResourceConflict，已完成的重算
        不会回滚损坏状态——损坏是既成事实，缺口用于提示调配备用教具。
        """
        self.pool.mark_damaged(unit_id, note)
        failures: list[str] = []
        for session in self.sessions.values():
            if session.status is not SessionStatus.SCHEDULED:
                continue
            try:
                self._reconcile_resources(session)
            except ResourceConflict as exc:
                failures.append(str(exc))
        if failures:
            raise ResourceConflict("；".join(failures))

    def repair_unit(self, unit_id: str) -> None:
        self.pool.repair(unit_id)
        for session in self.sessions.values():
            if session.status is SessionStatus.SCHEDULED:
                self._reconcile_resources(session)

    # ------------------------------------------------------------------ #
    # 完成与取消：只影响未发生场次，历史保留
    # ------------------------------------------------------------------ #
    def complete_session(self, session_id: str) -> None:
        session = self._session(session_id)
        if session.status is SessionStatus.CANCELLED:
            raise IllegalTransition("已取消场次不能标记完成")
        if session.status is SessionStatus.COMPLETED:
            return
        session.status = SessionStatus.COMPLETED
        for booking in self._session_bookings[session_id]:
            if booking.status is BookingStatus.RESERVED:
                booking.status = BookingStatus.NO_SHOW
        # 占用转为历史使用记录，不再参与后续排程互斥；教具回到可用池
        session.allocations.clear()

    def cancel_session(self, session_id: str) -> None:
        """取消场次：仅未发生场次可取消，释放预约与教具；内容依据快照保留。"""
        session = self._session(session_id)
        if session.status is SessionStatus.COMPLETED:
            raise IllegalTransition("已完成的演出不能取消，历史予以保留")
        if session.status is SessionStatus.CANCELLED:
            return
        session.status = SessionStatus.CANCELLED
        session.allocations.clear()
        for booking in self._session_bookings[session_id]:
            if booking.status is not BookingStatus.CHECKED_IN:
                booking.status = BookingStatus.CANCELLED

    # ------------------------------------------------------------------ #
    # 资源重算：实际人数驱动 + 个体编号互斥
    # ------------------------------------------------------------------ #
    def _headcount(self, session: Session) -> int:
        """当前仍占用名额的预约总人数（预约即预占，含已签到）。"""
        if session.status is not SessionStatus.SCHEDULED:
            return 0
        return sum(
            b.party_size
            for b in self._session_bookings[session.id]
            if b.active
        )

    def _demand_for(self, session: Session, kind_id: str, headcount: int) -> int:
        spec = session.equipment_plan.get(kind_id, 0)
        if isinstance(spec, int):
            return spec
        if spec == "person":
            return headcount if session.is_workshop else 0
        if spec == "group":
            return session.groups if session.is_workshop else 0
        return 0

    def _reconcile_resources(self, session: Session) -> None:
        if session.status is not SessionStatus.SCHEDULED:
            session.allocations.clear()
            session.requirements.clear()
            return

        headcount = self._headcount(session)
        blocked = self._blocked_units(session)
        new_allocations: dict[str, list[str]] = {}
        new_requirements: dict[str, int] = {}
        shortage: ResourceConflict | None = None

        for kind_id in sorted(session.equipment_plan):
            demand = self._demand_for(session, kind_id, headcount)
            new_requirements[kind_id] = demand
            try:
                chosen = self._pick_units(kind_id, demand, blocked, session.id)
            except ResourceConflict as exc:
                # 先提交能分配到的部分，缺口留给总览水位（有缺口）呈现
                shortage = shortage or exc
                chosen = self._pick_units(
                    kind_id, demand, blocked, session.id, allow_partial=True
                )
            blocked |= frozenset(chosen)
            if chosen:
                new_allocations[kind_id] = chosen

        session.requirements = new_requirements
        session.allocations = new_allocations
        if shortage is not None:
            raise shortage

    def _pick_units(
        self,
        kind_id: str,
        count: int,
        blocked: frozenset[str],
        session_id: str,
        *,
        allow_partial: bool = False,
    ) -> list[str]:
        """挑选 count 套未被时间重叠场次占用的个体编号。

        默认不足即抛 ResourceConflict；allow_partial 时返回全部可用编号，
        供缺口场景先提交部分占用。
        """
        if count <= 0:
            return []
        units = [
            u
            for u in self.pool.units_of_kind(kind_id)
            if u.state is not EquipmentState.DAMAGED and u.id not in blocked
        ]
        if len(units) < count and not allow_partial:
            kind = self.pool.kind(kind_id)
            raise ResourceConflict(
                f"场次 {session_id} 需要 {kind.name} {count} 套，"
                f"当前可分配仅 {len(units)} 套，存在资源缺口"
            )
        return [u.id for u in units[:count]]

    def _ranges_overlap(self, a, b) -> bool:
        a_end = a.starts_at.timestamp() + a.duration_minutes * 60
        b_end = b.starts_at.timestamp() + b.duration_minutes * 60
        return a.starts_at.timestamp() < b_end and b.starts_at.timestamp() < a_end

    def _blocked_units(self, session: Session) -> frozenset[str]:
        """时间重叠的其他未发生场次已占用的教具编号。"""
        blocked: set[str] = set()
        for other in self.sessions.values():
            if other.id == session.id:
                continue
            if other.status is not SessionStatus.SCHEDULED:
                continue
            if self._ranges_overlap(session, other):
                blocked.update(other.allocated_units())
        return frozenset(blocked)

    # ------------------------------------------------------------------ #
    # 负责人总览
    # ------------------------------------------------------------------ #
    def overview(self, session_id: str) -> SessionOverview:
        session = self._session(session_id)
        day = session.starts_at.date()
        usage = _usage_for(session.kind)

        basis: list[str] = []
        pending: list[str] = []
        for ref in session.program_items:
            version = self.program.artwork(ref.content_id).version(ref.version)
            ai_tag = ""
            if version.ai is not None:
                scopes = "、".join(s.value for s in version.ai.scope)
                ai_tag = (
                    f"｜AI 来源：{version.ai.provider}/{version.ai.tool}"
                    f"，适用范围：{scopes}"
                )
            basis.append(
                f"《{version.title}》v{version.version}"
                f"（{version.kind.value}，{version.status.value}）{ai_tag}"
            )
            # 待补授权只针对未发生场次；已完成场次按历史快照呈现
            if session.status is SessionStatus.SCHEDULED:
                pending.extend(
                    self.program.publishability_problems(version, usage, day)
                )

        waters: list[_KindWater] = []
        blocked = self._blocked_units(session)
        headcount = self._headcount(session)
        tracked = set(session.equipment_plan) | set(session.allocations)
        for kind_id in sorted(tracked):
            kind = self.pool.kind(kind_id)
            if session.status is SessionStatus.SCHEDULED:
                planned = self._demand_for(session, kind_id, headcount)
            else:
                # 已完成/已取消场次只保留历史，不再呈现为待满足需求
                planned = 0
            allocated = len(session.allocations.get(kind_id, ()))
            usable_total = self.pool.usable(kind_id)
            available_now = self.pool.available(kind_id, blocked)
            if allocated < planned:
                level = ResourceLevel.SHORTAGE
            elif usable_total and allocated / usable_total >= TIGHT_RATIO:
                level = ResourceLevel.TIGHT
            else:
                level = ResourceLevel.FULL
            waters.append(
                _KindWater(
                    kind_id=kind_id,
                    name=kind.name,
                    planned=planned,
                    allocated=allocated,
                    usable_total=usable_total,
                    available_now=available_now,
                    level=level,
                )
            )

        volunteer_issues: list[str] = []
        if session.volunteer_requirement is not None:
            volunteer_issues = session.volunteer_requirement.satisfied_by(
                session.volunteers
            )

        return SessionOverview(
            session=session,
            content_basis=basis,
            pending_licenses=pending,
            water=waters,
            owner=f"{session.owner.name}（{session.owner.title}）",
            volunteer_issues=volunteer_issues,
        )

    # ------------------------------------------------------------------ #
    def _session(self, session_id: str) -> Session:
        try:
            return self.sessions[session_id]
        except KeyError:
            raise NotFound(f"场次不存在: {session_id}") from None

    def _booking(self, booking_id: str) -> Booking:
        try:
            return self.bookings[booking_id]
        except KeyError:
            raise NotFound(f"预约不存在: {booking_id}") from None


def _usage_for(kind: SessionKind) -> Usage:
    return {
        SessionKind.PERFORMANCE: Usage.PUBLIC_PERFORMANCE,
        SessionKind.EXHIBITION: Usage.EXHIBITION,
        SessionKind.WORKSHOP: Usage.WORKSHOP,
    }[kind]


@dataclass(frozen=True)
class _CandidateSession:
    """排程校验阶段的临时时间区间载体。"""

    venue: Venue
    starts_at: datetime
    duration_minutes: int
