"""皮影剧场后台的领域错误。"""

from __future__ import annotations


class ShadowPlayError(Exception):
    """所有领域规则冲突的基类，便于上层统一捕获。"""


class ContentNotPublishable(ShadowPlayError):
    """公开场次使用了未确认剧本、未授权素材或来源/范围不明的 AI 动画。"""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("；".join(problems))


class ResourceConflict(ShadowPlayError):
    """教具或场地容量不足以按实际人数占用，存在双重分配风险。"""


class CapacityExceeded(ShadowPlayError):
    """预约人数超过工作坊或场地容量。"""


class IllegalTransition(ShadowPlayError):
    """场次或预约状态不允许该操作（如取消已完成场次）。"""


class QualificationMismatch(ShadowPlayError):
    """志愿者资质、语言或技能不满足场次要求。"""


class NotFound(ShadowPlayError):
    """引用的内容、场次、人员或资源不存在。"""
