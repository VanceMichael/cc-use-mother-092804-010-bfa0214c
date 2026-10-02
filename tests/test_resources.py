"""教具/场地/志愿者占用台账测试。"""

from __future__ import annotations

import unittest
from datetime import datetime

from src.shadow_play.errors import (
    InsufficientResource,
    QualificationError,
    ResourceConflict,
    ResourceError,
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

W1 = TimeWindow(datetime(2026, 10, 5, 14, 0), datetime(2026, 10, 5, 15, 0))
W1_LATE = TimeWindow(datetime(2026, 10, 5, 15, 0), datetime(2026, 10, 5, 16, 0))
W2 = TimeWindow(datetime(2026, 10, 5, 14, 30), datetime(2026, 10, 5, 15, 30))


class TimeWindowTest(unittest.TestCase):
    def test_invalid_window_rejected(self) -> None:
        with self.assertRaises(ResourceError):
            TimeWindow(datetime(2026, 10, 5, 15, 0), datetime(2026, 10, 5, 14, 0))

    def test_adjacent_windows_do_not_overlap(self) -> None:
        self.assertFalse(W1.overlaps(W1_LATE))
        self.assertTrue(W1.overlaps(W2))


class KitAllocationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ResourceRegistry()
        self.registry.register_kit_type(KitType("CARVING", "雕刻套"))
        for no in range(1, 6):  # K01..K05，共 5 套
            self.registry.register_kit_unit(KitUnit("CARVING", f"K{no:02d}"))

    def test_unit_cannot_be_double_allocated_in_overlapping_windows(self) -> None:
        first = self.registry.allocate_kit_units("W1", "CARVING", 5, W1)
        self.assertEqual(len(first), 5)
        with self.assertRaises(InsufficientResource) as caught:
            self.registry.allocate_kit_units("W2", "CARVING", 1, W2)
        self.assertEqual(caught.exception.required, 1)
        self.assertEqual(caught.exception.available, 0)

    def test_same_unit_can_serve_back_to_back_sessions(self) -> None:
        first = self.registry.allocate_kit_units("W1", "CARVING", 5, W1)
        second = self.registry.allocate_kit_units("W1-LATE", "CARVING", 5, W1_LATE)
        self.assertEqual(set(first), set(second))

    def test_allocation_is_atomic_when_insufficient(self) -> None:
        with self.assertRaises(InsufficientResource):
            self.registry.allocate_kit_units("W2", "CARVING", 6, W1)
        # 失败后不应留下任何占用
        self.assertEqual(self.registry.allocated_kit_units("W2", "CARVING"), ())

    def test_release_returns_units_to_pool(self) -> None:
        self.registry.allocate_kit_units("W1", "CARVING", 3, W1)
        released = self.registry.release_kit_units("W1", "CARVING")
        self.assertEqual(len(released), 3)
        self.assertEqual(len(self.registry.available_kit_units("CARVING", W1)), 5)

    def test_reconcile_shrinks_and_grows_with_actual_numbers(self) -> None:
        # 预约 5 人占 5 套
        self.registry.allocate_kit_units("W1", "CARVING", 5, W1)
        result = self.registry.reconcile_kit_units("W1", "CARVING", 3, W1)
        self.assertEqual(len(result.released), 2)
        # 临时加人到 5：窗内无其他场次，可全部补回
        result = self.registry.reconcile_kit_units("W1", "CARVING", 5, W1)
        self.assertEqual(len(result.allocated), 2)
        self.assertEqual(result.missing, 0)

    def test_reconcile_reports_missing_when_pool_is_shared(self) -> None:
        # 本场 3 套，重叠场占走另外 2 套，实际到场 5 人 -> 缺 2
        self.registry.allocate_kit_units("W1", "CARVING", 3, W1)
        self.registry.allocate_kit_units("OTHER", "CARVING", 2, W2)
        result = self.registry.reconcile_kit_units("W1", "CARVING", 5, W1)
        self.assertEqual(len(result.allocated), 0)
        self.assertEqual(result.missing, 2)

    def test_damaged_unit_leaves_pool_and_frees_open_ended_allocations(self) -> None:
        self.registry.allocate_kit_units("W1", "CARVING", 5, W1)
        self.registry.allocate_kit_units("W1-LATE", "CARVING", 5, W1_LATE)
        # 损坏发生在 W1 进行中、W1-LATE 未开始时；两场的未结束占用都解除，
        # 是否补位由场次层按实际人数决定
        affected = dict(
            self.registry.report_damage("K01", datetime(2026, 10, 5, 14, 30))
        )
        self.assertIn("W1", affected)
        self.assertIn("W1-LATE", affected)
        self.assertNotIn("K01", self.registry.available_kit_units("CARVING", W1))
        total, busy, available = self.registry.water_level("CARVING", W1)
        self.assertEqual((total, busy, available), (4, 4, 0))

    def test_water_level_counts(self) -> None:
        self.registry.allocate_kit_units("W1", "CARVING", 2, W1)
        total, busy, available = self.registry.water_level("CARVING", W2)
        self.assertEqual((total, busy, available), (5, 2, 3))


class VenueAndPersonTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ResourceRegistry()
        self.registry.register_venue(Venue("HALL", "主厅", 100))
        self.registry.register_person(
            Person("NR-01", "示例讲解", PersonRole.NARRATOR,
                   languages=frozenset({"zh", "en"}))
        )
        self.registry.register_person(
            Person("VL-01", "示例志愿者", PersonRole.VOLUNTEER,
                   qualifications=frozenset({"workshop:carving"}))
        )

    def test_venue_double_booking_is_rejected(self) -> None:
        self.registry.allocate_venue("P1", "HALL", W1)
        with self.assertRaises(ResourceConflict):
            self.registry.allocate_venue("P2", "HALL", W2)
        # 首尾相接可以
        self.registry.allocate_venue("P3", "HALL", W1_LATE)

    def test_back_to_back_person_assignment_allowed_but_overlap_rejected(self) -> None:
        self.registry.assign_person(
            "P1", "NR-01", AssignmentFunction.NARRATION, W1,
            required_languages=frozenset({"zh"}),
        )
        with self.assertRaises(ResourceConflict):
            self.registry.assign_person(
                "P2", "NR-01", AssignmentFunction.NARRATION, W2,
                required_languages=frozenset({"zh"}),
            )
        self.registry.assign_person(
            "P3", "NR-01", AssignmentFunction.NARRATION, W1_LATE,
            required_languages=frozenset({"en"}),
        )

    def test_language_and_qualification_are_checked(self) -> None:
        with self.assertRaises(QualificationError):
            self.registry.assign_person(
                "P1", "NR-01", AssignmentFunction.NARRATION, W1,
                required_languages=frozenset({"ja"}),
            )
        with self.assertRaises(QualificationError):
            self.registry.assign_person(
                "P1", "VL-01", AssignmentFunction.WORKSHOP_FACILITATOR, W1,
                required_qualification="workshop:painting",
            )

    def test_release_session_keeps_past_history(self) -> None:
        self.registry.allocate_venue("P1", "HALL", W1)
        released = self.registry.release_session(
            "P1", now=datetime(2026, 10, 5, 14, 30)
        )
        self.assertEqual(len(released), 1)
        # 已结束占用不解除
        self.registry.allocate_venue("P1", "HALL", W1)
        released = self.registry.release_session(
            "P1", now=datetime(2026, 10, 5, 16, 0)
        )
        self.assertEqual(released, ())


if __name__ == "__main__":
    unittest.main()
