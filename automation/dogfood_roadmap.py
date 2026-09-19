from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping

from automation import lifecycle_policy, queue_github


PROJECTION_PATH = Path(".autodev") / "dogfood.json"
PROPOSAL_ROOT = Path(".autodev-run") / "roadmap-proposals"
PROJECTION_VERSION = 1
PROPOSAL_VERSION = 1

ACTIVE = "active"
AWAITING_LEARNING = "awaiting-learning"
RECONCILED = "reconciled"
RECURSIVE_MVP_REQUIRED = "recursive-mvp-required"
SUPERSEDED = "superseded"

BUILDING = "building"
DOGFOODABLE = "dogfoodable"

DELIVERY_NOT_RECORDED = "not-recorded"
DELIVERY_RECORDED = "recorded"

LEARNING_NOT_READY = "not-ready"
LEARNING_PENDING = "pending"
LEARNING_RECONCILED = "reconciled"

ASSUMPTIONS_UNVALIDATED = "unvalidated"
ASSUMPTIONS_VALIDATED = "validated"
ASSUMPTIONS_INVALIDATED = "invalidated"
ASSUMPTIONS_MIXED = "mixed"


class DogfoodRoadmapError(RuntimeError):
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
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _canonical_sha(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _issue_snapshot(raw: Mapping[str, object]) -> dict[str, object]:
    return {
        "number": int(raw.get("number", 0) or 0),
        "title": str(raw.get("title", "") or ""),
        "body": str(raw.get("body", "") or ""),
        "url": str(raw.get("url", "") or ""),
        "state": str(raw.get("state", "") or "").casefold(),
    }


def fetch_deep_issue(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    result = queue_github._run_gh(
        repo,
        [
            "issue",
            "view",
            str(issue_number),
            "--repo",
            github_repo,
            "--json",
            "number,title,body,url,state",
        ],
        runner=runner,
    )
    raw = queue_github._json_result(result, context="gh issue view")
    if not isinstance(raw, dict):
        raise DogfoodRoadmapError("GitHub deep issue lookup did not return an object")
    value = _issue_snapshot(raw)
    if int(value["number"]) <= 0:
        raise DogfoodRoadmapError("GitHub deep issue lookup did not return an issue number")
    return value


def validate_proposal_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, dict):
        raise DogfoodRoadmapError("dogfood decomposition must be a JSON object")
    allowed = {
        "strategy",
        "rationale",
        "dogfood_readiness",
        "deep_contract_preserved",
        "slices",
        "deferred_requirements",
    }
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise DogfoodRoadmapError(
            "dogfood decomposition contains unsupported field(s): " + ", ".join(unknown)
        )
    strategy = str(payload.get("strategy", "") or "").strip()
    if strategy not in {"single-mvp", "spine"}:
        raise DogfoodRoadmapError("dogfood strategy must be 'single-mvp' or 'spine'")
    if payload.get("deep_contract_preserved") is not True:
        raise DogfoodRoadmapError(
            "dogfood decomposition must explicitly preserve the authoritative deep contract"
        )
    rationale = str(payload.get("rationale", "") or "").strip()
    readiness = str(payload.get("dogfood_readiness", "") or "").strip()
    if not rationale or not readiness:
        raise DogfoodRoadmapError("dogfood decomposition rationale/readiness must be non-empty")

    raw_slices = payload.get("slices", [])
    if not isinstance(raw_slices, list) or not raw_slices or len(raw_slices) > 12:
        raise DogfoodRoadmapError("dogfood decomposition must contain 1-12 slices")
    if strategy == "single-mvp" and len(raw_slices) != 1:
        raise DogfoodRoadmapError("single-mvp strategy must contain exactly one vertical slice")
    if strategy == "spine" and len(raw_slices) < 2:
        raise DogfoodRoadmapError(
            "spine strategy must contain at least two slices; use single-mvp for one broad vertical MVP"
        )

    slices: list[dict[str, object]] = []
    for index, raw in enumerate(raw_slices, start=1):
        if not isinstance(raw, dict):
            raise DogfoodRoadmapError(f"dogfood slice {index} must be an object")
        allowed_slice = {
            "title",
            "body",
            "journey",
            "vertical_value",
            "real_dependencies",
            "deferred_requirements",
        }
        extra = sorted(set(raw) - allowed_slice)
        if extra:
            raise DogfoodRoadmapError(
                f"dogfood slice {index} contains unsupported field(s): "
                + ", ".join(extra)
            )
        text_fields = {
            key: str(raw.get(key, "") or "").strip()
            for key in ("title", "body", "journey", "vertical_value")
        }
        if any(not value for value in text_fields.values()):
            raise DogfoodRoadmapError(
                f"dogfood slice {index} must include title, body, journey, and vertical_value"
            )
        dependencies = raw.get("real_dependencies", [])
        deferred = raw.get("deferred_requirements", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) and item.strip() for item in dependencies
        ):
            raise DogfoodRoadmapError(
                f"dogfood slice {index} real_dependencies must be non-empty strings"
            )
        if not isinstance(deferred, list) or not all(
            isinstance(item, str) and item.strip() for item in deferred
        ):
            raise DogfoodRoadmapError(
                f"dogfood slice {index} deferred_requirements must be non-empty strings"
            )
        slices.append(
            {
                **text_fields,
                "real_dependencies": [item.strip() for item in dependencies],
                "deferred_requirements": [item.strip() for item in deferred],
            }
        )

    deferred = payload.get("deferred_requirements", [])
    if not isinstance(deferred, list) or not all(
        isinstance(item, str) and item.strip() for item in deferred
    ):
        raise DogfoodRoadmapError(
            "dogfood decomposition deferred_requirements must be non-empty strings"
        )
    return {
        "strategy": strategy,
        "rationale": rationale,
        "dogfood_readiness": readiness,
        "deep_contract_preserved": True,
        "slices": slices,
        "deferred_requirements": [item.strip() for item in deferred],
    }


