from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import (
    continuation,
    continuation_recovery,
    opencode_resume_status,
    role_resume,
    run_manifest,
    workflow_storage,
    workflow_workspace,
)
from tests.test_continuation import ContinuationGitFixture


class LegacyContinuationCheckpointMigrationHotfixTests(
    ContinuationGitFixture,
    unittest.TestCase,
):
    def _legacy_checkpoint(
        self,
        repo: Path,
        *,
        current_semantics: bool = False,
    ) -> tuple[Path, dict[str, object], str, str, dict[str, object], dict[str, object]]:
        base, _feature = self._repo(repo)
        self._git(repo, "checkout", "feature/existing-work")
        (repo / ".github").mkdir()
        (repo / ".github" / "GitVersion.yaml").write_text(
            "mode: ContinuousDelivery\n",
            encoding="utf-8",
        )
        self._git(repo, "add", ".github/GitVersion.yaml")
        self._git(repo, "commit", "-m", "continuation source with legacy version file")
        adopted = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "checkout", "--detach", adopted)

        # A configured AutoDev repository excludes durable run state from source
        # identity. Mirror that boundary so the fixture contains exactly the one
        # source deletion from the real Sky Home reproducer.
        git_exclude = repo / ".git" / "info" / "exclude"
        git_exclude.write_text(
            git_exclude.read_text(encoding="utf-8") + "\n.autodev-run/\n",
            encoding="utf-8",
        )

        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        continuation.install_hooks()
        workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
        state: dict[str, object] = {
            "Status": "SemanticVerified",
            "RepoFullName": "example/project",
            "IssueNumber": 350,
            "Base": "develop",
            "BaseSha": base,
            "BranchName": "autodev/issue-350-continuation",
            "PreparedLocalHeadSha": adopted,
            "PreparedSnapshotHash": workflow_storage._file_sha256(
                current / "workspace-snapshot.json"
            ),
            "ContinuationSourceVersion": continuation.SCHEMA_VERSION,
            "ContinuationRequestedRef": "feature/existing-work",
            "ContinuationResolvedSha": adopted,
            "ContinuationAdoptedAt": "2026-09-14T00:00:00+00:00",
            "LastCommitSha": "",
        }
        workflow_storage.write_json(current / "state.json", state)

        # Exact Sky Home shape: one implementation deletion relative to the
        # adopted continuation snapshot.
        (repo / ".github" / "GitVersion.yaml").unlink()
        current_proof = workflow_workspace.source_identity(repo, current, state)
        legacy_state = dict(state)
        legacy_state["ContinuationResolvedSha"] = ""
        legacy_state["ContinuationRequestedRef"] = ""
        legacy_proof = workflow_workspace.source_identity(repo, current, legacy_state)
        self.assertEqual(
            current_proof["changes"],
            [{"path": ".github/GitVersion.yaml", "status": "deleted", "sha256": ""}],
        )
        self.assertEqual(current_proof["parent_sha"], adopted)
        self.assertEqual(legacy_proof["parent_sha"], base)
        self.assertNotEqual(current_proof["identity"], legacy_proof["identity"])

        manifest_path = current / run_manifest.MANIFEST_NAME
        run_manifest.create_manifest(
            manifest_path,
            repo_path=repo,
            github_repo="example/project",
            issue_number=350,
            mode="issue-to-pr",
            base_sha=base,
            branch=str(state["BranchName"]),
            role_snapshots={},
        )
        manifest = run_manifest.load_manifest(manifest_path)
        manifest["continuation_source"] = {
            "schema_version": continuation.SCHEMA_VERSION,
            "requested_ref": "feature/existing-work",
            "resolved_sha": adopted,
            "adopted_at": "2026-09-14T00:00:00+00:00",
        }
        run_manifest.save_manifest(manifest_path, manifest)
        for stage in (
            "issue-selected",
            "repository-read",
            "handoff-synthesized",
            "plan-created",
        ):
            run_manifest.complete_stage(manifest_path, stage, run_root=current)

        checkpoint_proof = current_proof if current_semantics else legacy_proof
        source_details = {
            "source_identity": str(checkpoint_proof["identity"]),
            "parent_sha": str(checkpoint_proof["parent_sha"]),
            "changed_paths": [
                str(item["path"])
                for item in checkpoint_proof["changes"]
                if isinstance(item, dict)
            ],
        }
        run_manifest.complete_stage(
            manifest_path,
            "implementation-generated",
            run_root=current,
            details=source_details,
        )
        run_manifest.complete_stage(
            manifest_path,
            "patch-applied",
            run_root=current,
            inputs={"source_identity": str(checkpoint_proof["identity"])},
            details={"kind": "implementation", "attempt": 0, **source_details},
        )
        run_manifest.complete_stage(
            manifest_path,
            "deterministic-verified",
            run_root=current,
            details={
                "source_identity": str(checkpoint_proof["identity"]),
                "parent_sha": str(checkpoint_proof["parent_sha"]),
            },
        )
        run_manifest.complete_stage(
            manifest_path,
            "semantic-verified",
            run_root=current,
            details={"source_identity": str(checkpoint_proof["identity"]), "verdict": "pass"},
        )
        return current, state, base, adopted, legacy_proof, current_proof

    def test_pre_347_checkpoint_migrates_then_fixer_invalidation_resumes_at_local_check(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, base, adopted, legacy_proof, current_proof = self._legacy_checkpoint(repo)

            self.assertEqual(continuation_recovery.finish_pending(repo, runner=self._runner), {})

            migrated = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
            patch_record = migrated["stages"]["patch-applied"]
            details = patch_record["details"]
            self.assertEqual(details["parent_sha"], adopted)
            self.assertEqual(details["source_identity"], current_proof["identity"])
            self.assertEqual(details["legacy_parent_sha"], base)
            self.assertEqual(details["legacy_source_identity"], legacy_proof["identity"])
            self.assertEqual(
                details["source_identity_migration"],
                continuation_recovery.LEGACY_SOURCE_PARENT_MIGRATION,
            )
            self.assertEqual(
                patch_record["input_hash"],
                run_manifest.hash_json({"source_identity": str(current_proof["identity"])}),
            )
            self.assertEqual(
                migrated["stages"]["implementation-generated"]["details"]["parent_sha"],
                adopted,
            )
            self.assertEqual(len(migrated["compatibility_migrations"]), 1)

            persisted_state = workflow_storage.read_json(current / "state.json")
            self.assertEqual(persisted_state["BaseSha"], base)
            self.assertEqual(persisted_state["ContinuationResolvedSha"], adopted)
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), adopted)
            self.assertEqual(
                self._git(repo, "status", "--short"),
                "D .github/GitVersion.yaml",
            )

            resumed = role_resume.resume(
                repo,
                {},
                invalidated_roles={"fixer"},
                runner=self._runner,
            )
            self.assertEqual(resumed["next_stage"], "deterministic-verified")
            self.assertEqual(resumed["next_action"], "local-check")
            after_resume = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
            self.assertTrue(run_manifest.stage_completed(after_resume, "patch-applied"))
            self.assertFalse(run_manifest.stage_completed(after_resume, "deterministic-verified"))
            self.assertFalse(run_manifest.stage_completed(after_resume, "semantic-verified"))

    def test_changed_file_digest_does_not_migrate_and_resume_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, _state, _base, _adopted, _legacy, _current = self._legacy_checkpoint(repo)
            (repo / "app.txt").write_text("drift after checkpoint\n", encoding="utf-8")

            self.assertEqual(continuation_recovery.finish_pending(repo, runner=self._runner), {})
            manifest = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
            details = manifest["stages"]["patch-applied"]["details"]
            self.assertNotIn("source_identity_migration", details)
            self.assertNotIn("compatibility_migrations", manifest)

            state = workflow_storage.read_json(current / "state.json")
            problems = opencode_resume_status._resume_problems(
                repo,
                current,
                manifest,
                state,
                runner=self._runner,
                validate_remote=False,
            )
            self.assertTrue(
                any("source/worktree drift detected" in problem for problem in problems),
                problems,
            )

    def test_mismatched_legacy_identity_cannot_be_upgraded(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, _state, base, _adopted, _legacy, _current = self._legacy_checkpoint(repo)
            path = current / run_manifest.MANIFEST_NAME
            manifest = run_manifest.load_manifest(path)
            record = manifest["stages"]["patch-applied"]
            record["details"]["source_identity"] = "0" * 64
            record["input_hash"] = run_manifest.hash_json({"source_identity": "0" * 64})
            run_manifest.save_manifest(path, manifest)

            self.assertFalse(
                continuation_recovery._migrate_legacy_patch_checkpoint(repo, runner=self._runner)
            )
            unchanged = run_manifest.load_manifest(path)
            self.assertEqual(unchanged["stages"]["patch-applied"]["details"]["parent_sha"], base)
            self.assertNotIn(
                "source_identity_migration",
                unchanged["stages"]["patch-applied"]["details"],
            )

    def test_post_347_checkpoint_does_not_take_legacy_path(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, _state, _base, adopted, _legacy, current_proof = self._legacy_checkpoint(
                repo,
                current_semantics=True,
            )

            self.assertFalse(
                continuation_recovery._migrate_legacy_patch_checkpoint(repo, runner=self._runner)
            )
            manifest = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
            details = manifest["stages"]["patch-applied"]["details"]
            self.assertEqual(details["parent_sha"], adopted)
            self.assertEqual(details["source_identity"], current_proof["identity"])
            self.assertNotIn("source_identity_migration", details)
            self.assertNotIn("compatibility_migrations", manifest)

    @staticmethod
    def _runner(command, **kwargs):
        import subprocess

        return subprocess.run(command, **kwargs)


if __name__ == "__main__":
    unittest.main()
