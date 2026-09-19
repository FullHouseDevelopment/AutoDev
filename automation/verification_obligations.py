from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping

from automation import workflow_stages

LEDGER_PATH = Path(".autodev-run") / "deferred-obligations.json"
CURRENT_ARTIFACT = "deferred-verification.json"
SCHEMA_VERSION = 1

OPEN = "open"
RESOLVED = "resolved"
SUPERSEDED = "superseded"

KIND_PLATFORM = "platform-verification"
KIND_SEMANTIC = "semantic"

ORIGIN_VERIFIER = "verifier"
ORIGIN_PLATFORM = "platform-verification"
ORIGIN_HUMAN_DOGFOOD = "human-dogfood"
ORIGIN_RUNTIME = "runtime-telemetry"
ORIGIN_REQUIREMENT = "new-product-requirement"
ORIGIN_PIVOT = "superseded-design-assumption"

class VerificationObligationError(RuntimeError):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stable_id(*parts: object) -> str:
    raw = "|".join(str(value) for value in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _run_id(state: Mapping[str, object]) -> str:
    return str(state.get("RunId", state.get("RunID", "")) or "").strip()


def _issue_number(state: Mapping[str, object]) -> int:
    return int(state.get("IssueNumber", 0) or 0)


def _repo_full(state: Mapping[str, object]) -> str:
    return str(state.get("RepoFullName", "") or "").strip()


def _load_ledger(repo: Path) -> dict[str, object]:
    path = repo.expanduser().resolve() / LEDGER_PATH
    value = _read_json(path)
    if not value:
        return {"schema_version": SCHEMA_VERSION, "obligations": []}
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        raise VerificationObligationError(
            f"invalid deferred-obligation ledger: {path}"
        )
    obligations = value.get("obligations", [])
    if not isinstance(obligations, list):
        raise VerificationObligationError(
            f"deferred-obligation ledger obligations must be a list: {path}"
        )
    return value


def _save_ledger(repo: Path, ledger: Mapping[str, object]) -> None:
    _write_json(repo.expanduser().resolve() / LEDGER_PATH, dict(ledger))


def all_obligations(repo: Path) -> list[dict[str, object]]:
    ledger = _load_ledger(repo)
    return [
        dict(item)
        for item in ledger.get("obligations", [])
        if isinstance(item, dict)
    ]


def open_obligations(
    repo: Path,
    *,
    kind: str = "",
) -> list[dict[str, object]]:
    return [
        item
        for item in all_obligations(repo)
        if str(item.get("status", OPEN)) == OPEN
        and (not kind or str(item.get("kind", "")) == kind)
    ]


def promotion_blockers(repo: Path) -> list[dict[str, object]]:
    """Return verifier-derived semantic debt that promotion policy may gate on."""
    return [
        item
        for item in open_obligations(repo, kind=KIND_SEMANTIC)
        if str(item.get("origin_kind", "")) == ORIGIN_VERIFIER
    ]


def _upsert_many(
    repo: Path,
    records: Iterable[Mapping[str, object]],
) -> list[dict[str, object]]:
    ledger = _load_ledger(repo)
    values = [
        dict(item)
        for item in ledger.get("obligations", [])
        if isinstance(item, dict)
    ]
    by_id = {str(item.get("id", "")): index for index, item in enumerate(values)}
    persisted: list[dict[str, object]] = []
    now = _utc_now()

    for raw in records:
        record = dict(raw)
        obligation_id = str(record.get("id", "") or "").strip()
        if not obligation_id:
            raise VerificationObligationError("deferred obligation is missing id")
        existing_index = by_id.get(obligation_id)
        if existing_index is None:
            record.setdefault("created_at", now)
            record.setdefault("status", OPEN)
            record.setdefault("tracking_issue", 0)
            record.setdefault("tracking_url", "")
            values.append(record)
            by_id[obligation_id] = len(values) - 1
            persisted.append(record)
            continue

        existing = dict(values[existing_index])
        immutable_created = str(existing.get("created_at", "") or "") or now
        tracking_issue = int(existing.get("tracking_issue", 0) or 0)
        tracking_url = str(existing.get("tracking_url", "") or "")
        status = str(existing.get("status", OPEN) or OPEN)
        resolution = str(existing.get("resolution", "") or "")
        resolved_at = str(existing.get("resolved_at", "") or "")
        first_verifier = str(existing.get("first_verifier_result_sha256", "") or "")
        existing.update(record)
        existing["created_at"] = immutable_created
        if tracking_issue:
            existing["tracking_issue"] = tracking_issue
            existing["tracking_url"] = tracking_url
        if status == SUPERSEDED:
            existing["status"] = status
            existing["resolution"] = resolution
            existing["resolved_at"] = resolved_at
        elif status == RESOLVED and str(record.get("status", OPEN)) != OPEN:
            existing["status"] = status
            existing["resolution"] = resolution
            existing["resolved_at"] = resolved_at
        else:
            existing.pop("resolution", None)
            existing.pop("resolved_at", None)
        if first_verifier:
            existing["first_verifier_result_sha256"] = first_verifier
        values[existing_index] = existing
        persisted.append(existing)

    _save_ledger(
        repo,
        {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now,
            "obligations": values,
        },
    )
    return persisted


def _current_records(repo: Path, state: Mapping[str, object]) -> list[dict[str, object]]:
    run_id = _run_id(state)
    issue = _issue_number(state)
    records = all_obligations(repo)
    if run_id:
        return [
            item for item in records
            if str(item.get("origin_run_id", "")) == run_id
        ]
    if issue:
        return [
            item for item in records
            if int(item.get("origin_issue_number", 0) or 0) == issue
            and not str(item.get("origin_run_id", ""))
        ]
    return [
        item for item in records
        if not str(item.get("origin_run_id", ""))
        and int(item.get("origin_issue_number", 0) or 0) == 0
    ]


def sync_current_state(
    repo: Path,
    state: dict[str, object],
    *,
    extra_artifact: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
    records = [
        item
        for item in _current_records(repo, state)
        if str(item.get("status", OPEN)) == OPEN
    ]
    state["DeferredVerificationObligations"] = records
    workflow_stages.write_state(current, state)
    payload: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "obligations": records,
        "open_count": sum(1 for item in records if item.get("status") == OPEN),
    }
    if extra_artifact:
        payload.update(dict(extra_artifact))
    _write_json(current / CURRENT_ARTIFACT, payload)
    return records


def record_platform_obligations(
    repo: Path,
    state: dict[str, object],
    obligations: Iterable[Mapping[str, object]],
    *,
    extra_artifact: Mapping[str, object] | None = None,
) -> list[dict[str, object]]:
    repo = repo.expanduser().resolve()
    run_id = _run_id(state)
    issue = _issue_number(state)
    source_identity = str(
        state.get("VerifiedSourceIdentity", state.get("ShippedSourceIdentity", "")) or ""
    )
    records: list[dict[str, object]] = []
    for raw in obligations:
        item = dict(raw)
        platform = str(item.get("platform", "") or "")
        message = str(item.get("message", "") or "")
        source = str(item.get("source", "") or "")
        legacy_id = str(item.get("id", "") or "")
        records.append(
            {
                **item,
                "id": "platform-" + _stable_id(
                    _repo_full(state),
                    issue,
                    run_id,
                    legacy_id or platform,
                    message,
                ),
                "kind": KIND_PLATFORM,
                "origin_kind": ORIGIN_PLATFORM,
                "status": OPEN,
                "platform": platform,
                "message": message,
                "source": source,
                "source_identity": source_identity,
                "origin_run_id": run_id,
                "origin_issue_number": issue,
                "origin_issue_url": str(state.get("IssueUrl", "") or ""),
                "origin_pr_url": str(state.get("PrUrl", "") or ""),
                "disposition": "deferred",
            }
        )
    _upsert_many(repo, records)
    return sync_current_state(repo, state, extra_artifact=extra_artifact)


def _semantic_record(
    state: Mapping[str, object],
    entry: Mapping[str, object],
    disposition: Mapping[str, object],
) -> dict[str, object]:
    verifier = disposition.get("verifier", {})
    verifier = verifier if isinstance(verifier, dict) else {}
    policy = disposition.get("policy", {})
    policy = policy if isinstance(policy, dict) else {}
    entry_id = str(entry.get("id", "") or "")
    criterion = str(entry.get("criterion", "") or "")
    finding = str(entry.get("message", "") or "")
    evidence = entry.get("evidence", [])
    evidence_values = [str(value) for value in evidence] if isinstance(evidence, list) else []
    verifier_sha = str(verifier.get("sha256", "") or "")
    obligation_id = "semantic-" + _stable_id(
        _repo_full(state),
        _issue_number(state),
        entry_id,
        criterion,
        finding,
    )
    deferred_by = str(entry.get("deferred_by", "") or "")
    override = disposition.get("override", {})
    override = override if isinstance(override, dict) else {}
    return {
        "id": obligation_id,
        "kind": KIND_SEMANTIC,
        "origin_kind": ORIGIN_VERIFIER,
        "status": OPEN,
        "criterion": criterion,
        "finding": finding,
        "evidence": evidence_values,
        "path": str(entry.get("path", "") or ""),
        "severity": str(entry.get("severity", "") or ""),
        "source_identity": str(state.get("VerifiedSourceIdentity", "") or ""),
        "verifier_result_sha256": verifier_sha,
        "first_verifier_result_sha256": verifier_sha,
        "lifecycle": str(policy.get("lifecycle", "") or ""),
        "work_exposure": str(policy.get("work_exposure", "") or ""),
        "policy_fingerprint": str(policy.get("fingerprint", "") or ""),
        "disposition": "deferred",
        "reason": str(entry.get("reason", "") or ""),
        "deferred_by": deferred_by,
        "human_override": deferred_by == "human",
        "human_override_reason": str(override.get("reason", "") or "") if deferred_by == "human" else "",
        "origin_run_id": _run_id(state),
        "origin_issue_number": _issue_number(state),
        "origin_issue_url": str(state.get("IssueUrl", "") or ""),
        "origin_pr_url": str(state.get("PrUrl", "") or ""),
        "tracking_group": _stable_id(
            _repo_full(state),
            _issue_number(state),
            _run_id(state),
            verifier_sha,
        ),
    }


def record_product_findings(
    repo: Path,
    state: dict[str, object],
    findings: Iterable[Mapping[str, object]],
    *,
    origin_kind: str,
    source_identity: str = "",
) -> list[dict[str, object]]:
    allowed = {
        ORIGIN_HUMAN_DOGFOOD,
        ORIGIN_RUNTIME,
        ORIGIN_REQUIREMENT,
        ORIGIN_PIVOT,
    }
    normalized_origin = origin_kind.strip()
    if normalized_origin not in allowed:
        raise VerificationObligationError(
            f"unsupported product-finding origin_kind: {origin_kind!r}"
        )

    records: list[dict[str, object]] = []
    for index, raw in enumerate(findings):
        item = dict(raw)
        finding = str(item.get("finding", item.get("message", "")) or "").strip()
        criterion = str(item.get("criterion", "") or "").strip()
        if not finding and not criterion:
            raise VerificationObligationError(
                "product finding requires finding/message or criterion"
            )
        external_id = str(item.get("id", "") or "").strip()
        obligation_id = "product-" + _stable_id(
            _repo_full(state),
            _issue_number(state),
            _run_id(state),
            normalized_origin,
            external_id or index,
            criterion,
            finding,
        )
        records.append(
            {
                "id": obligation_id,
                "kind": "product-learning",
                "origin_kind": normalized_origin,
                "status": OPEN,
                "criterion": criterion,
                "finding": finding,
                "severity": str(item.get("severity", "") or ""),
                "source_identity": source_identity.strip()
                or str(item.get("source_identity", "") or ""),
                "origin_run_id": _run_id(state),
                "origin_issue_number": _issue_number(state),
                "origin_issue_url": str(state.get("IssueUrl", "") or ""),
                "origin_pr_url": str(state.get("PrUrl", "") or ""),
                "disposition": str(item.get("disposition", "open-learning") or "open-learning"),
                "reason": str(item.get("reason", "") or ""),
                "core_journey": bool(item.get("core_journey", False)),
                "design_pivot": bool(
                    item.get("design_pivot", normalized_origin == ORIGIN_PIVOT)
                ),
                "tracking_group": str(item.get("tracking_group", "") or "")
                or _stable_id(
                    _repo_full(state),
                    _issue_number(state),
                    _run_id(state),
                    normalized_origin,
                ),
            }
        )
    if records:
        _upsert_many(repo, records)
    return sync_current_state(repo, state)


def record_semantic_deferrals(
    repo: Path,
    state: dict[str, object],
    disposition: Mapping[str, object],
) -> list[dict[str, object]]:
    entries = disposition.get("entries", [])
    values = [item for item in entries if isinstance(item, dict)] if isinstance(entries, list) else []
    deferred = [
        _semantic_record(state, item, disposition)
        for item in values
        if str(item.get("disposition", "")) == "deferred"
    ]
    if deferred:
        _upsert_many(repo, deferred)
    return sync_current_state(repo, state)


def resolve(
    repo: Path,
    obligation_ids: Iterable[str],
    *,
    reason: str,
    status: str = RESOLVED,
) -> list[dict[str, object]]:
    if status not in {RESOLVED, SUPERSEDED}:
        raise VerificationObligationError(f"unsupported obligation status: {status!r}")
    ids = {value.strip() for value in obligation_ids if value.strip()}
    if not ids:
        return []
    ledger = _load_ledger(repo)
    values = [
        dict(item)
        for item in ledger.get("obligations", [])
        if isinstance(item, dict)
    ]
    changed: list[dict[str, object]] = []
    now = _utc_now()
    for item in values:
        if str(item.get("id", "")) not in ids:
            continue
        item["status"] = status
        item["resolution"] = reason.strip()
        item["resolved_at"] = now
        changed.append(item)
    _save_ledger(
        repo,
        {
            "schema_version": SCHEMA_VERSION,
            "updated_at": now,
            "obligations": values,
        },
    )
    return changed


def supersede(repo: Path, obligation_ids: Iterable[str], *, reason: str) -> list[dict[str, object]]:
    return resolve(repo, obligation_ids, reason=reason, status=SUPERSEDED)


def resolve_current(
    repo: Path,
    state: Mapping[str, object],
    *,
    kind: str,
    reason: str,
) -> list[dict[str, object]]:
    ids = [
        str(item.get("id", ""))
        for item in _current_records(repo, state)
        if item.get("kind") == kind and item.get("status") == OPEN
    ]
    changed = resolve(repo, ids, reason=reason)
    if changed:
        current = repo.expanduser().resolve() / workflow_stages.CURRENT_DIR
        state_value = workflow_stages.read_state(current)
        sync_current_state(repo, state_value)
    return changed


def reconcile_verifier_result(
    repo: Path,
    result: Mapping[str, object],
) -> list[dict[str, object]]:
    requirements = result.get("requirements", [])
    met = {
        str(item.get("criterion", "") or "").strip()
        for item in requirements
        if isinstance(item, dict)
        and str(item.get("status", "") or "").strip().casefold() == "met"
        and str(item.get("criterion", "") or "").strip()
    } if isinstance(requirements, list) else set()
    if not met:
        return []
    matches = [
        item["id"]
        for item in open_obligations(repo, kind=KIND_SEMANTIC)
        if str(item.get("criterion", "") or "").strip() in met
    ]
    return resolve(
        repo,
        matches,
        reason="semantic verifier later reported the exact deferred criterion as met",
    )


def status_lines(repo: Path) -> list[str]:
    values = open_obligations(repo)
    semantic = [item for item in values if item.get("kind") == KIND_SEMANTIC]
    platform = [item for item in values if item.get("kind") == KIND_PLATFORM]
    lines = [
        f"Deferred obligations: open={len(values)} semantic={len(semantic)} platform={len(platform)}"
    ]
    for item in values[:3]:
        summary = (
            str(item.get("criterion", "") or "")
            or str(item.get("finding", "") or "")
            or str(item.get("message", "") or "")
            or str(item.get("id", "") or "")
        )
        if len(summary) > 120:
            summary = summary[:117] + "..."
        tracking = str(item.get("tracking_url", "") or "")
        suffix = f" tracking={tracking}" if tracking else ""
        lines.append(
            f"Deferred obligation [{item.get('kind', '')}] {summary}{suffix}"
        )
    if len(values) > 3:
        lines.append(f"Deferred obligations: {len(values) - 3} more open")
    return lines
