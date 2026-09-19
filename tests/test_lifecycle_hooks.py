from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import lifecycle_hooks, lifecycle_policy, run_manifest, workflow_stages


class LifecycleHookTests(unittest.TestCase):
    def _repo(self, root: Path, *, lifecycle: str = "preproduction", exposure: str = "experimental") -> Path:
        repo = root / "repo"
        repo.mkdir()
        autodev = repo / ".autodev"
        autodev.mkdir()
        (autodev / "repo.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "product": {
                        "lifecycle": lifecycle,
                        "default_work_exposure": exposure,
                    },
                }
            ),
            encoding="utf-8",
        )
        return repo

    def test_prepare_persists_effective_policy_in_durable_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / workflow_stages.CURRENT_DIR

            def original(resolved: Path, arguments: str, **_kwargs) -> Path:
                self.assertEqual(resolved, repo.resolve())
                self.assertEqual(arguments, "#355")
                current.mkdir(parents=True)
                workflow_stages.write_state(
                    current,
                    {
                        "IssueNumber": 355,
                        "CreatedAt": "2026-09-17T12:00:00Z",
                        "Status": "Prepared",
                    },
                )
                return current

            prepared = lifecycle_hooks._prepare_with_policy(original, repo, "#355")
            state = workflow_stages.read_state(prepared)

        self.assertEqual(state["DeliveryPolicyMode"], lifecycle_policy.LIFECYCLE_AWARE)
        self.assertEqual(state["ProductLifecycle"], "preproduction")
        self.assertEqual(state["DefaultWorkExposure"], "experimental")
        self.assertEqual(state["WorkExposure"], "experimental")
        self.assertEqual(state["WorkExposureSource"], "repository-default")
        self.assertTrue(str(state["LifecyclePolicyFingerprint"]))

    def test_prepare_refuses_policy_drift_for_same_active_run(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            original_policy = lifecycle_policy.load_lifecycle_policy(repo)
            state = {
                "IssueNumber": 355,
                "CreatedAt": "2026-09-17T12:00:00Z",
                "Status": "Prepared",
            }
            state.update(lifecycle_policy.state_fields(original_policy))
            workflow_stages.write_state(current, state)

            (repo / ".autodev" / "repo.json").write_text(
                json.dumps(
                    {
                        "version": 1,
                        "product": {
                            "lifecycle": "production",
                            "default_work_exposure": "user-facing",
                        },
                    }
                ),
                encoding="utf-8",
            )

            called = False

            def original(_repo: Path, _arguments: str, **_kwargs) -> Path:
                nonlocal called
                called = True
                return current

            with self.assertRaisesRegex(
                lifecycle_policy.LifecyclePolicyError,
                "different delivery policy",
            ):
                lifecycle_hooks._prepare_with_policy(original, repo, "#355")

        self.assertFalse(called)

    def test_manifest_records_same_policy_as_prepared_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir))
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            state = {"IssueNumber": 355}
            state.update(
                lifecycle_policy.state_fields(
                    lifecycle_policy.load_lifecycle_policy(repo)
                )
            )

            def original(_repo: Path, _state: dict[str, object], **_kwargs) -> Path:
                path = current / run_manifest.MANIFEST_NAME
                run_manifest.create_manifest(
                    path,
                    repo_path=repo,
                    github_repo="example/repo",
                    issue_number=355,
                    mode="issue-to-pr",
                    base_sha="abc123",
                    branch="feature/355-test",
                    role_snapshots={},
                )
                return path

            result = lifecycle_hooks._manifest_with_policy(original, repo, state)
            manifest = run_manifest.load_manifest(result)

        delivery = manifest["delivery_policy"]
        self.assertEqual(delivery["lifecycle"], "preproduction")
        self.assertEqual(delivery["work_exposure"], "experimental")
        self.assertEqual(
            delivery["fingerprint"],
            state["LifecyclePolicyFingerprint"],
        )

    def test_status_surfaces_effective_policy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = self._repo(Path(temp_dir), lifecycle="production", exposure="user-facing")
            current = repo / workflow_stages.CURRENT_DIR
            current.mkdir(parents=True)
            state = {"IssueNumber": 355}
            state.update(
                lifecycle_policy.state_fields(
                    lifecycle_policy.load_lifecycle_policy(repo)
                )
            )
            workflow_stages.write_state(current, state)

            text = lifecycle_hooks._status_with_policy(
                lambda _repo: "Status: Prepared\n",
                repo,
            )

        self.assertIn("lifecycle=production", text)
        self.assertIn("work-exposure=user-facing", text)


if __name__ == "__main__":
    unittest.main()