def proposal_path(repo: Path, proposal_id: str) -> Path:
    if not re.fullmatch(r"dogfood-[A-Za-z0-9._-]+", proposal_id):
        raise DogfoodRoadmapError(f"invalid dogfood proposal id: {proposal_id!r}")
    return repo.expanduser().resolve() / PROPOSAL_ROOT / f"{proposal_id}.json"


def persist_proposal(
    repo: Path,
    *,
    parent: Mapping[str, object],
    decomposition: Mapping[str, object],
    generation: int = 1,
    predecessor_spine_id: str = "",
    runtime_metadata: Mapping[str, object] | None = None,
) -> dict[str, object]:
    validated = validate_proposal_payload(dict(decomposition))
    parent_value = _issue_snapshot(parent)
    parent_sha = _canonical_sha(parent_value)
    generation = max(1, int(generation))
    identity = {
        "parent_issue": int(parent_value["number"]),
        "parent_snapshot_sha256": parent_sha,
        "generation": generation,
        "predecessor_spine_id": predecessor_spine_id.strip(),
        "decomposition": validated,
    }
    fingerprint = _canonical_sha(identity)
    proposal_id = (
        f"dogfood-{int(parent_value['number'])}-g{generation}-{fingerprint[:12]}"
    )
    value = {
        "version": PROPOSAL_VERSION,
        "proposal_id": proposal_id,
        "proposal_fingerprint": fingerprint,
        "review_state": "proposed",
        "created_at": _utc_now(),
        "parent": parent_value,
        "parent_snapshot_sha256": parent_sha,
        "generation": generation,
        "predecessor_spine_id": predecessor_spine_id.strip(),
        "decomposition": validated,
        "runtime": dict(runtime_metadata or {}),
    }
    _write_json(proposal_path(repo, proposal_id), value)
    return value


