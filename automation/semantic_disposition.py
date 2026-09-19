from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from automation import lifecycle_policy, run_manifest, workflow_stages


ARTIFACT_NAME = "semantic-disposition.json"
SCHEMA_VERSION = 1

CLEAN_PASS = "CLEAN_PASS"
AWAITING_REPAIR = "AWAITING_REPAIR"
AWAITING_HUMAN = "AWAITING_HUMAN_DISPOSITION"
ACCEPTED_WITH_DEFERRALS = "ACCEPTED_WITH_DEFERRALS"
HARD_BLOCK = "HARD_BLOCK"

BLOCKING = "blocking"
DEFERRABLE = "deferrable"
HUMAN_OVERRIDE = "human-override-required"
DEFERRED = "deferred"


class SemanticDispositionError(RuntimeError):
    pass


# Deliberately conservative, high-signal phrases only. A free-text verifier
# finding is not trusted to self-declare that it is safe. Anything blocking
# that is not confidently critical remains human-reviewable rather than being
# automatically deferred.
_CRITICAL_SIGNALS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "data-integrity",
        (
            "data loss",
            "data-loss",
            "data corruption",
            "corrupt data",
            "silently loses",
            "silently drops",
            "not durable",
            "isn't durable",
            "is not persisted",
            "not persisted",
            "lost after restart",
        ),
    ),
    (
        "migration-safety",
        (
            "destructive migration",
            "unsafe migration",
            "drops table",
            "drops column",
            "irreversible migration",
        ),
    ),
    (
        "security-privacy",
        (
            "security boundary",
            "privacy boundary",
            "authorization bypass",
            "authentication bypass",
            "unauthorized access",
            "secret exposure",
            "secrets exposure",
            "leaks secret",
            "leaks token",
            "credential exposure",
        ),
    ),
    (
        "dangerous-side-effect",
        (
            "dangerous side effect",
            "dangerous external side effect",
            "unintended external side effect",
            "sends without confirmation",
            "deletes without confirmation",
        ),
    ),
    (
        "core-journey-unavailable",
        (
            "core journey cannot",
            "core user journey cannot",
            "cannot exercise the core",
            "unable to exercise the core",
            "core journey is impossible",
            "core workflow is impossible",
        ),
    ),
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SemanticDispositionError(f"semantic verifier result is unavailable: {path}") from exc
    return hashlib.sha256(data).hexdigest()


def _critical_class(text: str) -> str:
    normalized = text.casefold()
    for category, signals in _CRITICAL_SIGNALS:
        if any(signal in normalized for signal in signals):
            return category
    return ""


def _entry_id(kind: str, index: int, payload: Mapping[str, object]) -> str:
    canonical = json.dumps(
        {"kind": kind, "index": index, "payload": dict(payload)},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]


def _finding_entry(
    finding: Mapping[str, object],
    index: int,
    *,
    policy: lifecycle_policy.LifecyclePolicy,
) -> dict[str, object]:
    message = str(finding.get("message", "") or "").strip()
    path = str(finding.get("path", "") or "").strip()
    severity = str(finding.get("severity", "") or "").strip().casefold()
    critical = _critical_class(" ".join(value for value in (message, path) if value))

    if critical:
        disposition = BLOCKING
        reason = f"critical-by-default:{critical}"
    elif not policy.configured:
        disposition = BLOCKING if severity == "blocking" else DEFERRABLE
        reason = "legacy-strict" if severity == "blocking" else "verifier-warning"
    elif severity == "warning":
        disposition = DEFERRABLE
        reason = "non-blocking verifier warning"
    else:
        disposition = HUMAN_OVERRIDE
        reason = "blocking semantic finding requires explicit human disposition"

    payload = {
        "severity": severity,
        "message": message,
        "path": path,
    }
    return {
        "id": _entry_id("finding", index, payload),
        "kind": "finding",
        "index": index,
        **payload,
        "critical_class": critical,
        "disposition": disposition,
        "reason": reason,
    }


