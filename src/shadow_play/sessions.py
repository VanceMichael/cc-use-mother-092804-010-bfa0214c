"""场次生命周期、参与者预约、签到与现场资源调整。

状态机::

    DRAFT ──publish──▶ PUBLISHED ──start──▶ IN_PROGRESS ──complete──▶ COMPLETED
      │                   │                    │
      └────cancel─────────┴──── cancel（仅限未开场）

规则要点：

- 发布门：场地无冲突、责任人已定、全部内容为已确认版本且授权覆盖本场类型
  与时间（AI 动画还须来源/适用范围标注覆盖）、讲解语言均有资质人员、
  教具水位满足预约；
- 预约成功即按人数占用教具；取消/减员立即释放，签到后按实际到场核对；
- 重复签到幂等，返回首次结果，不重复占用资源；
- 迟到在场次时间窗内仍可签到；
- 工作坊拆组：把预约的一部分人转到另一已发布场次，两边同时释放/占用；
- 临时加场走与常规场次相同的发布门；
- 教具损坏后受影响场次按实际人数补位，缺口登记上报；
- 取消只作用于未开场场次；已完成场次冻结，其内容与授权快照作为历史保留。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum

from src.shadow_play.content import (
    ContentRegistry,
    ContentVersion,
    GrantStatus,
    VersionStatus,
)
from src.shadow_play.errors import (
    CapacityExceeded,
    PublishBlocked,
    SessionStateError,
)
from src.shadow_play.resources import (
    AssignmentFunction,
    ReconcileResult,
    ResourceRegistry,
    TimeWindow,
)


class SessionType(str, Enum):
    PERFORMANCE = "performance"  # 公开皮影演出（可含 AI 动画）
    WORKSHOP = "workshop"        # 互动工作坊
    EXHIBIT = "exhibit"          # 主题展陈讲解


class SessionStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class BookingStatus(str, Enum):
    BOOKED = "booked"
    CHECKED_IN = "checked_in"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"  # 场次结束仍未签到，保留记录但不占资源


@dataclass
class Booking:
    """参与者预约。只保存匿名化参与者编号，不登记真实身份信息。"""

    code: str
    session_id: str
    participant_code: str
    party_size: int
    status: BookingStatus = BookingStatus.BOOKED
    checked_size: int = 0
    checked_at: datetime | None = None


@dataclass(frozen=True)
class CheckInResult:
    """一次签到的结果；重复签到时返回同一份（幂等）。"""

    booking_code: str
    session_id: str
    checked_size: int
    kit_results: tuple[tuple[str, ReconcileResult], ...]
    duplicate: bool = False


@dataclass
class Session:
    id: str
    title: str
    session_type: SessionType
    window: TimeWindow
    venue_code: str
    event_code: str = "default"
    capacity: int = 0                     # 互动项目容量；0 表示不限（如展陈）
    content_refs: list[str] = field(default_factory=list)
    auth_map: dict[str, list[str]] = field(default_factory=dict)
    languages: set[str] = field(default_factory=set)
    # 教具需求：kit_type -> 每名参与者占用套数
    kit_requirements: dict[str, int] = field(default_factory=dict)
    owner_code: str | None = None
    status: SessionStatus = SessionStatus.DRAFT
    completion_snapshot: dict[str, object] | None = None

    @property
    def is_finished(self) -> bool:
        return self.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED)


@dataclass(frozen=True)
class DamageReport:
    """教具损坏后的全场次补位报告。"""

    serial: str
    reconciled: tuple[tuple[str, ReconcileResult], ...]
    shortages: tuple[tuple[str, str, int], ...]  # (场次, 教具种类, 缺口套数)


class ProgramSystem:
    """活动后台门面：串联内容授权台账、资源台账与场次流程。"""

    def __init__(
        self,
        content: ContentRegistry | None = None,
        resources: ResourceRegistry | None = None,
    ) -> None:
        self.content = content or ContentRegistry()
        self.resources = resources or ResourceRegistry()
        self.sessions: dict[str, Session] = {}
        self.bookings: dict[str, Booking] = {}
        self._booking_seq = 0
        self._checkin_cache: dict[str, CheckInResult] = {}

    # ------------------------------------------------------------------
    # 场次排期与配置
    # ------------------------------------------------------------------

    def schedule_session(
        self,
        session_id: str,
        title: str,
        session_type: SessionType,
        window: TimeWindow,
        venue_code: str,
        *,
        event_code: str = "default",
        capacity: int = 0,
    ) -> Session:
        """建场次草稿，并立即锁定场地（场地冲突尽早暴露）。"""

        if session_id in self.sessions:
            raise SessionStateError(f"场次编号重复: {session_id}")
        if capacity < 0:
            raise SessionStateError("容量不能为负")
        # 场地存在性与冲突校验
        self.resources.allocate_venue(session_id, venue_code, window)
        session = Session(
            id=session_id,
            title=title,
            session_type=session_type,
            window=window,
            venue_code=venue_code,
            event_code=event_code,
            capacity=capacity,
        )
        self.sessions[session_id] = session
        return session

    def use_content(
        self, session_id: str, content_ref: str, auth_codes: list[str]
    ) -> None:
        session = self._session(session_id)
        self._require_editable(session)
        if content_ref not in session.content_refs:
            session.content_refs.append(content_ref)
        session.auth_map[content_ref] = list(auth_codes)

    def require_kit(self, session_id: str, kit_type: str, per_participant: int = 1) -> None:
        session = self._session(session_id)
        self._require_editable(session)
        if per_participant < 1:
            raise SessionStateError("每人教具占用至少为 1 套")
        session.kit_requirements[kit_type] = per_participant

    def offer_language(self, session_id: str, language: str) -> None:
        session = self._session(session_id)
        self._require_editable(session)
        session.languages.add(language)

    def set_owner(self, session_id: str, person_code: str) -> None:
        session = self._session(session_id)
        self._require_editable(session)
        self.resources.person(person_code)  # 人员必须已登记
        session.owner_code = person_code

    def assign_staff(
        self,
        session_id: str,
        person_code: str,
        function: AssignmentFunction,
        *,
        languages: frozenset[str] | None = None,
        qualification: str | None = None,
    ) -> None:
        session = self._session(session_id)
        self._require_editable(session)
        self.resources.assign_person(
            session_id,
            person_code,
            function,
            session.window,
            required_languages=languages,
            required_qualification=qualification,
        )

    # ------------------------------------------------------------------
    # 发布门
    # ------------------------------------------------------------------

    def publish_issues(self, session: Session, *, at: datetime) -> list[str]:
        """列出全部发布障碍；空列表表示可以发布。"""

        issues: list[str] = []
        if session.window.starts_at <= at:
            issues.append("场次开始时间已过，不能发布")

        if session.owner_code is None:
            issues.append("尚未指定责任人")

        # 内容依据：已确认版本 + 有效授权（AI 动画额外校验标注）
        if not session.content_refs:
            issues.append("尚未绑定任何演绎内容")
        for content_ref in session.content_refs:
            try:
                version = self.content.get_version(content_ref)
            except Exception as exc:  # 领域错误统一转为待补事项
                issues.append(str(exc))
                continue
            if version.status is not VersionStatus.CONFIRMED:
                issues.append(
                    f"内容 {content_ref} 状态为 {version.status.value}，须使用已确认版本"
                )
                continue
            try:
                self.content.assert_usable(
                    content_ref,
                    session.session_type.value,
                    session.window.starts_at,
                    auth_codes=session.auth_map.get(content_ref, ()),
                )
            except Exception as exc:
                issues.append(str(exc))

        # 讲解语言覆盖：讲解员或工作坊带领者中，至少有一人具备该语言能力
        staff = self.resources.staff_for(session.id)
        spoken: set[str] = set()
        facilitator_count = 0
        language_functions = {
            AssignmentFunction.NARRATION.value,
            AssignmentFunction.WORKSHOP_FACILITATOR.value,
        }
        for allocation in staff:
            person = self.resources.person(allocation.resource_id)
            if allocation.note in language_functions:
                spoken |= person.languages
            if allocation.note == AssignmentFunction.WORKSHOP_FACILITATOR.value:
                facilitator_count += 1
        missing_languages = session.languages - spoken
        if missing_languages:
            issues.append(f"讲解语言缺少合格排班: {sorted(missing_languages)}")
        if session.session_type is SessionType.WORKSHOP and facilitator_count == 0:
            issues.append("工作坊尚未安排具备资质的带领志愿者")

        # 教具水位：按当前预约人数校验
        reserved = self.reserved_headcount(session)
        for kit_type, per_participant in session.kit_requirements.items():
            needed = reserved * per_participant
            total, busy, available = self.resources.water_level(
                kit_type, session.window
            )
            if needed > available:
                issues.append(
                    f"教具 {kit_type} 水位不足：需 {needed} 套，"
                    f"窗内可占用 {available} 套（完好 {total}，已占用 {busy}）"
                )
        return issues

    def publish(self, session_id: str, *, at: datetime) -> Session:
        session = self._session(session_id)
        if session.status is not SessionStatus.DRAFT:
            raise SessionStateError("只有草稿场次可以发布")
        issues = self.publish_issues(session, at=at)
        if issues:
            raise PublishBlocked(issues)
        self._reconcile_reserved(session)
        session.status = SessionStatus.PUBLISHED
        return session

    # ------------------------------------------------------------------
    # 预约
    # ------------------------------------------------------------------

    def book(
        self,
        session_id: str,
        participant_code: str,
        party_size: int = 1,
        *,
        at: datetime,
    ) -> Booking:
        session = self._session(session_id)
        if session.status is not SessionStatus.PUBLISHED:
            raise SessionStateError("场次未发布，不能预约")
        if party_size < 1:
            raise SessionStateError("预约人数至少为 1")
        reserved = self.reserved_headcount(session)
        if session.capacity and reserved + party_size > session.capacity:
            raise CapacityExceeded(
                f"超出互动容量：已占 {reserved}，容量 {session.capacity}，"
                f"本次预约 {party_size}"
            )
        # 教具水位：本场已占用已计入窗内占用，只需为新增人数找到空位
        for kit_type, per_participant in session.kit_requirements.items():
            extra = party_size * per_participant
            _total, _busy, available = self.resources.water_level(
                kit_type, session.window
            )
            if extra > available:
                raise CapacityExceeded(
                    f"教具 {kit_type} 可占用不足：新增需 {extra} 套，"
                    f"窗内仅剩 {available} 套，预约拒绝"
                )
        self._booking_seq += 1
        booking = Booking(
            code=f"BK-{session.id}-{self._booking_seq:04d}",
            session_id=session_id,
            participant_code=participant_code,
            party_size=party_size,
        )
        self.bookings[booking.code] = booking
        self._reconcile_reserved(session)
        return booking

    def cancel_booking(self, booking_code: str, *, at: datetime) -> None:
        """开场前取消预约并释放资源；已签到记录保留不删。"""

        booking = self._booking(booking_code)
        session = self._session(booking.session_id)
        if booking.status is BookingStatus.CANCELLED:
            raise SessionStateError("预约已取消")
        if booking.status is BookingStatus.CHECKED_IN:
            raise SessionStateError("已签到预约不能取消，按现场离场登记")
        booking.status = BookingStatus.CANCELLED
        self._reconcile_reserved(session)

    # ------------------------------------------------------------------
    # 签到（幂等）与迟到
    # ------------------------------------------------------------------

    def check_in(
        self,
        booking_code: str,
        *,
        at: datetime,
        actual_size: int | None = None,
    ) -> CheckInResult:
        """签到。``actual_size`` 小于预约人数时按实际到场核减教具。

        在场次时间窗内（含开场后、结束前）允许迟到签到。同一预约重复签到
        直接返回首次结果，不再次调整资源。
        """

        if booking_code in self._checkin_cache:
            cached = self._checkin_cache[booking_code]
            return replace(cached, duplicate=True)

        booking = self._booking(booking_code)
        session = self._session(booking.session_id)
        if booking.status is BookingStatus.CANCELLED:
            raise SessionStateError("预约已取消，不能签到")
        if booking.status is BookingStatus.NO_SHOW:
            raise SessionStateError("该预约已按爽约处理，不能补签到")
        if session.status not in (SessionStatus.PUBLISHED, SessionStatus.IN_PROGRESS):
            raise SessionStateError("场次未发布或已结束，不能签到")
        if at < session.window.starts_at:
            raise SessionStateError("场次尚未开场，不能签到")
        if at >= session.window.ends_at:
            raise SessionStateError("场次已结束，不能签到")
        size = booking.party_size if actual_size is None else actual_size
        if size < 0 or size > booking.party_size:
            raise SessionStateError("到场人数须在 0 与预约人数之间")

        booking.status = BookingStatus.CHECKED_IN
        booking.checked_size = size
        booking.checked_at = at

        kit_results = tuple(
            (kit_type, self._reconcile(session, kit_type))
            for kit_type in session.kit_requirements
        )
        result = CheckInResult(
            booking_code=booking.code,
            session_id=session.id,
            checked_size=size,
            kit_results=kit_results,
        )
        self._checkin_cache[booking.code] = result
        return result

    # ------------------------------------------------------------------
    # 拆组与临时加场
    # ------------------------------------------------------------------

    def split_booking(
        self,
        booking_code: str,
        move_size: int,
        target_session_id: str,
        participant_code: str,
        *,
        at: datetime,
    ) -> tuple[Booking | None, Booking]:
        """把预约中的 ``move_size`` 人拆到另一已发布场次（常用于临时加场）。

        源场次释放对应占用，目标场次在容量与教具水位允许时新建预约并占用。
        ``move_size`` 等于原预约人数时，源预约整体取消。
        """

        booking = self._booking(booking_code)
        source = self._session(booking.session_id)
        target = self._session(target_session_id)
        if booking.status is not BookingStatus.BOOKED:
            raise SessionStateError("仅未签到预约可以拆组")
        if not 1 <= move_size <= booking.party_size:
            raise SessionStateError("拆组人数超出预约范围")
        if target.status is not SessionStatus.PUBLISHED:
            raise SessionStateError("目标场次未发布，不能接收拆组人员")
        if target.window.ends_at <= at:
            raise SessionStateError("目标加场已经结束")

        remaining = booking.party_size - move_size
        # 先在目标场次占位（容量与教具不足会在此抛错，源场次保持不变）
        new_booking = self.book(
            target.id, participant_code, move_size, at=at
        )
        if remaining == 0:
            booking.status = BookingStatus.CANCELLED
        else:
            booking.party_size = remaining
        self._reconcile_reserved(source)
        return (None if remaining == 0 else booking), new_booking

    # ------------------------------------------------------------------
    # 教具损坏
    # ------------------------------------------------------------------

    def report_kit_damage(self, serial: str, *, at: datetime, note: str = "") -> DamageReport:
        """登记损坏：受影响的未结束场次立即按当前人数补位，缺口上报。"""

        affected = self.resources.report_damage(serial, at, note=note)
        reconciled: list[tuple[str, ReconcileResult]] = []
        shortages: list[tuple[str, str, int]] = []
        for session_id, _window in affected:
            session = self.sessions.get(session_id)
            if session is None or session.is_finished:
                continue
            for kit_type in session.kit_requirements:
                result = self._reconcile(session, kit_type)
                reconciled.append((session_id, result))
                if result.missing:
                    shortages.append((session_id, kit_type, result.missing))
        return DamageReport(
            serial=serial, reconciled=tuple(reconciled), shortages=tuple(shortages)
        )

    # ------------------------------------------------------------------
    # 开场、结束、取消
    # ------------------------------------------------------------------

    def start(self, session_id: str, *, at: datetime) -> Session:
        session = self._session(session_id)
        if session.status is not SessionStatus.PUBLISHED:
            raise SessionStateError("只有已发布场次可以开场")
        if at < session.window.starts_at:
            raise SessionStateError("未到开场时间")
        session.status = SessionStatus.IN_PROGRESS
        return session

    def complete(self, session_id: str, *, at: datetime) -> Session:
        """结束场次：未签到者记为爽约并释放资源，随后冻结历史快照。"""

        session = self._session(session_id)
        if session.status is not SessionStatus.IN_PROGRESS:
            raise SessionStateError("只有进行中场次可以结束")

        # 未签到预约记为爽约（记录保留），其教具占用随之释放
        for booking in self._bookings_of(session):
            if booking.status is BookingStatus.BOOKED:
                booking.status = BookingStatus.NO_SHOW

        # 按实际签到人数核对，未使用教具全部释放
        kit_results = tuple(
            (kit_type, self._reconcile(session, kit_type))
            for kit_type in session.kit_requirements
        )
        snapshot: dict[str, object] = {
            "completed_at": at,
            "content": [],
            "checked_in": self.checked_in_headcount(session),
            "kit_results": kit_results,
        }
        for content_ref in session.content_refs:
            version = self.content.get_version(content_ref)
            snapshot["content"].append(
                {
                    "content_ref": content_ref,
                    "title": version.title,
                    "version_status": version.status.value,
                    "ai_provenance": (
                        version.ai_provenance.to_data()
                        if version.ai_provenance
                        else None
                    ),
                    "authorizations": [
                        {
                            "code": code,
                            "status": self.content.authorization(code).status.value,
                            "holder": self.content.authorization(
                                code
                            ).right_holder.name,
                        }
                        for code in session.auth_map.get(content_ref, [])
                    ],
                }
            )
        session.completion_snapshot = snapshot
        session.status = SessionStatus.COMPLETED
        return session

    def cancel_session(self, session_id: str, *, at: datetime) -> Session:
        """取消场次。仅未开场场次可取消；已完成/进行中记录保留。"""

        session = self._session(session_id)
        if session.status in (SessionStatus.COMPLETED, SessionStatus.CANCELLED):
            raise SessionStateError("已结束场次不能取消")
        if session.status is SessionStatus.IN_PROGRESS:
            raise SessionStateError("进行中场次不能整体取消，按现场预案处理")
        if session.window.starts_at <= at:
            raise SessionStateError("已到开场时间的场次不能取消")
        self.resources.release_session(session.id, now=at)
        for booking in self._bookings_of(session):
            if booking.status is not BookingStatus.CANCELLED:
                booking.status = BookingStatus.CANCELLED
        session.status = SessionStatus.CANCELLED
        return session

    def cancel_event(self, event_code: str, *, at: datetime) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """取消活动下全部未发生场次。

        返回 ``(已取消场次, 跳过场次)``；进行中与已完成场次一律跳过，
        其演出记录与作品授权历史保留。
        """

        cancelled: list[str] = []
        skipped: list[str] = []
        for session in self.sessions.values():
            if session.event_code != event_code:
                continue
            if session.is_finished or session.window.starts_at <= at:
                skipped.append(session.id)
                continue
            self.cancel_session(session.id, at=at)
            cancelled.append(session.id)
        return tuple(cancelled), tuple(skipped)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def _session(self, session_id: str) -> Session:
        try:
            return self.sessions[session_id]
        except KeyError:
            raise SessionStateError(f"场次不存在: {session_id}") from None

    @staticmethod
    def _require_editable(session: Session) -> None:
        """草稿才可改排期要素；已发布后内容、资源与排班即锁定。"""

        if session.status is not SessionStatus.DRAFT:
            raise SessionStateError(
                f"场次 {session.id} 已发布（{session.status.value}），排期要素锁定"
            )

    def _booking(self, booking_code: str) -> Booking:
        try:
            return self.bookings[booking_code]
        except KeyError:
            raise SessionStateError(f"预约不存在: {booking_code}") from None

    def _bookings_of(self, session: Session) -> list[Booking]:
        return [b for b in self.bookings.values() if b.session_id == session.id]

    def reserved_headcount(self, session: Session) -> int:
        """当前资源应保障人数：已签到按实际，其余按预约。"""

        total = 0
        for booking in self._bookings_of(session):
            if booking.status is BookingStatus.CHECKED_IN:
                total += booking.checked_size
            elif booking.status is BookingStatus.BOOKED:
                total += booking.party_size
        return total

    def checked_in_headcount(self, session: Session) -> int:
        return sum(
            b.checked_size
            for b in self._bookings_of(session)
            if b.status is BookingStatus.CHECKED_IN
        )

    def booked_headcount(self, session: Session) -> int:
        return sum(
            b.party_size
            for b in self._bookings_of(session)
            if b.status is BookingStatus.BOOKED
        )

    # ------------------------------------------------------------------
    # 内部：按应保障人数核对教具
    # ------------------------------------------------------------------

    def _required_kits(self, session: Session, kit_type: str) -> int:
        per_participant = session.kit_requirements[kit_type]
        return self.reserved_headcount(session) * per_participant

    def _reconcile(self, session: Session, kit_type: str) -> ReconcileResult:
        required = self._required_kits(session, kit_type)
        return self.resources.reconcile_kit_units(
            session.id, kit_type, required, session.window
        )

    def _reconcile_reserved(self, session: Session) -> None:
        for kit_type in session.kit_requirements:
            self._reconcile(session, kit_type)

    # ------------------------------------------------------------------
    # 内容只读辅助
    # ------------------------------------------------------------------

    def version_of(self, content_ref: str) -> ContentVersion:
        return self.content.get_version(content_ref)
