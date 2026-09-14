from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    continuation,
    local_verification,
    workflow_dispatch,
    workflow_github,
    workflow_stages,
    workflow_storage,
    workflow_verification,
    workflow_workspace,
)
from automation.workflow_contract import WorkflowStageError
from tests.test_continuation import ContinuationGitFixture


class ContinuationSourceIdentityHotfixTests(ContinuationGitFixture, unittest.TestCase):
    def _sky_home_shape(self, repo: Path) -> tuple[Path, dict[str, object], str, str]:
        base, _feature = self._repo(repo)
        self._git(repo, "checkout", "feature/existing-work")
        (repo / ".github").mkdir()
        (repo / ".github" / "GitVersion.yaml").write_text("mode: ContinuousDelivery\n", encoding="utf-8")
        self._git(repo, "add", ".github/GitVersion.yaml")
        self._git(repo, "commit", "-m", "continuation source")
        adopted = self._git(repo, "rev-parse", "HEAD")
        self._git(repo, "checkout", "--detach", adopted)

        # Real AutoDev repositories exclude durable run artifacts from source scope.
        # Mirror that contract so this fixture isolates the one implementation deletion.
        exclude = repo / ".git" / "info" / "exclude"
        existing_excludes = exclude.read_text(encoding="utf-8") if exclude.is_file() else ""
        exclude.write_text(existing_excludes + "\n.autodev-run/\n", encoding="utf-8")

        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        workflow_workspace.write_workspace_snapshot(repo, current / "workspace-snapshot.json")
        snapshot_hash = workflow_storage._file_sha256(current / "workspace-snapshot.json")
        (current / "commit-message.txt").write_text("#347 regression\n", encoding="utf-8")

        state: dict[str, object] = {
            "Status": "Implemented",
            "RepoFullName": "example/project",
            "IssueNumber": 347,
            "Base": "develop",
            "BaseSha": base,
            "BaseTreeSha": self._git(repo, "rev-parse", f"{base}^{{tree}}"),
            "BranchName": "autodev/issue-347-continuation",
            "PreparedLocalHeadSha": adopted,
            "PreparedSnapshotHash": snapshot_hash,
            "ContinuationRequestedRef": "feature/existing-work",
            "ContinuationResolvedSha": adopted,
            "LastCommitSha": "",
            "VerificationProofVersion": 1,
            "LastLocalCheckPassed": False,
            "LocalCheck": "noop-local-check",
            "LocalCheckSource": "",
        }
        workflow_storage.write_state(current, state)
        (repo / ".github" / "GitVersion.yaml").unlink()
        continuation.install_hooks()
        return current, state, base, adopted

    def _run_local_check(self, repo: Path, current: Path, state: dict[str, object]) -> dict[str, object]:
        with patch.object(
            local_verification,
            "refreshed_local_check",
            return_value=("noop-local-check", "", Path("")),
        ), patch.object(
            local_verification,
            "is_builtin_local_check",
            return_value=False,
        ), patch.object(
            workflow_verification,
            "_run_captured",
            return_value=SimpleNamespace(returncode=0, stdout="", stderr=""),
        ):
            self.assertTrue(
                workflow_verification.run_local_check(
                    repo,
                    current,
                    state,
                    repo,
                )
            )
        return workflow_storage.read_state(current)

    @staticmethod
    def _github_commit_mocks(adopted: str):
        def fake_gh_json(_repo, arguments, **_kwargs):
            endpoint = str(arguments[1]) if len(arguments) > 1 else ""
            method = "POST" if "POST" in arguments else "GET"
            if endpoint.endswith(f"/git/commits/{adopted}") and method == "GET":
                return {"tree": {"sha": "continuation-tree"}}
            if endpoint.endswith("/git/trees") and method == "POST":
                return {"sha": "created-tree"}
            if endpoint.endswith("/git/commits") and method == "POST":
                return {"sha": "created-commit"}
            if endpoint.endswith("/git/commits/created-commit") and method == "GET":
                return {
                    "tree": {"sha": "created-tree"},
                    "parents": [{"sha": adopted}],
                }
            raise AssertionError(f"unexpected gh_json call: {arguments}")

        def fake_gh(_repo, arguments, **_kwargs):
            endpoint = str(arguments[1]) if len(arguments) > 1 else ""
            if "/git/ref/heads/" in endpoint:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            if endpoint.endswith("/git/refs"):
                return SimpleNamespace(returncode=0, stdout="", stderr="")
            raise AssertionError(f"unexpected gh call: {arguments}")

        return fake_gh_json, fake_gh

    def test_continuation_local_semantic_identity_and_api_shipment_share_adopted_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, base, adopted = self._sky_home_shape(repo)
            state = self._run_local_check(repo, current, state)

            self.assertEqual(state["BaseSha"], base)
            self.assertEqual(state["VerifiedParentSha"], adopted)
            self.assertEqual(
                state["VerifiedChanges"],
                [{"path": ".github/GitVersion.yaml", "status": "deleted", "sha256": ""}],
            )

            proofs = [
                workflow_verification.source_identity(repo, current, state),
                workflow_workspace.source_identity(repo, current, state),
                workflow_dispatch.source_identity(repo, current, state),
                workflow_github.source_identity(repo, current, state),
                workflow_stages.source_identity(repo, current, state),
            ]
            self.assertTrue(all(proof["parent_sha"] == adopted for proof in proofs), proofs)
            self.assertEqual({str(proof["identity"]) for proof in proofs}, {state["VerifiedSourceIdentity"]})

            state["LastSemanticVerdict"] = "pass"
            state["SemanticSourceIdentity"] = state["VerifiedSourceIdentity"]
            workflow_storage.write_state(current, state)
            changes = workflow_workspace.workspace_changes(repo, current, state)
            self.assertEqual(changes, [{"Path": ".github/GitVersion.yaml", "Status": "deleted"}])

            fake_gh_json, fake_gh = self._github_commit_mocks(adopted)
            with patch.object(workflow_github, "gh_json", side_effect=fake_gh_json), patch.object(
                workflow_github,
                "gh",
                side_effect=fake_gh,
            ):
                created = workflow_verification.create_api_commit(
                    repo,
                    state,
                    changes,
                    current,
                )

            self.assertEqual(created, "created-commit")
            persisted = workflow_storage.read_state(current)
            self.assertEqual(persisted["BaseSha"], base)
            self.assertEqual(persisted["CreatedParentSha"], adopted)
            self.assertEqual(persisted["ShippedSourceIdentity"], persisted["VerifiedSourceIdentity"])

    def test_real_workspace_drift_still_blocks_api_commit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current, state, _base, adopted = self._sky_home_shape(repo)
            state = self._run_local_check(repo, current, state)
            (repo / "app.txt").write_text("drift after verification\n", encoding="utf-8")
            changes = workflow_workspace.workspace_changes(repo, current, state)
            fake_gh_json, fake_gh = self._github_commit_mocks(adopted)

            with patch.object(workflow_github, "gh_json", side_effect=fake_gh_json), patch.object(
                workflow_github,
                "gh",
                side_effect=fake_gh,
            ), self.assertRaises(WorkflowStageError) as raised:
                workflow_verification.create_api_commit(
                    repo,
                    state,
                    changes,
                    current,
                )

            self.assertIn("workspace no longer matches", str(raised.exception))

    def test_source_parent_precedence_transitions_from_continuation_to_successor_commit(self) -> None:
        self.assertEqual(
            workflow_workspace.source_parent_sha(
                {"BaseSha": "base", "ContinuationResolvedSha": "adopted", "LastCommitSha": ""}
            ),
            "adopted",
        )
        self.assertEqual(
            workflow_workspace.source_parent_sha(
                {
                    "BaseSha": "base",
                    "ContinuationResolvedSha": "adopted",
                    "LastCommitSha": "successor",
                }
            ),
            "successor",
        )
        self.assertEqual(workflow_workspace.source_parent_sha({"BaseSha": "base"}), "base")


if __name__ == "__main__":
    unittest.main()
