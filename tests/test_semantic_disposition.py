from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import (
    disposition_transition,
    lifecycle_policy,
    run_manifest,
    semantic_disposition,
    workflow_stages,
)


class SemanticDispositionTests(unittest.TestCase):
    def _repo(self, root: Path, *, configured: bool = True) -> tuple[Path, Path, dict[str, object]]:
        repo = root / "repo"
        repo.mkdir()
        autodev = repo / ".autodev"
        autodev.mkdir()
        config: dict[str, object] = {"version": 1}
        if configured:
            config["product"] = {
                "lifecycle": "preproduction",
                "default_work_exposure": "experimental",
            }
        (autodev / "repo.json").write_text(json.dumps(config), encoding="utf-8")
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state: dict[str, object] = {
            "IssueNumber": 356,
            "RunId": "run-356",
            "Status": "Blocked",
            "LastLocalCheckPassed": True,
            "LastSemanticVerdict": "repair",
            "VerifiedSourceIdentity": "source-identity",
            "BaseSha": "base",
            "BranchName": "feature/356-test",
            "RepoFullName": "example/repo",
        }
        state.update(
            lifecycle_policy.state_fields(lifecycle_policy.load_lifecycle_policy(repo))
        )
        workflow_stages.write_state(current, state)
        return repo, current, state

    def _result(
        self,
        current: Path,
        *,
        verdict: str = "repair",
        findings: list[dict[str, object]] | None = None,
        requirements: list[dict[str, object]] | None = None,
    ) -> bytes:
        payload = {
            "verdict": verdict,
            "requirements": requirements or [],
            "findings": findings or [],
            "repair_brief": "repair remaining gaps" if verdict == "repair" else "",
        }
        path = current / "verification-result.json"
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return path.read_bytes()

    def test_verifier_artifact_is_unchanged_after_human_deferral(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            before = self._result(
                current,
                findings=[
                    {
                        "severity": "blocking",
                        "message": "Dashboard does not immediately refresh after quick capture",
                        "path": "DashboardView.cs",
                    }
                ],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.AWAITING_HUMAN)

            accepted = semantic_disposition.accept_with_deferrals(
                repo,
                reason="Useful dogfood build; track the refresh gap separately",
            )

            self.assertEqual(accepted["state"], semantic_disposition.ACCEPTED_WITH_DEFERRALS)
            self.assertEqual(accepted["source_identity"], "source-identity")
            self.assertEqual((current / "verification-result.json").read_bytes(), before)
            state = workflow_stages.read_state(current)
            self.assertEqual(state["LastSemanticVerdict"], "repair")
            self.assertTrue(semantic_disposition.accepted_for_current_result(repo, state))
            self.assertEqual(accepted["entries"][0]["disposition"], semantic_disposition.DEFERRED)
            audit = accepted["override"]
            self.assertIn("dogfood build", audit["reason"])
            self.assertEqual(audit["issue_number"], 356)
            self.assertEqual(audit["run_id"], "run-356")

    def test_human_reason_is_required_for_blocking_noncritical_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                findings=[{"severity": "blocking", "message": "Keyboard focus coverage is incomplete"}],
            )
            with self.assertRaisesRegex(
                semantic_disposition.SemanticDispositionError,
                "deferral-reason",
            ):
                semantic_disposition.accept_with_deferrals(repo)

    def test_critical_data_integrity_finding_cannot_use_broad_override(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                findings=[
                    {
                        "severity": "blocking",
                        "message": "Quick capture is not persisted and is lost after restart",
                    }
                ],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.HARD_BLOCK)
            self.assertEqual(disposition["entries"][0]["critical_class"], "data-integrity")
            with self.assertRaisesRegex(
                semantic_disposition.SemanticDispositionError,
                "critical-by-default",
            ):
                semantic_disposition.accept_with_deferrals(
                    repo,
                    reason="ship anyway",
                )

    def test_mixed_critical_and_noncritical_findings_remain_hard_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                findings=[
                    {"severity": "blocking", "message": "Dialog keyboard focus coverage is incomplete"},
                    {"severity": "blocking", "message": "Authorization bypass permits unauthorized access"},
                ],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.HARD_BLOCK)
            dispositions = [item["disposition"] for item in disposition["entries"]]
            self.assertIn(semantic_disposition.HUMAN_OVERRIDE, dispositions)
            self.assertIn(semantic_disposition.BLOCKING, dispositions)

    def test_all_warning_findings_can_be_policy_deferred_without_reason(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                verdict="pass",
                findings=[
                    {"severity": "warning", "message": "Presentation test coverage can be stronger"},
                    {"severity": "warning", "message": "Dashboard refresh could be more immediate"},
                ],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.ACCEPTED_WITH_DEFERRALS)
            accepted = semantic_disposition.accept_with_deferrals(repo)
            self.assertEqual(accepted["state"], semantic_disposition.ACCEPTED_WITH_DEFERRALS)
            self.assertTrue(
                all(item["disposition"] == semantic_disposition.DEFERRED for item in accepted["entries"])
            )

    def test_legacy_strict_repository_preserves_existing_block(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir), configured=False)
            self._result(
                current,
                findings=[{"severity": "blocking", "message": "Keyboard behavior differs on Windows"}],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.HARD_BLOCK)
            with self.assertRaisesRegex(
                semantic_disposition.SemanticDispositionError,
                "legacy-strict",
            ):
                semantic_disposition.accept_with_deferrals(repo, reason="owner accepts")

    def test_acceptance_is_bound_to_exact_verifier_result_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                findings=[{"severity": "blocking", "message": "Focus behavior lacks automated coverage"}],
            )
            first = semantic_disposition.accept_with_deferrals(repo, reason="dogfood now")
            second = semantic_disposition.accept_with_deferrals(repo, reason="ignored duplicate")
            self.assertEqual(first["override"], second["override"])
            self.assertTrue(semantic_disposition.accepted_for_current_result(repo))

            state = workflow_stages.read_state(current)
            state["VerifiedSourceIdentity"] = "different-source-identity"
            workflow_stages.write_state(current, state)
            self.assertFalse(semantic_disposition.accepted_for_current_result(repo))
            state["VerifiedSourceIdentity"] = "source-identity"
            workflow_stages.write_state(current, state)
            self.assertTrue(semantic_disposition.accepted_for_current_result(repo))

            self._result(
                current,
                findings=[{"severity": "blocking", "message": "A different unresolved focus defect remains"}],
            )
            self.assertFalse(semantic_disposition.accepted_for_current_result(repo))

    def test_unmet_requirement_requires_explicit_human_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, _ = self._repo(Path(temp_dir))
            self._result(
                current,
                requirements=[
                    {
                        "criterion": "Enter accepts the dialog",
                        "status": "missing",
                        "evidence": ["No presentation test covers Enter"],
                    }
                ],
            )
            disposition = semantic_disposition.evaluate(repo)
            self.assertEqual(disposition["state"], semantic_disposition.AWAITING_HUMAN)
            self.assertEqual(disposition["entries"][0]["kind"], "requirement")

    def test_transition_completes_semantic_checkpoint_without_faking_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, state = self._repo(Path(temp_dir))
            self._result(
                current,
                findings=[{"severity": "blocking", "message": "Dashboard refresh is delayed"}],
            )
            manifest_path = current / run_manifest.MANIFEST_NAME
            run_manifest.create_manifest(
                manifest_path,
                repo_path=repo,
                github_repo="example/repo",
                issue_number=356,
                mode="issue-to-pr",
                base_sha="base",
                branch="feature/356-test",
                role_snapshots={},
            )
            run_manifest.record_stage_state(
                manifest_path,
                "semantic-verified",
                status="repair-required",
                details={"attempt": 2},
            )

            disposition_transition.accept(
                repo,
                reason="Owner accepts this bounded dogfood gap",
            )
            manifest = run_manifest.load_manifest(manifest_path)
            updated = workflow_stages.read_state(current)

            self.assertTrue(run_manifest.stage_completed(manifest, "semantic-verified"))
            details = manifest["stages"]["semantic-verified"]["details"]
            self.assertEqual(details["disposition"], semantic_disposition.ACCEPTED_WITH_DEFERRALS)
            self.assertEqual(details["verdict"], "repair")
            self.assertEqual(updated["LastSemanticVerdict"], "repair")
            self.assertTrue(semantic_disposition.accepted_for_current_result(repo, updated))


if __name__ == "__main__":
    unittest.main()
