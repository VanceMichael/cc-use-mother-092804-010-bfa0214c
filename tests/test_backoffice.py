"""皮影剧场后台的领域规则测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta

from src.shadow_play import (
    AIGenerated,
    BackOffice,
    BookingStatus,
    ContentKind,
    ContentNotPublishable,
    EquipmentKind,
    EquipmentPool,
    EquipmentState,
    EquipmentUnit,
    IllegalTransition,
    License,
    LicenseStatus,
    Participant,
    Person,
    Program,
    QualificationMismatch,
    ResourceConflict,
    ResourceLevel,
    SessionKind,
    SessionStatus,
    Usage,
    Venue,
    VersionStatus,
    Volunteer,
    VolunteerRequirement,
)
from src.shadow_play.bookings import LATE_GRACE
from src.shadow_play.content import ContentVersion
from src.shadow_play.demo import build_office

DAY = date(2026, 9, 26)


class World:
    """测试用小世界搭建器。"""

    def __init__(
        self,
        kits: int = 10,
        stands: int = 3,
        workshop_capacity: int = 16,
    ) -> None:
        self.program = Program()
        self.master = type(self.program) and _artisan()
        self.pool = EquipmentPool()
        self.pool.register_kind(EquipmentKind("PK", "皮影制作包", True))
        self.pool.register_kind(EquipmentKind("LS", "灯箱幕架", False))
        for i in range(1, kits + 1):
            self.pool.add_unit(EquipmentUnit(f"PK-{i:02d}", "PK"))
        for i in range(1, stands + 1):
            self.pool.add_unit(EquipmentUnit(f"LS-{i:02d}", "LS"))
        self.office = BackOffice(self.program, self.pool)

        self.owner = Person("P-01", "运营甲（代号）", "运营负责人")
        self.volunteer = Volunteer(
            "V-01", "志愿者甲（代号）",
            frozenset({"普通话", "粤语", "英语"}),
            frozenset({"皮影制作", "引导"}),
        )
        self.volunteer_b = Volunteer(
            "V-02", "志愿者乙（代号）",
            frozenset({"普通话", "粤语", "英语"}),
            frozenset({"皮影制作", "引导"}),
        )
        self.office.register_volunteer(self.volunteer)
        self.office.register_volunteer(self.volunteer_b)
        self.theater = Venue("VN-T", "月下剧场", 120)
        self.craft = Venue(
            "VN-C", "手艺坊", workshop_capacity, supports_workshop=True
        )
        self.craft_b = Venue(
            "VN-C2", "手艺坊二室", workshop_capacity, supports_workshop=True
        )

    def license(self, lid: str, status: LicenseStatus = LicenseStatus.CONFIRMED,
                expires: date | None = date(2027, 1, 1)) -> None:
        self.program.add_license(License(
            id=lid, licensor=self.master, title=f"授权 {lid}",
            status=status, granted_on=date(2026, 1, 1), expires_on=expires,
        ))

    def content(self, cid: str, title: str, kind: ContentKind,
                licenses: tuple[str, ...], *,
                version: int = 1, status: VersionStatus = VersionStatus.CONFIRMED,
                ai: AIGenerated | None = None) -> None:
        self.program.register_artwork(cid, title)
        self.program.add_version(ContentVersion(
            content_id=cid, version=version, title=title, kind=kind,
            status=status, required_licenses=licenses,
            created_on=DAY - timedelta(days=10), ai=ai,
        ))

    def performance(self, sid="S-1", refs=None, when=None,
                    volunteers=None, requirement=None) -> object:
        return self.office.schedule_session(
            sid, SessionKind.PERFORMANCE, self.theater,
            when or datetime(2026, 9, 26, 19, 30),
            language="普通话", owner=self.owner,
            content_refs=refs or [("C-SCRIPT", 1)],
            duration_minutes=60,
            volunteers=volunteers if volunteers is not None else [self.volunteer],
            volunteer_requirement=requirement,
        )

    def workshop(self, sid="S-WS", when=None, groups=1, plan=None,
                 venue=None, volunteer=None, requirement=None,
                 refs=None) -> object:
        return self.office.schedule_session(
            sid, SessionKind.WORKSHOP, venue or self.craft,
            when or datetime(2026, 9, 26, 14, 0),
            language="粤语", owner=self.owner,
            content_refs=refs or [("C-SCRIPT", 1)],
            duration_minutes=90, groups=groups,
            volunteers=[volunteer or self.volunteer],
            volunteer_requirement=requirement,
            equipment_plan=plan if plan is not None else {"PK": "person", "LS": "group"},
        )

    def reserve(self, bid: str, sid: str, size: int = 1) -> object:
        return self.office.reserve(
            bid, sid, Participant(f"U-{bid}", f"游客{bid}（代号）"), size
        )


def _artisan():
    from src.shadow_play import Artisan, ArtisanRole

    return Artisan("A-01", "陈师傅（代号）", ArtisanRole.INHERITOR,
                   frozenset({"潮俗皮影"}))


def _ai(scope: frozenset[Usage], tool: str = "月影模型", provider: str = "志愿团队"):
    return AIGenerated(tool=tool, provider=provider, scope=scope)


class ContentGateTest(unittest.TestCase):
    def test_confirmed_script_and_license_can_go_public(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        session = w.performance()
        self.assertIs(session.status, SessionStatus.SCHEDULED)

    def test_unconfirmed_version_is_rejected(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.program.artwork("C-SCRIPT").add_version(ContentVersion(
            "C-SCRIPT", 2, "月下追舟·修改稿", ContentKind.SCRIPT,
            VersionStatus.DRAFT, ("L1",), DAY))
        with self.assertRaises(ContentNotPublishable) as ctx:
            w.performance(refs=[("C-SCRIPT", 2)])
        self.assertIn("已确认", str(ctx.exception))

    def test_pending_and_expired_licenses_block_public_use(self) -> None:
        w = World()
        w.license("LP", LicenseStatus.PENDING, expires=None)
        w.content("CP", "待授权图像", ContentKind.IMAGE, ("LP",))
        with self.assertRaises(ContentNotPublishable) as ctx:
            w.performance(refs=[("CP", 1)])
        self.assertIn("待确认", str(ctx.exception))

        w.license("LE", LicenseStatus.CONFIRMED, expires=date(2026, 9, 1))
        w.content("CE", "过期授权图像", ContentKind.IMAGE, ("LE",))
        with self.assertRaises(ContentNotPublishable) as ctx:
            w.performance(refs=[("CE", 1)])
        self.assertIn("已过期", ctx.exception.problems[0])

    def test_unlicensed_material_cannot_enter_event(self) -> None:
        w = World()
        # 素材未登记进内容库：场次根本无法引用
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        from src.shadow_play import NotFound

        with self.assertRaises(NotFound):
            w.performance(refs=[("C-GHOST", 1)])

        # 版本声明了从未登记的授权（未获许可）：版本不能入库
        art = w.program.register_artwork("C-X", "来路不明图像")
        with self.assertRaises(KeyError):
            w.program.add_version(ContentVersion(
                "C-X", 1, "来路不明图像", ContentKind.IMAGE,
                VersionStatus.CONFIRMED, ("L-MISSING",), DAY))

    def test_ai_animation_must_declare_source_and_scope(self) -> None:
        w = World()
        w.license("L1")
        with self.assertRaises(ValueError):
            w.content("C-AI", "无范围动画", ContentKind.AI_ANIMATION, ("L1",),
                      ai=_ai(frozenset()))
        with self.assertRaises(ValueError):
            w.content("C-AI2", "无来源动画", ContentKind.AI_ANIMATION, ("L1",),
                      ai=_ai(frozenset({Usage.PUBLIC_PERFORMANCE}), tool=" "))

        # 范围只覆盖展陈，不能进公开演出
        w.content("C-AI3", "展陈限定动画", ContentKind.AI_ANIMATION, ("L1",),
                  ai=_ai(frozenset({Usage.EXHIBITION})))
        with self.assertRaises(ContentNotPublishable) as ctx:
            w.performance(refs=[("C-AI3", 1)])
        self.assertIn("适用范围", str(ctx.exception))

        # 范围覆盖公开演出且标注完整：可以上演，并在依据中可见来源
        w.content("C-AI4", "中秋动画", ContentKind.AI_ANIMATION, ("L1",),
                  ai=_ai(frozenset({Usage.PUBLIC_PERFORMANCE})))
        w.performance(refs=[("C-AI4", 1)])
        basis = w.office.overview("S-1").content_basis[0]
        self.assertIn("AI 来源", basis)
        self.assertIn("公开演出", basis)

    def test_versions_must_increase_and_history_keeps_snapshot(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        with self.assertRaises(ValueError):
            w.program.add_version(ContentVersion(
                "C-SCRIPT", 1, "重复版本", ContentKind.SCRIPT,
                VersionStatus.DRAFT, ("L1",), DAY))
        session = w.performance(refs=[("C-SCRIPT", 1)])
        w.office.complete_session(session.id)
        # 已完成场次保留版本号与标题快照，不依赖内容库后续状态
        ref = session.program_items[0]
        self.assertEqual((ref.content_id, ref.version), ("C-SCRIPT", 1))
        self.assertEqual(ref.title, "月下追舟")


class QualificationTest(unittest.TestCase):
    def test_language_qualification_is_checked_at_scheduling(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        mandarin_only = Volunteer(
            "V-9", "志愿者乙（代号）", frozenset({"普通话"}),
            frozenset({"引导"}))
        w.office.register_volunteer(mandarin_only)
        with self.assertRaises(QualificationMismatch):
            w.performance(
                sid="S-EN",
                volunteers=[mandarin_only],
                requirement=VolunteerRequirement(
                    languages=frozenset({"英语"}), minimum=1),
            )


class ResourceSchedulingTest(unittest.TestCase):
    def test_same_equipment_cannot_be_double_booked(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        when = datetime(2026, 9, 26, 14, 0)
        w.workshop("S-A", when=when, plan={"PK": "person"})
        w.workshop("S-B", when=when + timedelta(minutes=30),
                   venue=w.craft_b, volunteer=w.volunteer_b,
                   plan={"PK": "person"})
        for i in range(8):
            w.reserve(f"A{i}", "S-A")
        w.reserve("B1", "S-B")
        w.reserve("B2", "S-B")
        with self.assertRaises(ResourceConflict):
            w.reserve("B3", "S-B")  # 8+3=11 > 10
        units_a = w.office.sessions["S-A"].allocated_units()
        units_b = w.office.sessions["S-B"].allocated_units()
        self.assertTrue(units_a.isdisjoint(units_b))

    def test_back_to_back_sessions_share_the_same_units(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop("S-A", when=datetime(2026, 9, 26, 13, 0),
                   plan={"PK": "person"})  # 13:00-14:30
        w.workshop("S-B", when=datetime(2026, 9, 26, 14, 30),
                   plan={"PK": "person"})  # 14:30-16:00
        w.reserve("A1", "S-A", size=10)
        w.reserve("B1", "S-B", size=10)  # 不重叠，可复用全部 10 套
        self.assertEqual(
            w.office.sessions["S-A"].allocated_units(),
            w.office.sessions["S-B"].allocated_units(),
        )

    def test_regroup_changes_group_shared_equipment(self) -> None:
        w = World(stands=3)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop("S-WS", groups=2, plan={"LS": "group"})
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("LS"), 2)
        w.office.regroup("S-WS", 3)
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("LS"), 3)
        with self.assertRaises(ResourceConflict):
            w.office.regroup("S-WS", 4)  # 只有 3 套
        self.assertEqual(w.office.sessions["S-WS"].groups, 3)
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("LS"), 3)

    def test_capacity_is_enforced(self) -> None:
        w = World(workshop_capacity=2)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop(plan={"PK": "person"})
        w.reserve("B1", "S-WS", size=2)
        from src.shadow_play import CapacityExceeded

        with self.assertRaises(CapacityExceeded):
            w.reserve("B2", "S-WS")

    def test_damage_frees_unit_and_reallocates_when_spare_exists(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop(plan={"PK": "person"})
        w.reserve("B1", "S-WS", size=4)
        before = set(w.office.sessions["S-WS"].allocations["PK"])
        damaged = sorted(before)[1]
        w.office.report_damage(damaged, "幕杆断裂")
        after = set(w.office.sessions["S-WS"].allocations["PK"])
        self.assertNotIn(damaged, after)
        self.assertEqual(len(after), 4)
        self.assertIs(w.pool.unit(damaged).state, EquipmentState.DAMAGED)

    def test_damage_without_spare_is_reported_as_shortage(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop(plan={"PK": "person"})
        w.reserve("B1", "S-WS", size=10)
        with self.assertRaises(ResourceConflict):
            w.office.report_damage("PK-05", "材料缺失")
        session = w.office.sessions["S-WS"]
        self.assertNotIn("PK-05", session.allocated_units())
        self.assertEqual(session.allocated_count("PK"), 9)
        overview = w.office.overview("S-WS")
        self.assertEqual(
            overview.water[0].level, ResourceLevel.SHORTAGE
        )

    def test_venue_and_volunteer_cannot_be_double_used(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        when = datetime(2026, 9, 26, 14, 0)
        w.workshop("S-A", when=when, plan={"PK": "person"})
        # 同场地时间重叠
        with self.assertRaises(ResourceConflict):
            w.workshop("S-B", when=when + timedelta(minutes=30),
                       volunteer=w.volunteer_b, plan={"PK": "person"})
        # 不同场地但同一志愿者
        with self.assertRaises(ResourceConflict):
            w.workshop("S-C", when=when + timedelta(minutes=30),
                       venue=w.craft_b, plan={"PK": "person"})
        # 错开时间（14:00+90=15:30）则正常
        w.workshop("S-D", when=datetime(2026, 9, 26, 15, 30),
                   plan={"PK": "person"})

    def test_last_minute_extra_session_goes_through_same_gate(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop("S-A", when=datetime(2026, 9, 26, 14, 0),
                   plan={"PK": "person"})
        w.reserve("A1", "S-A", size=10)
        # 同时间临时加场（不同场地、不同志愿者）：排期允许（尚无人），
        # 但第一个预约就撞上教具互斥
        w.workshop("S-EXTRA", when=datetime(2026, 9, 26, 14, 0),
                   venue=w.craft_b, volunteer=w.volunteer_b,
                   plan={"PK": "person"})
        with self.assertRaises(ResourceConflict):
            w.reserve("X1", "S-EXTRA")
        self.assertEqual(
            w.office.sessions["S-EXTRA"].allocated_units(), frozenset()
        )

        # 加场若想使用未确认内容，则在排期闸门直接被拒
        w.license("LP", LicenseStatus.PENDING, expires=None)
        w.content("CP", "待授权图像", ContentKind.IMAGE, ("LP",))
        with self.assertRaises(ContentNotPublishable):
            w.performance(
                sid="S-EXTRA-2",
                when=datetime(2026, 9, 26, 20, 0),
                refs=[("CP", 1)],
            )


class BookingLifecycleTest(unittest.TestCase):
    def _world_with_booking(self, size=10):
        w = World(kits=10, workshop_capacity=16)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop(plan={"PK": "person"})
        for i in range(1, size + 1):
            w.reserve(f"B{i:02d}", "S-WS")
        return w

    def test_idempotent_check_in_returns_original_receipt(self) -> None:
        w = self._world_with_booking(1)
        when = datetime(2026, 9, 26, 14, 0)
        first = w.office.check_in("B01", when)
        second = w.office.check_in("B01", when + timedelta(minutes=30))
        self.assertIs(first, second)
        self.assertEqual(first.receipt, second.receipt)
        self.assertFalse(first.late)

    def test_late_check_in_is_flagged_but_keeps_resources(self) -> None:
        w = self._world_with_booking(10)
        late_at = datetime(2026, 9, 26, 14, 0) + LATE_GRACE + timedelta(minutes=1)
        receipt = w.office.check_in("B01", late_at)
        self.assertTrue(receipt.late)
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("PK"), 10)

    def test_no_show_releases_resources(self) -> None:
        w = self._world_with_booking(10)
        w.office.mark_no_show("B01")
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("PK"), 9)
        self.assertIs(w.office.bookings["B01"].status, BookingStatus.NO_SHOW)

    def test_cancelled_booking_cannot_check_in(self) -> None:
        w = self._world_with_booking(1)
        w.office.cancel_booking("B01")
        with self.assertRaises(IllegalTransition):
            w.office.check_in("B01", datetime(2026, 9, 26, 14, 0))

    def test_failed_reservation_rolls_back(self) -> None:
        w = self._world_with_booking(10)
        with self.assertRaises(ResourceConflict):
            w.reserve("BX", "S-WS")
        self.assertNotIn("BX", w.office.bookings)
        self.assertEqual(w.office.sessions["S-WS"].allocated_count("PK"), 10)


class CancelAndHistoryTest(unittest.TestCase):
    def test_cancel_only_affects_future_sessions(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop("S-FUTURE", when=datetime(2026, 9, 26, 14, 0),
                   plan={"PK": "person"})
        w.workshop("S-DONE", when=datetime(2026, 9, 26, 10, 0),
                   plan={"PK": "person"})
        w.reserve("F1", "S-FUTURE", size=10)
        w.reserve("D1", "S-DONE", size=5)
        w.office.check_in("D1", datetime(2026, 9, 26, 10, 0))
        w.office.complete_session("S-DONE")

        with self.assertRaises(IllegalTransition):
            w.office.cancel_session("S-DONE")
        w.office.cancel_session("S-FUTURE")

        self.assertIs(w.office.sessions["S-FUTURE"].status,
                      SessionStatus.CANCELLED)
        self.assertIs(w.office.bookings["F1"].status, BookingStatus.CANCELLED)
        self.assertEqual(w.office.sessions["S-FUTURE"].allocations, {})
        # 已完成演出：签到记录、内容依据保留
        self.assertIs(w.office.bookings["D1"].status, BookingStatus.CHECKED_IN)
        self.assertEqual(
            w.office.sessions["S-DONE"].program_items[0].title, "月下追舟"
        )
        self.assertIs(w.office.sessions["S-DONE"].status,
                      SessionStatus.COMPLETED)

    def test_cannot_cancel_booking_of_completed_session(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.workshop("S-DONE", when=datetime(2026, 9, 26, 10, 0))
        w.reserve("D1", "S-DONE")
        w.office.complete_session("S-DONE")
        with self.assertRaises(IllegalTransition):
            w.office.cancel_booking("D1")

    def test_revoked_license_after_completion_keeps_performance_history(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.performance(when=datetime(2026, 9, 26, 19, 30))
        w.office.complete_session("S-1")
        # 授权事后被撤销，作品授权记录与场次依据仍可追溯
        w.program.update_license(License(
            id="L1", licensor=w.master, title="授权 L1",
            status=LicenseStatus.REJECTED))
        overview = w.office.overview("S-1")
        self.assertEqual(overview.session.status, SessionStatus.COMPLETED)
        self.assertTrue(overview.content_basis)
        self.assertIn("月下追舟", overview.content_basis[0])


class OverviewTest(unittest.TestCase):
    def test_overview_shows_basis_water_owner_and_pending_license(self) -> None:
        w = World(kits=10)
        w.license("L1")
        w.license("L2", LicenseStatus.PENDING, expires=None)
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.content("C-IMG", "新编图谱", ContentKind.IMAGE, ("L2",))
        # 待授权内容无法排进公开场次；先看一个正常场次的完整总览
        w.workshop(groups=2)
        w.reserve("B1", "S-WS", size=8)
        overview = w.office.overview("S-WS")
        data = overview.as_dict()
        self.assertEqual(data["责任人"], "运营甲（代号）（运营负责人）")
        self.assertTrue(data["内容依据"])
        self.assertEqual(data["待补授权"], [])
        water = {row["教具"]: row for row in data["资源水位"]}
        self.assertEqual(water["皮影制作包"]["需要"], 8)
        self.assertEqual(water["皮影制作包"]["已分配"], 8)
        self.assertEqual(water["灯箱幕架"]["需要"], 2)
        self.assertEqual(data["志愿者问题"], [])

    def test_revoked_license_after_scheduling_shows_up_as_pending(self) -> None:
        w = World()
        w.license("L1")
        w.content("C-SCRIPT", "月下追舟", ContentKind.SCRIPT, ("L1",))
        w.performance(when=datetime(2026, 9, 27, 19, 30))
        self.assertEqual(w.office.overview("S-1").pending_licenses, [])
        # 传承人临时撤销授权：未发生场次立即在总览中提示待补
        w.program.update_license(License(
            id="L1", licensor=w.master, title="授权 L1",
            status=LicenseStatus.REJECTED))
        pending = w.office.overview("S-1").pending_licenses
        self.assertTrue(pending)
        self.assertIn("未获许可", pending[0])

    def test_demo_office_overview_is_renderable(self) -> None:
        office = build_office()
        for sid in ("S-PERF-1001", "S-WS-2001"):
            data = office.overview(sid).as_dict()
            self.assertTrue(data["内容依据"])
            self.assertIn("责任人", data)
        # 演示工作坊预约 12 人（2+3+2+1+4）：12 个制作包、2 组灯箱
        ws = office.sessions["S-WS-2001"]
        self.assertEqual(ws.allocated_count("PK"), 12)
        self.assertEqual(ws.allocated_count("LS"), 2)


if __name__ == "__main__":
    unittest.main()
