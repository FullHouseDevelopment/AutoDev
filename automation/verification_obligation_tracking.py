from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Callable, Iterable, Mapping

from automation import verification_obligations as core, workflow_stages
from automation.workflow_commands import gh, gh_json


FOLLOW_UP_MANUAL = "manual"
FOLLOW_UP_GITHUB = "github-issue"


def follow_up_policy(repo: Path) -> str:
    path = repo.expanduser().resolve() / ".autodev" / "repo.json"
    value = core._read_json(path)
    if not isinstance(value, dict):
        return FOLLOW_UP_MANUAL
    raw = value.get("deferred_obligations")
    if raw is None:
        return FOLLOW_UP_MANUAL
    if not isinstance(raw, dict):
        raise core.VerificationObligationError(
            f"deferred_obligations in {path} must be an object"
        )
    unknown = sorted(str(key) for key in raw if key not in {"follow_up"})
    if unknown:
        raise core.VerificationObligationError(
            f"unsupported deferred_obligations field(s) in {path}: {', '.join(unknown)}"
        )
    mode = str(raw.get("follow_up", FOLLOW_UP_MANUAL) or "").strip().casefold()
    if mode not in {FOLLOW_UP_MANUAL, FOLLOW_UP_GITHUB}:
        raise core.VerificationObligationError(
            f"unsupported deferred_obligations.follow_up in {path}: {mode!r}; "
            f"expected {FOLLOW_UP_MANUAL!r} or {FOLLOW_UP_GITHUB!r}"
        )
    return mode


def _stdout_text(completed: object) -> str:
    value = getattr(completed, "stdout", "")
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value or "")


def _issue_body(state: Mapping[str, object], records: list[dict[str, object]]) -> str:
    lines = [
        "AutoDev grouped these deferred semantic obligations from one verifier/run identity.",
        "",
        f"Origin issue: #{core._issue_number(state)}",
        f"Origin issue URL: {str(state.get('IssueUrl', '') or '')}",
        f"Origin run: {core._run_id(state)}",
        f"Origin PR: {str(state.get('PrUrl', '') or '')}",
        "",
        "## Deferred obligations",
        "",
    ]
    for item in records:
        lines.extend(
            [
                f"### {item.get('id', '')}",
                f"- Severity: {item.get('severity', '')}",
                f"- Lifecycle: {item.get('lifecycle', '')}",
                f"- Work exposure: {item.get('work_exposure', '')}",
                f"- Deferred by: {item.get('deferred_by', '')}",
                f"- Deferral reason: {item.get('reason', '')}",
                f"- Human override reason: {item.get('human_override_reason', '')}",
                f"- Verifier result SHA-256: {item.get('verifier_result_sha256', '')}",
                f"- Source identity: {item.get('source_identity', '')}",
            ]
        )
        if item.get("criterion"):
            lines.append(f"- Criterion: {item.get('criterion', '')}")
        if item.get("finding"):
            lines.append(f"- Finding: {item.get('finding', '')}")
        if item.get("path"):
            lines.append(f"- Path: {item.get('path', '')}")
        evidence = item.get("evidence", [])
        if isinstance(evidence, list) and evidence:
            lines.append("- Evidence:")
            lines.extend(f"  - {value}" for value in evidence)
        lines.append("")
    lines.extend(
        [
            "This issue tracks follow-up only. Its existence does not make a critical finding deferrable.",
            "",
        ]
    )
    return "\n".join(lines)


def _persist_tracking(
    repo: Path,
    group: str,
    *,
    issue_number: int,
    url: str,
) -> None:
    ledger = core._load_ledger(repo)
    values = [
        dict(item)
        for item in ledger.get("obligations", [])
        if isinstance(item, dict)
    ]
    for item in values:
        if str(item.get("tracking_group", "")) != group:
            continue
        item["tracking_issue"] = issue_number
        item["tracking_url"] = url
    core._save_ledger(
        repo,
        {
            "schema_version": core.SCHEMA_VERSION,
            "updated_at": core._utc_now(),
            "obligations": values,
        },
    )


