"""Diagnostic bundles: the classifier's raw inputs, kept for reading.

Every non-merged watch outcome writes one JSON bundle outside the
repository (never inside, it would get committed; never to the
forge, it would get published). The premise: a predicate vector
nobody has seen before IS an uncovered case, findable by reading
bundles instead of waiting for it to bite again.

Each section guards itself, so a token that cannot read one still
yields the rest, and the section's own error is recorded as data;
for a scope-poor token that error is itself the diagnosis.
Structural fields only, no bodies, no tokens: a bundle is shareable.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery.forge import Repository
from livery.workshop._verdict import Verdict

#: Bundle format version; bump on any structural change.
SCHEMA = 1

#: Newest bundles kept; older ones are pruned on each write.
KEEP = 20


def diagnostics_dir() -> Path:
    """Where bundles live: the runner's data directory, never the repo."""
    import footman

    return footman.data_dir() / "diagnostics"


def _safely(section: Callable[[], Any]) -> Any:
    """A section's value, or its own error recorded as data."""
    try:
        return section()
    except (Exception, SystemExit) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def gather_bundle(repo: Repository, verdict: Verdict, *, branch: str) -> dict[str, Any]:
    """The bundle's content for one verdict on one branch."""

    def _pr() -> Any:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            return None
        return {
            "number": pr.number,
            "state": pr.state,
            "merged": pr.merged,
            "head_sha": pr.head_sha,
            "base_branch": pr.base_branch,
            "author": pr.author,
        }

    def _protection() -> Any:
        protection = repo.protection("main")
        return None if protection is None else asdict(protection)

    def _reviews() -> Any:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            return []
        return [asdict(review) for review in repo.pr.reviews(pr.number)]

    def _schedule() -> Any:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            return []
        return [asdict(event) for event in repo.pr.schedule_events(pr.number)]

    def _status() -> Any:
        pr = repo.pr.find_by_head(branch, state="all")
        if pr is None:
            return None
        status = repo.checks.status(pr.head_sha)
        return {"state": status.state, "contexts": status.contexts}

    return {
        "schema": SCHEMA,
        "written": datetime.now(UTC).isoformat(timespec="seconds"),
        "branch": branch,
        "verdict": {
            "state": verdict.state,
            "exit_code": verdict.exit_code,
            "detail": verdict.detail,
            "pr_number": verdict.pr_number,
        },
        "pull_request": _safely(_pr),
        "protection": _safely(_protection),
        "reviews": _safely(_reviews),
        "schedule_events": _safely(_schedule),
        "combined_status": _safely(_status),
    }


def record(repo: Repository, verdict: Verdict, *, branch: str) -> Path | None:
    """Write one bundle; the path, or None when even writing failed.

    Never raises: the verdict being recorded is the thing worth
    seeing, and a failed recording must not take it down.
    """
    try:
        directory = diagnostics_dir()
        directory.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        safe_branch = branch.replace("/", "-")
        path = directory / f"{stamp}-{safe_branch}-{verdict.state}.json"
        path.write_text(
            json.dumps(gather_bundle(repo, verdict, branch=branch), indent=2),
            encoding="utf-8",
        )
        bundles = sorted(directory.glob("*.json"))
        for old in bundles[:-KEEP]:
            old.unlink(missing_ok=True)
        return path
    except (Exception, SystemExit):
        return None