def _requirement_entry(
    requirement: Mapping[str, object],
    index: int,
    *,
    policy: lifecycle_policy.LifecyclePolicy,
) -> dict[str, object] | None:
    status = str(requirement.get("status", "") or "").strip().casefold()
    if status == "met":
        return None
    criterion = str(requirement.get("criterion", "") or "").strip()
    evidence = requirement.get("evidence", [])
    evidence_values = [str(value) for value in evidence] if isinstance(evidence, list) else []
    critical = _critical_class(" ".join([criterion, *evidence_values]))
    if critical:
        disposition = BLOCKING
        reason = f"critical-by-default:{critical}"
    elif policy.configured:
        disposition = HUMAN_OVERRIDE
        reason = "unmet acceptance criterion requires explicit human disposition"
    else:
        disposition = BLOCKING
        reason = "legacy-strict"
    payload = {
        "criterion": criterion,
        "status": status,
        "evidence": evidence_values,
    }
    return {
        "id": _entry_id("requirement", index, payload),
        "kind": "requirement",
        "index": index,
        **payload,
        "critical_class": critical,
        "disposition": disposition,
        "reason": reason,
    }


def _state_for(verdict: str, entries: list[dict[str, object]]) -> str:
    if any(item.get("disposition") == BLOCKING for item in entries):
        return HARD_BLOCK
    if any(item.get("disposition") == HUMAN_OVERRIDE for item in entries):
        return AWAITING_HUMAN
    if entries and all(item.get("disposition") in {DEFERRABLE, DEFERRED} for item in entries):
        return ACCEPTED_WITH_DEFERRALS
    if verdict == "pass":
        return CLEAN_PASS
    return AWAITING_REPAIR


def evaluate(repo: Path) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    policy = lifecycle_policy.policy_from_state(state)
    lifecycle_policy.assert_resume_compatible(repo, state)

    result_path = current / "verification-result.json"
    raw = _read_json(result_path)
    if not isinstance(raw, dict):
        raise SemanticDispositionError("semantic verifier result is missing or invalid")
    verdict = str(raw.get("verdict", "") or "").strip().casefold()
    if verdict not in {"pass", "repair", "blocked"}:
        raise SemanticDispositionError(f"unsupported semantic verifier verdict: {verdict!r}")

    entries: list[dict[str, object]] = []
    findings = raw.get("findings", [])
    if isinstance(findings, list):
        for index, item in enumerate(findings):
            if isinstance(item, dict):
                entries.append(_finding_entry(item, index, policy=policy))
    requirements = raw.get("requirements", [])
    if isinstance(requirements, list):
        for index, item in enumerate(requirements):
            if isinstance(item, dict):
                entry = _requirement_entry(item, index, policy=policy)
                if entry is not None:
                    entries.append(entry)

    verifier_sha = _sha256(result_path)
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "verifier": {
            "path": str(result_path),
            "sha256": verifier_sha,
            "verdict": verdict,
        },
        "policy": lifecycle_policy.evidence_from_state(state),
        "state": _state_for(verdict, entries),
        "entries": entries,
        "created_at": _utc_now(),
        "run_id": str(state.get("RunId", state.get("RunID", "")) or ""),
        "issue_number": int(state.get("IssueNumber", 0) or 0),
    }

    existing = _read_json(current / ARTIFACT_NAME)
    if isinstance(existing, dict):
        same_identity = (
            str(existing.get("verifier", {}).get("sha256", "")) == verifier_sha
            if isinstance(existing.get("verifier"), dict)
            else False
        ) and (
            str(existing.get("policy", {}).get("fingerprint", "")) == policy.fingerprint
            if isinstance(existing.get("policy"), dict)
            else False
        )
        if same_identity and existing.get("state") == ACCEPTED_WITH_DEFERRALS:
            return existing

    _write_json(current / ARTIFACT_NAME, artifact)
    return artifact


