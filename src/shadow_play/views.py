"""负责人场次视图：一次查询同时看到内容依据、资源水位、责任人与待补授权。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from src.shadow_play.content import GrantStatus, VersionStatus
from src.shadow_play.resources import AssignmentFunction
from src.shadow_play.sessions import ProgramSystem, Session, SessionStatus


@dataclass(frozen=True)
class ContentBasis:
    """单条内容依据。"""

    content_ref: str
    title: str
    kind: str
    version_status: str
    ai_provenance: dict | None
    authorizations: tuple[dict, ...]
    usable: bool
    issue: str | None


@dataclass(frozen=True)
class KitWaterLevel:
    """单类教具水位。"""

    kit_type: str
    intact_total: int       # 完好总数
    busy: int              # 本场时间窗内已占用（含其他场次）
    available: int         # 窗内仍可占用
    required: int          # 本场按人数应占用
    allocated: tuple[str, ...]  # 本场实际占用的教具编号


@dataclass(frozen=True)
class StaffItem:
    person_code: str
    name: str
    role: str
    function: str
    languages: tuple[str, ...]


@dataclass(frozen=True)
class SessionView:
    """负责人查看场次所需的聚合信息。"""

    session_id: str
    title: str
    type: str
    status: str
    starts_at: datetime
    ends_at: datetime
    venue: str
    owner: str | None
    capacity: int
    reserved: int
    checked_in: int
    content_basis: tuple[ContentBasis, ...]
    kit_levels: tuple[KitWaterLevel, ...]
    staff: tuple[StaffItem, ...]
    pending_authorizations: tuple[str, ...]
    publish_issues: tuple[str, ...]

    @property
    def ready(self) -> bool:
        return not self.publish_issues

    def summary(self) -> str:
        """便于终端/日志阅读的中文摘要。"""

        lines = [
            f"场次 {self.session_id}《{self.title}》"
            f"[{self.type}/{self.status}] {self.starts_at:%Y-%m-%d %H:%M}"
            f"-{self.ends_at:%H:%M} @{self.venue}",
            f"  责任人: {self.owner or '未指定'}；"
            f"预约 {self.reserved}/{self.capacity or '不限'}；"
            f"已签到 {self.checked_in}",
        ]
        for basis in self.content_basis:
            mark = "✓" if basis.usable else "✗"
            lines.append(f"  内容 {mark} {basis.content_ref}《{basis.title}》"
                         f"（{basis.version_status}）{basis.issue or ''}")
        for level in self.kit_levels:
            lines.append(
                f"  教具 {level.kit_type}: 完好{level.intact_total} "
                f"占用{level.busy} 可占{level.available} "
                f"本场需{level.required} 已配{len(level.allocated)}"
            )
        if self.pending_authorizations:
            lines.append(f"  待补授权: {', '.join(self.pending_authorizations)}")
        if self.publish_issues and self.status == SessionStatus.DRAFT.value:
            lines.append("  发布障碍: " + "；".join(self.publish_issues))
        return "\n".join(lines)


class SessionViewBuilder:
    """从 :class:`ProgramSystem` 构建只读场次视图。"""

    def __init__(self, system: ProgramSystem) -> None:
        self.system = system

    def build(self, session_id: str, *, at: datetime) -> SessionView:
        system = self.system
        session = system._session(session_id)

        basis = tuple(
            self._content_basis(session, ref, at)
            for ref in session.content_refs
        )
        levels = tuple(
            self._kit_level(session, kit_type)
            for kit_type in session.kit_requirements
        )
        staff = self._staff(session)

        owner = None
        if session.owner_code:
            owner = system.resources.person(session.owner_code).name

        pending = self._pending_authorizations(session, at)
        issues = (
            tuple(system.publish_issues(session, at=at))
            if session.status is SessionStatus.DRAFT
            else ()
        )
        venue = system.resources.venue(session.venue_code)
        return SessionView(
            session_id=session.id,
            title=session.title,
            type=session.session_type.value,
            status=session.status.value,
            starts_at=session.window.starts_at,
            ends_at=session.window.ends_at,
            venue=venue.name,
            owner=owner,
            capacity=session.capacity,
            reserved=system.reserved_headcount(session),
            checked_in=system.checked_in_headcount(session),
            content_basis=basis,
            kit_levels=levels,
            staff=staff,
            pending_authorizations=pending,
            publish_issues=issues,
        )

    def build_all(self, *, at: datetime) -> tuple[SessionView, ...]:
        return tuple(
            self.build(session_id, at=at)
            for session_id in sorted(self.system.sessions)
        )

    # ------------------------------------------------------------------

    def _content_basis(self, session: Session, content_ref: str, at: datetime) -> ContentBasis:
        content = self.system.content
        issue: str | None = None
        usable = True
        try:
            version = content.get_version(content_ref)
        except Exception as exc:
            return ContentBasis(
                content_ref=content_ref,
                title="（找不到版本）",
                kind="",
                version_status="missing",
                ai_provenance=None,
                authorizations=(),
                usable=False,
                issue=str(exc),
            )

        auth_rows: list[dict] = []
        for code in session.auth_map.get(content_ref, []):
            grant = content.authorization(code)
            grant.expire_if_due(session.window.starts_at)
            auth_rows.append(
                {
                    "code": grant.code,
                    "holder": grant.right_holder.name,
                    "status": grant.status.value,
                    "scope": list(grant.requested_scope),
                    "until": grant.requested_until.isoformat(),
                }
            )

        try:
            content.assert_usable(
                content_ref,
                session.session_type.value,
                session.window.starts_at,
                auth_codes=session.auth_map.get(content_ref, ()),
            )
        except Exception as exc:
            usable = False
            issue = str(exc)

        provenance = version.ai_provenance.to_data() if version.ai_provenance else None
        return ContentBasis(
            content_ref=content_ref,
            title=version.title,
            kind=version.kind.value,
            version_status=version.status.value,
            ai_provenance=provenance,
            authorizations=tuple(auth_rows),
            usable=usable,
            issue=issue,
        )

    def _kit_level(self, session: Session, kit_type: str) -> KitWaterLevel:
        resources = self.system.resources
        total, busy, available = resources.water_level(kit_type, session.window)
        required = (
            self.system.reserved_headcount(session)
            * session.kit_requirements[kit_type]
        )
        allocated = resources.allocated_kit_units(session.id, kit_type)
        return KitWaterLevel(
            kit_type=kit_type,
            intact_total=total,
            busy=busy,
            available=available,
            required=required,
            allocated=allocated,
        )

    def _staff(self, session: Session) -> tuple[StaffItem, ...]:
        items = []
        for allocation in self.system.resources.staff_for(session.id):
            person = self.system.resources.person(allocation.resource_id)
            items.append(
                StaffItem(
                    person_code=person.code,
                    name=person.name,
                    role=person.role.value,
                    function=allocation.note or AssignmentFunction.ONSITE_SUPPORT.value,
                    languages=tuple(sorted(person.languages)),
                )
            )
        return tuple(items)

    def _pending_authorizations(self, session: Session, at: datetime) -> tuple[str, ...]:
        """待补授权：已绑定内容上仍待处理/已过期/被拒/被撤的授权编号。"""

        pending: list[str] = []
        for codes in session.auth_map.values():
            for code in codes:
                grant = self.system.content.authorization(code)
                grant.expire_if_due(at)
                if grant.status is not GrantStatus.GRANTED:
                    pending.append(
                        f"{code}（{grant.content_ref}/"
                        f"{grant.right_holder.name}/{grant.status.value}）"
                    )
        # 内容已绑定但未挂任何授权
        for ref in session.content_refs:
            if not session.auth_map.get(ref):
                pending.append(f"缺授权（{ref} 未挂接任何授权）")
        return tuple(pending)
