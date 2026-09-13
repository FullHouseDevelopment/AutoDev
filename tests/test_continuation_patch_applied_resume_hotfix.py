from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from automation import (
    continuation,
    opencode_resume_status,
    run_manifest,
    workflow_stages,
    workflow_storage,
    workflow_workspace,
)
from tests.test_continuation import ContinuationGitFixture


class ContinuationPatchAppliedResumeHotfixTests(ContinuationGitFixture, unittest.TestCase):
    def _checkpointed_continuation(self, repo: Path) -> tuple[Path, dict[str, object], dict[str, object], str, str]:
        base, adopted = self._repo(repo)
        self._git(repo, "checkout", "--detach", adopted)
        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)

        continuation.install_hooks()
        workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
        state: dict[str, object] = {
            "Status": "ImplementerPromptRendered",
            "RepoFullName": "example/project",
            "IssueNumber": 342,
            "Base": "develop",
            "BaseSha": base,
            "BranchName": "autodev/issue-342-continuation",
            "PreparedLocalHeadSha": adopted,
            "PreparedSnapshotHash": workflow_storage._file_sha256(
                current / "workspace-snapshot.json"
            ),
            "ContinuationRequestedRef": "feature/existing-work",
            "ContinuationResolvedSha": adopted,
            "LastCommitSha": "",
        }
        workflow_storage.write_json(current / "state.json", state)

        manifest_path = current / run_manifest.MANIFEST_NAME
        run_manifest.create_manifest(
            manifest_path,
            repo_path=repo,
            github_repo="example/project",
            issue_number=342,
            mode="issue-to-pr",
            base_sha=base,
            branch=str(state["BranchName"]),
            role_snapshots={},
        )
        source = workflow_stages.source_identity(repo, current, state)
        run_manifest.complete_stage(
            manifest_path,
            "patch-applied",
            run_root=current,
            details={"source_identity": str(source["identity"])},
        )
        return current, state, run_manifest.load_manifest(manifest_path), base, adopted

    def test_patch_applied_still_accepts_adopted_head_until_successor_commit_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, manifest, base, adopted = self._checkpointed_continuation(repo)

            self.assertNotEqual(base, adopted)
            self.assertEqual(state["LastCommitSha"], "")
            self.assertTrue(run_manifest.stage_completed(manifest, "patch-applied"))
            self.assertEqual(self._git(repo, "rev-parse", "HEAD"), adopted)

            problems = opencode_resume_status._resume_problems(
                repo,
                current,
                manifest,
                state,
                runner=self._runner,
                validate_remote=False,
            )

            self.assertEqual(problems, [])
            self.assertEqual(opencode_resume_status.resume_action(manifest, state), "local-check")

    def test_patch_applied_continuation_drift_still_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, manifest, _base, adopted = self._checkpointed_continuation(repo)
            (repo / "app.txt").write_text("unexpected successor\n", encoding="utf-8")
            self._git(repo, "add", "app.txt")
            self._git(repo, "commit", "-m", "unexpected successor")
            drifted = self._git(repo, "rev-parse", "HEAD")
            self.assertNotEqual(drifted, adopted)

            problems = opencode_resume_status._resume_problems(
                repo,
                current,
                manifest,
                state,
                runner=self._runner,
                validate_remote=False,
            )

            self.assertTrue(
                any("continuation source drift detected" in problem for problem in problems),
                problems,
            )

    def test_last_commit_sha_ends_continuation_head_authority(self) -> None:
        state = {
            "ContinuationResolvedSha": "adopted-sha",
            "LastCommitSha": "autodev-created-successor",
        }
        self.assertEqual(continuation._continuation_sha(state), "")

    def test_non_continuation_head_base_mismatch_remains_a_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, manifest, base, adopted = self._checkpointed_continuation(repo)
            state.pop("ContinuationRequestedRef", None)
            state.pop("ContinuationResolvedSha", None)

            problems = opencode_resume_status._resume_problems(
                repo,
                current,
                manifest,
                state,
                runner=self._runner,
                validate_remote=False,
            )

            self.assertIn(
                f"local HEAD {adopted} no longer matches prepared base {base}",
                problems,
            )

    @staticmethod
    def _runner(command, **kwargs):
        import subprocess

        return subprocess.run(command, **kwargs)


if __name__ == "__main__":
    unittest.main()
