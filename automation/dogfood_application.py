from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from automation import dogfood_roadmap, queue_github, verification_obligations


FINDING_BUG = "bug"
FINDING_DEFERRED_GAP = "deferred-gap"
FINDING_NEW_REQUIREMENT = "new-requirement"
FINDING_DESIGN_PIVOT = "design-pivot"
FINDING_CLASSES = {
    FINDING_BUG,
    FINDING_DEFERRED_GAP,
    FINDING_NEW_REQUIREMENT,
    FINDING_DESIGN_PIVOT,
}


def _issue_body(
    parent: Mapping[str, object],
    slice_value: Mapping[str, object],
    *,
    spine_id: str,
    generation: int,
    order: int,
    total: int,
) -> str:
    deferred = slice_value.get("deferred_requirements", [])
    dependencies = slice_value.get("real_dependencies", [])
    lines = [
        str(slice_value.get("body", "")).rstrip(),
        "",
        "## AutoDev dogfood geometry",
        "",
        f"- Authoritative deep issue: #{int(parent.get('number', 0) or 0)}",
        f"- Deep issue URL: {str(parent.get('url', '') or '')}",
        f"- Dogfood spine: {spine_id}",
        f"- Recursive MVP generation: {generation}",
        f"- Dogfood order: {order}/{total}",
        f"- Real user journey: {str(slice_value.get('journey', '') or '')}",
        f"- Vertical value: {str(slice_value.get('vertical_value', '') or '')}",
        "",
        "The deep issue remains authoritative. Completing this slice means the vertical dogfood slice shipped; it does not close or weaken the full deep-product contract.",
    ]
    if isinstance(dependencies, list) and dependencies:
        lines.extend(["", "### Real dependencies", ""])
        lines.extend(f"- {item}" for item in dependencies)
    if isinstance(deferred, list) and deferred:
        lines.extend(["", "### Deferred requirements retained for hardening", ""])
        lines.extend(f"- {item}" for item in deferred)
    return "\n".join(lines).rstrip() + "\n"


def _created_issue_url(completed: object) -> str:
    stdout = getattr(completed, "stdout", "")
    if isinstance(stdout, bytes):
        text = stdout.decode("utf-8", errors="replace")
    else:
        text = str(stdout or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    url = lines[-1] if lines else ""
    if re.search(r"/issues/\d+(?:$|[?#])", url) is None:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "gh issue create did not return an issue URL"
        )
    return url


def _issue_number_from_url(url: str) -> int:
    match = re.search(r"/issues/(\d+)(?:$|[?#])", url)
    if not match:
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"cannot parse issue number from {url!r}"
        )
    return int(match.group(1))


