from __future__ import annotations

from pathlib import Path
from typing import Iterable

from area_reader import repository as area_reader_repository
from area_reader import routing as area_reader_routing
from area_reader import verification as area_reader_verification
from automation import project_native_verification, run_manifest, workflow_workspace
from automation.workflow_contract import WorkflowStageError
from automation.workflow_storage import read_json, read_text, write_json


REFRESHABLE_DISCOVERY_ARTIFACTS = (
    "detected-facts.json",
    "verification-command-groups.json",
    "recommended-command-groups.json",
)


def _routed_areas(current: Path, issue_text: str, *, preserve_existing: bool) -> tuple[list[str], dict[str, object]]:
    if preserve_existing:
        routed = read_json(current / "routed-areas.json")
        if isinstance(routed, dict):
            areas = [
                str(value)
                for value in routed.get("areas", [])
                if isinstance(value, str) and value
            ]
            if areas:
                routing = {key: value for key, value in routed.items() if key != "areas"}
                return areas, routing
    return area_reader_routing.route_areas(issue_text, "auto")


def _current_changed_paths(repo: Path, current: Path) -> list[str]:
    state = read_json(current / "state.json")
    if not isinstance(state, dict) or not state:
        return []
    try:
        changes = workflow_workspace.workspace_changes(repo, current, state)
    except (OSError, WorkflowStageError):
        return []
    return [
        str(item.get("Path", "")).replace("\\", "/")
        for item in changes
        if isinstance(item, dict) and str(item.get("Path", "")).strip()
    ]


def _project_native_discovery(
    repo: Path,
    current: Path,
    *,
    issue_text: str,
    changed_paths: Iterable[str] | None = None,
) -> dict[str, object]:
    paths = list(changed_paths) if changed_paths is not None else _current_changed_paths(repo, current)
    return project_native_verification.discover(
        repo,
        issue_text=issue_text,
        changed_paths=paths,
    )


def _append_required_groups(
    recommendations: dict[str, object],
    required_names: Iterable[str],
) -> None:
    raw = recommendations.get("recommended_command_groups", [])
    values = [str(value) for value in raw if isinstance(value, str) and value] if isinstance(raw, list) else []
    seen = set(values)
    for name in required_names:
        value = str(name)
        if value and value not in seen:
            values.append(value)
            seen.add(value)
    recommendations["recommended_command_groups"] = values


