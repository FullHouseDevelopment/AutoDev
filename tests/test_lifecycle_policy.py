from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import lifecycle_policy


class LifecyclePolicyTests(unittest.TestCase):
    def _write_repo_policy(self, repo: Path, product: object | None) -> None:
        autodev = repo / ".autodev"
        autodev.mkdir(parents=True, exist_ok=True)
        config: dict[str, object] = {"version": 1}
        if product is not None:
            config["product"] = product
        (autodev / "repo.json").write_text(
            json.dumps(config),
            encoding="utf-8",
        )

    def test_all_four_lifecycle_exposure_combinations_are_supported(self) -> None:
        for lifecycle in (lifecycle_policy.PREPRODUCTION, lifecycle_policy.PRODUCTION):
            for exposure in (lifecycle_policy.EXPERIMENTAL, lifecycle_policy.USER_FACING):
                with self.subTest(lifecycle=lifecycle, exposure=exposure):
                    policy = lifecycle_policy.parse_lifecycle_policy(
                        {
                            "lifecycle": lifecycle,
                            "default_work_exposure": exposure,
                        }
                    )
                    self.assertTrue(policy.configured)
                    self.assertEqual(policy.mode, lifecycle_policy.LIFECYCLE_AWARE)
                    self.assertEqual(policy.lifecycle, lifecycle)
                    self.assertEqual(policy.work_exposure, exposure)
                    self.assertTrue(policy.fingerprint)

    def test_missing_product_policy_preserves_legacy_strict_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_repo_policy(repo, None)
            policy = lifecycle_policy.load_lifecycle_policy(repo)

        self.assertFalse(policy.configured)
        self.assertEqual(policy.mode, lifecycle_policy.LEGACY_STRICT)
        self.assertEqual(policy.lifecycle, "")
        self.assertEqual(policy.work_exposure, "")

    def test_invalid_values_and_unknown_fields_fail_closed(self) -> None:
        invalid = (
            {"lifecycle": "prototype", "default_work_exposure": "experimental"},
            {"lifecycle": "preproduction", "default_work_exposure": "internal"},
            {"lifecycle": "preproduction"},
            {"default_work_exposure": "experimental"},
            {
                "lifecycle": "preproduction",
                "default_work_exposure": "experimental",
                "inferred_from_issue": True,
            },
        )
        for raw in invalid:
            with self.subTest(raw=raw):
                with self.assertRaises(lifecycle_policy.LifecyclePolicyError):
                    lifecycle_policy.parse_lifecycle_policy(raw)

    def test_state_round_trip_is_explicit_and_inspectable(self) -> None:
        policy = lifecycle_policy.parse_lifecycle_policy(
            {
                "lifecycle": "preproduction",
                "default_work_exposure": "experimental",
            },
            source="test-policy",
        )
        state = lifecycle_policy.state_fields(policy)
        restored = lifecycle_policy.policy_from_state(state)
        evidence = lifecycle_policy.evidence_from_state(state)

        self.assertEqual(restored.lifecycle, "preproduction")
        self.assertEqual(restored.work_exposure, "experimental")
        self.assertEqual(restored.fingerprint, policy.fingerprint)
        self.assertEqual(evidence["mode"], lifecycle_policy.LIFECYCLE_AWARE)
        self.assertEqual(evidence["work_exposure_source"], "repository-default")
        self.assertEqual(evidence["fingerprint"], policy.fingerprint)

    def test_legacy_run_remains_compatible_while_repository_stays_undeclared(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_repo_policy(repo, None)
            effective = lifecycle_policy.assert_resume_compatible(
                repo,
                {"IssueNumber": 355},
            )

        self.assertEqual(effective.mode, lifecycle_policy.LEGACY_STRICT)

    def test_resume_rejects_policy_reinterpretation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_repo_policy(
                repo,
                {
                    "lifecycle": "preproduction",
                    "default_work_exposure": "experimental",
                },
            )
            prepared = lifecycle_policy.state_fields(
                lifecycle_policy.load_lifecycle_policy(repo)
            )
            self._write_repo_policy(
                repo,
                {
                    "lifecycle": "production",
                    "default_work_exposure": "user-facing",
                },
            )

            with self.assertRaisesRegex(
                lifecycle_policy.LifecyclePolicyError,
                "different delivery policy",
            ):
                lifecycle_policy.assert_resume_compatible(repo, prepared)

    def test_adding_policy_to_a_legacy_active_run_requires_re_evaluation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            self._write_repo_policy(repo, None)
            legacy_state = {"IssueNumber": 355}
            self._write_repo_policy(
                repo,
                {
                    "lifecycle": "preproduction",
                    "default_work_exposure": "experimental",
                },
            )

            with self.assertRaisesRegex(
                lifecycle_policy.LifecyclePolicyError,
                "different delivery policy",
            ):
                lifecycle_policy.assert_resume_compatible(repo, legacy_state)

    def test_manifest_policy_must_match_prepared_state(self) -> None:
        prepared = lifecycle_policy.parse_lifecycle_policy(
            {
                "lifecycle": "preproduction",
                "default_work_exposure": "experimental",
            }
        )
        state = lifecycle_policy.state_fields(prepared)
        manifest = {"delivery_policy": lifecycle_policy.evidence_from_state(state)}
        lifecycle_policy.assert_manifest_compatible(manifest, state)

        mismatched = lifecycle_policy.parse_lifecycle_policy(
            {
                "lifecycle": "production",
                "default_work_exposure": "user-facing",
            }
        )
        manifest["delivery_policy"] = mismatched.to_json()
        with self.assertRaisesRegex(
            lifecycle_policy.LifecyclePolicyError,
            "does not match",
        ):
            lifecycle_policy.assert_manifest_compatible(manifest, state)

    def test_status_distinguishes_legacy_and_declared_policy(self) -> None:
        self.assertIn(
            "legacy-strict",
            lifecycle_policy.status_line({"IssueNumber": 355}),
        )
        state = lifecycle_policy.state_fields(
            lifecycle_policy.parse_lifecycle_policy(
                {
                    "lifecycle": "production",
                    "default_work_exposure": "experimental",
                }
            )
        )
        line = lifecycle_policy.status_line(state)
        self.assertIn("lifecycle=production", line)
        self.assertIn("work-exposure=experimental", line)


if __name__ == "__main__":
    unittest.main()
