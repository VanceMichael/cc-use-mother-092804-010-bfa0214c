"""领域错误。所有业务规则冲突都抛出 ``ProgramError`` 的子类。"""

from __future__ import annotations


class ProgramError(Exception):
    """领域错误基类。"""


class ContentError(ProgramError):
    """演绎内容相关错误。"""


class VersionSequenceError(ContentError):
    """资料版本没有按递增顺序提交。"""


class ContentStateError(ContentError):
    """内容版本状态不允许该操作（如重复确认、修改已确认版本）。"""


class ProvenanceError(ContentError):
    """人工智能动画缺少来源或适用范围标注。"""


class AuthorizationError(ProgramError):
    """传承人授权相关错误。"""


class ResourceError(ProgramError):
    """资源排程相关错误。"""


class ResourceConflict(ResourceError):
    """同一资源在重叠时间窗内被重复占用。"""


class InsufficientResource(ResourceError):
    """可用资源数量不足。

    ``required`` 为需要数量，``available`` 为当前时间窗内可占用数量。
    """

    def __init__(self, message: str, *, required: int, available: int) -> None:
        super().__init__(message)
        self.required = required
        self.available = available


class QualificationError(ResourceError):
    """人员不具备该岗位所需资质或语言能力。"""


class SessionError(ProgramError):
    """场次相关错误。"""


class SessionStateError(SessionError):
    """场次状态不允许该操作。"""


class CapacityExceeded(SessionError):
    """预约或到场人数超过互动项目容量。"""


class PublishBlocked(SessionError):
    """场次发布条件未满足，``issues`` 列出全部待补事项。"""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("；".join(issues))
        self.issues = list(issues)
