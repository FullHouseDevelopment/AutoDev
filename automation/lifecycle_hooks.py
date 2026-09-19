from __future__ import annotations

from pathlib import Path
from typing import Callable

from automation import (
    lifecycle_policy,
    opencode_resume_execution,
    opencode_resume_status,
    role_resume,
    run_manifest,
    workflow_contract,
    workflow_dispatch,
    workflow_stages,
)


_INSTALLED = False


def _state_has_delivery_policy(state: dict[str, object]) -> bool:
    return any(
        key in state
        for key in (
            "DeliveryPolicyMode",
            "LifecyclePolicyConfigured",
            "ProductLifecycle",
            "WorkExposure",
            "LifecyclePolicyFingerprint",
        )
    )


def _requested_issue(arguments: str) -> int:
    try:
        return workflow_contract.issue_number_from_arguments(arguments)
    except Exception:
        return 0


def _same_prepared_run(
    before: dict[str, object],
    after: dict[str, object],
    requested_issue: int,
) -> bool:
    if not before or not after:
        return False
    before_issue = int(before.get("IssueNumber", 0) or 0)
    after_issue = int(after.get("IssueNumber", 0) or 0)
    if requested_issue and (before_issue != requested_issue or after_issue != requested_issue):
        return False
    before_created = str(before.get("CreatedAt", "") or "")
    after_created = str(after.get("CreatedAt", "") or "")
    return bool(before_created and before_created == after_created)


def _prepare_with_policy(
    original: Callable[..., Path],
    repo: Path,
    arguments: str,
    **kwargs,
) -> Path:
    resolved = Path(repo).expanduser().resolve()
    effective = lifecycle_policy.load_lifecycle_policy(resolved)
    current = resolved / workflow_stages.CURRENT_DIR
    before = workflow_stages.read_json(current / "state.json") if current.is_dir() else {}
    before_state = before if isinstance(before, dict) else {}
    requested_issue = _requested_issue(arguments)

    if (
        before_state
        and requested_issue
        and int(before_state.get("IssueNumber", 0) or 0) == requested_issue
        and _state_has_delivery_policy(before_state)
    ):
        lifecycle_policy.assert_resume_compatible(resolved, before_state)

    prepared = original(resolved, arguments, **kwargs)
    state = workflow_stages.read_state(prepared)

    if _same_prepared_run(before_state, state, requested_issue):
        lifecycle_policy.assert_resume_compatible(resolved, state)
        return prepared

    if _state_has_delivery_policy(state):
        lifecycle_policy.assert_resume_compatible(resolved, state)
        return prepared

    state.update(lifecycle_policy.state_fields(effective))
    workflow_stages.write_state(prepared, state)
    return prepared


def _manifest_with_policy(
    original: Callable[..., Path],
    repo: Path,
    state: dict[str, object],
    **kwargs,
) -> Path:
    resolved = Path(repo).expanduser().resolve()
    path = role_resume.manifest_path(resolved)
    existed = path.is_file()
    result = original(resolved, state, **kwargs)
    manifest = run_manifest.load_manifest(result)
    if existed:
        lifecycle_policy.assert_manifest_compatible(manifest, state)
        return result
    manifest["delivery_policy"] = lifecycle_policy.evidence_from_state(state)
    run_manifest.save_manifest(result, manifest)
    return result


def _assert_resume_policy(repo: Path) -> dict[str, object]:
    resolved = Path(repo).expanduser().resolve()
    current = resolved / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    lifecycle_policy.assert_resume_compatible(resolved, state)
    path = current / run_manifest.MANIFEST_NAME
    if path.is_file():
        manifest = run_manifest.load_manifest(path)
        lifecycle_policy.assert_manifest_compatible(manifest, state)
    return state


def _resume_with_policy(
    original: Callable[..., dict[str, object]],
    repo: Path,
    *args,
    **kwargs,
) -> dict[str, object]:
    state = _assert_resume_policy(repo)
    payload = original(repo, *args, **kwargs)
    result = dict(payload)
    result["delivery_policy"] = lifecycle_policy.evidence_from_state(state)
    return result


def _status_with_policy(
    original: Callable[..., str],
    repo: Path,
    *args,
    **kwargs,
) -> str:
    text = original(repo, *args, **kwargs).rstrip("\n")
    resolved = Path(repo).expanduser().resolve()
    current = resolved / workflow_stages.CURRENT_DIR
    try:
        state = workflow_stages.read_state(current)
        line = lifecycle_policy.status_line(state)
        effective = lifecycle_policy.load_lifecycle_policy(resolved)
        prepared = lifecycle_policy.policy_from_state(state)
        if effective.fingerprint != prepared.fingerprint:
            line += " | POLICY CHANGED: resume requires re-evaluation"
    except Exception as exc:
        line = f"Delivery policy: invalid ({exc})"
    return text + "\n" + line + "\n"


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    current_prepare = workflow_dispatch.ensure_prepared_issue
    if not getattr(current_prepare, "_autodev_lifecycle_policy", False):
        original_prepare = current_prepare

        def ensure_prepared_issue(repo: Path, arguments: str, **kwargs) -> Path:
            return _prepare_with_policy(original_prepare, repo, arguments, **kwargs)

        ensure_prepared_issue._autodev_lifecycle_policy = True  # type: ignore[attr-defined]
        workflow_dispatch.ensure_prepared_issue = ensure_prepared_issue

    current_create = role_resume.create_manifest
    if not getattr(current_create, "_autodev_lifecycle_policy", False):
        original_create = current_create

        def create_manifest(repo: Path, state: dict[str, object], **kwargs) -> Path:
            return _manifest_with_policy(original_create, repo, state, **kwargs)

        create_manifest._autodev_lifecycle_policy = True  # type: ignore[attr-defined]
        role_resume.create_manifest = create_manifest

    current_role_resume = role_resume.resume
    if not getattr(current_role_resume, "_autodev_lifecycle_policy", False):
        original_role_resume = current_role_resume

        def resume(repo: Path, *args, **kwargs) -> dict[str, object]:
            return _resume_with_policy(original_role_resume, repo, *args, **kwargs)

        resume._autodev_lifecycle_policy = True  # type: ignore[attr-defined]
        role_resume.resume = resume

    current_opencode_resume = opencode_resume_execution.resume
    if not getattr(current_opencode_resume, "_autodev_lifecycle_policy", False):
        original_opencode_resume = current_opencode_resume

        def opencode_resume(repo: Path, *args, **kwargs) -> dict[str, object]:
            return _resume_with_policy(original_opencode_resume, repo, *args, **kwargs)

        opencode_resume._autodev_lifecycle_policy = True  # type: ignore[attr-defined]
        opencode_resume_execution.resume = opencode_resume

    current_status = opencode_resume_status.status_text
    if not getattr(current_status, "_autodev_lifecycle_policy", False):
        original_status = current_status

        def status_text(repo: Path, *args, **kwargs) -> str:
            return _status_with_policy(original_status, repo, *args, **kwargs)

        status_text._autodev_lifecycle_policy = True  # type: ignore[attr-defined]
        opencode_resume_status.status_text = status_text

    _INSTALLED = True
