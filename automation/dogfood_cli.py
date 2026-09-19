from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import cli_help, dogfood_application, dogfood_decomposition, dogfood_roadmap
from automation.queue_github import resolve_github_repo


def register_help() -> None:
    cli_help.HELP.setdefault(
        ("roadmap",),
        cli_help.HelpEntry(
            usage="autodev roadmap dogfood <command> [options]",
            summary="Plan and reconcile recursive vertical dogfood geometry.",
            description=(
                "The tracked deep roadmap remains authoritative. Dogfood commands create a separate "
                "reviewable projection for single broad MVPs or short vertical spines, then reconcile "
                "real human-use learning without silently rewriting the parent contract."
            ),
            subcommands=(
                ("dogfood propose", "Generate a reviewable vertical MVP/spine proposal."),
                ("dogfood approve", "Explicitly approve a reviewed proposal."),
                ("dogfood apply", "Create approved slice issues and write the dogfood projection."),
                ("dogfood reconcile", "Refresh slice/deep completion from GitHub."),
                ("dogfood deliver", "Attach exact build/session evidence delivered for dogfood."),
                ("dogfood learn", "Record post-dogfood learning and roadmap supersession."),
                ("dogfood status", "Inspect deep/dogfood completion and learning state."),
            ),
            examples=(
                "autodev roadmap dogfood propose 123",
                "autodev roadmap dogfood approve dogfood-123-g1-abc123",
                "autodev roadmap dogfood apply dogfood-123-g1-abc123 --manage",
                "autodev roadmap dogfood reconcile",
                "autodev roadmap dogfood deliver dogfood-123-g1-abc123 --input delivery.json",
                "autodev roadmap dogfood learn dogfood-123-g1-abc123 --input findings.json",
            ),
            privacy_note=cli_help.CLOUD_MODEL_NOTE,
        ),
    )
    cli_help.KNOWN_TOP_LEVEL.add("roadmap")
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for title, rows in cli_help.TOP_LEVEL_GROUPS:
        if title == "Automation and operations" and not any(
            name == "roadmap" for name, _description in rows
        ):
            rows = (("roadmap", "Plan/reconcile deep roadmap and dogfood geometry."), *rows)
        groups.append((title, rows))
    cli_help.TOP_LEVEL_GROUPS = tuple(groups)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev roadmap")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--github-repo", default="")
    roadmap = parser.add_subparsers(dest="area", required=True)
    dogfood = roadmap.add_parser("dogfood")
    commands = dogfood.add_subparsers(dest="command", required=True)

    propose = commands.add_parser("propose")
    propose.add_argument("issue", type=int)
    propose.add_argument("--predecessor-spine", default="")
    propose.add_argument("--runtime", default="")
    propose.add_argument("--json", action="store_true")

    approve = commands.add_parser("approve")
    approve.add_argument("proposal")
    approve.add_argument("--json", action="store_true")

    apply = commands.add_parser("apply")
    apply.add_argument("proposal")
    apply.add_argument("--manage", action="store_true")
    apply.add_argument("--json", action="store_true")

    reconcile = commands.add_parser("reconcile")
    reconcile.add_argument("--json", action="store_true")

    deliver = commands.add_parser("deliver")
    deliver.add_argument("spine")
    deliver.add_argument("--input", required=True)
    deliver.add_argument("--json", action="store_true")

    learn = commands.add_parser("learn")
    learn.add_argument("spine")
    learn.add_argument("--input", required=True)
    learn.add_argument("--json", action="store_true")

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    return parser


def _repo(value: str) -> Path:
    repo = Path(value).expanduser().resolve()
    if not repo.is_dir():
        raise dogfood_roadmap.DogfoodRoadmapError(
            f"repository directory does not exist: {repo}"
        )
    return repo


def _github_repo(repo: Path, explicit: str, *, runner: Callable[..., object]) -> str:
    return resolve_github_repo(repo, explicit=explicit, runner=runner)


def _status_payload(repo: Path) -> dict[str, object]:
    projection = dogfood_roadmap.load_projection(repo)
    spines = [
        item for item in projection.get("spines", []) if isinstance(item, dict)
    ]
    return {
        "projection": dogfood_roadmap.PROJECTION_PATH.as_posix(),
        "priority_active": dogfood_roadmap.dogfood_priority_active(repo),
        "next_slice": dogfood_roadmap.active_next_slice_issue(projection),
        "spines": spines,
    }


def _render_status(payload: dict[str, object]) -> str:
    lines = [
        f"Dogfood projection: {payload['projection']}",
        f"Dogfood scheduler priority active: {'yes' if payload['priority_active'] else 'no'}",
    ]
    next_slice = payload.get("next_slice")
    if isinstance(next_slice, tuple):
        lines.append(f"Next dogfood slice: #{next_slice[0]} spine={next_slice[1]}")
    else:
        lines.append("Next dogfood slice: (none)")
    spines = payload.get("spines", [])
    if isinstance(spines, list):
        for spine in spines:
            if not isinstance(spine, dict):
                continue
            learning = spine.get("learning", {})
            learning = learning if isinstance(learning, dict) else {}
            delivery = spine.get("delivery", {})
            delivery = delivery if isinstance(delivery, dict) else {}
            evidence = delivery.get("evidence", [])
            evidence_count = len(evidence) if isinstance(evidence, list) else 0
            lines.append(
                "Spine "
                f"{spine.get('id', '')}: generation={spine.get('generation', 1)} "
                f"status={spine.get('status', '')} readiness={spine.get('readiness', '')} "
                f"delivery={delivery.get('state', dogfood_roadmap.DELIVERY_NOT_RECORDED)}"
                f"/{evidence_count} "
                f"deep={spine.get('deep_issue_state', '')} "
                f"learning={learning.get('state', '')}/{learning.get('assumptions', '')}"
            )
    return "\n".join(lines)


