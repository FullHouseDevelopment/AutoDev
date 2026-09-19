from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Callable, Mapping

from automation import (
    dogfood_roadmap,
    role_output_contract,
    role_runtime,
    workflow_stages,
)


CONTRACT = role_output_contract.RoleOutputContract(
    role="planner",
    name="autodev.dogfood-decomposition",
    version=1,
    schema_file="dogfood-decomposition-v1.json",
    output_artifact="dogfood-decomposition.json",
    semantic_validator="automation.dogfood_roadmap.validate_proposal_payload",
    fallback_parser="automation.dogfood_roadmap.validate_proposal_payload",
    native_retry_count=2,
)


def _previous_context(
    repo: Path,
    predecessor_spine_id: str,
) -> tuple[int, dict[str, object] | None]:
    if not predecessor_spine_id:
        return 1, None
    projection = dogfood_roadmap.load_projection(repo)
    spine = dogfood_roadmap.find_spine(projection, predecessor_spine_id)
    if spine is None:
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"predecessor dogfood spine {predecessor_spine_id!r} does not exist"
        )
    return int(spine.get("generation", 1) or 1) + 1, dict(spine)


def _prompt(
    parent: Mapping[str, object],
    *,
    predecessor: Mapping[str, object] | None,
) -> str:
    previous = (
        json.dumps(predecessor, indent=2, sort_keys=True, ensure_ascii=False)
        if predecessor is not None
        else "(none: this is the first dogfood decomposition)"
    )
    return f"""You are planning AutoDev dogfood geometry for one authoritative deep GitHub issue.

Your job is NOT to weaken or rewrite the parent issue. Propose the shortest real vertical path that gets a human to meaningful hands-on use.

Choose exactly one strategy:
- single-mvp: one deliberately broad vertical MVP issue when that reaches human use faster than artificial decomposition.
- spine: a short ordered chain of 2-12 vertical slices when distinct independently useful user journeys make that faster/safer.

Rules:
1. Every slice must exercise an end-to-end human-visible journey. Do not decompose by architecture layer.
2. Use real persistence, real hardware/device paths, or real external integration where those are central to the learning hypothesis. Do not use mocks that invalidate the intended UX learning.
3. Preserve the full deep contract. Put non-critical work that can legitimately wait into deferred_requirements; never silently delete it.
4. Security/privacy boundaries, destructive data-loss risks, unsafe migrations, and inability to exercise the declared core journey are not acceptable deferrals.
5. Optimize time-to-human-contact, not elegance of intermediate architecture.
6. A prior dogfood result may invalidate the current interaction model. In that case propose the next recursive feature/product MVP before hardening assumptions that are now wrong.
7. Keep issue bodies implementation-ready and explicit about the real user journey and what is intentionally deferred.
8. Return only the structured result required by the runtime schema.

Authoritative deep issue:
{json.dumps(dict(parent), indent=2, sort_keys=True, ensure_ascii=False)}

Previous dogfood spine / learning evidence:
{previous}
"""


def propose(
    repo: Path,
    github_repo: str,
    issue_number: int,
    *,
    predecessor_spine_id: str = "",
    runtime_name: str = "",
    registry=None,
    runner: Callable[..., object] = subprocess.run,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    parent = dogfood_roadmap.fetch_deep_issue(
        repo,
        github_repo,
        issue_number,
        runner=runner,
    )
    generation, predecessor = _previous_context(repo, predecessor_spine_id)

    runtime, runtime_source = role_runtime.select_runtime(
        repo,
        requested=runtime_name,
        registry=registry,
    )
    context = role_runtime.RoleInvocationContext(
        repo=repo,
        role="planner",
        prompt=_prompt(parent, predecessor=predecessor),
        phase="roadmap-dogfood",
        timeout_seconds=900,
        output_contract=CONTRACT,
    )
    result = runtime.invoke(context, runner=runner)
    if result.termination != "completed" or result.returncode not in {0, None}:
        detail = result.stderr or result.stdout or result.termination
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"dogfood decomposition planner failed: {detail}"
        )

    artifact = (
        repo
        / workflow_stages.CURRENT_DIR
        / CONTRACT.output_artifact
    )
    raw = dogfood_roadmap._read_json(artifact)
    decomposition = dogfood_roadmap.validate_proposal_payload(raw)
    proposal = dogfood_roadmap.persist_proposal(
        repo,
        parent=parent,
        decomposition=decomposition,
        generation=generation,
        predecessor_spine_id=predecessor_spine_id,
        runtime_metadata={
            "runtime": result.runtime,
            "runtime_source": runtime_source,
            "model": result.model,
            "structured_output_mode": result.structured_output_mode,
            "structured_output_state": result.structured_output_state,
            "contract": CONTRACT.identity,
        },
    )
    artifact.unlink(missing_ok=True)
    return proposal