def load_proposal(repo: Path, proposal: str | Path) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    path = Path(proposal)
    if not path.is_absolute():
        candidate = repo / path
        path = candidate if candidate.is_file() else proposal_path(repo, str(proposal))
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("version") != PROPOSAL_VERSION:
        raise DogfoodRoadmapError(f"invalid dogfood proposal: {path}")
    parent = raw.get("parent")
    decomposition = raw.get("decomposition")
    if not isinstance(parent, dict):
        raise DogfoodRoadmapError("dogfood proposal parent snapshot is missing")
    validate_proposal_payload(decomposition)
    expected = _canonical_sha(
        {
            "parent_issue": int(parent.get("number", 0) or 0),
            "parent_snapshot_sha256": str(raw.get("parent_snapshot_sha256", "") or ""),
            "generation": int(raw.get("generation", 1) or 1),
            "predecessor_spine_id": str(raw.get("predecessor_spine_id", "") or ""),
            "decomposition": decomposition,
        }
    )
    if str(raw.get("proposal_fingerprint", "")) != expected:
        raise DogfoodRoadmapError("dogfood proposal fingerprint does not match its contents")
    return raw


def approve_proposal(repo: Path, proposal: str | Path) -> dict[str, object]:
    value = load_proposal(repo, proposal)
    value["review_state"] = "approved"
    value["approved_at"] = _utc_now()
    _write_json(proposal_path(repo, str(value["proposal_id"])), value)
    return value


def load_projection(repo: Path) -> dict[str, object]:
    path = repo.expanduser().resolve() / PROJECTION_PATH
    if not path.is_file():
        return {"version": PROJECTION_VERSION, "spines": []}
    raw = _read_json(path)
    if not isinstance(raw, dict) or raw.get("version") != PROJECTION_VERSION:
        raise DogfoodRoadmapError(f"invalid dogfood projection: {path}")
    spines = raw.get("spines", [])
    if not isinstance(spines, list):
        raise DogfoodRoadmapError(f"dogfood projection spines must be a list: {path}")
    return raw


def save_projection(repo: Path, value: Mapping[str, object]) -> None:
    _write_json(repo.expanduser().resolve() / PROJECTION_PATH, dict(value))


def active_next_slice_issue(projection: Mapping[str, object]) -> tuple[int, str] | None:
    candidates: list[tuple[int, int, str]] = []
    for raw in projection.get("spines", []):
        if not isinstance(raw, dict):
            continue
        status = str(raw.get("status", ""))
        if status not in {ACTIVE, RECURSIVE_MVP_REQUIRED}:
            continue
        generation = int(raw.get("generation", 1) or 1)
        if status == RECURSIVE_MVP_REQUIRED and raw.get("successor_spine_id"):
            continue
        slices = [
            item
            for item in raw.get("slices", [])
            if isinstance(item, dict)
            and item.get("state") not in {"complete", SUPERSEDED}
        ] if isinstance(raw.get("slices", []), list) else []
        if not slices:
            continue
        first = min(slices, key=lambda item: int(item.get("order", 0) or 0))
        candidates.append(
            (
                -generation,
                int(first.get("issue", 0) or 0),
                str(raw.get("id", "")),
            )
        )
    if not candidates:
        return None
    _neg_generation, issue, spine_id = sorted(candidates)[0]
    return (issue, spine_id) if issue > 0 else None


def dogfood_priority_active(repo: Path) -> bool:
    try:
        policy = lifecycle_policy.load_lifecycle_policy(repo)
    except lifecycle_policy.LifecyclePolicyError:
        return False
    return bool(
        policy.configured
        and policy.lifecycle == lifecycle_policy.PREPRODUCTION
        and policy.work_exposure == lifecycle_policy.EXPERIMENTAL
    )


def find_spine(projection: Mapping[str, object], spine_id: str) -> dict[str, object] | None:
    for item in projection.get("spines", []):
        if isinstance(item, dict) and item.get("id") == spine_id:
            return item
    return None
