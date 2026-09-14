from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath
from typing import Iterable

from automation.workflow_contract import FAILURE_SETUP, WorkflowStageError


REPO_CONFIG = Path(".autodev") / "repo.json"
_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_IGNORED_PARTS = {
    ".git",
    ".autodev-run",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".turbo",
    "bin",
    "obj",
    "node_modules",
    "dist",
    "build",
    "coverage",
}
_GODOT_RELEVANT_SUFFIXES = {".gd", ".tscn", ".tres"}


def _normalized_paths(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        str(value).replace("\\", "/").removeprefix("./").strip()
        for value in values
        if str(value).strip()
    )


def _ignored(relative: str) -> bool:
    return any(part.casefold() in _IGNORED_PARTS for part in PurePosixPath(relative).parts)


def _godot_markers(repo: Path) -> list[str]:
    markers: list[str] = []
    for path in repo.rglob("project.godot"):
        if not path.is_file():
            continue
        try:
            relative = path.resolve().relative_to(repo.resolve()).as_posix()
        except (OSError, ValueError):
            continue
        if not _ignored(relative):
            markers.append(relative)
    return sorted(set(markers))


def _path_under_root(relative: str, root: str) -> bool:
    if root == ".":
        return True
    return relative == root or relative.startswith(root.rstrip("/") + "/")


def _godot_relevant(
    marker: str,
    *,
    issue_text: str,
    changed_paths: tuple[str, ...],
) -> bool:
    if "godot" in issue_text.casefold():
        return True
    root = PurePosixPath(marker).parent.as_posix()
    root = "." if root in {"", "."} else root
    for relative in changed_paths:
        if not _path_under_root(relative, root):
            continue
        path = PurePosixPath(relative)
        if path.name == "project.godot" or path.suffix.casefold() in _GODOT_RELEVANT_SUFFIXES:
            return True
    return False


def _group_name(prefix: str, identity: str) -> str:
    if identity in {"", "."}:
        return prefix
    digest = hashlib.sha256(identity.encode("utf-8", errors="replace")).hexdigest()[:8]
    return f"{prefix}-{digest}"


def _godot_requirements(
    repo: Path,
    *,
    issue_text: str,
    changed_paths: tuple[str, ...],
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    groups: list[dict[str, object]] = []
    requirements: list[dict[str, object]] = []
    for marker in _godot_markers(repo):
        if not _godot_relevant(marker, issue_text=issue_text, changed_paths=changed_paths):
            continue
        root = PurePosixPath(marker).parent.as_posix()
        root = "." if root in {"", "."} else root
        name = _group_name("project-native-godot", root)
        command = {
            "label": f"Load Godot project at {root} headlessly",
            "cwd": root,
            "argv": ["godot", "--headless", "--path", ".", "--quit-after", "1"],
            "optional": False,
        }
        reason = (
            f"Detected {marker} and the current issue/source changes require Godot project validation."
        )
        groups.append(
            {
                "name": name,
                "description": "Load the active Godot project headlessly as required project-native verification.",
                "recommended": True,
                "reason": reason,
                "manual": False,
                "commands": [command],
            }
        )
        requirements.append(
            {
                "id": f"builtin:godot:{root}",
                "source": "builtin-detector",
                "group": name,
                "marker": marker,
                "reason": reason,
                "cwd": root,
                "argv": list(command["argv"]),
                "required": True,
            }
        )
    return groups, requirements


def _read_repo_config(repo: Path) -> dict[str, object]:
    path = repo / REPO_CONFIG
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkflowStageError(
            f"project-native verification could not read repository config {path}: {exc}",
            classification=FAILURE_SETUP,
        ) from exc
    if not isinstance(value, dict):
        raise WorkflowStageError(
            f"project-native verification repository config must be an object: {path}",
            classification=FAILURE_SETUP,
        )
    return value


def _configured_requirements(
    repo: Path,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    config = _read_repo_config(repo)
    raw_policy = config.get("verification")
    if raw_policy is None:
        return [], []
    if not isinstance(raw_policy, dict):
        raise WorkflowStageError(
            f"verification in {repo / REPO_CONFIG} must be an object",
            classification=FAILURE_SETUP,
        )
    raw_commands = raw_policy.get("required_commands", [])
    if not isinstance(raw_commands, list):
        raise WorkflowStageError(
            f"verification.required_commands in {repo / REPO_CONFIG} must be a list",
            classification=FAILURE_SETUP,
        )

    groups: list[dict[str, object]] = []
    requirements: list[dict[str, object]] = []
    seen: set[str] = set()
    for index, raw in enumerate(raw_commands, start=1):
        if not isinstance(raw, dict):
            raise WorkflowStageError(
                f"verification.required_commands entry {index} must be an object",
                classification=FAILURE_SETUP,
            )
        name = str(raw.get("name", "")).strip().casefold()
        if not _NAME_RE.fullmatch(name):
            raise WorkflowStageError(
                f"verification.required_commands entry {index} has invalid name {name!r}; use lowercase letters, digits, '.', '_' or '-'",
                classification=FAILURE_SETUP,
            )
        if name in seen:
            raise WorkflowStageError(
                f"verification.required_commands contains duplicate name: {name}",
                classification=FAILURE_SETUP,
            )
        seen.add(name)
        argv_raw = raw.get("argv", [])
        if not isinstance(argv_raw, list) or not argv_raw or not all(
            isinstance(value, str) and value.strip() for value in argv_raw
        ):
            raise WorkflowStageError(
                f"verification.required_commands {name} must define a non-empty string argv list",
                classification=FAILURE_SETUP,
            )
        cwd = str(raw.get("cwd", ".") or ".").replace("\\", "/").strip() or "."
        reason = str(raw.get("reason", "") or "Repository policy declares this project-native command as required.")
        group_name = f"project-native-config-{name}"
        command = {
            "label": str(raw.get("label", "") or f"Run required project-native check {name}"),
            "cwd": cwd,
            "argv": [str(value) for value in argv_raw],
            "optional": False,
        }
        groups.append(
            {
                "name": group_name,
                "description": "Run a repository-declared required project-native verification command.",
                "recommended": True,
                "reason": reason,
                "manual": False,
                "commands": [command],
            }
        )
        requirements.append(
            {
                "id": f"repo-config:{name}",
                "source": REPO_CONFIG.as_posix(),
                "group": group_name,
                "reason": reason,
                "cwd": cwd,
                "argv": list(command["argv"]),
                "required": True,
            }
        )
    return groups, requirements


def _fingerprint(requirements: list[dict[str, object]]) -> str:
    if not requirements:
        return ""
    payload = json.dumps(
        requirements,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8", errors="replace")).hexdigest()


def discover(
    repo: Path,
    *,
    issue_text: str = "",
    changed_paths: Iterable[str] = (),
) -> dict[str, object]:
    repo = repo.expanduser().resolve()
    normalized_changes = _normalized_paths(changed_paths)
    configured_groups, configured_requirements = _configured_requirements(repo)
    godot_groups, godot_requirements = _godot_requirements(
        repo,
        issue_text=issue_text,
        changed_paths=normalized_changes,
    )
    groups = [*configured_groups, *godot_groups]
    requirements = [*configured_requirements, *godot_requirements]
    names = [str(group["name"]) for group in groups]
    if len(names) != len(set(names)):
        raise WorkflowStageError(
            "project-native verification produced duplicate command-group names",
            classification=FAILURE_SETUP,
        )
    return {
        "groups": groups,
        "required_group_names": names,
        "requirements": requirements,
        "fingerprint": _fingerprint(requirements),
    }
