"""演绎内容、版本与非遗传承人授权。

规则落点：
- 资料与剧本版本只能递增，旧版本不可改写（保留历史依据）；
- 公开演出只能采用状态为"已确认"的剧本与图像；
- 每个版本声明所依赖的授权，授权未确认或已过期则不得进入公开活动；
- 人工智能动画必须标明来源（工具/提供方）与适用范围，范围之外不得使用；
- 未获许可的素材没有登记或授权，自然无法通过发布闸门。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Iterator

from .errors import ContentNotPublishable
from .people import Artisan

ContentId = str


class ContentKind(str, Enum):
    SCRIPT = "剧本"
    IMAGE = "图像"
    AI_ANIMATION = "人工智能动画"
    MUSIC = "配乐"


class VersionStatus(str, Enum):
    DRAFT = "草稿"
    CONFIRMED = "已确认"
    ARCHIVED = "已归档"   # 归档后不再用于新场次，但历史场次保留快照


class LicenseStatus(str, Enum):
    PENDING = "待确认"
    CONFIRMED = "已确认"
    REJECTED = "未获许可"
    EXPIRED = "已过期"


class Usage(str, Enum):
    """素材的使用场景，AI 动画适用范围按此声明。"""

    PUBLIC_PERFORMANCE = "公开演出"
    EXHIBITION = "主题展陈"
    WORKSHOP = "互动工作坊"
    PROMOTION = "宣传物料"


@dataclass(frozen=True)
class AIGenerated:
    """人工智能动画的来源与适用范围标注，缺一项即不得公开使用。"""

    tool: str                       # 生成工具/模型
    provider: str                   # 提供方
    scope: frozenset[Usage]         # 授权适用范围
    source_materials: tuple[str, ...] = ()  # 参考素材登记（内容标题）
    note: str = ""

    def covers(self, usage: Usage) -> bool:
        return usage in self.scope


@dataclass(frozen=True)
class License:
    """非遗传承人授权记录。"""

    id: str
    licensor: Artisan
    title: str                  # 授权说明，如"《月下追舟》图像公开演出授权"
    status: LicenseStatus
    granted_on: date | None = None
    expires_on: date | None = None

    def effective_on(self, day: date) -> bool:
        return self.invalid_reason(day) is None

    def invalid_reason(self, day: date) -> str | None:
        """返回当日不可用的原因；有效时为 None。"""
        if self.status is not LicenseStatus.CONFIRMED:
            return self.status.value
        if self.granted_on and day < self.granted_on:
            return "尚未生效"
        if self.expires_on and day > self.expires_on:
            return "已过期"
        return None


@dataclass(frozen=True)
class ContentVersion:
    """同一内容的一个不可变版本。"""

    content_id: ContentId
    version: int
    title: str
    kind: ContentKind
    status: VersionStatus
    required_licenses: tuple[str, ...]
    created_on: date
    ai: AIGenerated | None = None

    @property
    def is_ai(self) -> bool:
        return self.kind is ContentKind.AI_ANIMATION or self.ai is not None


@dataclass
class Artwork:
    """一个演绎内容及其全部版本，新版本号必须严格递增。"""

    id: ContentId
    title: str
    versions: list[ContentVersion] = field(default_factory=list)

    def add_version(self, candidate: ContentVersion) -> ContentVersion:
        if candidate.content_id != self.id:
            raise ValueError("内容编号不一致")
        if self.versions:
            latest = max(v.version for v in self.versions)
            if candidate.version <= latest:
                raise ValueError("资料版本只能递增")
        elif candidate.version < 1:
            raise ValueError("首个版本号必须从 1 开始")
        self.versions.append(candidate)
        return candidate

    def version(self, number: int) -> ContentVersion:
        for v in self.versions:
            if v.version == number:
                return v
        raise KeyError(number)

    def latest_confirmed(self) -> ContentVersion | None:
        confirmed = [v for v in self.versions if v.status is VersionStatus.CONFIRMED]
        return max(confirmed, key=lambda v: v.version, default=None)


class Program:
    """内容库：登记内容版本与授权，并负责公开使用前的发布闸门。"""

    def __init__(self) -> None:
        self._artworks: dict[ContentId, Artwork] = {}
        self._licenses: dict[str, License] = {}

    # ---- 登记 ----------------------------------------------------------
    def register_artwork(self, content_id: ContentId, title: str) -> Artwork:
        if content_id in self._artworks:
            raise ValueError("内容已登记")
        artwork = Artwork(id=content_id, title=title)
        self._artworks[content_id] = artwork
        return artwork

    def add_license(self, license_: License) -> None:
        if license_.id in self._licenses:
            raise ValueError("授权编号重复")
        self._licenses[license_.id] = license_

    def update_license(self, license_: License) -> None:
        """更新授权状态（如传承人撤销、补确认）；编号必须已登记。"""
        if license_.id not in self._licenses:
            raise KeyError(license_.id)
        self._licenses[license_.id] = license_

    def add_version(self, version: ContentVersion) -> None:
        artwork = self._artworks.get(version.content_id)
        if artwork is None:
            raise KeyError(version.content_id)
        for license_id in version.required_licenses:
            if license_id not in self._licenses:
                # 未登记即未获许可，先登记、确认授权，版本才能进入活动
                raise KeyError(f"授权未登记: {license_id}")
        if version.ai is not None:
            if not version.ai.tool.strip() or not version.ai.provider.strip():
                raise ValueError("人工智能动画必须标明来源")
            if not version.ai.scope:
                raise ValueError("人工智能动画必须声明适用范围")
        artwork.add_version(version)

    # ---- 查询 ----------------------------------------------------------
    def artwork(self, content_id: ContentId) -> Artwork:
        return self._artworks[content_id]

    def license(self, license_id: str) -> License:
        return self._licenses[license_id]

    def confirmed_version(self, content_id: ContentId) -> ContentVersion:
        version = self._artworks[content_id].latest_confirmed()
        if version is None:
            raise KeyError(f"内容尚无已确认版本: {content_id}")
        return version

    def iter_artworks(self) -> Iterator[Artwork]:
        return iter(self._artworks.values())

    # ---- 发布闸门 -------------------------------------------------------
    def publishability_problems(
        self, version: ContentVersion, usage: Usage, day: date
    ) -> list[str]:
        """返回阻碍公开使用的问题清单；空清单表示可以公开使用。"""
        problems: list[str] = []
        if version.status is not VersionStatus.CONFIRMED:
            problems.append(
                f"{version.title} v{version.version} 状态为{version.status.value}，"
                "公开演出只能采用已确认版本"
            )
        for license_id in version.required_licenses:
            license_ = self._licenses[license_id]
            reason = license_.invalid_reason(day)
            if reason is not None:
                problems.append(
                    f"{version.title} 缺少有效授权 {license_.id}"
                    f"（{license_.title}：{reason}）"
                )
        if version.ai is not None:
            if not version.ai.tool.strip() or not version.ai.provider.strip():
                problems.append(f"{version.title} 未标明人工智能来源")
            if not version.ai.scope:
                problems.append(f"{version.title} 未声明适用范围")
            elif not version.ai.covers(usage):
                scopes = "、".join(s.value for s in version.ai.scope)
                problems.append(
                    f"{version.title} 的 AI 适用范围（{scopes}）不含{usage.value}"
                )
        return problems

    def ensure_publishable(
        self, version: ContentVersion, usage: Usage, day: date
    ) -> None:
        problems = self.publishability_problems(version, usage, day)
        if problems:
            raise ContentNotPublishable(problems)
