"""Which run replaced a cancelled one, so a watcher can say so and follow.

A forge cancels a run when a newer one supersedes it: a twin for the
same head (the pull request opened and its branch pushed in the same
breath), or the run for a head that moved under a watch. Read alone,
the cancelled run says "cancelled" and sends its reader to the forge
to learn nothing went wrong. [livery.workshop._runs.successor][] names
the run that took over, and the watchers speak it.
"""

from __future__ import annotations

from dataclasses import dataclass

from livery.forge import ForgeError, Repository, Run, Unsupported


@dataclass(frozen=True)
class Succession:
    """A cancelled run's successor and how it relates.

    Attributes:
        run: The run that took over.
        same_head: Whether it runs the same head (a twin), or the
            pull request's newer head.
    """

    run: Run
    same_head: bool


def successor(repo: Repository, run: Run, *, branch: str = "") -> Succession | None:
    """The run that superseded *run*, or ``None`` when none is known.

    A twin is a newer run of the same workflow for the same head that
    was not itself cancelled. Failing that, *branch*, when the caller
    knows which branch it watched, names the pull request whose head
    moved past *run*'s and its newest run for the new head; a forge
    files a pull request under its current head only, so the old
    head cannot find it. A forge that cannot answer answers ``None``:
    the caller then reports the cancellation as it stands.
    """
    try:
        twins = [
            other
            for other in repo.checks.runs(head_sha=run.head_sha)
            if other.id > run.id
            and other.workflow == run.workflow
            and other.conclusion != "cancelled"
        ]
        if twins:
            return Succession(max(twins, key=lambda other: other.id), same_head=True)
        if not branch:
            return None
        pr = repo.pr.find_by_head(branch)
        if pr is None or not pr.head_sha or pr.head_sha == run.head_sha:
            return None
        moved = [
            other
            for other in repo.checks.runs(head_sha=pr.head_sha)
            if other.workflow == run.workflow and other.conclusion != "cancelled"
        ]
    except (ForgeError, Unsupported, OSError):
        return None
    if not moved:
        return None
    return Succession(max(moved, key=lambda other: other.id), same_head=False)


def supersession_note(repo: Repository, run: Run, *, branch: str = "") -> str:
    """The words for a cancelled *run*: who took over, or that no one did."""
    if run.conclusion != "cancelled":
        return ""
    found = successor(repo, run, branch=branch)
    if found is None:
        return "cancelled; no newer run for its head or its pull request is known"
    where = (
        "for the same head"
        if found.same_head
        else f"for the moved head {found.run.head_sha[:12]}"
    )
    verdict = found.run.conclusion or found.run.status
    return f"cancelled, superseded by run {found.run.id} {where} ({verdict})"