def apply_proposal(
    repo: Path,
    github_repo: str,
    proposal: str | Path,
    *,
    manage: bool = False,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    value = dogfood_roadmap.load_proposal(repo, proposal)
    if value.get("review_state") != "approved":
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood proposal must be explicitly approved before issue creation"
        )
    parent = value["parent"]
    assert isinstance(parent, dict)
    current_parent = dogfood_roadmap.fetch_deep_issue(
        repo,
        github_repo,
        int(parent.get("number", 0) or 0),
        runner=runner,
    )
    if dogfood_roadmap._canonical_sha(current_parent) != str(
        value.get("parent_snapshot_sha256", "")
    ):
        raise dogfood_roadmap.DogfoodRoadmapError(
            "authoritative deep issue changed after dogfood proposal generation; re-propose before creating slices"
        )

    projection = dogfood_roadmap.load_projection(repo)
    spines = [
        dict(item)
        for item in projection.get("spines", [])
        if isinstance(item, dict)
    ]
    fingerprint = str(value.get("proposal_fingerprint", ""))
    existing = next(
        (item for item in spines if item.get("source_proposal_fingerprint") == fingerprint),
        None,
    )
    if existing is not None:
        return existing

    generation = int(value.get("generation", 1) or 1)
    predecessor = str(value.get("predecessor_spine_id", "") or "")
    if predecessor and dogfood_roadmap.find_spine(projection, predecessor) is None:
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"predecessor dogfood spine {predecessor!r} does not exist"
        )

    spine_id = (
        f"dogfood-{int(parent['number'])}-g{generation}-{fingerprint[:10]}"
    )
    decomposition = value["decomposition"]
    assert isinstance(decomposition, dict)
    slices = decomposition.get("slices", [])
    assert isinstance(slices, list)
    created: list[dict[str, object]] = []
    total = len(slices)

    for order, raw in enumerate(slices, start=1):
        assert isinstance(raw, dict)
        body_path = (
            repo / ".autodev-run" / "roadmap-work" / f"{spine_id}-{order}.md"
        )
        body_path.parent.mkdir(parents=True, exist_ok=True)
        body_path.write_text(
            _issue_body(
                parent,
                raw,
                spine_id=spine_id,
                generation=generation,
                order=order,
                total=total,
            ),
            encoding="utf-8",
        )
        args = [
            "issue",
            "create",
            "--repo",
            github_repo,
            "--title",
            str(raw.get("title", "")),
            "--body-file",
            str(body_path),
        ]
        if manage:
            args.extend(["--label", "autodev:managed"])
        completed = queue_github._run_gh(repo, args, runner=runner)
        url = _created_issue_url(completed)
        created.append(
            {
                "issue": _issue_number_from_url(url),
                "url": url,
                "order": order,
                "title": str(raw.get("title", "")),
                "journey": str(raw.get("journey", "")),
                "vertical_value": str(raw.get("vertical_value", "")),
                "deferred_requirements": list(
                    raw.get("deferred_requirements", [])
                ),
                "state": "planned",
            }
        )

    spine = {
        "id": spine_id,
        "parent_issue": int(parent["number"]),
        "parent_url": str(parent.get("url", "")),
        "parent_snapshot_sha256": str(value.get("parent_snapshot_sha256", "")),
        "generation": generation,
        "predecessor_spine_id": predecessor,
        "source_proposal_id": str(value.get("proposal_id", "")),
        "source_proposal_fingerprint": fingerprint,
        "strategy": str(decomposition.get("strategy", "")),
        "rationale": str(decomposition.get("rationale", "")),
        "dogfood_readiness_definition": str(
            decomposition.get("dogfood_readiness", "")
        ),
        "deferred_requirements": list(
            decomposition.get("deferred_requirements", [])
        ),
        "status": dogfood_roadmap.ACTIVE,
        "readiness": dogfood_roadmap.BUILDING,
        "deep_issue_state": str(parent.get("state", "open")),
        "deep_complete": str(parent.get("state", "")) == "closed",
        "slices": created,
        "delivery": {
            "state": dogfood_roadmap.DELIVERY_NOT_RECORDED,
            "evidence": [],
        },
        "learning": {
            "state": dogfood_roadmap.LEARNING_NOT_READY,
            "assumptions": dogfood_roadmap.ASSUMPTIONS_UNVALIDATED,
            "finding_ids": [],
            "recursive_mvp_required": False,
        },
        "created_at": dogfood_roadmap._utc_now(),
    }
    spines.append(spine)

    if predecessor:
        previous = next((item for item in spines if item.get("id") == predecessor), None)
        assert previous is not None
        previous["successor_spine_id"] = spine_id
        if previous.get("status") == dogfood_roadmap.RECURSIVE_MVP_REQUIRED:
            previous["status"] = dogfood_roadmap.RECONCILED

    dogfood_roadmap.save_projection(
        repo,
        {
            "version": dogfood_roadmap.PROJECTION_VERSION,
            "updated_at": dogfood_roadmap._utc_now(),
            "spines": spines,
        },
    )
    return spine


