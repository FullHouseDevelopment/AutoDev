from __future__ import annotations

from pathlib import Path
from automation import role_output_contract, run_manifest, workflow_stages

from automation.opencode_resume_contract import (
    OpenCodeResumeError,
    ROLE_NAMES,
    manifest_path,
)


def create_open_code_manifest(repo: Path, state: dict[str, object]) -> Path:
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
            semantic_verification={"enabled": True, "frontend": "opencode"},
            ux_artifact=dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
        )
        run_manifest.complete_stage(
            path,
            "issue-selected",
            run_root=current,
            artifacts=[current / "issue.md"],
            inputs={
                "github_repo": str(state.get("RepoFullName", "")),
                "issue_number": int(state.get("IssueNumber", 0) or 0),
                "base_sha": str(state.get("BaseSha", "")),
                "ux_immutable_identity": str((state.get("UXArtifact", {}) if isinstance(state.get("UXArtifact", {}), dict) else {}).get("immutable_identity", "")),
            },
            details={
                "branch": str(state.get("BranchName", "")),
                "base_tree_sha": str(state.get("BaseTreeSha", "")),
                "prepared_snapshot_hash": str(state.get("PreparedSnapshotHash", "")),
                "ux_artifact": dict(state.get("UXArtifact", {})) if isinstance(state.get("UXArtifact", {}), dict) else {},
            },
        )
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc
    return path


def role_snapshots(mappings: dict[str, dict[str, str]]) -> dict[str, object]:
    """Build the same base snapshot identity as OpenCodeRoleRuntime.

    The public/legacy status and resume paths still consume mappings directly.
    They must not compare a pre-Structured-Output snapshot shape against the
    runtime-neutral coordinator's contract-bearing snapshot shape.
    """

    snapshots: dict[str, object] = {}
    for role in ROLE_NAMES:
        mapping = mappings.get(role, {})
        model = str(mapping.get("model", ""))
        provider = model.split("/", 1)[0] if "/" in model else ""
        configured: dict[str, object] = {
            "transport": "opencode",
            "agent": str(mapping.get("agent", f"autodev-{role}")),
            "model": model,
            "source": str(mapping.get("source", "inherited")),
            "inherits_from": str(mapping.get("inherits_from", "")),
        }
        safe: dict[str, object] = {
            "transport": "opencode",
            "provider": provider,
            "profile_name": str(mapping.get("source", "inherited")),
            "model": model,
            "agent": configured["agent"],
        }
        contract = role_output_contract.contract_for_role(role)
        if contract is not None:
            metadata = contract.safe_metadata()
            configured["output_contract"] = metadata
            safe["output_contract"] = metadata
        snapshots[role] = run_manifest.build_role_snapshot(configured, safe)
    return snapshots


def _adopt_pending_snapshot(
    path: Path,
    snapshots: dict[str, object],
    role: str,
) -> None:
    if not role:
        return
    manifest = run_manifest.load_manifest(path)
    try:
        own_stage = run_manifest.invalidation_start_for_role(role)
    except run_manifest.ManifestError:
        return
    if run_manifest.stage_completed(manifest, own_stage):
        return
    snapshot = snapshots.get(role)
    if not isinstance(snapshot, dict):
        return
    roles = manifest.get("roles", {})
    if not isinstance(roles, dict):
        raise run_manifest.ManifestError("run manifest roles must be an object")
    roles[role] = snapshot
    manifest["roles"] = roles
    run_manifest.save_manifest(path, manifest)


def reconcile_models(
    repo: Path,
    mappings: dict[str, dict[str, str]],
    *,
    invalidated_roles: set[str] | None = None,
    pending_role: str = "",
) -> dict[str, list[str]]:
    path = manifest_path(repo)
    if not path.is_file():
        raise OpenCodeResumeError(".autodev-run/current/run-manifest.json is missing; this run predates OpenCode resumability")
    snapshots = role_snapshots(mappings)
    role_output_contract.bind_snapshot_set_to_existing_contexts(repo, snapshots)
    try:
        _adopt_pending_snapshot(path, snapshots, pending_role)
        return run_manifest.reconcile_role_snapshots(
            path,
            snapshots,
            explicit_invalidations=invalidated_roles or set(),
        )
    except run_manifest.ManifestError as exc:
        raise OpenCodeResumeError(str(exc)) from exc
