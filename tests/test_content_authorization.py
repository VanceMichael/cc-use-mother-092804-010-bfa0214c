"""演绎内容版本与传承人授权规则测试。"""

from __future__ import annotations

import unittest
from datetime import date, datetime

from src.shadow_play.content import (
    AiProvenance,
    ContentKind,
    ContentRegistry,
    GrantStatus,
    RightHolder,
    VersionStatus,
)
from src.shadow_play.errors import (
    AuthorizationError,
    ContentStateError,
    ProvenanceError,
    VersionSequenceError,
)

AT = datetime(2026, 10, 1, 10, 0)
HOLDER = RightHolder("RH-01", "示例传承人")


def ai_provenance(
    *,
    scope=("performance",),
    valid_from=date(2026, 9, 1),
    valid_until=date(2026, 11, 30),
):
    return AiProvenance(
        model="示例模型",
        prompt_ref="PR-1",
        source_works=("BASE-1",),
        scope_session_types=tuple(scope),
        valid_from=valid_from,
        valid_until=valid_until,
    )


class ContentVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ContentRegistry()

    def test_versions_are_sequential_and_start_as_draft(self) -> None:
        v1 = self.registry.submit_version("X", ContentKind.SCRIPT, "一", AT)
        v2 = self.registry.submit_version("X", ContentKind.SCRIPT, "二", AT)
        self.assertEqual((v1.version, v2.version), (1, 2))
        self.assertEqual(v2.based_on, 1)
        self.assertIs(v1.status, VersionStatus.DRAFT)
        self.assertIs(self.registry.latest_version("X"), v2)

    def test_sequences_are_independent_per_content(self) -> None:
        a = self.registry.submit_version("A", ContentKind.SCRIPT, "甲", AT)
        b = self.registry.submit_version("B", ContentKind.IMAGE, "乙", AT)
        self.assertEqual((a.version, b.version), (1, 1))

    def test_confirmed_version_is_frozen_and_only_confirmed_is_usable(self) -> None:
        v = self.registry.submit_version("X", ContentKind.SCRIPT, "本", AT)
        with self.assertRaises(ContentStateError):
            self.registry.assert_usable("X:1", "performance", AT)
        v.confirm(AT)
        self.registry.authorizations_for  # sanity: 无授权
        with self.assertRaises(AuthorizationError):
            self.registry.assert_usable("X:1", "performance", AT)

    def test_withdrawn_version_cannot_be_confirmed_again(self) -> None:
        v = self.registry.submit_version("X", ContentKind.SCRIPT, "本", AT)
        v.confirm(AT)
        v.withdraw(AT)
        with self.assertRaises(ContentStateError):
            v.confirm(AT)

    def test_missing_version_reference_is_rejected(self) -> None:
        with self.assertRaises(VersionSequenceError):
            self.registry.get_version("NOPE:9")
        with self.assertRaises(ContentStateError):
            self.registry.get_version("bad-reference")

    def test_ai_animation_requires_provenance_on_submit_and_confirm(self) -> None:
        with self.assertRaises(ProvenanceError):
            self.registry.submit_version("AI", ContentKind.AI_ANIMATION, "动", AT)
        v = self.registry.submit_version(
            "AI", ContentKind.AI_ANIMATION, "动", AT, ai_provenance=ai_provenance()
        )
        v.confirm(AT)
        self.assertIs(v.status, VersionStatus.CONFIRMED)

    def test_ai_provenance_scope_and_period_are_enforced(self) -> None:
        prov = ai_provenance(scope=("performance",))
        prov.covers("performance", datetime(2026, 10, 5, 19, 0))  # 不抛错
        with self.assertRaises(ProvenanceError):
            prov.covers("workshop", datetime(2026, 10, 5, 19, 0))
        with self.assertRaises(ProvenanceError):
            prov.covers("performance", datetime(2026, 12, 1, 19, 0))


class AuthorizationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = ContentRegistry()
        self.registry.submit_version("X", ContentKind.SCRIPT, "本", AT).confirm(AT)

    def _request(self, scope=("performance",), until=date(2026, 12, 31)):
        return self.registry.request_authorization(
            "X:1", HOLDER, scope, until, AT
        )

    def test_pending_authorization_blocks_public_use(self) -> None:
        grant = self._request()
        self.assertIs(grant.status, GrantStatus.PENDING)
        with self.assertRaisesRegex(AuthorizationError, "尚未确认"):
            self.registry.assert_usable("X:1", "performance", AT,
                                        auth_codes=[grant.code])

    def test_granted_authorization_allows_use_and_keeps_history(self) -> None:
        grant = self._request()
        grant.grant(AT, note="同意")
        valid = self.registry.assert_usable(
            "X:1", "performance", datetime(2026, 10, 5, 19, 0),
            auth_codes=[grant.code],
        )
        self.assertEqual(valid, [grant.code])
        statuses = [s for s, _t, _n in grant.history]
        self.assertEqual(statuses, [GrantStatus.PENDING, GrantStatus.GRANTED])

    def test_rejected_material_cannot_enter_activity(self) -> None:
        grant = self._request()
        grant.reject(AT, note="传承人不同意")
        with self.assertRaisesRegex(AuthorizationError, "已被拒绝"):
            self.registry.assert_usable("X:1", "performance", AT,
                                        auth_codes=[grant.code])

    def test_scope_mismatch_blocks_use(self) -> None:
        grant = self._request(scope=("exhibit",))
        grant.grant(AT)
        with self.assertRaisesRegex(AuthorizationError, "不覆盖"):
            self.registry.assert_usable("X:1", "performance", AT,
                                        auth_codes=[grant.code])

    def test_expired_authorization_is_detected_and_retained(self) -> None:
        grant = self._request(until=date(2026, 9, 30))
        grant.grant(AT)
        changed = grant.expire_if_due(datetime(2026, 10, 5))
        self.assertTrue(changed)
        self.assertIs(grant.status, GrantStatus.EXPIRED)
        with self.assertRaises(AuthorizationError):
            self.registry.assert_usable(
                "X:1", "performance", datetime(2026, 10, 5),
                auth_codes=[grant.code],
            )
        # 历史仍然可追溯
        self.assertEqual([s for s, _t, _n in grant.history][1], GrantStatus.GRANTED)

    def test_revoked_authorization_blocks_future_sessions(self) -> None:
        grant = self._request()
        grant.grant(AT)
        grant.revoke(datetime(2026, 10, 4), note="争议素材")
        with self.assertRaises(AuthorizationError):
            self.registry.assert_usable(
                "X:1", "performance", datetime(2026, 10, 5),
                auth_codes=[grant.code],
            )

    def test_authorization_code_must_match_content(self) -> None:
        other = self.registry.submit_version("Y", ContentKind.IMAGE, "图", AT)
        other.confirm(AT)
        grant = self._request()
        grant.grant(AT)
        with self.assertRaisesRegex(AuthorizationError, "不属于内容 Y:1"):
            self.registry.assert_usable("Y:1", "performance", AT,
                                        auth_codes=[grant.code])

    def test_empty_scope_is_rejected(self) -> None:
        with self.assertRaises(AuthorizationError):
            self.registry.request_authorization("X:1", HOLDER, [], date(2026, 12, 31), AT)

    def test_decision_transitions_are_guarded(self) -> None:
        grant = self._request()
        grant.grant(AT)
        with self.assertRaises(AuthorizationError):
            grant.grant(AT)
        with self.assertRaises(AuthorizationError):
            grant.reject(AT)
        grant.revoke(AT)
        with self.assertRaises(AuthorizationError):
            grant.revoke(AT)


if __name__ == "__main__":
    unittest.main()
