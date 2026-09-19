from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Callable, TextIO

from automation import cli_help, verification_obligation_tracking, verification_obligations
from automation.queue_github import fetch_issue, resolve_github_repo


def register_help() -> None:
    cli_help.HELP.setdefault(
        ("obligations",),
        cli_help.HelpEntry(
            usage="autodev obligations <status|reconcile|resolve|supersede|link> [options]",
            summary="Inspect and reconcile durable deferred verification/product obligations.",
            description=(
                "The obligation ledger survives replacement of the active run directory. "
                "Verifier debt, platform verification debt, and future human/runtime product-learning "
                "records retain distinct provenance rather than being flattened into one severity model."
            ),
            subcommands=(
                ("status", "List durable open obligations and tracking links."),
                ("reconcile", "Resolve obligations whose linked GitHub issue is now closed."),
                ("resolve", "Explicitly mark one or more obligations resolved."),
                ("supersede", "Explicitly mark one or more obligations superseded."),
                ("link", "Link one or more obligations to an existing GitHub issue."),
            ),
            examples=(
                "autodev obligations status",
                "autodev obligations reconcile",
                "autodev obligations resolve semantic-abc --reason criterion-verified",
                "autodev obligations supersede product-abc --reason design-replaced-by-MVP2",
                "autodev obligations link semantic-abc --issue 412",
            ),
        ),
    )
    cli_help.KNOWN_TOP_LEVEL.add("obligations")
    groups: list[tuple[str, tuple[tuple[str, str], ...]]] = []
    for title, rows in cli_help.TOP_LEVEL_GROUPS:
        if title == "Automation and operations" and not any(
            name == "obligations" for name, _description in rows
        ):
            rows = (
                ("obligations", "Inspect and reconcile durable deferred obligations."),
                *rows,
            )
        groups.append((title, rows))
    cli_help.TOP_LEVEL_GROUPS = tuple(groups)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="autodev obligations")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--github-repo", default="", help="GitHub repository as owner/name. Default: detect from Git remote.")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")

    reconcile = sub.add_parser("reconcile")
    reconcile.add_argument("--json", action="store_true")

    resolve = sub.add_parser("resolve")
    resolve.add_argument("ids", nargs="+")
    resolve.add_argument("--reason", required=True)

    supersede = sub.add_parser("supersede")
    supersede.add_argument("ids", nargs="+")
    supersede.add_argument("--reason", required=True)

    link = sub.add_parser("link")
    link.add_argument("ids", nargs="+")
    link.add_argument("--issue", type=int, required=True)
    return parser


def _repo(value: str) -> Path:
    repo = Path(value).expanduser().resolve()
    if not repo.is_dir():
        raise verification_obligations.VerificationObligationError(
            f"repository directory does not exist: {repo}"
        )
    return repo


def _print_status(repo: Path, *, as_json: bool, out: TextIO) -> None:
    values = verification_obligations.open_obligations(repo)
    if as_json:
        print(
            json.dumps(
                {
                    "ledger": str(repo / verification_obligations.LEDGER_PATH),
                    "open_count": len(values),
                    "obligations": values,
                },
                sort_keys=True,
            ),
            file=out,
        )
        return
    for line in verification_obligations.status_lines(repo):
        print(line, file=out)


def run_cli(
    argv: list[str] | None = None,
    *,
    runner: Callable[..., object] = subprocess.run,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    args = _parser().parse_args(argv)
    try:
        repo = _repo(args.repo)
        if args.command == "status":
            _print_status(repo, as_json=bool(args.json), out=out)
            return 0

        if args.command == "reconcile":
            github_repo = resolve_github_repo(
                repo,
                explicit=str(args.github_repo or ""),
                runner=runner,
            )
            changed = verification_obligation_tracking.reconcile_tracking_issues(
                repo,
                {"RepoFullName": github_repo},
                runner=runner,
            )
            if args.json:
                print(
                    json.dumps(
                        {"resolved_count": len(changed), "obligations": changed},
                        sort_keys=True,
                    ),
                    file=out,
                )
            else:
                print(f"Reconciled deferred obligations: {len(changed)} resolved", file=out)
            return 0

        if args.command in {"resolve", "supersede"}:
            reason = str(args.reason or "").strip()
            if not reason:
                raise verification_obligations.VerificationObligationError(
                    "--reason must not be empty"
                )
            if args.command == "resolve":
                changed = verification_obligations.resolve(
                    repo,
                    args.ids,
                    reason=reason,
                )
            else:
                changed = verification_obligations.supersede(
                    repo,
                    args.ids,
                    reason=reason,
                )
            print(
                f"{args.command.capitalize()}d deferred obligations: {len(changed)}",
                file=out,
            )
            return 0

        if args.command == "link":
            if int(args.issue or 0) <= 0:
                raise verification_obligations.VerificationObligationError(
                    "--issue must be a positive integer"
                )
            github_repo = resolve_github_repo(
                repo,
                explicit=str(args.github_repo or ""),
                runner=runner,
            )
            issue = fetch_issue(repo, github_repo, int(args.issue), runner=runner)
            verification_obligation_tracking.link_tracking_issue(
                repo,
                args.ids,
                issue_number=issue.number,
                url=issue.url,
            )
            print(
                f"Linked {len(args.ids)} obligation(s) to #{issue.number}: {issue.url}",
                file=out,
            )
            return 0
    except Exception as exc:
        print(f"autodev obligations: {exc}", file=err)
        return 2

    print("autodev obligations: unsupported command", file=err)
    return 2


def main() -> int:
    return run_cli()


if __name__ == "__main__":
    raise SystemExit(main())