def accept_with_deferrals(repo: Path, *, reason: str = "") -> dict[str, object]:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    state = workflow_stages.read_state(current)
    policy = lifecycle_policy.policy_from_state(state)
    if not policy.configured:
        raise SemanticDispositionError(
            "legacy-strict repositories do not support semantic deferral; configure lifecycle/work exposure explicitly first"
        )

    disposition = evaluate(repo)
    if disposition.get("state") == CLEAN_PASS:
        return disposition
    entries = disposition.get("entries", [])
    if not isinstance(entries, list):
        raise SemanticDispositionError("semantic disposition entries are invalid")
    hard = [item for item in entries if isinstance(item, dict) and item.get("disposition") == BLOCKING]
    if hard:
        classes = sorted(
            {str(item.get("critical_class", "critical")) or "critical" for item in hard}
        )
        raise SemanticDispositionError(
            "semantic deferral refused because critical-by-default findings remain: "
            + ", ".join(classes)
        )
    human = [item for item in entries if isinstance(item, dict) and item.get("disposition") == HUMAN_OVERRIDE]
    normalized_reason = reason.strip()
    if human and not normalized_reason:
        raise SemanticDispositionError(
            "--accept-with-deferrals requires --deferral-reason when overriding blocking semantic findings"
        )

    for item in entries:
        if not isinstance(item, dict):
            continue
        if item.get("disposition") in {DEFERRABLE, HUMAN_OVERRIDE}:
            item["disposition"] = DEFERRED
            item["deferred_by"] = "human" if item in human else "policy"

    verifier = disposition.get("verifier", {})
    verifier_sha = str(verifier.get("sha256", "")) if isinstance(verifier, dict) else ""
    prior_audit = disposition.get("override")
    if isinstance(prior_audit, dict) and disposition.get("state") == ACCEPTED_WITH_DEFERRALS:
        return disposition

    disposition["state"] = ACCEPTED_WITH_DEFERRALS
    disposition["accepted_at"] = _utc_now()
    disposition["override"] = {
        "source": "autodev resume --accept-with-deferrals",
        "reason": normalized_reason,
        "accepted_at": disposition["accepted_at"],
        "verifier_sha256": verifier_sha,
        "policy_fingerprint": policy.fingerprint,
        "run_id": str(state.get("RunId", state.get("RunID", "")) or ""),
        "issue_number": int(state.get("IssueNumber", 0) or 0),
        "deferred_entry_ids": [
            str(item.get("id", "")) for item in entries if isinstance(item, dict)
        ],
    }
    _write_json(current / ARTIFACT_NAME, disposition)

    state["SemanticDisposition"] = ACCEPTED_WITH_DEFERRALS
    state["SemanticDispositionVerifierSha256"] = verifier_sha
    state["SemanticDispositionPolicyFingerprint"] = policy.fingerprint
    state["SemanticDispositionArtifact"] = str(current / ARTIFACT_NAME)
    # Bind the accepted disposition to the same deterministic source proof that
    # the verifier evaluated without rewriting LastSemanticVerdict to "pass".
    if state.get("VerifiedSourceIdentity"):
        state["SemanticSourceIdentity"] = str(state.get("VerifiedSourceIdentity", ""))
    if str(state.get("Status", "")) == "Blocked":
        state["Status"] = "SemanticDispositionAccepted"
    workflow_stages.write_state(current, state)

    manifest_path = current / run_manifest.MANIFEST_NAME
    if manifest_path.is_file():
        manifest = run_manifest.load_manifest(manifest_path)
        manifest["semantic_disposition"] = {
            "state": ACCEPTED_WITH_DEFERRALS,
            "artifact": ARTIFACT_NAME,
            "verifier_sha256": verifier_sha,
            "policy_fingerprint": policy.fingerprint,
            "accepted_at": disposition["accepted_at"],
        }
        manifest["failure"] = {}
        run_manifest.save_manifest(manifest_path, manifest)
    return disposition


def accepted_for_current_result(repo: Path, state: Mapping[str, object] | None = None) -> bool:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    actual_state = dict(state) if state is not None else workflow_stages.read_state(current)
    if str(actual_state.get("SemanticDisposition", "")) != ACCEPTED_WITH_DEFERRALS:
        return False
    result_path = current / "verification-result.json"
    if not result_path.is_file():
        return False
    try:
        policy = lifecycle_policy.policy_from_state(actual_state)
    except lifecycle_policy.LifecyclePolicyError:
        return False
    return (
        str(actual_state.get("SemanticDispositionVerifierSha256", "")) == _sha256(result_path)
        and str(actual_state.get("SemanticDispositionPolicyFingerprint", "")) == policy.fingerprint
    )


def status_line(repo: Path) -> str:
    repo = repo.expanduser().resolve()
    current = repo / workflow_stages.CURRENT_DIR
    artifact = _read_json(current / ARTIFACT_NAME)
    if not isinstance(artifact, dict) or not artifact:
        return "Semantic disposition: not evaluated"
    state = str(artifact.get("state", "") or "")
    entries = artifact.get("entries", [])
    values = [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
    deferred = sum(1 for item in values if item.get("disposition") == DEFERRED)
    blocking = sum(1 for item in values if item.get("disposition") == BLOCKING)
    human = sum(1 for item in values if item.get("disposition") == HUMAN_OVERRIDE)
    return (
        f"Semantic disposition: {state} "
        f"deferred={deferred} blocking={blocking} human-review={human}"
    )