def reconcile_projection(
    repo: Path,
    github_repo: str,
    *,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    projection = dogfood_roadmap.load_projection(repo)
    changed = False
    spines: list[dict[str, object]] = []
    for raw_spine in projection.get("spines", []):
        if not isinstance(raw_spine, dict):
            continue
        spine = dict(raw_spine)
        slices: list[dict[str, object]] = []
        for raw_slice in spine.get("slices", []):
            if not isinstance(raw_slice, dict):
                continue
            item = dict(raw_slice)
            if item.get("state") != dogfood_roadmap.SUPERSEDED:
                issue = queue_github.fetch_issue(
                    repo,
                    github_repo,
                    int(item.get("issue", 0) or 0),
                    runner=runner,
                )
                state = "complete" if issue.state == "closed" else "planned"
                if item.get("state") != state:
                    item["state"] = state
                    changed = True
            slices.append(item)
        spine["slices"] = slices

        parent = queue_github.fetch_issue(
            repo,
            github_repo,
            int(spine.get("parent_issue", 0) or 0),
            runner=runner,
        )
        deep_state = parent.state
        if spine.get("deep_issue_state") != deep_state:
            spine["deep_issue_state"] = deep_state
            spine["deep_complete"] = deep_state == "closed"
            changed = True

        relevant = [
            item
            for item in slices
            if item.get("state") != dogfood_roadmap.SUPERSEDED
        ]
        dogfoodable = bool(relevant) and all(
            item.get("state") == "complete" for item in relevant
        )
        readiness = (
            dogfood_roadmap.DOGFOODABLE
            if dogfoodable
            else dogfood_roadmap.BUILDING
        )
        if spine.get("readiness") != readiness:
            spine["readiness"] = readiness
            changed = True
        learning = spine.get("learning", {})
        learning = dict(learning) if isinstance(learning, dict) else {}
        if (
            dogfoodable
            and learning.get("state") == dogfood_roadmap.LEARNING_NOT_READY
        ):
            learning["state"] = dogfood_roadmap.LEARNING_PENDING
            spine["status"] = dogfood_roadmap.AWAITING_LEARNING
            changed = True
        spine["learning"] = learning
        spines.append(spine)

    if changed:
        projection = {
            "version": dogfood_roadmap.PROJECTION_VERSION,
            "updated_at": dogfood_roadmap._utc_now(),
            "spines": spines,
        }
        dogfood_roadmap.save_projection(repo, projection)
    return projection


def record_delivery(
    repo: Path,
    spine_id: str,
    delivery_payload: object,
) -> dict[str, object]:
    if not isinstance(delivery_payload, dict):
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood delivery payload must be a JSON object"
        )
    allowed = {
        "kind",
        "source_identity",
        "artifact_identity",
        "reference",
        "notes",
    }
    unknown = sorted(set(delivery_payload) - allowed)
    if unknown:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood delivery payload contains unsupported field(s): "
            + ", ".join(unknown)
        )
    kind = str(delivery_payload.get("kind", "") or "").strip()
    source_identity = str(
        delivery_payload.get("source_identity", "") or ""
    ).strip()
    if not kind or not source_identity:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood delivery payload requires non-empty kind and source_identity"
        )
    normalized = {
        "kind": kind,
        "source_identity": source_identity,
        "artifact_identity": str(
            delivery_payload.get("artifact_identity", "") or ""
        ).strip(),
        "reference": str(delivery_payload.get("reference", "") or "").strip(),
        "notes": str(delivery_payload.get("notes", "") or "").strip(),
    }
    evidence_id = dogfood_roadmap._canonical_sha(normalized)[:20]

    projection = dogfood_roadmap.load_projection(repo)
    spines = [
        dict(item)
        for item in projection.get("spines", [])
        if isinstance(item, dict)
    ]
    spine = next((item for item in spines if item.get("id") == spine_id), None)
    if spine is None:
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"unknown dogfood spine: {spine_id}"
        )
    if spine.get("readiness") != dogfood_roadmap.DOGFOODABLE:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "delivery evidence cannot be recorded until the vertical slice/spine is dogfoodable"
        )

    delivery = spine.get("delivery", {})
    delivery = dict(delivery) if isinstance(delivery, dict) else {}
    raw_evidence = delivery.get("evidence", [])
    evidence = [
        dict(item) for item in raw_evidence if isinstance(item, dict)
    ] if isinstance(raw_evidence, list) else []
    if not any(item.get("id") == evidence_id for item in evidence):
        evidence.append(
            {
                "id": evidence_id,
                **normalized,
                "recorded_at": dogfood_roadmap._utc_now(),
            }
        )
    delivery.update(
        {
            "state": dogfood_roadmap.DELIVERY_RECORDED,
            "evidence": evidence,
            "recorded_at": dogfood_roadmap._utc_now(),
        }
    )
    spine["delivery"] = delivery
    dogfood_roadmap.save_projection(
        repo,
        {
            "version": dogfood_roadmap.PROJECTION_VERSION,
            "updated_at": dogfood_roadmap._utc_now(),
            "spines": spines,
        },
    )
    return spine


def _finding_origin(classification: str) -> str:
    if classification == FINDING_DESIGN_PIVOT:
        return verification_obligations.ORIGIN_PIVOT
    if classification == FINDING_NEW_REQUIREMENT:
        return verification_obligations.ORIGIN_REQUIREMENT
    return verification_obligations.ORIGIN_HUMAN_DOGFOOD