def refresh_verification_discovery(
    repo: Path,
    current: Path,
    *,
    issue_text: str = "",
    changed_paths: Iterable[str] = (),
    preserve_routing: bool = True,
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    issue_text = issue_text or read_text(current / "issue.md")
    changed_paths = tuple(changed_paths)

    files, skipped_large, skipped_unreadable = area_reader_repository.collect_repo_files(repo)
    repo_map = area_reader_repository.build_repo_map(
        repo,
        files,
        skipped_large,
        skipped_unreadable,
    )
    areas, routing = _routed_areas(
        current,
        issue_text,
        preserve_existing=preserve_routing,
    )
    facts = area_reader_repository.detect_repo_facts(repo, files, areas, routing)
    groups = area_reader_verification.build_verification_command_groups(facts, areas)
    native = _project_native_discovery(
        repo,
        current,
        issue_text=issue_text,
        changed_paths=changed_paths,
    )
    native_groups = native.get("groups", [])
    if isinstance(native_groups, list):
        groups.extend(group for group in native_groups if isinstance(group, dict))
    recommendations = area_reader_verification.recommended_command_groups(
        groups,
        issue_text=issue_text,
        changed_paths=changed_paths,
    )
    required_names = native.get("required_group_names", [])
    if isinstance(required_names, list):
        _append_required_groups(recommendations, required_names)
    recommendations["project_native_requirements"] = (
        native.get("requirements", []) if isinstance(native.get("requirements", []), list) else []
    )
    recommendations["project_native_fingerprint"] = str(native.get("fingerprint", "") or "")
    area_reader_verification.apply_recommended_command_groups(groups, recommendations)

    if not preserve_routing or not (current / "routed-areas.json").is_file():
        write_json(current / "routed-areas.json", {"areas": areas, **routing})
    write_json(current / "detected-facts.json", facts)
    write_json(current / "verification-command-groups.json", groups)
    write_json(current / "recommended-command-groups.json", recommendations)

    manifest_path = current / "run-manifest.json"
    if manifest_path.is_file():
        try:
            run_manifest.mark_stage_artifacts_refreshable(
                manifest_path,
                "repository-read",
                REFRESHABLE_DISCOVERY_ARTIFACTS,
            )
        except run_manifest.ManifestError:
            pass

    return {
        "files": files,
        "skipped_large": skipped_large,
        "skipped_unreadable": skipped_unreadable,
        "repo_map": repo_map,
        "areas": areas,
        "routing": routing,
        "facts": facts,
        "groups": groups,
        "recommendations": recommendations,
        "project_native": native,
    }


def stale_verification_reason(repo: Path, current: Path) -> str:
    repo = repo.expanduser().resolve()
    facts = read_json(current / "detected-facts.json")
    if isinstance(facts, dict):
        roots = facts.get("package_roots", [])
        if isinstance(roots, list):
            for item in roots:
                if not isinstance(item, dict):
                    continue
                root = str(item.get("root", "") or ".")
                if area_reader_repository.is_generated_relative_path(root):
                    return f"generated package root is recorded in deterministic verification discovery: {root}"

    groups = read_json(current / "verification-command-groups.json")
    recommendations = read_json(current / "recommended-command-groups.json")
    if not isinstance(groups, list) or not isinstance(recommendations, dict):
        return "deterministic verification discovery artifacts are missing or invalid"

    issue_text = read_text(current / "issue.md")
    native = _project_native_discovery(
        repo,
        current,
        issue_text=issue_text,
    )
    current_fingerprint = str(native.get("fingerprint", "") or "")
    stored_fingerprint = str(recommendations.get("project_native_fingerprint", "") or "")
    if current_fingerprint != stored_fingerprint:
        return "project-native deterministic verification requirements changed"

    required_names = {
        str(value)
        for value in native.get("required_group_names", [])
        if isinstance(value, str) and value
    } if isinstance(native.get("required_group_names", []), list) else set()
    recommended_names = {
        str(value)
        for value in recommendations.get("recommended_command_groups", [])
        if isinstance(value, str) and value
    }
    group_names = {
        str(group.get("name", ""))
        for group in groups
        if isinstance(group, dict) and str(group.get("name", ""))
    }
    missing_native = sorted(required_names - recommended_names) or sorted(required_names - group_names)
    if missing_native:
        return "required project-native verification group is missing: " + ", ".join(missing_native)

    recommended = recommended_names
    for group in groups:
        if not isinstance(group, dict) or str(group.get("name", "")) not in recommended:
            continue
        commands = group.get("commands", [])
        if not isinstance(commands, list):
            return f"recommended verification group has invalid commands: {group.get('name', '')}"
        for command in commands:
            if not isinstance(command, dict):
                continue
            raw_cwd = str(command.get("cwd", ".") or ".").replace("\\", "/")
            if area_reader_repository.is_generated_relative_path(raw_cwd):
                return f"recommended verification command points into generated output: {raw_cwd}"
            candidate = (repo / raw_cwd).resolve()
            try:
                candidate.relative_to(repo)
            except ValueError:
                continue
            if not candidate.is_dir():
                return f"recommended verification command cwd no longer exists: {raw_cwd}"
    return ""


def refresh_stale_verification_discovery(
    repo: Path,
    current: Path,
    *,
    issue_text: str = "",
) -> str:
    reason = stale_verification_reason(repo, current)
    if not reason:
        return ""
    refresh_verification_discovery(
        repo,
        current,
        issue_text=issue_text,
        changed_paths=_current_changed_paths(repo, current),
        preserve_routing=True,
    )
    remaining = stale_verification_reason(repo, current)
    if remaining:
        raise ValueError(
            "deterministic verification discovery remains stale after refresh: " + remaining
        )
    return reason
