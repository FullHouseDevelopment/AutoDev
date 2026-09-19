from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import (
    disposition_hooks,
    lifecycle_policy,
    semantic_disposition,
    workflow_stages,
)


class DispositionHookTests(unittest.TestCase):
    def _repo(self, root: Path) -> tuple[Path, Path]:
        repo = root / "repo"
        repo.mkdir()
        (repo / ".autodev").mkdir()
        (repo / ".autodev" / "repo.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "product": {
                        "lifecycle": "preproduction",
                        "default_work_exposure": "experimental",
                    },
                }
            ),
            encoding="utf-8",
        )
        current = repo / workflow_stages.CURRENT_DIR
        current.mkdir(parents=True)
        state: dict[str, object] = {
            "IssueNumber": 356,
            "LastLocalCheckPassed": True,
            "LastSemanticVerdict": "repair",
            "VerifiedSourceIdentity": "source-id",
        }
        state.update(
            lifecycle_policy.state_fields(lifecycle_policy.load_lifecycle_policy(repo))
        )
        workflow_stages.write_state(current, state)
        return repo, current

    def test_blocking_noncritical_semantic_outcome_surfaces_human_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            (current / "verification-result.json").write_text(
                json.dumps(
                    {
                        "verdict": "repair",
                        "requirements": [],
                        "findings": [
                            {
                                "severity": "blocking",
                                "message": "Dashboard refresh is delayed after quick capture",
                            }
                        ],
                        "repair_brief": "refresh immediately",
                    }
                ),
                encoding="utf-8",
            )
            code, payload = disposition_hooks._decorate_semantic_outcome(
                repo,
                0,
                {"state": "BLOCKED", "reason": "semantic repair-attempt limit exhausted"},
            )

        self.assertEqual(code, 0)
        self.assertEqual(payload["state"], "BLOCKED")
        self.assertEqual(
            payload["semantic_disposition"],
            semantic_disposition.AWAITING_HUMAN,
        )

    def test_ready_proof_uses_in_memory_pass_view_only_for_current_accepted_disposition(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            (current / "verification-result.json").write_text(
                json.dumps(
                    {
                        "verdict": "repair",
                        "requirements": [],
                        "findings": [
                            {"severity": "blocking", "message": "Focus coverage is incomplete"}
                        ],
                        "repair_brief": "add coverage",
                    }
                ),
                encoding="utf-8",
            )
            semantic_disposition.accept_with_deferrals(repo, reason="owner accepts dogfood gap")
            durable_before = workflow_stages.read_state(current)
            observed: dict[str, object] = {}

            def original(_current: Path, state: dict[str, object], *, runner) -> None:
                observed.update(state)

            guarded = disposition_hooks._ready_proof_wrapper(original)
            guarded(current, durable_before, runner=lambda *_args, **_kwargs: None)
            durable_after = workflow_stages.read_state(current)

        self.assertEqual(observed["LastSemanticVerdict"], "pass")
        self.assertEqual(durable_before["LastSemanticVerdict"], "repair")
        self.assertEqual(durable_after["LastSemanticVerdict"], "repair")

    def test_pr_and_ci_path_accepts_separate_disposition_gate_without_rewriting_verdict(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo, current = self._repo(Path(temp_dir))
            (current / "verification-result.json").write_text(
                json.dumps(
                    {
                        "verdict": "repair",
                        "requirements": [],
                        "findings": [
                            {"severity": "blocking", "message": "Presentation coverage is incomplete"}
                        ],
                        "repair_brief": "add coverage",
                    }
                ),
                encoding="utf-8",
            )
            semantic_disposition.accept_with_deferrals(repo, reason="owner accepts dogfood gap")
            with patch.object(workflow_stages, "pr_and_ci", return_value=True):
                code, payload = disposition_hooks._accepted_pr_and_ci(
                    repo,
                    attempt=0,
                    runner=lambda *_args, **_kwargs: None,
                )
            durable = workflow_stages.read_state(current)

        self.assertEqual(code, 0)
        self.assertEqual(payload["state"], "CONTINUE")
        self.assertEqual(
            payload["semantic_disposition"],
            semantic_disposition.ACCEPTED_WITH_DEFERRALS,
        )
        self.assertEqual(durable["LastSemanticVerdict"], "repair")


if __name__ == "__main__":
    unittest.main()
