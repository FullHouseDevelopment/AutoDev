from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from automation import continuation, run_manifest, workflow_contract, workflow_storage, workflow_workspace
from automation.workflow_commands import git


PENDING_FILE = "continuation-pending.json"
LEGACY_SOURCE_PARENT_MIGRATION = "legacy-policy-base-to-continuation-parent-v2"


def _current(repo: Path) -> Path:
    return repo.expanduser().resolve() / workflow_contract.CURRENT_DIR


def _pending_path(repo: Path) -> Path:
    return _current(repo) / PENDING_FILE


def _load_pending(repo: Path) -> dict[str, object]:
    value = workflow_storage.read_json(_pending_path(repo))
    return value if isinstance(value, dict) else {}


def _head(repo: Path, *, runner: Callable[..., object]) -> str:
    completed = git(repo, ["rev-parse", "HEAD"], runner=runner)
    return str(getattr(completed, "stdout", "") or "").strip()


def _stage_record(manifest: dict[str, object], stage: str) -> dict[str, object]:
    stages = manifest.get("stages", {})
    record = stages.get(stage, {}) if isinstance(stages, dict) else {}
    return record if isinstance(record, dict) else {}


def _source_paths(proof: dict[str, object]) -> list[str]:
    changes = proof.get("changes", [])
    if not isinstance(changes, list):
        return []
    return [
        str(item.get("path", ""))
        for item in changes
        if isinstance(item, dict) and str(item.get("path", ""))
    ]


def _rebind_source_record(
    manifest: dict[str, object],
    stage: str,
    *,
    legacy_identity: str,
    legacy_parent: str,
    current_identity: str,
    current_parent: str,
    changed_paths: list[str],
) -> None:
    record = _stage_record(manifest, stage)
    if not record:
        return
    details = record.get("details", {})
    if not isinstance(details, dict):
        return
    if (
        str(details.get("source_identity", "")).strip() != legacy_identity
        or str(details.get("parent_sha", "")).strip() != legacy_parent
    ):
        return
    rebound = dict(details)
    rebound.update(
        {
            "source_identity": current_identity,
            "parent_sha": current_parent,
            "changed_paths": list(changed_paths),
            "legacy_source_identity": legacy_identity,
            "legacy_parent_sha": legacy_parent,
            "source_identity_migration": LEGACY_SOURCE_PARENT_MIGRATION,
        }
    )
    record["details"] = rebound


