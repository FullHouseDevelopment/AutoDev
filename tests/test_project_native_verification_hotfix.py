from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from automation import (
    local_verification,
    verification_discovery,
    workflow_verification,
    workflow_workspace,
)
from automation.workflow_contract import FAILURE_SETUP, WorkflowStageError
from automation.workflow_storage import read_json, read_state, write_state


REPO_ROOT = Path(__file__).resolve().parents[1]


class ProjectNativeVerificationHotfixTests(unittest.TestCase):
    @staticmethod
    def _write(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    @staticmethod
    def _git(repo: Path, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=repo,
            text=True,
            capture_output=True,
            check=True,
        )
        return completed.stdout.strip()

    @staticmethod
    def _success_runner(argv, **_kwargs):
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    def _prepared_run(self, repo: Path, issue_text: str) -> Path:
        self._git(repo, "init")
        self._git(repo, "config", "user.email", "autodev@example.test")
        self._git(repo, "config", "user.name", "AutoDev Test")
        self._write(repo / "Legacy.sln", "\n")
        self._write(repo / "legacy.txt", "legacy stack remains\n")
        self._git(repo, "add", "Legacy.sln", "legacy.txt")
        self._git(repo, "commit", "-m", "legacy foundation")
        base = self._git(repo, "rev-parse", "HEAD")

        current = repo / ".autodev-run" / "current"
        current.mkdir(parents=True)
        self._write(current / "issue.md", issue_text)
        workflow_workspace.write_workspace_snapshot(
            repo,
            current / "workspace-snapshot.json",
        )
        write_state(
            current,
            {
                "BaseSha": base,
                "RunDir": str(current),
                "LocalCheck": local_verification.BUILTIN_LOCAL_CHECK,
                "LocalCheckSource": "legacy",
                "LastLocalCheckPassed": False,
            },
        )
        verification_discovery.refresh_verification_discovery(
            repo,
            current,
            issue_text=issue_text,
        )
        return current

    def test_new_project_native_stack_invalidates_discovery_and_missing_tool_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = self._prepared_run(
                repo,
                "Migrate the project foundation to the new engine and verify it opens cleanly.",
            )
            before = read_json(current / "recommended-command-groups.json")
            self.assertEqual(before.get("project_native_requirements"), [])

            self._write(repo / "project.godot", "[application]\nconfig/name=\"Sky Home\"\n")
            self._write(repo / "scripts" / "bootstrap.gd", "extends Node\n")

            reason = verification_discovery.stale_verification_reason(repo, current)
            self.assertIn("project-native", reason)

            state = read_state(current)
            with patch.object(
                workflow_verification.shutil,
                "which",
                side_effect=lambda name: None if name == "godot" else f"/tools/{name}",
            ):
                with self.assertRaises(WorkflowStageError) as caught:
                    workflow_verification.run_local_check(
                        repo,
                        current,
                        state,
                        REPO_ROOT,
                        runner=self._success_runner,
                    )

            after = read_json(current / "recommended-command-groups.json")
            persisted = read_state(current)

        self.assertEqual(caught.exception.classification, FAILURE_SETUP)
        self.assertIn("required executable 'godot' is unavailable", str(caught.exception))
        self.assertIn(
            "project-native-godot",
            after["recommended_command_groups"],
        )
        self.assertEqual(
            after["project_native_requirements"][0]["marker"],
            "project.godot",
        )
        self.assertTrue(after["project_native_fingerprint"])
        self.assertEqual(persisted["Status"], "LocalCheckSetupFailed")
        self.assertFalse(persisted["LastLocalCheckPassed"])
        self.assertEqual(persisted["LocalCheckFailureClassification"], FAILURE_SETUP)

    def test_project_native_command_runs_when_required_tool_is_available(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            self._write(current / "issue.md", "Validate the Godot 4 project foundation.\n")
            self._write(repo / "project.godot", "[application]\n")
            calls: list[tuple[list[str], Path]] = []

            verification_discovery.refresh_verification_discovery(
                repo,
                current,
                changed_paths=["project.godot"],
            )

            def runner(argv, **kwargs):
                calls.append((list(argv), Path(kwargs["cwd"])))
                return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

            result = local_verification.run_recommended_verification(
                repo,
                current,
                runner=runner,
                which=lambda name: f"/tools/{name}",
                refresh_stale=False,
            )

        self.assertEqual(result.returncode, 0)
        self.assertTrue(
            any(
                argv == ["godot", "--headless", "--path", ".", "--quit-after", "1"]
                and cwd.resolve() == repo.resolve()
                for argv, cwd in calls
            )
        )

    def test_repository_can_declare_required_project_native_command_without_toolchain_special_case(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            self._write(current / "issue.md", "Update the custom native application.\n")
            self._write(
                repo / ".autodev" / "repo.json",
                json.dumps(
                    {
                        "version": 1,
                        "verification": {
                            "required_commands": [
                                {
                                    "name": "native-smoke",
                                    "label": "Run custom native smoke",
                                    "cwd": ".",
                                    "argv": ["custom-native-check", "--verify"],
                                    "reason": "Repository policy requires the native runtime smoke.",
                                }
                            ]
                        },
                    }
                ),
            )

            discovery = verification_discovery.refresh_verification_discovery(
                repo,
                current,
            )
            recommendations = discovery["recommendations"]

            with self.assertRaises(WorkflowStageError) as caught:
                local_verification.run_recommended_verification(
                    repo,
                    current,
                    runner=self._success_runner,
                    which=lambda name: None if name == "custom-native-check" else f"/tools/{name}",
                    refresh_stale=False,
                )

        self.assertIn(
            "project-native-config-native-smoke",
            recommendations["recommended_command_groups"],
        )
        self.assertEqual(
            recommendations["project_native_requirements"][0]["source"],
            ".autodev/repo.json",
        )
        self.assertEqual(caught.exception.classification, FAILURE_SETUP)
        self.assertIn("custom-native-check", str(caught.exception))

    def test_unrelated_documentation_change_does_not_force_godot_runtime(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir)
            current = repo / ".autodev-run" / "current"
            current.mkdir(parents=True)
            self._write(current / "issue.md", "Documentation-only: update the README.\n")
            self._write(repo / "project.godot", "[application]\n")
            self._write(repo / "README.md", "# Project\n")

            discovery = verification_discovery.refresh_verification_discovery(
                repo,
                current,
                changed_paths=["README.md"],
            )

        self.assertEqual(
            discovery["recommendations"]["project_native_requirements"],
            [],
        )
        self.assertFalse(
            any(
                str(name).startswith("project-native-godot")
                for name in discovery["recommendations"]["recommended_command_groups"]
            )
        )


if __name__ == "__main__":
    unittest.main()
