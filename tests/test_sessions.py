"""场次生命周期、预约签到与现场资源调整测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from tests.helpers import OCT5, build_system, make_window
from src.shadow_play.content import GrantStatus
from src.shadow_play.errors import (
    CapacityExceeded,
    PublishBlocked,
    ResourceConflict,
    SessionStateError,
)
from src.shadow_play.resources import AssignmentFunction, TimeWindow
from src.shadow_play.sessions import (
    BookingStatus,
    ProgramSystem,
    SessionStatus,
    SessionType,
)

PUBLISH_AT = datetime(2026, 10, 1, 12, 0)
DAY5 = datetime(2026, 10, 5)


def at(hour: int, minute: int = 0, day: int = 5) -> datetime:
    return datetime(2026, 10, day, hour, minute)


class PublishGateTest(unittest.TestCase):
    def test_well_prepared_sessions_publish(self) -> None:
        ctx = build_system()
        ctx.system.publish("P1", at=PUBLISH_AT)
        ctx.system.publish("W1", at=PUBLISH_AT)
        self.assertEqual(ctx.perf.status, SessionStatus.PUBLISHED)
        self.assertEqual(ctx.workshop.status, SessionStatus.PUBLISHED)

    def test_unconfirmed_script_blocks_publish(self) -> None:
        ctx = build_system()
        draft = ctx.content.submit_version(
            "MULAN-02", ctx.content.get_version("MULAN-01:1").kind,
            "木兰从军·修订稿", PUBLISH_AT,
        )
        ctx.system.use_content("P1", "MULAN-02:1", [])
        with self.assertRaises(PublishBlocked) as caught:
            ctx.system.publish("P1", at=PUBLISH_AT)
        self.assertTrue(any("须使用已确认版本" in i for i in caught.exception.issues))

    def test_unlicensed_material_blocks_publish(self) -> None:
        ctx = build_system()
        # 工作坊只挂了 AI 动画，而 AI 授权与标注均不覆盖 workshop
        ctx.system.use_content("W1", "AI-MOON:1", [ctx.grant_ai.code])
        with self.assertRaises(PublishBlocked) as caught:
            ctx.system.publish("W1", at=PUBLISH_AT)
        self.assertTrue(
            any("适用范围" in i or "不覆盖" in i for i in caught.exception.issues)
        )

    def test_pending_authorization_blocks_publish(self) -> None:
        ctx = build_system()
        pending = ctx.content.request_authorization(
            "MULAN-01:1", ctx.other_holder, ["performance"],
            date(2026, 12, 31), PUBLISH_AT,
        )
        ctx.system.use_content("P1", "MULAN-01:1", [pending.code])
        with self.assertRaises(PublishBlocked):
            ctx.system.publish("P1", at=PUBLISH_AT)
        pending.grant(PUBLISH_AT)
        ctx.system.publish("P1", at=PUBLISH_AT)  # 补齐后可发布

    def test_missing_owner_language_and_facilitator_block_publish(self) -> None:
        ctx = build_system()
        system = ctx.system
        raw = ProgramSystem(content=ctx.content, resources=ctx.resources)
        s = raw.schedule_session(
            "WX", "新工作坊", SessionType.WORKSHOP,
            make_window(16, 17), "ROOM-B", capacity=6,
        )
        raw.use_content("WX", "MULAN-01:1", [ctx.grant_script.code])
        raw.require_kit("WX", "CARVING")
        raw.offer_language("WX", "en")
        with self.assertRaises(PublishBlocked) as caught:
            raw.publish("WX", at=PUBLISH_AT)
        joined = "；".join(caught.exception.issues)
        self.assertIn("责任人", joined)
        self.assertIn("en", joined)
        self.assertIn("带领志愿者", joined)

    def test_venue_conflict_is_rejected_at_scheduling(self) -> None:
        ctx = build_system()
        with self.assertRaises(ResourceConflict):
            ctx.system.schedule_session(
                "P2", "撞场演出", SessionType.PERFORMANCE,
                make_window(19, 20), "MOON-HALL",
            )

    def test_published_session_is_locked_from_editing(self) -> None:
        ctx = build_system()
        ctx.system.publish("P1", at=PUBLISH_AT)
        with self.assertRaises(SessionStateError):
            ctx.system.require_kit("P1", "CARVING")


class BookingAndCheckInTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = build_system()
        self.system = self.ctx.system
        self.system.publish("W1", at=PUBLISH_AT)
        self.system.publish("P1", at=PUBLISH_AT)

    def test_booking_occupies_kits_and_capacity_is_enforced(self) -> None:
        for i in range(3):
            self.system.book("W1", f"VIS-{i}", 4, at=at(10))
        total, busy, available = self.system.resources.water_level(
            "CARVING", self.ctx.workshop.window
        )
        self.assertEqual((busy, available), (12, 1))
        with self.assertRaises(CapacityExceeded):
            self.system.book("W1", "VIS-X", 1, at=at(10))  # 容量 12

    def test_repeated_check_in_is_idempotent(self) -> None:
        booking = self.system.book("W1", "VIS-1", 3, at=at(10))
        first = self.system.check_in(booking.code, at=at(14), actual_size=2)
        second = self.system.check_in(booking.code, at=at(14, 5), actual_size=3)
        self.assertTrue(second.duplicate)
        self.assertEqual(second.checked_size, 2)
        self.assertEqual(
            self.system.checked_in_headcount(self.ctx.workshop), 2
        )

    def test_late_check_in_within_window_but_not_after_close(self) -> None:
        booking = self.system.book("W1", "VIS-1", 2, at=at(10))
        self.system.check_in(booking.code, at=at(14, 50))  # 迟到仍可签
        with self.assertRaises(SessionStateError):
            self.system.check_in(  # 另一场：结束后不能签
                self.system.book("P1", "VIS-2", 1, at=at(10)).code,
                at=at(20, 1),
            )

    def test_no_show_releases_kits_after_completion(self) -> None:
        early = self.system.book("W1", "VIS-1", 8, at=at(10))
        absent = self.system.book("W1", "VIS-2", 4, at=at(10))
        self.system.start("W1", at=at(14))
        self.system.check_in(early.code, at=at(14), actual_size=8)
        self.system.complete("W1", at=at(15))
        self.assertEqual(self.system.bookings[absent.code].status,
                         BookingStatus.NO_SHOW)
        allocated = self.system.resources.allocated_kit_units("W1", "CARVING")
        self.assertEqual(len(allocated), 8)

    def test_under_attendance_releases_kits(self) -> None:
        booking = self.system.book("W1", "VIS-1", 6, at=at(10))
        self.system.start("W1", at=at(14))
        result = self.system.check_in(booking.code, at=at(14), actual_size=4)
        _, kit_result = result.kit_results[0]
        self.assertEqual(len(kit_result.released), 2)
        self.assertEqual(
            len(self.system.resources.allocated_kit_units("W1", "CARVING")), 4
        )

    def test_cancel_booking_releases_kits(self) -> None:
        booking = self.system.book("W1", "VIS-1", 5, at=at(10))
        self.system.cancel_booking(booking.code, at=at(11))
        _total, _busy, available = self.system.resources.water_level(
            "CARVING", self.ctx.workshop.window
        )
        self.assertEqual(available, 13)


class SplitAndExtraSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.ctx = build_system()
        self.system = self.ctx.system
        self.system.publish("W1", at=PUBLISH_AT)

    def _schedule_extra_workshop(self) -> str:
        extra = self.system.schedule_session(
            "W2", "加场·雕刻工作坊", SessionType.WORKSHOP,
            make_window(16, 17), "ROOM-B", event_code="MID-AUTUMN", capacity=12,
        )
        self.system.use_content("W2", "MULAN-01:1", [self.ctx.grant_script.code])
        self.system.set_owner("W2", "ST-01")
        self.system.require_kit("W2", "CARVING")
        self.system.offer_language("W2", "zh")
        self.system.assign_staff(
            "W2", "VL-01", AssignmentFunction.WORKSHOP_FACILITATOR,
            qualification="workshop:carving",
        )
        self.system.publish("W2", at=PUBLISH_AT)
        return "W2"

    def test_split_moves_people_and_rebalances_kits(self) -> None:
        target = self._schedule_extra_workshop()
        booking = self.system.book("W1", "VIS-1", 8, at=at(10))
        remain, moved = self.system.split_booking(
            booking.code, 4, target, "VIS-1B", at=at(13)
        )
        self.assertIsNotNone(remain)
        self.assertEqual(remain.party_size, 4)
        self.assertEqual(moved.party_size, 4)
        self.assertEqual(
            len(self.system.resources.allocated_kit_units("W1", "CARVING")), 4
        )
        self.assertEqual(
            len(self.system.resources.allocated_kit_units(target, "CARVING")), 4
        )

    def test_split_whole_booking_cancels_source(self) -> None:
        target = self._schedule_extra_workshop()
        booking = self.system.book("W1", "VIS-1", 3, at=at(10))
        remain, moved = self.system.split_booking(
            booking.code, 3, target, "VIS-1B", at=at(13)
        )
        self.assertIsNone(remain)
        self.assertEqual(booking.status, BookingStatus.CANCELLED)

    def test_split_blocked_when_target_lacks_capacity(self) -> None:
        target = self._schedule_extra_workshop()
        # 占掉加场 10 个名额/教具
        self.system.book(target, "VIS-X", 10, at=at(10))
        booking = self.system.book("W1", "VIS-1", 6, at=at(10))
        with self.assertRaises(CapacityExceeded):
            self.system.split_booking(booking.code, 4, target, "VIS-1B", at=at(13))
        # 源场次资源不变
        self.assertEqual(
            len(self.system.resources.allocated_kit_units("W1", "CARVING")), 6
        )


class DamageTest(unittest.TestCase):
    def test_damage_reassigns_by_actual_headcount_and_reports_shortage(self) -> None:
        ctx = build_system()
        system = ctx.system
        system.publish("W1", at=PUBLISH_AT)
        booking = system.book("W1", "VIS-1", 12, at=at(10))
        allocated_before = set(
            system.resources.allocated_kit_units("W1", "CARVING")
        )
        system.start("W1", at=at(14))
        system.check_in(booking.code, at=at(14), actual_size=12)
        # 损坏一套：池内剩 12 套完好，恰好补位
        report = system.report_kit_damage(sorted(allocated_before)[0], at=at(14, 10))
        self.assertEqual(report.shortages, ())
        self.assertEqual(
            len(system.resources.allocated_kit_units("W1", "CARVING")), 12
        )

    def test_damage_shortage_is_reported_when_pool_exhausted(self) -> None:
        ctx = build_system()
        system = ctx.system
        system.publish("W1", at=PUBLISH_AT)
        booking = system.book("W1", "VIS-1", 12, at=at(10))
        # 人为损坏两套：12 人到场时只剩 11 套完好 -> 缺 1
        system.start("W1", at=at(14))
        system.check_in(booking.code, at=at(14), actual_size=12)
        allocated = sorted(system.resources.allocated_kit_units("W1", "CARVING"))
        first = system.report_kit_damage(allocated[0], at=at(14, 10))
        self.assertEqual(first.shortages, ())
        second = system.report_kit_damage(allocated[1], at=at(14, 20))
        self.assertEqual(second.shortages, (("W1", "CARVING", 1),))


class CancellationTest(unittest.TestCase):
    def test_cancel_only_affects_future_sessions(self) -> None:
        ctx = build_system()
        system = ctx.system
        system.publish("P1", at=PUBLISH_AT)
        system.publish("W1", at=PUBLISH_AT)
        b_future = system.book("W1", "VIS-1", 2, at=at(10))
        system.start("P1", at=at(19))
        system.complete("P1", at=at(20))

        # 整活动取消：工作坊取消，已完成演出跳过
        cancelled, skipped = system.cancel_event("MID-AUTUMN", at=at(10, day=5))
        self.assertEqual(cancelled, ("W1",))
        self.assertEqual(skipped, ("P1",))
        self.assertEqual(b_future.status, BookingStatus.CANCELLED)
        self.assertEqual(ctx.perf.status, SessionStatus.COMPLETED)

        # 已完成不能再取消，取消过的不能重复取消
        with self.assertRaises(SessionStateError):
            system.cancel_session("P1", at=at(10, day=6))
        with self.assertRaises(SessionStateError):
            system.cancel_session("W1", at=at(10, day=6))

    def test_in_progress_session_cannot_be_cancelled(self) -> None:
        ctx = build_system()
        system = ctx.system
        system.publish("W1", at=PUBLISH_AT)
        system.start("W1", at=at(14))
        with self.assertRaises(SessionStateError):
            system.cancel_session("W1", at=at(14, 30))

    def test_completed_performance_keeps_content_and_authorization_history(self) -> None:
        ctx = build_system()
        system = ctx.system
        system.publish("P1", at=PUBLISH_AT)
        system.start("P1", at=at(19))
        system.complete("P1", at=at(20))

        snapshot = ctx.perf.completion_snapshot
        self.assertIsNotNone(snapshot)
        refs = {row["content_ref"]: row for row in snapshot["content"]}
        self.assertEqual(refs["AI-MOON:1"]["version_status"], "confirmed")
        self.assertIsNotNone(refs["AI-MOON:1"]["ai_provenance"])
        self.assertEqual(
            refs["MULAN-01:1"]["authorizations"][0]["status"],
            GrantStatus.GRANTED.value,
        )

        # 事后撤销授权，历史快照里仍是 granted；新版本不受影响
        ctx.grant_ai.revoke(at(21), note="展后争议")
        self.assertEqual(
            refs["AI-MOON:1"]["authorizations"][0]["status"], "granted"
        )
        self.assertEqual(ctx.grant_ai.status, GrantStatus.REVOKED)


if __name__ == "__main__":
    unittest.main()
