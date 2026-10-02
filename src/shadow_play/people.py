"""参与方：非遗传承人、志愿者、参与者。

参与方对象只承载身份与资质，不做真实个人信息存储：演示和测试中一律使用
代号或昵称，后台不接收手机号、证件号等敏感信息。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ArtisanRole(str, Enum):
    """传承链条上的身份，用于授权主体核验。"""

    INHERITOR = "非遗传承人"
    APPRENTICE = "学徒"
    INVITED_ARTIST = "特邀艺人"


@dataclass(frozen=True)
class Artisan:
    """非遗传承人或传统艺人，是剧本、图像等素材的授权来源。"""

    id: str
    name: str
    role: ArtisanRole
    schools: frozenset[str] = frozenset()  # 潮俗皮影流派

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("传承人编号与称谓不能为空")


@dataclass(frozen=True)
class Volunteer:
    """讲解与互动协助志愿者，资质按语言和技能两个维度登记。"""

    id: str
    name: str
    languages: frozenset[str]          # 讲解语言，如 {"普通话", "粤语", "英语"}
    skills: frozenset[str]             # 互动技能，如 {"皮影制作", "引导"}
    active: bool = True

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("志愿者编号与称谓不能为空")

    def meets(self, languages: frozenset[str], skills: frozenset[str]) -> bool:
        return languages <= self.languages and skills <= self.skills


@dataclass(frozen=True)
class Participant:
    """预约参与者；后台只存代号，不留真实身份信息。"""

    id: str
    label: str

    def __post_init__(self) -> None:
        if not self.id or not self.label:
            raise ValueError("参与者编号与称谓不能为空")


@dataclass(frozen=True)
class Person:
    """场次责任人：运营或物资管理岗位上的负责人（岗位代号）。"""

    id: str
    name: str
    title: str

    def __post_init__(self) -> None:
        if not self.id or not self.name:
            raise ValueError("责任人编号与称谓不能为空")
