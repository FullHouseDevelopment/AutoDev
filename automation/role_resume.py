from __future__ import annotations

from automation import opencode_resume_status
from automation import opencode_resume_execution
from automation import opencode_resume_contract
from automation import opencode_resume_checkpoint

import subprocess
from pathlib import Path
from typing import Callable

from automation import (
    coordination_state,
    repair_lineage,
    role_output_contract,
    run_manifest,
    workflow_stages,
    ux_resolver,
    ux_workflow,
)


class RoleResumeError(ValueError):
    pass


def manifest_path(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_stages.CURRENT_DIR / run_manifest.MANIFEST_NAME


def has_manifest(repo: Path) -> bool:
    return manifest_path(repo).is_file()


def create_manifest(
    repo: Path,
    state: dict[str, object],
    *,
    runtime_name: str,
) -> Path:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if path.is_file():
        return path
    try:
        run_manifest.create_manifest(
            path,
            repo_path=repo,
            github_repo=str(state.get("RepoFullName", "")),
            issue_number=int(state.get("IssueNumber", 0) or 0),
            mode="issue-to-pr",
            base_sha=str(state.get("BaseSha", "")),
            branch=str(state.get("BranchName", "")),
            role_snapshots={},
            prompt_policy={},
            semantic_verification={"enabled": True, "frontend": runtime_name},
            ux_artifact=dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
        )
        manifest = run_manifest.load_manifest(path)
        manifest["role_runtime"] = {"name": runtime_name}
        run_manifest.save_manifest(path, manifest)
        run_manifest.complete_stage(
            path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
            inputs={
                "github_repo": str(state.get("RepoFullName", "")),
                "issue_number": int(state.get("IssueNumber", 0) or 0),
                "base_sha": str(state.get("BaseSha", "")),
                "role_runtime": runtime_name,
                "ux_immutable_identity": str((state.get("UXArtifact", {}) if isinstance(state.get("UXArtifact", {}), dict) else {}).get("immutable_identity", "")),
            },
            details={
                "branch": str(state.get("BranchName", "")),
                "base_tree_sha": str(state.get("BaseTreeSha", "")),
                "prepared_snapshot_hash": str(state.get("PreparedSnapshotHash", "")),
                "role_runtime": runtime_name,
                "ux_artifact": dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
            },
        )
    except run_manifest.ManifestError as exc:
        raise RoleResumeError(str(exc)) from exc
    return path


def begin_role(repo: Path, role: str, arguments: str) -> None:
    try:
        opencode_resume_checkpoint.begin_role(repo, role, arguments)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def _role_stage_completed(manifest: dict[str, object], role: str) -> bool:
    try:
        stage = run_manifest.invalidation_start_for_role(role)
    except run_manifest.ManifestError:
        return False
    return run_manifest.stage_completed(manifest, stage)


def _snapshot_base_fingerprint(snapshot: object) -> str:
    if not isinstance(snapshot, dict):
        return ""
    safe = snapshot.get("safe_metadata", {})
    if isinstance(safe, dict):
        base = str(safe.get("role_output_base_fingerprint", "") or "")
        if base:
            return base
    return str(snapshot.get("fingerprint", "") or "")


def _adopt_pending_role_snapshot(
    path: Path,
    snapshots: dict[str, object],
    role: str,
) -> bool:
    """Bind the current invocation identity before that role owns completed work."""

    if not role:
        return False
    manifest = run_manifest.load_manifest(path)
    if _role_stage_completed(manifest, role):
        return False
    snapshot = snapshots.get(role)
    if not isinstance(snapshot, dict):
        return False
    roles = manifest.get("roles", {})
    if not isinstance(roles, dict):
        raise run_manifest.ManifestError("run manifest roles must be an object")
    previous = roles.get(role)
    previous_fingerprint = previous.get("fingerprint") if isinstance(previous, dict) else ""
    current_fingerprint = str(snapshot.get("fingerprint", "") or "")
    if previous_fingerprint == current_fingerprint:
        return False
    roles[role] = snapshot
    manifest["roles"] = roles
    run_manifest.save_manifest(path, manifest)
    return True


def reconcile_snapshots(
    repo: Path,
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
    pending_role: str = "",
) -> dict[str, list[str]]:
    repo = repo.expanduser().resolve()
    path = manifest_path(repo)
    if not path.is_file():
        raise RoleResumeError(
            ".autodev-run/current/run-manifest.json is missing; this run cannot be resumed"
        )
    role_output_contract.bind_snapshot_set_to_existing_contexts(repo, snapshots)
    try:
        if pending_role:
            _adopt_pending_role_snapshot(path, snapshots, pending_role)
        return run_manifest.reconcile_role_snapshots(
            path,
            snapshots,
            explicit_invalidations=invalidated_roles or set(),
        )
    except run_manifest.ManifestError as exc:
        raise RoleResumeError(str(exc)) from exc


def _complete_implementer_checkpoint(
    repo: Path,
    current: Path,
    path: Path,
    proof: dict[str, object] | None = None,
) -> None:
    proof = proof or workflow_stages.source_identity(
        repo,
        current,
        workflow_stages.read_state(current),
    )
    manifest = run_manifest.load_manifest(path)
    run_manifest.complete_stage(
        path,
        "implementation-generated",
        run_root=current,
        artifacts=[current / "commit-message.txt"],
        inputs={
            "plan_output": opencode_resume_checkpoint._stage_output_hash(manifest, "plan-created"),
            "implementer_fingerprint": run_manifest.stage_role_fingerprint(manifest, "implementer"),
        },
        details=opencode_resume_checkpoint._source_details(proof),
    )
    opencode_resume_checkpoint._checkpoint_patch_applied(
        path,
        current,
        proof,
        kind="implementation",
        attempt=0,
    )


def _complete_fixer_checkpoint(
    repo: Path,
    current: Path,
    path: Path,
    *,
    runtime_name: str,
    proof: dict[str, object] | None = None,
) -> None:
    manifest = run_manifest.load_manifest(path)
    repair = opencode_resume_checkpoint._stage_record(manifest, "repair-generated")
    details = repair.get("details", {}) if isinstance(repair, dict) else {}
    kind = str(details.get("kind", "")) if isinstance(details, dict) else ""
    attempt = int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0
    if not kind:
        raise RoleResumeError(
            "fixer completion has no durable repair kind in the run manifest"
        )

    # Applying a repair, not merely binding/changing Fixer configuration, is the
    # event that invalidates verification derived from the pre-repair source.
    run_manifest.invalidate_role(
        path,
        "fixer",
        reason=f"{runtime_name} {kind} repair applied",
    )
    proof = proof or workflow_stages.source_identity(
        repo,
        current,
        workflow_stages.read_state(current),
    )
    run_manifest.complete_stage(
        path,
        "repair-generated",
        run_root=current,
        inputs={
            "fixer_fingerprint": run_manifest.stage_role_fingerprint(
                run_manifest.load_manifest(path),
                "fixer",
            ),
            "kind": kind,
            "attempt": attempt,
        },
        details={
            "kind": kind,
            "attempt": attempt,
            **opencode_resume_checkpoint._source_details(proof),
        },
    )
    opencode_resume_checkpoint._checkpoint_patch_applied(
        path,
        current,
        proof,
        kind=kind,
        attempt=attempt,
    )
    pending_details = {"attempt": attempt, "repair_kind": kind}
    if kind == "local":
        state = workflow_stages.read_state(current)
        pending_details["failure_fingerprint"] = str(
            state.get(repair_lineage.LOCAL_FAILURE_FINGERPRINT_KEY, "") or ""
        )
    run_manifest.record_stage_state(
        path,
        opencode_resume_checkpoint._stage_for_repair_kind(kind),
        status="pending",
        details=pending_details,
    )


def _accepted_pending_role(
    repo: Path,
    manifest: dict[str, object],
    role: str,
) -> dict[str, object]:
    if _role_stage_completed(manifest, role):
        return {}
    acceptance = coordination_state.role_acceptance(repo, role)
    return acceptance if acceptance.get("state") == "ACCEPTED" else {}


def _accepted_in_progress_fixer(repo: Path, manifest: dict[str, object]) -> dict[str, object]:
    if run_manifest.stage_completed(manifest, "repair-generated"):
        return {}
    repair = opencode_resume_checkpoint._stage_record(manifest, "repair-generated")
    if str(repair.get("status", "")) != "in-progress":
        return {}
    acceptance = coordination_state.role_acceptance(repo, "fixer")
    return acceptance if acceptance.get("state") == "ACCEPTED" else {}


def _assert_pending_accepted_role_base_identity(
    path: Path,
    manifest: dict[str, object],
    snapshots: dict[str, object],
    role: str,
    acceptance: dict[str, object],
) -> None:
    if not acceptance:
        return
    roles = manifest.get("roles", {})
    previous = roles.get(role) if isinstance(roles, dict) else None
    current = snapshots.get(role)
    previous_base = _snapshot_base_fingerprint(previous)
    current_base = _snapshot_base_fingerprint(current)
    if previous_base and current_base and previous_base != current_base:
        raise RoleResumeError(
            f"accepted {role} work was interrupted before checkpointing, but the effective {role} configuration has since changed"
        )
    _adopt_pending_role_snapshot(path, snapshots, role)


def _prepare_pending_source_role_snapshots_for_resume(
    repo: Path,
    path: Path,
    manifest: dict[str, object],
    snapshots: dict[str, object],
) -> None:
    implementer = _accepted_pending_role(repo, manifest, "implementer")
    _assert_pending_accepted_role_base_identity(
        path,
        manifest,
        snapshots,
        "implementer",
        implementer,
    )

    fixer = _accepted_in_progress_fixer(repo, manifest)
    if fixer:
        _assert_pending_accepted_role_base_identity(
            path,
            manifest,
            snapshots,
            "fixer",
            fixer,
        )
    else:
        repair = opencode_resume_checkpoint._stage_record(manifest, "repair-generated")
        if str(repair.get("status", "")) == "in-progress":
            # No accepted Fixer result exists. It is safe to bind the configuration
            # that a future retry would use, but source drift remains fail-closed.
            _adopt_pending_role_snapshot(path, snapshots, "fixer")


def _current_source_proof(
    repo: Path,
    current: Path,
) -> dict[str, object]:
    return workflow_stages.source_identity(
        repo,
        current,
        workflow_stages.read_state(current),
    )


def _acceptance_matches_source(
    acceptance: dict[str, object],
    proof: dict[str, object],
) -> bool:
    expected = str(acceptance.get("source_identity", "") or "")
    if not expected or expected != str(proof.get("identity", "") or ""):
        return False
    expected_parent = str(acceptance.get("source_parent_sha", "") or "")
    return not expected_parent or expected_parent == str(proof.get("parent_sha", "") or "")


def _legacy_339_fixer_recovery_allowed(
    manifest: dict[str, object],
    acceptance: dict[str, object],
) -> bool:
    if str(acceptance.get("source_identity", "") or ""):
        return False
    failure = manifest.get("failure", {})
    if not isinstance(failure, dict):
        return False
    reason = str(failure.get("reason", "") or "")
    classification = str(failure.get("classification", "") or "")
    return (
        classification == workflow_stages.FAILURE_DETERMINISTIC
        and "execution-affecting role configuration changed for completed work" in reason
        and "fixer -> deterministic-verified" in reason
        and run_manifest.stage_completed(manifest, "deterministic-verified")
    )


def _recover_interrupted_implementer_checkpoint(
    repo: Path,
    current: Path,
    path: Path,
    manifest: dict[str, object],
) -> bool:
    acceptance = _accepted_pending_role(repo, manifest, "implementer")
    if not acceptance or not str(acceptance.get("source_identity", "") or ""):
        return False
    proof = _current_source_proof(repo, current)
    if not _acceptance_matches_source(acceptance, proof):
        return False
    _complete_implementer_checkpoint(repo, current, path, proof)
    return True


def _recover_interrupted_fixer_checkpoint(
    repo: Path,
    current: Path,
    path: Path,
    manifest: dict[str, object],
) -> bool:
    acceptance = _accepted_in_progress_fixer(repo, manifest)
    if not acceptance:
        return False
    proof = _current_source_proof(repo, current)
    if str(acceptance.get("source_identity", "") or ""):
        if not _acceptance_matches_source(acceptance, proof):
            return False
    elif not _legacy_339_fixer_recovery_allowed(manifest, acceptance):
        return False
    _complete_fixer_checkpoint(
        repo,
        current,
        path,
        runtime_name="resume-recovery",
        proof=proof,
    )
    return True


def checkpoint_role(
    repo: Path,
    role: str,
    outputs: list[Path],
    snapshots: dict[str, object],
    *,
    runtime_name: str,
) -> None:
    repo = repo.expanduser().resolve()
    path = manifest_path(repo)
    if not path.is_file():
        return
    current = repo / workflow_stages.CURRENT_DIR
    reconcile_snapshots(repo, snapshots, pending_role=role)
    manifest = run_manifest.load_manifest(path)
    try:
        if role == "reader":
            artifacts = opencode_resume_checkpoint._existing(
                current,
                "reader-brief.md",
                "routed-areas.json",
                "detected-facts.json",
                "recommended-command-groups.json",
                "verification-command-groups.json",
            )
            run_manifest.complete_stage(
                path,
                "repository-read",
                run_root=current,
                artifacts=artifacts,
                inputs={
                    "issue_sha256": run_manifest.hash_file(current / "issue.md"),
                    "reader_fingerprint": run_manifest.stage_role_fingerprint(manifest, "reader"),
                },
                details={
                    "refreshable_artifacts": [
                        "detected-facts.json",
                        "verification-command-groups.json",
                        "recommended-command-groups.json",
                    ]
                },
            )
            return
        if role == "synthesizer":
            run_manifest.complete_stage(
                path,
                "handoff-synthesized",
                run_root=current,
                artifacts=[current / "synthesized-handoff.md"],
                inputs={
                    "repository_read_output": opencode_resume_checkpoint._stage_output_hash(manifest, "repository-read"),
                    "synthesizer_fingerprint": run_manifest.stage_role_fingerprint(manifest, "synthesizer"),
                },
            )
            return
        if role == "planner":
            run_manifest.complete_stage(
                path,
                "plan-created",
                run_root=current,
                artifacts=[current / "plan.md"],
                inputs={
                    "handoff_output": opencode_resume_checkpoint._stage_output_hash(manifest, "handoff-synthesized"),
                    "planner_fingerprint": run_manifest.stage_role_fingerprint(manifest, "planner"),
                },
            )
            return
        if role == "implementer":
            _complete_implementer_checkpoint(repo, current, path)
            return
        if role == "fixer":
            _complete_fixer_checkpoint(
                repo,
                current,
                path,
                runtime_name=runtime_name,
            )
            return
        if role == "verifier":
            return
    except (run_manifest.ManifestError, workflow_stages.WorkflowStageError) as exc:
        raise RoleResumeError(str(exc)) from exc


def checkpoint_stage(repo: Path, name: str, payload: dict[str, object], attempt: int) -> None:
    try:
        opencode_resume_checkpoint.checkpoint_stage(repo, name, payload, attempt)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def checkpoint_failure(repo: Path, stage: str, error: BaseException) -> None:
    try:
        opencode_resume_checkpoint.checkpoint_failure(repo, stage, error)
    except opencode_resume_contract.OpenCodeResumeError as exc:
        raise RoleResumeError(str(exc)) from exc


def resume(
    repo: Path,
    snapshots: dict[str, object],
    *,
    invalidated_roles: set[str] | None = None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    path = manifest_path(repo)
    if not path.is_file():
        raise RoleResumeError(
            ".autodev-run/current/run-manifest.json is missing; this run cannot be resumed"
        )
    role_output_contract.bind_snapshot_set_to_existing_contexts(repo, snapshots)
    try:
        manifest = run_manifest.load_manifest(path)
        try:
            ux_workflow.validate_resume_identity(
                repo,
                manifest.get("ux_artifact", {}),
            )
        except ux_resolver.UXResolutionError as exc:
            raise RoleResumeError(str(exc)) from exc
        invalidated_roles = invalidated_roles or set()
        for role in invalidated_roles:
            affected = run_manifest.invalidated_stages_for_role(manifest, role)
            if "patch-applied" in affected:
                state = workflow_stages.read_state(current)
                if workflow_stages.workspace_changes(repo, current, state):
                    raise RoleResumeError(
                        f"cannot invalidate completed {role} work while direct runtime edits remain in the worktree; restore the prepared base first"
                    )

        _prepare_pending_source_role_snapshots_for_resume(
            repo,
            path,
            manifest,
            snapshots,
        )
        run_manifest.reconcile_role_snapshots(
            path,
            snapshots,
            explicit_invalidations=invalidated_roles,
        )
        manifest = run_manifest.load_manifest(path)
        state = workflow_stages.read_state(current)

        # Recover source-editing roles only when their fresh acceptance marker is
        # bound to the exact current source identity. Historical #339 Fixer runs
        # get one narrow compatibility path keyed to the known terminal signature.
        if _recover_interrupted_implementer_checkpoint(repo, current, path, manifest):
            manifest = run_manifest.load_manifest(path)
            state = workflow_stages.read_state(current)

        opencode_resume_execution._repair_atomic_implementation_checkpoint(
            repo,
            current,
            path,
            manifest,
            state,
        )
        manifest = run_manifest.load_manifest(path)

        if _recover_interrupted_fixer_checkpoint(repo, current, path, manifest):
            manifest = run_manifest.load_manifest(path)
            state = workflow_stages.read_state(current)

        problems = opencode_resume_status._resume_problems(
            repo,
            current,
            manifest,
            state,
            runner=runner,
            validate_remote=True,
        )
        if problems:
            raise RoleResumeError("resume refused: " + "; ".join(problems))
    except (run_manifest.ManifestError, workflow_stages.WorkflowStageError) as exc:
        raise RoleResumeError(str(exc)) from exc

    action = opencode_resume_status.resume_action(manifest, state)
    role = opencode_resume_status._role_for_action(action)
    attempts = opencode_resume_status.repair_attempts(manifest)
    target = manifest.get("target", {}) if isinstance(manifest.get("target", {}), dict) else {}
    snapshot = snapshots.get(role, {}) if role else {}
    safe = snapshot.get("safe_metadata", {}) if isinstance(snapshot, dict) else {}
    safe = safe if isinstance(safe, dict) else {}
    return {
        "state": "COMPLETE" if action == "complete" else "RESUME",
        "issue_number": int(target.get("issue_number", state.get("IssueNumber", 0)) or 0),
        "branch": str(target.get("branch", state.get("BranchName", ""))),
        "run_id": str(manifest.get("run_id", "")),
        "run_dir": str(current),
        "next_stage": run_manifest.next_stage(manifest),
        "next_action": action,
        "next_role": role,
        "next_model": str(safe.get("model", "")) if role else "",
        "runtime": str(safe.get("runtime", safe.get("transport", ""))) if role else "",
        "local_repair_attempt": attempts["local"],
        "semantic_repair_attempt": attempts["semantic"],
        "ci_repair_attempt": attempts["ci"],
        "commit_sha": str(state.get("LastCommitSha", "")),
        "pr_url": str(state.get("PrUrl", "")),
    }
