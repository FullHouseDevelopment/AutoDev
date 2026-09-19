from __future__ import annotations

import io
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from automation import (
    obligations_cli,
    verification_obligation_tracking,
    verification_obligations,
    workflow_stages,
)


class VerificationObligationTests(unittest.TestCase):
    def _repo(self, root: Path, *, follow_up: str = "manual") -> tuple[Path, Path, dict[str, object]]:
        repo = root / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()
        autodev = repo / ".autodev"
        autodev.mkdir()
        (autodev / "repo.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "product": {
                        "lifecycle": "preproduction",
                        "default_work_exposure": "experimental",
                    },
                    "deferred_obligations": {"follow_up": follow_up},
                }
            ),
            encoding="utf-8",
        )
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state: dict[str, object] = {
            "RunId": "run-357",
            "IssueNumber": 357,
            "IssueUrl": "https://github.com/example/repo/issues/357",
            "PrUrl": "https://github.com/example/repo/pull/400",
            "RepoFullName": "example/repo",
            "VerifiedSourceIdentity": "source-identity",
        }
        workflow_stages.write_state(current, state)
        return repo, current, state

    def test_platform_obligations_use_shared_durable_ledger(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(Path(temp_dir))
            records = verification_obligations.record_platform_obligations(
                repo,
                state,
                [
                    {
                        "id": "windows-smoke",
                        "platform": "windows",
                        "message": "Windows interaction smoke remains deferred",
                        "source": "local-check",
                    }
                ],
                extra_artifact={"windows_required": True},
            )

            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]["kind"], verification_obligations.KIND_PLATFORM)
            self.assertEqual(records[0]["origin_kind"], verification_obligations.ORIGIN_PLATFORM)
            self.assertEqual(records[0]["status"], verification_obligations.OPEN)
            ledger = json.loads((repo / verification_obligations.LEDGER_PATH).read_text(encoding="utf-8"))
            artifact = json.loads((current / verification_obligations.CURRENT_ARTIFACT).read_text(encoding="utf-8"))

        self.assertEqual(len(ledger["obligations"]), 1)
        self.assertTrue(artifact["windows_required"])
        self.assertEqual(artifact["obligations"][0]["platform"], "windows")

    def test_semantic_deferral_preserves_verifier_source_policy_and_override_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            disposition = {
                "verifier": {"sha256": "a" * 64},
                "policy": {
                    "lifecycle": "preproduction",
                    "work_exposure": "experimental",
                    "fingerprint": "policy-fingerprint",
                },
                "override": {"reason": "dogfood build now"},
                "entries": [
                    {
                        "id": "finding-1",
                        "kind": "finding",
                        "severity": "blocking",
                        "message": "Dashboard refresh is delayed",
                        "path": "Dashboard.cs",
                        "disposition": "deferred",
                        "deferred_by": "human",
                        "reason": "blocking semantic finding requires explicit human disposition",
                    }
                ],
            }
            records = verification_obligations.record_semantic_deferrals(
                repo,
                state,
                disposition,
            )
            semantic = [item for item in records if item["kind"] == verification_obligations.KIND_SEMANTIC]

        self.assertEqual(len(semantic), 1)
        record = semantic[0]
        self.assertEqual(record["origin_kind"], verification_obligations.ORIGIN_VERIFIER)
        self.assertEqual(record["verifier_result_sha256"], "a" * 64)
        self.assertEqual(record["source_identity"], "source-identity")
        self.assertEqual(record["lifecycle"], "preproduction")
        self.assertEqual(record["work_exposure"], "experimental")
        self.assertTrue(record["human_override"])
        self.assertEqual(record["human_override_reason"], "dogfood build now")

    def test_ledger_survives_replacement_of_current_run_directory(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(Path(temp_dir))
            verification_obligations.record_product_findings(
                repo,
                state,
                [{"finding": "Save & Quit is needed after long dogfood sessions"}],
                origin_kind=verification_obligations.ORIGIN_HUMAN_DOGFOOD,
                source_identity="playtest-110-waves",
            )

            shutil.rmtree(current)
            current.mkdir(parents=True)
            workflow_stages.write_state(
                current,
                {
                    "RunId": "run-next",
                    "IssueNumber": 358,
                    "RepoFullName": "example/repo",
                },
            )

            values = verification_obligations.open_obligations(repo)

        self.assertEqual(len(values), 1)
        self.assertEqual(values[0]["origin_kind"], verification_obligations.ORIGIN_HUMAN_DOGFOOD)
        self.assertEqual(values[0]["source_identity"], "playtest-110-waves")

    def test_product_learning_origins_remain_distinct_from_verifier_debt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            for origin in (
                verification_obligations.ORIGIN_HUMAN_DOGFOOD,
                verification_obligations.ORIGIN_RUNTIME,
                verification_obligations.ORIGIN_REQUIREMENT,
                verification_obligations.ORIGIN_PIVOT,
            ):
                verification_obligations.record_product_findings(
                    repo,
                    state,
                    [
                        {
                            "finding": f"finding from {origin}",
                            "core_journey": origin == verification_obligations.ORIGIN_PIVOT,
                        }
                    ],
                    origin_kind=origin,
                )

            values = verification_obligations.open_obligations(repo)

        self.assertEqual(
            {item["origin_kind"] for item in values},
            {
                verification_obligations.ORIGIN_HUMAN_DOGFOOD,
                verification_obligations.ORIGIN_RUNTIME,
                verification_obligations.ORIGIN_REQUIREMENT,
                verification_obligations.ORIGIN_PIVOT,
            },
        )
        self.assertTrue(
            next(
                item
                for item in values
                if item["origin_kind"] == verification_obligations.ORIGIN_PIVOT
            )["design_pivot"]
        )
        self.assertEqual(verification_obligations.promotion_blockers(repo), [])

    def test_exact_met_criterion_reconciles_only_matching_semantic_obligation(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            verification_obligations.record_semantic_deferrals(
                repo,
                state,
                {
                    "verifier": {"sha256": "b" * 64},
                    "policy": {
                        "lifecycle": "preproduction",
                        "work_exposure": "experimental",
                        "fingerprint": "policy",
                    },
                    "entries": [
                        {
                            "id": "r1",
                            "criterion": "Save & Quit persists the run",
                            "status": "unmet",
                            "evidence": [],
                            "disposition": "deferred",
                            "deferred_by": "policy",
                            "reason": "deferred",
                        },
                        {
                            "id": "r2",
                            "criterion": "Combat log explains damage",
                            "status": "unmet",
                            "evidence": [],
                            "disposition": "deferred",
                            "deferred_by": "policy",
                            "reason": "deferred",
                        },
                    ],
                },
            )

            changed = verification_obligations.reconcile_verifier_result(
                repo,
                {
                    "requirements": [
                        {
                            "criterion": "Save & Quit persists the run",
                            "status": "met",
                        }
                    ]
                },
            )
            values = verification_obligations.all_obligations(repo)

        self.assertEqual(len(changed), 1)
        by_criterion = {item["criterion"]: item for item in values}
        self.assertEqual(by_criterion["Save & Quit persists the run"]["status"], verification_obligations.RESOLVED)
        self.assertEqual(by_criterion["Combat log explains damage"]["status"], verification_obligations.OPEN)

    def test_grouped_follow_up_issue_creation_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir), follow_up="github-issue")
            verification_obligations.record_semantic_deferrals(
                repo,
                state,
                {
                    "verifier": {"sha256": "c" * 64},
                    "policy": {
                        "lifecycle": "preproduction",
                        "work_exposure": "experimental",
                        "fingerprint": "policy",
                    },
                    "entries": [
                        {
                            "id": "f1",
                            "message": "Refresh is delayed",
                            "severity": "warning",
                            "disposition": "deferred",
                            "deferred_by": "policy",
                            "reason": "warning",
                        },
                        {
                            "id": "f2",
                            "message": "Presentation coverage is incomplete",
                            "severity": "warning",
                            "disposition": "deferred",
                            "deferred_by": "policy",
                            "reason": "warning",
                        },
                    ],
                },
            )
            calls: list[list[str]] = []

            def runner(command, **kwargs):
                values = [str(value) for value in command]
                calls.append(values)
                if values[:3] == ["gh", "issue", "create"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout="https://github.com/example/repo/issues/500\n",
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {values}")

            first = verification_obligation_tracking.ensure_follow_up_issues(
                repo,
                state,
                runner=runner,
            )
            second = verification_obligation_tracking.ensure_follow_up_issues(
                repo,
                state,
                runner=runner,
            )
            semantic = verification_obligations.open_obligations(
                repo,
                kind=verification_obligations.KIND_SEMANTIC,
            )

        self.assertEqual(first, ["https://github.com/example/repo/issues/500"])
        self.assertEqual(second, ["https://github.com/example/repo/issues/500"])
        self.assertEqual(
            len([call for call in calls if call[:3] == ["gh", "issue", "create"]]),
            1,
        )
        self.assertEqual({item["tracking_issue"] for item in semantic}, {500})
        self.assertEqual(
            {item["tracking_url"] for item in semantic},
            {"https://github.com/example/repo/issues/500"},
        )

    def test_closed_tracking_issue_reconciles_linked_obligations(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            records = verification_obligations.record_product_findings(
                repo,
                state,
                [{"finding": "Dogfood balance pass"}],
                origin_kind=verification_obligations.ORIGIN_HUMAN_DOGFOOD,
            )
            obligation_id = records[0]["id"]
            verification_obligation_tracking.link_tracking_issue(
                repo,
                [obligation_id],
                issue_number=501,
                url="https://github.com/example/repo/issues/501",
            )

            def runner(command, **kwargs):
                values = [str(value) for value in command]
                if values[:3] == ["gh", "issue", "view"]:
                    return SimpleNamespace(
                        returncode=0,
                        stdout=json.dumps(
                            {
                                "state": "CLOSED",
                                "url": "https://github.com/example/repo/issues/501",
                            }
                        ),
                        stderr="",
                    )
                raise AssertionError(f"unexpected command: {values}")

            changed = verification_obligation_tracking.reconcile_tracking_issues(
                repo,
                state,
                runner=runner,
            )

        self.assertEqual(len(changed), 1)
        self.assertEqual(changed[0]["status"], verification_obligations.RESOLVED)
        self.assertIn("tracking GitHub issue #501 is closed", changed[0]["resolution"])

    def test_explicit_supersession_is_not_reopened_by_same_product_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            records = verification_obligations.record_product_findings(
                repo,
                state,
                [{"id": "pivot", "finding": "Old combat model is superseded"}],
                origin_kind=verification_obligations.ORIGIN_PIVOT,
            )
            obligation_id = records[0]["id"]
            verification_obligations.supersede(
                repo,
                [obligation_id],
                reason="MVP2 replaces the old combat model",
            )
            verification_obligations.record_product_findings(
                repo,
                state,
                [{"id": "pivot", "finding": "Old combat model is superseded"}],
                origin_kind=verification_obligations.ORIGIN_PIVOT,
            )
            record = next(
                item
                for item in verification_obligations.all_obligations(repo)
                if item["id"] == obligation_id
            )

        self.assertEqual(record["status"], verification_obligations.SUPERSEDED)
        self.assertIn("MVP2", record["resolution"])

    def test_same_run_semantic_recording_is_idempotent_across_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _current, state = self._repo(Path(temp_dir))
            disposition = {
                "verifier": {"sha256": "d" * 64},
                "policy": {
                    "lifecycle": "preproduction",
                    "work_exposure": "experimental",
                    "fingerprint": "policy",
                },
                "entries": [
                    {
                        "id": "same-finding",
                        "message": "Combat result explanation is incomplete",
                        "severity": "warning",
                        "disposition": "deferred",
                        "deferred_by": "policy",
                        "reason": "warning",
                    }
                ],
            }

            first = verification_obligations.record_semantic_deferrals(
                repo,
                state,
                disposition,
            )
            second = verification_obligations.record_semantic_deferrals(
                repo,
                state,
                disposition,
            )
            ledger = verification_obligations.all_obligations(repo)

        self.assertEqual(
            [item["id"] for item in first if item["kind"] == verification_obligations.KIND_SEMANTIC],
            [item["id"] for item in second if item["kind"] == verification_obligations.KIND_SEMANTIC],
        )
        self.assertEqual(
            len([item for item in ledger if item["kind"] == verification_obligations.KIND_SEMANTIC]),
            1,
        )

    def test_resolving_current_platform_obligation_clears_active_view_but_preserves_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(Path(temp_dir))
            verification_obligations.record_platform_obligations(
                repo,
                state,
                [
                    {
                        "id": "windows-smoke",
                        "platform": "windows",
                        "message": "Windows smoke pending",
                        "source": "local-check",
                    }
                ],
            )

            changed = verification_obligations.resolve_current(
                repo,
                state,
                kind=verification_obligations.KIND_PLATFORM,
                reason="Windows verification completed successfully",
            )
            active = workflow_stages.read_state(current)
            history = verification_obligations.all_obligations(repo)

        self.assertEqual(len(changed), 1)
        self.assertEqual(active["DeferredVerificationObligations"], [])
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["status"], verification_obligations.RESOLVED)

    def test_obligations_cli_status_reads_durable_ledger_without_active_run_dependency(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(Path(temp_dir))
            verification_obligations.record_product_findings(
                repo,
                state,
                [{"finding": "Dogfood exposed opaque combat math"}],
                origin_kind=verification_obligations.ORIGIN_HUMAN_DOGFOOD,
            )
            shutil.rmtree(current)
            out = io.StringIO()

            code = obligations_cli.run_cli(
                ["--repo", str(repo), "status"],
                stdout=out,
            )

        self.assertEqual(code, 0)
        self.assertIn("open=1", out.getvalue())
        self.assertIn("Dogfood exposed opaque combat math", out.getvalue())


if __name__ == "__main__":
    unittest.main()
