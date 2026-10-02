"""演示数据工厂：搭建一座"月下剧场"活动日的完整后台。

所有人物均为代号化虚构身份，不含真实个人信息；可通过
``python3 -m src.shadow_play.demo`` 直接查看场次总览。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from .content import (
    AIGenerated,
    ContentKind,
    License,
    LicenseStatus,
    Program,
    Usage,
    VersionStatus,
)
from .office import BackOffice
from .people import Artisan, ArtisanRole, Participant, Person, Volunteer
from .resources import (
    EquipmentKind,
    EquipmentPool,
    EquipmentUnit,
    Venue,
    VolunteerRequirement,
)
from .sessions import SessionKind

EVENT_DAY = date(2026, 9, 26)


def build_office() -> BackOffice:
    # ---- 内容库与授权 --------------------------------------------------
    program = Program()
    master = Artisan(
        id="A-01",
        name="陈师傅（代号）",
        role=ArtisanRole.INHERITOR,
        schools=frozenset({"潮俗皮影"}),
    )

    program.add_license(
        License(
            id="L-SCRIPT-01",
            licensor=master,
            title="《月下追舟》剧本公开演出与工作坊授权",
            status=LicenseStatus.CONFIRMED,
            granted_on=date(2026, 1, 1),
            expires_on=date(2027, 1, 1),
        )
    )
    program.add_license(
        License(
            id="L-IMAGE-01",
            licensor=master,
            title="追舟影人图谱图像授权",
            status=LicenseStatus.CONFIRMED,
            granted_on=date(2026, 1, 1),
            expires_on=date(2027, 1, 1),
        )
    )
    program.add_license(
        License(
            id="L-IMAGE-02",
            licensor=master,
            title="新编影人图像授权（传承人尚在确认）",
            status=LicenseStatus.PENDING,
        )
    )

    ai = AIGenerated(
        tool="月影动画模型 v3",
        provider="海滨数字内容志愿团队",
        scope=frozenset({Usage.PUBLIC_PERFORMANCE, Usage.EXHIBITION}),
        source_materials=("追舟影人图谱",),
        note="仅用于中秋故事段落，需与影人图谱同场呈现",
    )

    script = program.register_artwork("C-SCRIPT", "月下追舟")
    program.add_version(
        _version(
            "C-SCRIPT", 1, "月下追舟", ContentKind.SCRIPT,
            ("L-SCRIPT-01",),
        )
    )
    image = program.register_artwork("C-IMAGE", "追舟影人图谱")
    program.add_version(
        _version(
            "C-IMAGE", 1, "追舟影人图谱", ContentKind.IMAGE,
            ("L-IMAGE-01",),
        )
    )
    unreviewed = program.register_artwork("C-IMAGE-NEW", "新编影人（待授权）")
    program.add_version(
        _version(
            "C-IMAGE-NEW", 1, "新编影人（待授权）", ContentKind.IMAGE,
            ("L-IMAGE-02",),
            status=VersionStatus.CONFIRMED,  # 版本确认但授权仍待确认
        )
    )
    ai_film = program.register_artwork("C-AI", "中秋奇缘·AI 动画")
    program.add_version(
        _version(
            "C-AI", 1, "中秋奇缘·AI 动画", ContentKind.AI_ANIMATION,
            ("L-IMAGE-01",), ai=ai,
        )
    )

    # ---- 教具库存 ------------------------------------------------------
    pool = EquipmentPool()
    pool.register_kind(EquipmentKind("PK", "皮影制作包", per_person=True))
    pool.register_kind(EquipmentKind("LS", "灯箱幕架", per_person=False))
    for i in range(1, 13):
        pool.add_unit(EquipmentUnit(id=f"PK-{i:02d}", kind_id="PK"))
    for i in range(1, 4):
        pool.add_unit(EquipmentUnit(id=f"LS-{i:02d}", kind_id="LS"))

    office = BackOffice(program, pool)

    # ---- 人员 ----------------------------------------------------------
    owner_ops = Person(id="P-01", name="周岚（代号）", title="运营负责人")
    owner_assets = Person(id="P-02", name="吴桐（代号）", title="物资管理员")

    v_mandarin_cantonese = Volunteer(
        id="V-01", name="阿琳（代号）",
        languages=frozenset({"普通话", "粤语"}),
        skills=frozenset({"皮影制作", "引导"}),
    )
    v_english = Volunteer(
        id="V-02", name="Ben（代号）",
        languages=frozenset({"英语", "普通话"}),
        skills=frozenset({"引导"}),
    )
    office.register_volunteer(v_mandarin_cantonese)
    office.register_volunteer(v_english)

    # ---- 场地 ----------------------------------------------------------
    theater = Venue(id="VN-THEATER", name="月下剧场", capacity=120)
    workshop_room = Venue(
        id="VN-CRAFT", name="手艺坊", capacity=16, supports_workshop=True
    )

    # ---- 场次 ----------------------------------------------------------
    office.schedule_session(
        "S-PERF-1001",
        SessionKind.PERFORMANCE,
        theater,
        datetime(2026, 9, 26, 19, 30),
        language="普通话",
        owner=owner_ops,
        content_refs=[("C-SCRIPT", 1), ("C-IMAGE", 1), ("C-AI", 1)],
        duration_minutes=75,
        volunteer_requirement=VolunteerRequirement(
            languages=frozenset({"普通话"}),
            skills=frozenset({"引导"}),
            minimum=1,
        ),
        volunteers=[v_mandarin_cantonese],
    )
    office.schedule_session(
        "S-WS-2001",
        SessionKind.WORKSHOP,
        workshop_room,
        datetime(2026, 9, 26, 14, 0),
        language="粤语",
        owner=owner_assets,
        content_refs=[("C-SCRIPT", 1), ("C-IMAGE", 1)],
        duration_minutes=90,
        groups=2,
        volunteer_requirement=VolunteerRequirement(
            languages=frozenset({"粤语"}),
            skills=frozenset({"皮影制作", "引导"}),
            minimum=1,
        ),
        volunteers=[v_mandarin_cantonese],
        equipment_plan={"PK": "person", "LS": "group"},
    )

    # ---- 预约（代号化参与者）------------------------------------------
    for idx, size in enumerate((2, 3, 2, 1, 4), start=1):
        office.reserve(
            f"B-{idx:03d}",
            "S-WS-2001",
            Participant(id=f"G-{idx:02d}", label=f"游客组{idx}（代号）"),
            party_size=size,
        )
    return office


def _version(
    content_id: str,
    number: int,
    title: str,
    kind: ContentKind,
    licenses: tuple[str, ...],
    *,
    status: VersionStatus = VersionStatus.CONFIRMED,
    ai: AIGenerated | None = None,
):
    from .content import ContentVersion

    return ContentVersion(
        content_id=content_id,
        version=number,
        title=title,
        kind=kind,
        status=status,
        required_licenses=licenses,
        created_on=EVENT_DAY - timedelta(days=30),
        ai=ai,
    )


def main() -> None:
    import json

    office = build_office()
    for session_id in ("S-PERF-1001", "S-WS-2001"):
        print(json.dumps(office.overview(session_id).as_dict(),
                         ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
