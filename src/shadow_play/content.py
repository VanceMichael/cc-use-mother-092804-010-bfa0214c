"""演绎内容版本与非遗传承人授权。

关键规则：

- 同一内容编号下版本号只能递增，且新版本默认草稿状态；
- 已确认（published）的版本内容不可再修改，只能基于其提交新版本；
- 公开演出只能采用已确认版本；
- 人工智能动画必须标明来源（模型/提示词/底本）和适用范围（可用场次类型、
  授权期限），且必须取得覆盖该用途与时间的传承人授权；
- 未获许可（无授权、已拒绝、已过期、已撤销）的素材不能进入活动；
- 授权的每次状态变化都保留历史，已完成演出引用的版本与授权记录不删除。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable

from src.shadow_play.errors import (
    AuthorizationError,
    ContentStateError,
    ProvenanceError,
    VersionSequenceError,
)


class ContentKind(str, Enum):
    """演绎内容类型。"""

    SCRIPT = "script"          # 皮影剧本
    IMAGE = "image"            # 图像/皮影造型素材
    AI_ANIMATION = "ai_animation"  # 人工智能动画
    EXHIBIT = "exhibit"        # 主题展陈文案/图板


class VersionStatus(str, Enum):
    """内容版本状态。"""

    DRAFT = "draft"
    CONFIRMED = "confirmed"
    # 撤回后不可再用于新场次；已完成场次中的引用保留
    WITHDRAWN = "withdrawn"


class GrantStatus(str, Enum):
    """传承人授权状态。"""

    PENDING = "pending"
    GRANTED = "granted"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"


_TERMINAL_GRANT_STATUS = frozenset(
    {GrantStatus.REJECTED, GrantStatus.EXPIRED, GrantStatus.REVOKED}
)


@dataclass(frozen=True)
class RightHolder:
    """非遗传承人（权利人）。``code`` 为内部编号，不保存证件等敏感信息。"""

    code: str
    name: str
    tradition: str = "潮俗皮影"


@dataclass(frozen=True)
class AiProvenance:
    """人工智能动画的来源与适用范围标注。

    - ``model``：生成所用模型/服务；
    - ``prompt_ref``：提示词记录编号；
    - ``source_works``：底本（传统曲目、传承人手稿等）编号列表；
    - ``scope_session_types``：允许使用的场次类型；
    - ``valid_from`` / ``valid_until``：适用期限。
    """

    model: str
    prompt_ref: str
    source_works: tuple[str, ...]
    scope_session_types: tuple[str, ...]
    valid_from: date
    valid_until: date

    def covers(self, session_type: str, moment: datetime) -> None:
        """校验该标注是否覆盖某场次类型与时间，不覆盖则抛错。"""

        if session_type not in self.scope_session_types:
            raise ProvenanceError(
                f"AI 动画适用范围不含场次类型 {session_type}"
            )
        day = moment.date()
        if not (self.valid_from <= day <= self.valid_until):
            raise ProvenanceError("AI 动画超出标注的适用期限")

    def to_data(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "prompt_ref": self.prompt_ref,
            "source_works": list(self.source_works),
            "scope_session_types": list(self.scope_session_types),
            "valid_from": self.valid_from.isoformat(),
            "valid_until": self.valid_until.isoformat(),
        }


@dataclass(frozen=True)
class _GrantEvent:
    """授权状态变更记录，只追加，不修改。"""

    status: GrantStatus
    at: datetime
    note: str = ""


@dataclass
class Authorization:
    """一份传承人对某内容版本的使用授权。"""

    code: str
    content_ref: str          # 形如 "MULAN-01:3"
    right_holder: RightHolder
    requested_scope: tuple[str, ...]  # 申请覆盖的场次类型
    requested_until: date
    events: list[_GrantEvent] = field(default_factory=list)

    @property
    def status(self) -> GrantStatus:
        return self.events[-1].status if self.events else GrantStatus.PENDING

    @property
    def decided_at(self) -> datetime | None:
        for event in reversed(self.events):
            if event.status in (
                GrantStatus.GRANTED,
                GrantStatus.REJECTED,
                GrantStatus.REVOKED,
            ):
                return event.at
        return None

    def _append(self, status: GrantStatus, at: datetime, note: str) -> None:
        self.events.append(_GrantEvent(status=status, at=at, note=note))

    def grant(self, at: datetime, *, note: str = "") -> None:
        if self.status != GrantStatus.PENDING:
            raise AuthorizationError("只有待处理授权可以授予")
        self._append(GrantStatus.GRANTED, at, note)

    def reject(self, at: datetime, *, note: str = "") -> None:
        if self.status != GrantStatus.PENDING:
            raise AuthorizationError("只有待处理授权可以拒绝")
        self._append(GrantStatus.REJECTED, at, note)

    def revoke(self, at: datetime, *, note: str = "") -> None:
        if self.status != GrantStatus.GRANTED:
            raise AuthorizationError("只有已授予授权可以撤销")
        self._append(GrantStatus.REVOKED, at, note)

    def expire_if_due(self, at: datetime) -> bool:
        """到期自动失效。返回是否发生了状态变化。"""

        if (
            self.status == GrantStatus.GRANTED
            and at.date() > self.requested_until
        ):
            self._append(GrantStatus.EXPIRED, at, "授权到期自动失效")
            return True
        return False

    def is_valid_for(self, session_type: str, moment: datetime) -> bool:
        """该授权在给定时刻是否可合法支持某类场次。"""

        if self.status != GrantStatus.GRANTED:
            return False
        if session_type not in self.requested_scope:
            return False
        return moment.date() <= self.requested_until

    @property
    def history(self) -> list[tuple[GrantStatus, datetime, str]]:
        return [(e.status, e.at, e.note) for e in self.events]


@dataclass
class ContentVersion:
    """一个演绎内容的某一版本。"""

    ref: str                  # 内容编号，如 "MULAN-01"
    version: int
    kind: ContentKind
    title: str
    created_at: datetime
    status: VersionStatus = VersionStatus.DRAFT
    based_on: int | None = None
    ai_provenance: AiProvenance | None = None
    confirmed_at: datetime | None = None

    @property
    def content_ref(self) -> str:
        return f"{self.ref}:{self.version}"

    def confirm(self, at: datetime) -> None:
        if self.status is VersionStatus.CONFIRMED:
            raise ContentStateError("该版本已经确认")
        if self.status is VersionStatus.WITHDRAWN:
            raise ContentStateError("已撤回版本不能重新确认")
        if self.kind is ContentKind.AI_ANIMATION and self.ai_provenance is None:
            raise ProvenanceError("AI 动画确认前必须标明来源和适用范围")
        self.status = VersionStatus.CONFIRMED
        self.confirmed_at = at

    def withdraw(self, at: datetime, *, note: str = "") -> None:
        if self.status is not VersionStatus.CONFIRMED:
            raise ContentStateError("只有已确认版本可以撤回")
        self.status = VersionStatus.WITHDRAWN


class ContentRegistry:
    """演绎内容版本与传承人授权台账。"""

    def __init__(self) -> None:
        self._versions: dict[str, list[ContentVersion]] = {}
        self._authorizations: dict[str, Authorization] = {}

    # ---- 版本管理 ----

    def submit_version(
        self,
        ref: str,
        kind: ContentKind,
        title: str,
        created_at: datetime,
        *,
        ai_provenance: AiProvenance | None = None,
    ) -> ContentVersion:
        """提交新版本。版本号按该内容编号下的顺序自动递增。"""

        history = self._versions.setdefault(ref, [])
        next_no = len(history) + 1
        based_on = history[-1].version if history else None
        if kind is ContentKind.AI_ANIMATION and ai_provenance is None:
            raise ProvenanceError("AI 动画必须随版本提交来源和适用范围")
        version = ContentVersion(
            ref=ref,
            version=next_no,
            kind=kind,
            title=title,
            created_at=created_at,
            based_on=based_on,
            ai_provenance=ai_provenance,
        )
        history.append(version)
        return version

    def get_version(self, content_ref: str) -> ContentVersion:
        ref, _, raw_no = content_ref.partition(":")
        try:
            no = int(raw_no)
        except ValueError:
            raise ContentStateError(f"内容引用格式错误: {content_ref}") from None
        history = self._versions.get(ref, [])
        if not 1 <= no <= len(history):
            raise VersionSequenceError(f"内容版本不存在: {content_ref}")
        return history[no - 1]

    def latest_version(self, ref: str) -> ContentVersion:
        try:
            return self._versions[ref][-1]
        except (KeyError, IndexError):
            raise VersionSequenceError(f"内容不存在: {ref}") from None

    def versions(self, ref: str) -> tuple[ContentVersion, ...]:
        return tuple(self._versions.get(ref, ()))

    # ---- 授权管理 ----

    def request_authorization(
        self,
        content_ref: str,
        right_holder: RightHolder,
        scope: Iterable[str],
        until: date,
        at: datetime,
        *,
        code: str | None = None,
    ) -> Authorization:
        """登记一份传承人授权申请。申请时内容版本必须存在。"""

        version = self.get_version(content_ref)
        scope_tuple = tuple(dict.fromkeys(scope))
        if not scope_tuple:
            raise AuthorizationError("授权申请至少覆盖一种场次类型")
        grant_code = code or f"AUTH-{content_ref.replace(':', '-')}-{len(self._authorizations) + 1:03d}"
        if grant_code in self._authorizations:
            raise AuthorizationError(f"授权编号重复: {grant_code}")
        grant = Authorization(
            code=grant_code,
            content_ref=content_ref,
            right_holder=right_holder,
            requested_scope=scope_tuple,
            requested_until=until,
            events=[_GrantEvent(GrantStatus.PENDING, at, "提出授权申请")],
        )
        self._authorizations[grant_code] = grant
        return grant

    def authorization(self, code: str) -> Authorization:
        try:
            return self._authorizations[code]
        except KeyError:
            raise AuthorizationError(f"授权不存在: {code}") from None

    def authorizations_for(self, content_ref: str) -> tuple[Authorization, ...]:
        return tuple(
            grant
            for grant in self._authorizations.values()
            if grant.content_ref == content_ref
        )

    def pending_authorizations(self, *, at: datetime) -> tuple[Authorization, ...]:
        """所有待处理（含未决定与可标注到期的）授权，用于负责人待补清单。"""

        result = []
        for grant in self._authorizations.values():
            grant.expire_if_due(at)
            if grant.status in (GrantStatus.PENDING, GrantStatus.EXPIRED):
                result.append(grant)
        return tuple(result)

    # ---- 可用性评估 ----

    def assert_usable(
        self,
        content_ref: str,
        session_type: str,
        moment: datetime,
        *,
        auth_codes: Iterable[str] | None = None,
    ) -> list[str]:
        """断言某内容版本可进入活动，返回实际生效的授权编号列表。

        依次检查：版本存在 -> 已确认且未撤回 -> AI 标注覆盖 -> 授权有效。
        任一不满足都抛出对应领域错误（未获许可素材不能进入活动）。
        """

        version = self.get_version(content_ref)
        if version.status is not VersionStatus.CONFIRMED:
            raise ContentStateError(
                f"内容 {content_ref} 未确认（当前状态 {version.status.value}），不能公开使用"
            )
        if version.kind is ContentKind.AI_ANIMATION:
            assert version.ai_provenance is not None
            version.ai_provenance.covers(session_type, moment)

        codes = list(auth_codes or [])
        valid: list[str] = []
        for code in codes:
            grant = self.authorization(code)
            grant.expire_if_due(moment)
            if grant.content_ref != content_ref:
                raise AuthorizationError(
                    f"授权 {code} 不属于内容 {content_ref}"
                )
            if grant.is_valid_for(session_type, moment):
                valid.append(code)

        if not valid:
            # 原因优先基于本场次实际挂接的授权给出，便于负责人补齐
            if not codes:
                existing = self.authorizations_for(content_ref)
                reason = (
                    "未登记任何传承人授权"
                    if not existing
                    else "场次未挂接传承人授权"
                )
            else:
                checked = [self.authorization(code) for code in codes]
                if all(g.status is GrantStatus.PENDING for g in checked):
                    reason = "传承人授权尚未确认"
                elif any(g.status is GrantStatus.REJECTED for g in checked):
                    reason = "传承人授权已被拒绝"
                elif any(g.status is GrantStatus.REVOKED for g in checked):
                    reason = "传承人授权已被撤销"
                elif any(g.status is GrantStatus.EXPIRED for g in checked):
                    reason = "传承人授权已过期"
                else:
                    reason = "挂接授权不覆盖该场次类型或时间"
            raise AuthorizationError(
                f"内容 {content_ref} 未获许可：{reason}"
            )
        return valid

    def usable_for_session(
        self,
        content_refs: Iterable[str],
        session_type: str,
        moment: datetime,
        auth_map: dict[str, tuple[str, ...]],
    ) -> dict[str, tuple[str, ...]]:
        """批量校验场次内容，返回每个内容生效的授权编号。"""

        result: dict[str, tuple[str, ...]] = {}
        for content_ref in content_refs:
            result[content_ref] = tuple(
                self.assert_usable(
                    content_ref,
                    session_type,
                    moment,
                    auth_codes=auth_map.get(content_ref, ()),
                )
            )
        return result

    def replace(self, version: ContentVersion, **changes: Any) -> ContentVersion:
        """仅供测试/投影使用的非变更复制，不写回台账。"""

        return replace(version, **changes)
