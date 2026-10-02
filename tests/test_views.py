"""负责人场次视图测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from tests.helpers import build_system
from src.shadow_play.views import SessionViewBuilder
from src.shadow_play.sessions import SessionStatus

PUBLISH_AT = datetime(2026, 10, 1, 12, 0)


class SessionViewTest(unittest.TestCase):
    def test_draft_view_collects_basis_levels_owner_and_pending_auth(self) -> None:
        ctx = build_system()
        builder = SessionViewBuilder(ctx.system)
        view = builder.build("P1", at=PUBLISH_AT)

        self.assertEqual(view.owner, "周姓负责人（示例）")
        self.assertEqual(view.venue, "月下剧场主厅")
        refs = {b.content_ref: b for b in view.content_basis}
        self.assertTrue(refs["MULAN-01:1"].usable)
        self.assertTrue(refs["AI-MOON:1"].usable)
        self.assertEqual(
            refs["AI-MOON:1"].ai_provenance["model"], "示例动画模型 X"
        )
        # 草稿：无待补授权，也无发布障碍
        self.assertEqual(view.pending_authorizations, ())
        self.assertTrue(view.ready)
        self.assertIn("内容 ✓", view.summary())

    def test_view_flags_pending_authorization_and_kit_gap(self) -> None:
        ctx = build_system()
        pending = ctx.content.request_authorization(
            "AI-MOON:1", ctx.other_holder, ["performance"],
            date(2026, 12, 31), PUBLISH_AT,
        )
        # 用待处理授权替换已授予授权
        ctx.system.use_content("P1", "AI-MOON:1", [pending.code])
        view = SessionViewBuilder(ctx.system).build("P1", at=PUBLISH_AT)
        self.assertFalse(view.ready)
        joined = "；".join(view.publish_issues)
        self.assertIn("尚未确认", joined)
        self.assertTrue(
            any(pending.code in p for p in view.pending_authorizations)
        )

    def test_workshop_view_shows_water_level_and_staff(self) -> None:
        ctx = build_system()
        ctx.system.publish("W1", at=PUBLISH_AT)
        ctx.system.book("W1", "VIS-1", 5, at=PUBLISH_AT)
        view = SessionViewBuilder(ctx.system).build("W1", at=PUBLISH_AT)
        self.assertEqual(view.status, SessionStatus.PUBLISHED.value)
        level = view.kit_levels[0]
        self.assertEqual((level.intact_total, level.busy, level.required), (13, 5, 5))
        self.assertEqual(len(level.allocated), 5)
        functions = {s.function for s in view.staff}
        self.assertIn("facilitator", functions)
        self.assertEqual(view.reserved, 5)

    def test_build_all_returns_every_session(self) -> None:
        ctx = build_system()
        views = SessionViewBuilder(ctx.system).build_all(at=PUBLISH_AT)
        self.assertEqual({v.session_id for v in views}, {"P1", "W1"})


if __name__ == "__main__":
    unittest.main()
