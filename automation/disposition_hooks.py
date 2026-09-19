from __future__ import annotations

from pathlib import Path

from automation import (
    disposition_transition,
    opencode_resume_status,
    semantic_disposition,
    workflow_dispatch,
    workflow_stages,
)


_INSTALLED = False


def _accepted_pr_and_ci(
    repo: Path,
    *,
    attempt: int,
    runner,
) -> tuple[int, dict[str, object]]:
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    if not bool(state.get("LastLocalCheckPassed")):
        raise workflow_stages.WorkflowStageError(
            "pr-and-ci prerequisite not met: deterministic local verification has not passed"
        )
    if not semantic_disposition.accepted_for_current_result(repo, state):
        raise workflow_stages.WorkflowStageError(
            "pr-and-ci prerequisite not met: semantic verification has neither passed nor received a current accepted disposition"
        )

    max_attempts = workflow_stages.configured_attempt_limit(
        "MAX_REPAIR_ATTEMPTS",
        workflow_stages.DEFAULT_MAX_REPAIR_ATTEMPTS,
    )
    ci_passed = workflow_stages.pr_and_ci(
        repo,
        current,
        state,
        workflow_stages.AUTODEV_ROOT,
        runner=runner,
    )
    if ci_passed:
        return 0, workflow_stages.stage_payload(
            repo,
            "CONTINUE",
            "pr-and-ci",
            semantic_disposition=semantic_disposition.ACCEPTED_WITH_DEFERRALS,
            next_action="mark the PR ready for human review with semantic deferrals visible",
            max_repair_attempts=max_attempts,
        )
    if attempt >= max_attempts:
        return 0, workflow_stages.stage_payload(
            repo,
            "BLOCKED",
            "pr-and-ci",
            reason="CI repair-attempt limit exhausted",
            artifact=current / "ci-repair.md",
            failure_classification=workflow_stages.FAILURE_DETERMINISTIC,
            next_action="mark the run blocked",
            max_repair_attempts=max_attempts,
        )
    return 0, workflow_stages.stage_payload(
        repo,
        "REPAIR",
        "pr-and-ci",
        reason="required PR checks failed",
        artifact=current / "ci-repair.md",
        failure_classification=workflow_stages.FAILURE_CODE_REPAIRABLE,
        next_action="delegate the CI repair to autodev-fixer, increment the attempt, rerun local-check and semantic verification, then retry pr-and-ci",
        max_repair_attempts=max_attempts,
    )


def _decorate_semantic_outcome(
    repo: Path,
    code: int,
    payload: dict[str, object],
) -> tuple[int, dict[str, object]]:
    try:
        disposition = semantic_disposition.evaluate(repo)
    except (semantic_disposition.SemanticDispositionError, workflow_stages.WorkflowStageError):
        return code, payload

    state = str(disposition.get("state", "") or "")
    result = dict(payload)
    result["semantic_disposition"] = state
    result["semantic_disposition_artifact"] = str(
        repo / workflow_stages.CURRENT_DIR / semantic_disposition.ARTIFACT_NAME
    )

    policy = disposition.get("policy", {})
    configured = bool(policy.get("configured")) if isinstance(policy, dict) else False
    if state == semantic_disposition.ACCEPTED_WITH_DEFERRALS and configured:
        # Only policy-deferrable entries exist; no human override is being
        # invented here. Persist the exact verifier/policy identity and continue.
        accepted = disposition_transition.accept(repo)
        result.update(
            {
                "state": "CONTINUE",
                "reason": "semantic findings accepted under lifecycle-aware deferral policy",
                "semantic_disposition": accepted.get("state", state),
                "next_action": "run commit/push/PR/CI with deferred semantic findings preserved",
            }
        )
        return 0, result
    return code, result


def _ready_proof_wrapper(original):
    def validate_ready_proof(current: Path, state: dict[str, object], *, runner):
        if semantic_disposition.accepted_for_current_result(current.parents[1], state):
            effective = dict(state)
            # The underlying proof function uses this value solely as the old
            # semantic shipment gate. Pass a view, never mutate durable state.
            effective["LastSemanticVerdict"] = "pass"
            return original(current, effective, runner=runner)
        return original(current, state, runner=runner)

    validate_ready_proof._autodev_semantic_disposition = True  # type: ignore[attr-defined]
    return validate_ready_proof


def _status_with_disposition(original, repo: Path, *args, **kwargs) -> str:
    text = original(repo, *args, **kwargs).rstrip("\n")
    return text + "\n" + semantic_disposition.status_line(Path(repo)) + "\n"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    current_execute = workflow_stages.execute_stage
    if not getattr(current_execute, "_autodev_semantic_disposition", False):
        original_execute = current_execute

        def execute_stage(
            name: str,
            repo: Path,
            *,
            arguments: str = "",
            semver_intent_override: str = "",
            autodev_root: Path = workflow_stages.AUTODEV_ROOT,
            attempt: int = 0,
            reason: str = "",
            runner=workflow_stages.subprocess.run,
            which=workflow_stages.shutil.which,
        ):
            resolved = Path(repo).expanduser().resolve()
            if name == "pr-and-ci":
                state = workflow_stages.read_state(resolved / workflow_stages.CURRENT_DIR)
                if semantic_disposition.accepted_for_current_result(resolved, state):
                    return _accepted_pr_and_ci(
                        resolved,
                        attempt=attempt,
                        runner=runner,
                    )
            code, payload = original_execute(
                name,
                resolved,
                arguments=arguments,
                semver_intent_override=semver_intent_override,
                autodev_root=autodev_root,
                attempt=attempt,
                reason=reason,
                runner=runner,
                which=which,
            )
            if name == "semantic":
                return _decorate_semantic_outcome(resolved, code, payload)
            return code, payload

        execute_stage._autodev_semantic_disposition = True  # type: ignore[attr-defined]
        workflow_stages.execute_stage = execute_stage

    # Ready proof is called through imported bindings in both modules. Compose
    # with whatever CI-outcome guard is effective at installation time.
    for module in (workflow_stages, workflow_dispatch):
        current_validate = module.validate_ready_proof
        if getattr(current_validate, "_autodev_semantic_disposition", False):
            continue
        module.validate_ready_proof = _ready_proof_wrapper(current_validate)

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_semantic_disposition", False):
        original_status = current_status

        def status_text(repo: Path, *args, **kwargs) -> str:
            return _status_with_disposition(original_status, repo, *args, **kwargs)

        status_text._autodev_semantic_disposition = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text

    _INSTALLED = True