def ensure_follow_up_issues(
    repo: Path,
    state: dict[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> list[str]:
    repo = repo.expanduser().resolve()
    if follow_up_policy(repo) != FOLLOW_UP_GITHUB:
        return []
    repo_full = core._repo_full(state)
    if not repo_full:
        raise core.VerificationObligationError(
            "cannot create deferred-obligation follow-up issue without RepoFullName"
        )

    current_semantic = [
        item
        for item in core._current_records(repo, state)
        if item.get("kind") == core.KIND_SEMANTIC
        and item.get("status") == core.OPEN
    ]
    groups: dict[str, list[dict[str, object]]] = {}
    for item in current_semantic:
        group = str(item.get("tracking_group", "") or "")
        if group:
            groups.setdefault(group, []).append(item)

    urls: list[str] = []
    current = repo / workflow_stages.CURRENT_DIR
    for group, records in groups.items():
        existing = next(
            (
                item for item in records
                if int(item.get("tracking_issue", 0) or 0) > 0
                and str(item.get("tracking_url", "") or "")
            ),
            None,
        )
        if existing is not None:
            urls.append(str(existing.get("tracking_url", "")))
            continue

        body_path = current / f"semantic-follow-up-{group[:12]}.md"
        body_path.write_text(_issue_body(state, records), encoding="utf-8")
        title = (
            f"Deferred semantic hardening from #{core._issue_number(state)}"
            if core._issue_number(state)
            else "Deferred semantic hardening"
        )
        completed = gh(
            repo,
            [
                "issue",
                "create",
                "--repo",
                repo_full,
                "--title",
                title,
                "--body-file",
                str(body_path),
            ],
            runner=runner,
        )
        output = _stdout_text(completed)
        candidates = [
            line.strip()
            for line in output.splitlines()
            if line.strip()
        ]
        url = candidates[-1] if candidates else ""
        match = re.search(r"/issues/(\d+)(?:$|[?#])", url)
        if not match:
            raise core.VerificationObligationError(
                "gh issue create did not return an issue URL for deferred obligations"
            )
        tracking_issue = int(match.group(1))
        _persist_tracking(
            repo,
            group,
            issue_number=tracking_issue,
            url=url,
        )
        urls.append(url)

    core.sync_current_state(repo, state)
    return urls


def link_tracking_issue(
    repo: Path,
    obligation_ids: Iterable[str],
    *,
    issue_number: int,
    url: str,
) -> None:
    ids = {value.strip() for value in obligation_ids if value.strip()}
    ledger = core._load_ledger(repo)
    values = [
        dict(item)
        for item in ledger.get("obligations", [])
        if isinstance(item, dict)
    ]
    for item in values:
        if str(item.get("id", "")) in ids:
            item["tracking_issue"] = int(issue_number)
            item["tracking_url"] = url.strip()
    core._save_ledger(
        repo,
        {
            "schema_version": core.SCHEMA_VERSION,
            "updated_at": core._utc_now(),
            "obligations": values,
        },
    )


def reconcile_tracking_issues(
    repo: Path,
    state: Mapping[str, object],
    *,
    runner: Callable[..., object] = subprocess.run,
) -> list[dict[str, object]]:
    repo_full = core._repo_full(state)
    if not repo_full:
        return []
    open_values = core.open_obligations(repo)
    by_issue: dict[int, list[str]] = {}
    for item in open_values:
        issue = int(item.get("tracking_issue", 0) or 0)
        if issue:
            by_issue.setdefault(issue, []).append(str(item.get("id", "")))

    resolved: list[dict[str, object]] = []
    for issue, ids in by_issue.items():
        value = gh_json(
            repo,
            [
                "issue",
                "view",
                str(issue),
                "--repo",
                repo_full,
                "--json",
                "state,url",
            ],
            runner=runner,
        )
        if str(value.get("state", "") or "").casefold() != "closed":
            continue
        resolved.extend(
            core.resolve(
                repo,
                ids,
                reason=f"tracking GitHub issue #{issue} is closed",
            )
        )
    return resolved


