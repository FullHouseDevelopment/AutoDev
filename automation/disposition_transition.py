from __future__ import annotations

from pathlib import Path

from automation import run_manifest, semantic_disposition, workflow_stages


def accept(repo: Path, *, reason: str = "") -> dict[str, object]:
    repo = repo.expanduser().resolve()
    disposition = semantic_disposition.accept_with_deferrals(repo, reason=reason)
    if disposition.get("state") != semantic_disposition.ACCEPTED_WITH_DEFERRALS:
        return disposition

    current = repo / workflow_stages.CURRENT_DIR
    manifest_path = current / run_manifest.MANIFEST_NAME
    if not manifest_path.is_file():
        return disposition

    state = workflow_stages.read_state(current)
    verifier = disposition.get("verifier", {})
    verifier_sha = str(verifier.get("sha256", "")) if isinstance(verifier, dict) else ""
    policy = disposition.get("policy", {})
    policy_fingerprint = (
        str(policy.get("fingerprint", "")) if isinstance(policy, dict) else ""
    )
    manifest = run_manifest.load_manifest(manifest_path)
    existing = manifest.get("stages", {})
    semantic = existing.get("semantic-verified", {}) if isinstance(existing, dict) else {}
    details = semantic.get("details", {}) if isinstance(semantic, dict) else {}
    attempt = int(details.get("attempt", 0) or 0) if isinstance(details, dict) else 0

    run_manifest.complete_stage(
        manifest_path,
        "semantic-verified",
        run_root=current,
        artifacts=[
            current / "verification-result.json",
            current / semantic_disposition.ARTIFACT_NAME,
        ],
        inputs={
            "source_identity": str(state.get("SemanticSourceIdentity", "")),
            "verifier_sha256": verifier_sha,
            "policy_fingerprint": policy_fingerprint,
        },
        details={
            "attempt": attempt,
            "verdict": str(state.get("LastSemanticVerdict", "")),
            "source_identity": str(state.get("SemanticSourceIdentity", "")),
            "disposition": semantic_disposition.ACCEPTED_WITH_DEFERRALS,
            "verifier_sha256": verifier_sha,
            "policy_fingerprint": policy_fingerprint,
        },
    )
    manifest = run_manifest.load_manifest(manifest_path)
    manifest["semantic_disposition"] = {
        "state": semantic_disposition.ACCEPTED_WITH_DEFERRALS,
        "artifact": semantic_disposition.ARTIFACT_NAME,
        "verifier_sha256": verifier_sha,
        "policy_fingerprint": policy_fingerprint,
        "accepted_at": str(disposition.get("accepted_at", "")),
    }
    run_manifest.save_manifest(manifest_path, manifest)
    return disposition
