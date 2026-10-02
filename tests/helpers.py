"""测试共用构造：搭建一个最小可运营的月下剧场后台。

所有姓名均为虚构示例，不含真实个人信息。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from src.shadow_play.content import (
    AiProvenance,
    ContentKind,
    ContentRegistry,
    RightHolder,
)
from src.shadow_play.resources import (
    AssignmentFunction,
    KitType,
    KitUnit,
    Person,
    PersonRole,
    ResourceRegistry,
    TimeWindow,
    Venue,
)
from src.shadow_play.sessions import ProgramSystem, SessionType

OCT5 = date(2026, 10, 5)
PREPARE_AT = datetime(2026, 10, 1, 9, 0)


def make_window(hour_start: int, hour_end: int, day: date = OCT5) -> TimeWindow:
    return TimeWindow(datetime(day.year, day.month, day.day, hour_start),
                      datetime(day.year, day.month, day.day, hour_end))


def build_system() -> SimpleNamespace:
    content = ContentRegistry()
    resources = ResourceRegistry()

    holder = RightHolder("RH-001", "陈姓传承人（示例）")
    other_holder = RightHolder("RH-002", "林姓传承人（示例）")

    # 已确认剧本：1，授权覆盖三类用途
    script = content.submit_version(
        "MULAN-01", ContentKind.SCRIPT, "木兰从军·潮俗皮影本", PREPARE_AT
    )
    script.confirm(PREPARE_AT)
    grant_script = content.request_authorization(
        "MULAN-01:1",
        holder,
        ["performance", "workshop", "exhibit"],
        date(2026, 12, 31),
        PREPARE_AT,
    )
    grant_script.grant(PREPARE_AT, note="示例授权：全年展期")

    # AI 动画：来源与适用范围齐备，仅限公开演出
    provenance = AiProvenance(
        model="示例动画模型 X",
        prompt_ref="PR-MOON-01",
        source_works=("MULAN-01",),
        scope_session_types=("performance",),
        valid_from=date(2026, 9, 1),
        valid_until=date(2026, 11, 30),
    )
    ai = content.submit_version(
        "AI-MOON", ContentKind.AI_ANIMATION, "月下追月·AI 动画",
        PREPARE_AT, ai_provenance=provenance,
    )
    ai.confirm(PREPARE_AT)
    grant_ai = content.request_authorization(
        "AI-MOON:1", holder, ["performance"], date(2026, 11, 30), PREPARE_AT
    )
    grant_ai.grant(PREPARE_AT)

    # 场地
    resources.register_venue(Venue("MOON-HALL", "月下剧场主厅", 200, ("performance",)))
    resources.register_venue(Venue("ROOM-A", "互动教室 A", 12, ("workshop",)))
    resources.register_venue(Venue("ROOM-B", "互动教室 B", 12, ("workshop",)))

    # 教具：雕刻套 13 套
    resources.register_kit_type(KitType("CARVING", "皮影雕刻教具套", 1))
    for no in range(1, 14):
        resources.register_kit_unit(KitUnit("CARVING", f"K{no:02d}"))

    # 人员：责任人、双语讲解、有资质志愿者
    resources.register_person(
        Person("ST-01", "周姓负责人（示例）", PersonRole.STAFF)
    )
    resources.register_person(
        Person(
            "NR-01", "吴姓讲解（示例）", PersonRole.NARRATOR,
            languages=frozenset({"zh", "en"}),
        )
    )
    resources.register_person(
        Person(
            "VL-01", "郑姓志愿者（示例）", PersonRole.VOLUNTEER,
            languages=frozenset({"zh"}),
            qualifications=frozenset({"workshop:carving"}),
        )
    )

    system = ProgramSystem(content=content, resources=resources)

    # 晚间公开演出（含 AI 动画）
    perf = system.schedule_session(
        "P1", "中秋夜·潮俗皮影展演", SessionType.PERFORMANCE,
        make_window(19, 20), "MOON-HALL", event_code="MID-AUTUMN",
    )
    system.use_content("P1", "MULAN-01:1", [grant_script.code])
    system.use_content("P1", "AI-MOON:1", [grant_ai.code])
    system.set_owner("P1", "ST-01")
    system.offer_language("P1", "zh")
    system.assign_staff("P1", "NR-01", AssignmentFunction.NARRATION,
                        languages=frozenset({"zh"}))

    # 下午工作坊
    workshop = system.schedule_session(
        "W1", "动手做皮影·雕刻工作坊", SessionType.WORKSHOP,
        make_window(14, 15), "ROOM-A", event_code="MID-AUTUMN", capacity=12,
    )
    system.use_content("W1", "MULAN-01:1", [grant_script.code])
    system.set_owner("W1", "ST-01")
    system.require_kit("W1", "CARVING")
    system.offer_language("W1", "zh")
    system.assign_staff("W1", "VL-01", AssignmentFunction.WORKSHOP_FACILITATOR,
                        qualification="workshop:carving")

    return SimpleNamespace(
        system=system,
        content=content,
        resources=resources,
        holder=holder,
        other_holder=other_holder,
        grant_script=grant_script,
        grant_ai=grant_ai,
        perf=perf,
        workshop=workshop,
        prepare_at=PREPARE_AT,
    )