def run_cli(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    runtime_registry=None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        repo = _repo(args.repo)
        if args.area != "dogfood":
            raise dogfood_roadmap.DogfoodRoadmapError(
                f"unsupported roadmap area: {args.area}"
            )

        if args.command == "propose":
            if int(args.issue) <= 0:
                raise dogfood_roadmap.DogfoodRoadmapError(
                    "dogfood parent issue must be positive"
                )
            github_repo = _github_repo(repo, str(args.github_repo or ""), runner=runner)
            proposal = dogfood_decomposition.propose(
                repo,
                github_repo,
                int(args.issue),
                predecessor_spine_id=str(args.predecessor_spine or ""),
                runtime_name=str(args.runtime or ""),
                registry=runtime_registry,
                runner=runner,
            )
            if args.json:
                print(json.dumps(proposal, sort_keys=True), file=out)
            else:
                path = dogfood_roadmap.proposal_path(repo, str(proposal["proposal_id"]))
                decomposition = proposal.get("decomposition", {})
                decomposition = decomposition if isinstance(decomposition, dict) else {}
                print(f"Dogfood proposal: {proposal['proposal_id']}", file=out)
                print(f"Review file: {path}", file=out)
                slices = decomposition.get("slices", [])
                print(
                    f"Strategy: {decomposition.get('strategy', '')}; "
                    f"slices={len(slices) if isinstance(slices, list) else 0}",
                    file=out,
                )
                print(
                    "No GitHub issue was created. Review/edit the proposal, then explicitly approve it.",
                    file=out,
                )
            return 0

        if args.command == "approve":
            proposal = dogfood_roadmap.approve_proposal(repo, args.proposal)
            if args.json:
                print(json.dumps(proposal, sort_keys=True), file=out)
            else:
                print(f"Approved dogfood proposal: {proposal['proposal_id']}", file=out)
                print("Approval does not create issues; apply remains an explicit action.", file=out)
            return 0

        if args.command == "apply":
            github_repo = _github_repo(repo, str(args.github_repo or ""), runner=runner)
            spine = dogfood_application.apply_proposal(
                repo,
                github_repo,
                args.proposal,
                manage=bool(args.manage),
                runner=runner,
            )
            if args.json:
                print(json.dumps(spine, sort_keys=True), file=out)
            else:
                print(f"Dogfood spine: {spine['id']}", file=out)
                print(f"Projection updated: {repo / dogfood_roadmap.PROJECTION_PATH}", file=out)
                for item in spine.get("slices", []):
                    if isinstance(item, dict):
                        print(
                            f"  {item.get('order')}. #{item.get('issue')} {item.get('title')}",
                            file=out,
                        )
                print(
                    "Commit the projection file so scheduler workers see the same dogfood geometry.",
                    file=out,
                )
            return 0

        if args.command == "reconcile":
            github_repo = _github_repo(repo, str(args.github_repo or ""), runner=runner)
            dogfood_application.reconcile_projection(
                repo,
                github_repo,
                runner=runner,
            )
            payload = _status_payload(repo)
            print(
                json.dumps(payload, sort_keys=True) if args.json else _render_status(payload),
                file=out,
            )
            return 0

        if args.command == "deliver":
            path = Path(args.input).expanduser()
            if not path.is_absolute():
                path = repo / path
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise dogfood_roadmap.DogfoodRoadmapError(
                    f"cannot read dogfood delivery file {path}: {exc}"
                ) from exc
            spine = dogfood_application.record_delivery(
                repo,
                str(args.spine),
                value,
            )
            if args.json:
                print(json.dumps(spine, sort_keys=True), file=out)
            else:
                delivery = spine.get("delivery", {})
                delivery = delivery if isinstance(delivery, dict) else {}
                evidence = delivery.get("evidence", [])
                evidence_count = len(evidence) if isinstance(evidence, list) else 0
                print(
                    f"Dogfood delivery recorded: {spine['id']} evidence={evidence_count}",
                    file=out,
                )
            return 0

        if args.command == "learn":
            path = Path(args.input).expanduser()
            if not path.is_absolute():
                path = repo / path
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise dogfood_roadmap.DogfoodRoadmapError(
                    f"cannot read dogfood learning file {path}: {exc}"
                ) from exc
            spine = dogfood_application.record_learning(repo, str(args.spine), value)
            if args.json:
                print(json.dumps(spine, sort_keys=True), file=out)
            else:
                learning = spine.get("learning", {})
                learning = learning if isinstance(learning, dict) else {}
                print(
                    f"Dogfood learning reconciled: {spine['id']} "
                    f"assumptions={learning.get('assumptions', '')} "
                    f"recursive-mvp={'yes' if learning.get('recursive_mvp_required') else 'no'}",
                    file=out,
                )
            return 0

        if args.command == "status":
            payload = _status_payload(repo)
            print(
                json.dumps(payload, sort_keys=True) if args.json else _render_status(payload),
                file=out,
            )
            return 0
    except Exception as exc:
        print(f"autodev roadmap: {exc}", file=err)
        return 2

    print("autodev roadmap: unsupported command", file=err)
    return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
