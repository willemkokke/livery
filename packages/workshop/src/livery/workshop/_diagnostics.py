"""Diagnostic bundles: the classifier's raw inputs, kept for reading.

Every non-merged watch outcome writes one bundle as a row of a local
series of the state store ([livery.workshop._state][]): in the
checkout's git directory, never in its working tree (it would get
committed) and never on the forge (it would get published). The
premise: a predicate vector nobody has seen before IS an uncovered
case, findable by reading bundles instead of waiting for it to bite
again.

Each section guards itself, so a token that cannot read one still
yields the rest, and the section's own error is recorded as data;
for a scope-poor token that error is itself the diagnosis.
Structural fields only, no bodies, no tokens: a bundle is shareable.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from livery.forge import Repository
from livery.workshop._state import Series
from livery.workshop._verdict import Verdict

#: Bundle format version; bump on any structural change.
SCHEMA = 1

#: Newest bundles kept; the window drops the older ones on each write.
KEEP = 20

#: The bundles: a local series, one row per non-merged ending.
SERIES = Series("diagnostics", window=KEEP, ci_only=False, local=True, schema=SCHEMA)


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


def record(
    root: Path, repo: Repository, verdict: Verdict, *, branch: str
) -> tuple[str, str]:
    """Write one bundle as a row of the local series in *root*'s checkout.

    Returns the row's name and ``""``, or ``""`` and why nothing was
    written. Never raises: the verdict being recorded is the thing
    worth seeing, and a failed recording must not take it down.
    """
    try:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        name = f"{stamp}-{branch.replace('/', '-')}-{verdict.state}"
        why = SERIES.put(
            root,
            {name: gather_bundle(repo, verdict, branch=branch)},
            message=f"diagnostics: {branch} {verdict.state}",
        )
    except (Exception, SystemExit) as exc:
        return "", f"{type(exc).__name__}: {exc}"
    return ("", why) if why else (name, "")
