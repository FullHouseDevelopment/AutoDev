from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    opencode_adapter_protocol,
    opencode_resume_contract,
    opencode_resume_manifest,
    opencode_resume_status,
    role_coordinator_flow,
    role_output_contract,
    role_resume,
    run_manifest,
    workflow_stages,
)


def _mappings(**overrides: str) -> dict[str, dict[str, str]]:
    values = {
        role: {
            "agent": f"autodev-{role}",
            "source": "explicit",
            "model": f"provider/{role}",
            "inherits_from": "",
        }
        for role in opencode_resume_contract.ROLE_NAMES
    }
    for role, model in overrides.items():
        values[role] = {
            "agent": f"autodev-{role}",
            "source": "explicit",
            "model": model,
            "inherits_from": "",
        }
    return values


class RoleTransitionAtomicityHotfixTests(unittest.TestCase):
    def _repo(self, root: str) -> tuple[Path, Path, Path]:
        repo = Path(root)
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state = {
            "Status": "Prepared",
            "IssueNumber": 308,
            "RepoFullName": "com-mit-group/ShuffleTask",
            "BranchName": "autodev/issue-308",
            "BaseSha": "base-sha",
            "BaseTreeSha": "base-tree",
            "PreparedSnapshotHash": "snapshot",
            "LastCommitSha": "",
            "PrUrl": "",
            "PrNumber": 0,
            "PrHeadSha": "",
        }
        (current / "state.json").write_text(json.dumps(state), encoding="utf-8")
        (current / "issue.md").write_text("#308 title-first quick capture\n", encoding="utf-8")
        (current / "workspace-snapshot.json").write_text("{}\n", encoding="utf-8")
        path = role_resume.create_manifest(repo, state, runtime_name="opencode")
        role_resume.reconcile_snapshots(repo, opencode_resume_manifest.role_snapshots(_mappings()))
        return repo, current, path

    def _complete_through_plan(self, current: Path, path: Path) -> None:
        for stage in (
            "repository-read",
            "handoff-synthesized",
            "plan-created",
        ):
            run_manifest.complete_stage(path, stage, run_root=current)

    def _complete_pre_repair_pipeline(self, current: Path, path: Path) -> None:
        self._complete_through_plan(current, path)
        run_manifest.complete_stage(path, "implementation-generated", run_root=current)
        run_manifest.complete_stage(
            path,
            "patch-applied",
            run_root=current,
            details={"source_identity": "source-before-fixer", "parent_sha": "base-sha"},
        )
        run_manifest.complete_stage(
            path,
            "deterministic-verified",
            run_root=current,
            details={"attempt": 0, "source_identity": "source-before-fixer"},
        )

    def _write_fixer_context(self, current: Path) -> None:
        (current / "ux-context-fixer.json").write_text(
            json.dumps({"ux_context_fingerprint": "ux-fixer"}),
            encoding="utf-8",
        )

    def test_first_fixer_binding_does_not_invalidate_pre_repair_verification(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_pre_repair_pipeline(current, path)
            self._write_fixer_context(current)
            current_snapshots = opencode_resume_manifest.role_snapshots(_mappings())

            role_resume.reconcile_snapshots(
                repo,
                current_snapshots,
                pending_role="fixer",
            )

            manifest = run_manifest.load_manifest(path)
            self.assertTrue(run_manifest.stage_completed(manifest, "deterministic-verified"))
            bound = dict(current_snapshots)
            role_output_contract.bind_snapshot_set_to_existing_contexts(repo, bound)
            self.assertEqual(
                manifest["roles"]["fixer"]["fingerprint"],
                bound["fixer"]["fingerprint"],
            )

    def test_legacy_checkpoint_reconciliation_allows_first_fixer_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_pre_repair_pipeline(current, path)
            self._write_fixer_context(current)

            opencode_resume_manifest.reconcile_models(
                repo,
                _mappings(),
                pending_role="fixer",
            )

            manifest = run_manifest.load_manifest(path)
            self.assertTrue(run_manifest.stage_completed(manifest, "deterministic-verified"))

    def test_changed_completed_fixer_still_requires_explicit_invalidation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_pre_repair_pipeline(current, path)
            self._write_fixer_context(current)
            original = opencode_resume_manifest.role_snapshots(_mappings())
            role_resume.reconcile_snapshots(repo, original, pending_role="fixer")
            run_manifest.complete_stage(
                path,
                "repair-generated",
                run_root=current,
                details={"kind": "semantic", "attempt": 1},
            )

            changed = opencode_resume_manifest.role_snapshots(
                _mappings(fixer="provider/new-fixer")
            )
            with self.assertRaises(role_resume.RoleResumeError) as raised:
                role_resume.reconcile_snapshots(repo, changed, pending_role="fixer")

            self.assertIn("--invalidate-role", str(raised.exception))

    def test_historical_339_fixer_recovery_preserves_existing_accepted_edits(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_pre_repair_pipeline(current, path)
            run_manifest.record_stage_state(
                path,
                "repair-generated",
                status="in-progress",
                details={"kind": "semantic", "attempt": 1},
            )
            run_manifest.record_failure(
                path,
                classification=workflow_stages.FAILURE_DETERMINISTIC,
                reason=(
                    "execution-affecting role configuration changed for completed work; "
                    "resume requires --invalidate-role for: fixer -> deterministic-verified"
                ),
                stage="python-coordinator",
            )
            self._write_fixer_context(current)
            # Historical #339 runs predate source-bound acceptance markers.
            opencode_adapter_protocol._mark_role_accepted(current, "fixer", [])

            repaired = {
                "identity": "source-after-fixer",
                "parent_sha": "base-sha",
                "changes": [
                    {
                        "path": "ShuffleTask.Presentation.Shared/ViewModels/TasksViewModel.cs",
                        "status": "modified",
                        "sha256": "abc",
                    }
                ],
            }
            snapshots = opencode_resume_manifest.role_snapshots(_mappings())
            role_output_contract.bind_snapshot_set_to_existing_contexts(repo, snapshots)
            manifest = run_manifest.load_manifest(path)
            role_resume._prepare_pending_source_role_snapshots_for_resume(
                repo,
                path,
                manifest,
                snapshots,
            )
            run_manifest.reconcile_role_snapshots(path, snapshots)
            manifest = run_manifest.load_manifest(path)

            with patch.object(workflow_stages, "source_identity", return_value=repaired):
                recovered = role_resume._recover_interrupted_fixer_checkpoint(
                    repo,
                    current,
                    path,
                    manifest,
                )

            self.assertTrue(recovered)
            manifest = run_manifest.load_manifest(path)
            state = workflow_stages.read_state(current)
            self.assertEqual(opencode_resume_status.resume_action(manifest, state), "local-check")
            self.assertTrue(run_manifest.stage_completed(manifest, "repair-generated"))
            self.assertTrue(run_manifest.stage_completed(manifest, "patch-applied"))
            self.assertFalse(run_manifest.stage_completed(manifest, "deterministic-verified"))
            patch_record = manifest["stages"]["patch-applied"]
            self.assertEqual(
                patch_record["details"]["source_identity"],
                "source-after-fixer",
            )
            semantic_record = manifest["stages"]["semantic-verified"]
            self.assertEqual(semantic_record["status"], "pending")

    def test_source_bound_interrupted_implementer_is_recovered_without_rerun(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_through_plan(current, path)
            (current / "commit-message.txt").write_text("Implement quick capture\n", encoding="utf-8")
            proof = {
                "identity": "accepted-implementation-source",
                "parent_sha": "base-sha",
                "changes": [
                    {"path": "TasksPage.xaml", "status": "modified", "sha256": "impl"}
                ],
            }
            opencode_adapter_protocol._mark_role_accepted(
                current,
                "implementer",
                [current / "commit-message.txt"],
                source_proof=proof,
            )
            snapshots = opencode_resume_manifest.role_snapshots(_mappings())
            role_output_contract.bind_snapshot_set_to_existing_contexts(repo, snapshots)
            manifest = run_manifest.load_manifest(path)
            role_resume._prepare_pending_source_role_snapshots_for_resume(
                repo,
                path,
                manifest,
                snapshots,
            )
            run_manifest.reconcile_role_snapshots(path, snapshots)
            manifest = run_manifest.load_manifest(path)

            with patch.object(workflow_stages, "source_identity", return_value=proof):
                recovered = role_resume._recover_interrupted_implementer_checkpoint(
                    repo,
                    current,
                    path,
                    manifest,
                )

            self.assertTrue(recovered)
            manifest = run_manifest.load_manifest(path)
            self.assertTrue(run_manifest.stage_completed(manifest, "implementation-generated"))
            self.assertTrue(run_manifest.stage_completed(manifest, "patch-applied"))
            self.assertEqual(opencode_resume_status.resume_action(manifest, workflow_stages.read_state(current)), "local-check")

    def test_source_bound_acceptance_rejects_later_worktree_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_through_plan(current, path)
            (current / "commit-message.txt").write_text("Implement quick capture\n", encoding="utf-8")
            accepted = {
                "identity": "accepted-source",
                "parent_sha": "base-sha",
                "changes": [{"path": "TasksPage.xaml", "status": "modified", "sha256": "a"}],
            }
            changed = {
                "identity": "changed-after-acceptance",
                "parent_sha": "base-sha",
                "changes": [{"path": "TasksPage.xaml", "status": "modified", "sha256": "b"}],
            }
            opencode_adapter_protocol._mark_role_accepted(
                current,
                "implementer",
                [current / "commit-message.txt"],
                source_proof=accepted,
            )
            manifest = run_manifest.load_manifest(path)
            with patch.object(workflow_stages, "source_identity", return_value=changed):
                recovered = role_resume._recover_interrupted_implementer_checkpoint(
                    repo,
                    current,
                    path,
                    manifest,
                )
            self.assertFalse(recovered)
            self.assertFalse(run_manifest.stage_completed(run_manifest.load_manifest(path), "implementation-generated"))

    def test_unaccepted_in_progress_fixer_does_not_authorize_dirty_worktree(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._complete_pre_repair_pipeline(current, path)
            run_manifest.record_stage_state(
                path,
                "repair-generated",
                status="in-progress",
                details={"kind": "semantic", "attempt": 1},
            )
            self._write_fixer_context(current)

            dirty = {
                "identity": "uncheckpointed-dirty-source",
                "parent_sha": "base-sha",
                "changes": [{"path": "unexpected.cs", "status": "modified", "sha256": "bad"}],
            }
            snapshots = opencode_resume_manifest.role_snapshots(_mappings())
            with patch.object(
                workflow_stages,
                "git",
                return_value=SimpleNamespace(stdout="base-sha\n", returncode=0),
            ), patch.object(
                workflow_stages,
                "source_identity",
                return_value=dirty,
            ), patch.object(
                role_resume.ux_workflow,
                "validate_resume_identity",
                return_value=None,
            ):
                with self.assertRaises(role_resume.RoleResumeError) as raised:
                    role_resume.resume(repo, snapshots)

            self.assertIn("source/worktree drift", str(raised.exception))

    def test_public_status_snapshot_identity_matches_structured_contract_and_ux_binding(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current, path = self._repo(temp_dir)
            self._write_fixer_context(current)
            snapshots = opencode_resume_manifest.role_snapshots(_mappings())
            role_resume.reconcile_snapshots(repo, snapshots, pending_role="fixer")
            manifest = run_manifest.load_manifest(path)

            changed = opencode_resume_status._changed_role_consequences(
                repo,
                manifest,
                _mappings(),
            )

            self.assertEqual(changed, {})
            fixer_safe = snapshots["fixer"]["safe_metadata"]
            self.assertEqual(
                fixer_safe["output_contract"]["identity"],
                "autodev.fixer-result/v1",
            )

    def test_role_identity_is_reconciled_before_role_runtime_invocation(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, _, _ = self._repo(temp_dir)
            snapshots = opencode_resume_manifest.role_snapshots(_mappings())
            order: list[str] = []

            with patch.object(
                role_coordinator_flow.role_resume,
                "reconcile_snapshots",
                side_effect=lambda *args, **kwargs: order.append("reconcile"),
            ), patch.object(
                role_coordinator_flow,
                "run_role",
                side_effect=lambda *args, **kwargs: order.append("invoke") or {"state": "ACCEPTED"},
            ):
                role_coordinator_flow._run_role_checked(
                    repo,
                    "fixer",
                    object(),
                    snapshots,
                    repair_kind="semantic",
                )

            self.assertEqual(order, ["reconcile", "invoke"])


if __name__ == "__main__":
    unittest.main()