def _migrate_legacy_patch_checkpoint(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> bool:
    """Safely rebind a pre-#347 continuation checkpoint to the adopted parent.

    The compatibility path is deliberately narrower than normal source validation.
    It is available only while the run is still on its immutable continuation
    source and before AutoDev has created a successor commit.  The current bytes
    must reproduce the exact legacy identity (including changed-file digests)
    using the policy base as parent before any durable proof is rewritten.
    """

    repo = repo.expanduser().resolve()
    current = _current(repo)
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if not state or str(state.get("LastCommitSha", "")).strip():
        return False

    base_sha = str(state.get("BaseSha", "")).strip()
    requested_ref = str(state.get("ContinuationRequestedRef", "")).strip()
    continuation_sha = str(state.get("ContinuationResolvedSha", "")).strip()
    prepared_head = str(state.get("PreparedLocalHeadSha", "")).strip()
    if not base_sha or not requested_ref or not continuation_sha:
        return False
    if prepared_head != continuation_sha:
        return False
    if int(state.get("ContinuationSourceVersion", 0) or 0) != continuation.SCHEMA_VERSION:
        return False

    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return False
    try:
        manifest = run_manifest.load_manifest(manifest_path)
    except run_manifest.ManifestError:
        return False

    continuation_source = manifest.get("continuation_source", {})
    target = manifest.get("target", {})
    if not isinstance(continuation_source, dict) or not isinstance(target, dict):
        return False
    if (
        str(continuation_source.get("requested_ref", "")).strip() != requested_ref
        or str(continuation_source.get("resolved_sha", "")).strip() != continuation_sha
        or int(continuation_source.get("schema_version", 0) or 0) != continuation.SCHEMA_VERSION
        or str(target.get("base_sha", "")).strip() != base_sha
    ):
        return False
    if not run_manifest.stage_completed(manifest, "patch-applied"):
        return False
    if _head(repo, runner=runner) != continuation_sha:
        return False

    patch_record = _stage_record(manifest, "patch-applied")
    details = patch_record.get("details", {}) if patch_record else {}
    if not isinstance(details, dict):
        return False
    stored_identity = str(details.get("source_identity", "")).strip()
    stored_parent = str(details.get("parent_sha", "")).strip()
    stored_paths = details.get("changed_paths", [])
    if (
        not stored_identity
        or stored_parent != base_sha
        or not isinstance(stored_paths, list)
        or any(not isinstance(path, str) for path in stored_paths)
    ):
        return False

    try:
        current_proof = workflow_workspace.source_identity(repo, current, state)
    except workflow_contract.WorkflowStageError:
        return False
    current_identity = str(current_proof.get("identity", "")).strip()
    current_parent = str(current_proof.get("parent_sha", "")).strip()
    current_paths = _source_paths(current_proof)
    if current_parent != continuation_sha or not current_identity:
        return False
    if current_identity == stored_identity:
        # A checkpoint already written with current semantics is not legacy.
        return False
    if list(stored_paths) != current_paths:
        return False

    legacy_state = dict(state)
    legacy_state["ContinuationResolvedSha"] = ""
    legacy_state["ContinuationRequestedRef"] = ""
    try:
        legacy_proof = workflow_workspace.source_identity(repo, current, legacy_state)
    except workflow_contract.WorkflowStageError:
        return False
    if (
        str(legacy_proof.get("parent_sha", "")).strip() != base_sha
        or str(legacy_proof.get("identity", "")).strip() != stored_identity
        or legacy_proof.get("changes", []) != current_proof.get("changes", [])
    ):
        return False

    # The exact legacy hash reproduced from the current changed paths and file
    # digests, and HEAD is still the immutable adopted source.  Rebind only the
    # source proof; policy BaseSha remains untouched.
    migrated_at = datetime.now(timezone.utc).isoformat()
    rebound_details = dict(details)
    rebound_details.update(
        {
            "source_identity": current_identity,
            "parent_sha": current_parent,
            "changed_paths": current_paths,
            "legacy_source_identity": stored_identity,
            "legacy_parent_sha": base_sha,
            "source_identity_migration": LEGACY_SOURCE_PARENT_MIGRATION,
            "source_identity_migrated_at": migrated_at,
        }
    )
    patch_record["details"] = rebound_details
    patch_record["input_hash"] = run_manifest.hash_json(
        {"source_identity": current_identity}
    )

    kind = str(details.get("kind", "")).strip()
    if kind == "implementation":
        _rebind_source_record(
            manifest,
            "implementation-generated",
            legacy_identity=stored_identity,
            legacy_parent=base_sha,
            current_identity=current_identity,
            current_parent=current_parent,
            changed_paths=current_paths,
        )
    elif kind:
        _rebind_source_record(
            manifest,
            "repair-generated",
            legacy_identity=stored_identity,
            legacy_parent=base_sha,
            current_identity=current_identity,
            current_parent=current_parent,
            changed_paths=current_paths,
        )

    migrations = manifest.setdefault("compatibility_migrations", [])
    if isinstance(migrations, list):
        migrations.append(
            {
                "kind": LEGACY_SOURCE_PARENT_MIGRATION,
                "stage": "patch-applied",
                "migrated_at": migrated_at,
                "legacy_parent_sha": base_sha,
                "current_parent_sha": current_parent,
                "legacy_source_identity": stored_identity,
                "current_source_identity": current_identity,
                "changed_paths": current_paths,
            }
        )
    run_manifest.save_manifest(manifest_path, manifest)
    return True


def _validate_before_pending(
    repo: Path,
    resolved_sha: str,
    *,
    runner: Callable[..., object],
) -> None:
    current = _current(repo)
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if not state or not (current / run_manifest.MANIFEST_NAME).is_file():
        raise continuation.ContinuationError(
            "--continue-from on resume requires an existing durable AutoDev run"
        )
    base_sha = str(state.get("BaseSha", "")).strip()
    if not base_sha:
        raise continuation.ContinuationError(
            "current run is missing its configured development-base SHA"
        )
    continuation._require_policy_ancestry(
        repo,
        base_sha,
        resolved_sha,
        runner=runner,
    )
    dirty = continuation._dirty_paths(repo, runner=runner)
    if dirty:
        raise continuation.ContinuationError(
            "cannot adopt continuation source with a dirty worktree; commit/stash the current changes first: "
            + ", ".join(dirty[:20])
        )


def _persist_requested_identity(
    repo: Path,
    *,
    requested_ref: str,
    resolved_sha: str,
) -> None:
    current = _current(repo)
    state_path = current / "state.json"
    state_value = workflow_storage.read_json(state_path)
    state = state_value if isinstance(state_value, dict) else {}
    state["ContinuationRequestedRef"] = requested_ref
    state["ContinuationResolvedSha"] = resolved_sha
    workflow_storage.write_json(state_path, state)

    manifest_path = current / run_manifest.MANIFEST_NAME
    manifest = run_manifest.load_manifest(manifest_path)
    record = manifest.get("continuation_source", {})
    if not isinstance(record, dict):
        record = {}
    record["schema_version"] = continuation.SCHEMA_VERSION
    record["requested_ref"] = requested_ref
    record["resolved_sha"] = resolved_sha
    manifest["continuation_source"] = record
    run_manifest.save_manifest(manifest_path, manifest)


def _already_adopted(
    repo: Path,
    resolved_sha: str,
    *,
    runner: Callable[..., object],
) -> bool:
    current = _current(repo)
    state_value = workflow_storage.read_json(current / "state.json")
    state = state_value if isinstance(state_value, dict) else {}
    if (
        str(state.get("ContinuationResolvedSha", "")).strip() != resolved_sha
        or _head(repo, runner=runner) != resolved_sha
    ):
        return False

    try:
        manifest = run_manifest.load_manifest(current / run_manifest.MANIFEST_NAME)
    except run_manifest.ManifestError:
        return False
    record = manifest.get("continuation_source", {})
    target = manifest.get("target", {})
    if not isinstance(record, dict) or not isinstance(target, dict):
        return False
    return (
        str(record.get("resolved_sha", "")).strip() == resolved_sha
        and str(target.get("branch", "")).strip()
        == str(state.get("BranchName", "")).strip()
    )


def adopt(
    repo: Path,
    requested_ref: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    """Begin an existing-run continuation transaction and finish it synchronously.

    The pending record is persisted before the checkout/adoption mutation. If the
    process disappears afterward, an ordinary public resume can call
    ``finish_pending`` and deterministically complete the same immutable SHA.
    """

    repo = repo.expanduser().resolve()
    pending = _load_pending(repo)
    if pending:
        existing_requested = str(pending.get("requested_ref", "")).strip()
        existing_sha = str(pending.get("resolved_sha", "")).strip()
        raise continuation.ContinuationError(
            "a continuation adoption is already pending"
            + (
                f" for {existing_requested!r} -> {existing_sha}; run `autodev resume` without a new --continue-from to finish it"
                if existing_requested or existing_sha
                else "; run `autodev resume` without a new --continue-from to finish it"
            )
        )

    resolved = continuation.resolve_ref(repo, requested_ref, runner=runner)
    _validate_before_pending(repo, resolved, runner=runner)
    record: dict[str, object] = {
        "schema_version": continuation.SCHEMA_VERSION,
        "requested_ref": requested_ref,
        "resolved_sha": resolved,
        "prepared_at": datetime.now(timezone.utc).isoformat(),
    }
    workflow_storage.write_json(_pending_path(repo), record)
    return finish_pending(repo, runner=runner)


def finish_pending(
    repo: Path,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    pending = _load_pending(repo)
    if not pending:
        _migrate_legacy_patch_checkpoint(repo, runner=runner)
        return {}
    if int(pending.get("schema_version", 0) or 0) != continuation.SCHEMA_VERSION:
        raise continuation.ContinuationError(
            f"unsupported pending continuation schema {pending.get('schema_version')}"
        )
    requested = str(pending.get("requested_ref", "")).strip()
    resolved = str(pending.get("resolved_sha", "")).strip()
    if not requested or not resolved:
        raise continuation.ContinuationError(
            "pending continuation record is missing requested_ref/resolved_sha"
        )

    if not _already_adopted(repo, resolved, runner=runner):
        # Pass the immutable SHA to the core adopter so a moved branch/tag can
        # never change the transaction after the pending boundary was written.
        continuation.adopt_existing_run(repo, resolved, runner=runner)

    _persist_requested_identity(
        repo,
        requested_ref=requested,
        resolved_sha=resolved,
    )
    _pending_path(repo).unlink(missing_ok=True)
    return {
        "schema_version": continuation.SCHEMA_VERSION,
        "requested_ref": requested,
        "resolved_sha": resolved,
    }