def record_learning(
    repo: Path,
    spine_id: str,
    learning_payload: object,
) -> dict[str, object]:
    if not isinstance(learning_payload, dict):
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood learning payload must be a JSON object"
        )
    allowed = {
        "assumptions",
        "findings",
        "supersede_issue_ids",
        "recursive_mvp_required",
        "notes",
    }
    unknown = sorted(set(learning_payload) - allowed)
    if unknown:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood learning payload contains unsupported field(s): "
            + ", ".join(unknown)
        )
    assumptions = str(learning_payload.get("assumptions", "") or "").strip()
    if assumptions not in {
        dogfood_roadmap.ASSUMPTIONS_VALIDATED,
        dogfood_roadmap.ASSUMPTIONS_INVALIDATED,
        dogfood_roadmap.ASSUMPTIONS_MIXED,
    }:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood learning assumptions must be validated, invalidated, or mixed"
        )
    raw_findings = learning_payload.get("findings", [])
    if not isinstance(raw_findings, list):
        raise dogfood_roadmap.DogfoodRoadmapError(
            "dogfood learning findings must be a list"
        )

    projection = dogfood_roadmap.load_projection(repo)
    spines = [
        dict(item)
        for item in projection.get("spines", [])
        if isinstance(item, dict)
    ]
    spine = next((item for item in spines if item.get("id") == spine_id), None)
    if spine is None:
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"unknown dogfood spine: {spine_id}"
        )
    if spine.get("readiness") != dogfood_roadmap.DOGFOODABLE:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "post-dogfood learning cannot be reconciled until the vertical slice/spine is dogfoodable"
        )
    delivery = spine.get("delivery", {})
    delivery = dict(delivery) if isinstance(delivery, dict) else {}
    if delivery.get("state") != dogfood_roadmap.DELIVERY_RECORDED:
        raise dogfood_roadmap.DogfoodRoadmapError(
            "record delivery evidence before reconciling post-dogfood learning"
        )
    raw_delivery_evidence = delivery.get("evidence", [])
    delivery_evidence = [
        item for item in raw_delivery_evidence if isinstance(item, dict)
    ] if isinstance(raw_delivery_evidence, list) else []
    default_source_identity = (
        str(delivery_evidence[-1].get("source_identity", "") or "")
        if delivery_evidence
        else spine_id
    )

    state: dict[str, object] = {
        "RunId": f"dogfood-learning:{spine_id}",
        "IssueNumber": int(spine.get("parent_issue", 0) or 0),
        "IssueUrl": str(spine.get("parent_url", "")),
    }
    recorded_ids: list[str] = []
    for index, raw in enumerate(raw_findings):
        if not isinstance(raw, dict):
            raise dogfood_roadmap.DogfoodRoadmapError(
                f"dogfood finding {index + 1} must be an object"
            )
        classification = str(raw.get("classification", "") or "").strip()
        if classification not in FINDING_CLASSES:
            raise dogfood_roadmap.DogfoodRoadmapError(
                f"dogfood finding {index + 1} has unsupported classification {classification!r}"
            )
        finding = str(
            raw.get("finding", raw.get("message", "")) or ""
        ).strip()
        if not finding:
            raise dogfood_roadmap.DogfoodRoadmapError(
                f"dogfood finding {index + 1} must include finding/message"
            )
        values = verification_obligations.record_product_findings(
            repo,
            state,
            [
                {
                    "id": str(
                        raw.get("id", "") or f"{spine_id}-{index + 1}"
                    ),
                    "finding": finding,
                    "severity": str(raw.get("severity", "") or ""),
                    "reason": str(raw.get("reason", "") or ""),
                    "core_journey": bool(raw.get("core_journey", False)),
                    "design_pivot": classification == FINDING_DESIGN_PIVOT,
                    "classification": classification,
                    "tracking_group": spine_id,
                }
            ],
            origin_kind=_finding_origin(classification),
            source_identity=str(
                raw.get("source_identity", "") or default_source_identity
            ),
            sync_active=False,
        )
        recorded_ids.extend(
            str(item.get("id", ""))
            for item in values
            if item.get("id")
        )

    raw_supersede = learning_payload.get("supersede_issue_ids", [])
    if not isinstance(raw_supersede, list) or any(
        not isinstance(value, int) or value <= 0 for value in raw_supersede
    ):
        raise dogfood_roadmap.DogfoodRoadmapError(
            "supersede_issue_ids must contain positive issue numbers"
        )
    supersede_ids = set(raw_supersede)
    for item in spines:
        slices = item.get("slices", [])
        if not isinstance(slices, list):
            continue
        for raw_slice in slices:
            if not isinstance(raw_slice, dict):
                continue
            if int(raw_slice.get("issue", 0) or 0) in supersede_ids:
                raw_slice["state"] = dogfood_roadmap.SUPERSEDED
                raw_slice["superseded_by_learning_checkpoint"] = spine_id

    learning = spine.get("learning", {})
    learning = dict(learning) if isinstance(learning, dict) else {}
    recursive_required = bool(
        learning_payload.get("recursive_mvp_required", False)
    )
    learning.update(
        {
            "state": dogfood_roadmap.LEARNING_RECONCILED,
            "assumptions": assumptions,
            "finding_ids": recorded_ids,
            "delivery_evidence_ids": [
                str(item.get("id", ""))
                for item in delivery_evidence
                if item.get("id")
            ],
            "recursive_mvp_required": recursive_required,
            "notes": str(learning_payload.get("notes", "") or ""),
            "reconciled_at": dogfood_roadmap._utc_now(),
        }
    )
    spine["learning"] = learning
    spine["status"] = (
        dogfood_roadmap.RECURSIVE_MVP_REQUIRED
        if recursive_required
        else dogfood_roadmap.RECONCILED
    )
    dogfood_roadmap.save_projection(
        repo,
        {
            "version": dogfood_roadmap.PROJECTION_VERSION,
            "updated_at": dogfood_roadmap._utc_now(),
            "spines": spines,
        },
    )
    return spine
