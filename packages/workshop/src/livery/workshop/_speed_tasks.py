"""``fm speed.judge`` and ``fm speed.accept``: the test speed marks' verbs.

``speed.judge`` runs in the gate job after the timing rows are
collected: it judges every check leg's summed test time per package
against the marks ([livery.workshop._speed][]), records the first marks
and the ratchets, warns on a run over the mark, and is red on the
second run over in a row on a reference leg. ``speed.accept`` is the
person's raise of a mark, with the reason on the record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import livery.footman as footman
from livery.footman import doc, fail, group
from livery.workshop import _speed
from livery.workshop._layers import workspace_root
from livery.workshop._packages import discover_packages
from livery.workshop._state import RunContext, run_context

speed = group("speed", help="The test suites' speed marks")


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


def judge_flow(root: Path, run: RunContext) -> list[str]:
    """Judge the run's legs, record what moves, and return the red lines.

    Prints every verdict and every write. Fails open: a store that
    cannot be read prints its reason and reddens nothing.
    """
    verdicts, why = _speed.judge_run(root, run)
    for line in why:
        print(line)
    for verdict in verdicts:
        for line in _speed.render(verdict):
            print(line)
    for line in _speed.apply(root, verdicts, by=f"run {run.run_id}"):
        print(line)
    return [
        f"{v.sample.package} on {v.sample.leg}: {v.sample.seconds:.1f}s over the"
        f" limit {v.limit:.1f}s for the second run in a row"
        for v in verdicts
        if v.red and v.limit is not None
    ]


@speed.task(name="judge", hidden=True)
def speed_judge() -> None:
    """Judge the run's test times against the speed marks; red on the second run over.

    Runs in the gate job after the timing rows are collected. Outside
    CI it says so and judges nothing.
    """
    run = run_context()
    if run is None:
        print("  not a CI run: the speed marks are judged by the gate job")
        return
    red = judge_flow(_root(), run)
    if red:
        fail(
            "test speed over the mark for two runs in a row:\n  "
            + "\n  ".join(red)
            + "\n  make the suite faster, or accept the cost with"
            f" `{footman.prog()} speed.accept <package> <seconds> --reason=<why>`"
        )


@speed.task(name="accept")
def speed_accept(
    package: Annotated[str, doc("the package path, as packages/forge")],
    seconds: Annotated[float, doc("the new mark: the suite's summed test time")],
    reason: Annotated[
        str, doc("why the suite may take longer; goes on the record")
    ] = "",
    leg: Annotated[str, doc("the check leg; the reference leg when empty")] = "",
) -> None:
    """Raise a package's speed mark deliberately, with the reason on the record.

    The mark comes down on its own when the runs beat it; raising it
    is a person's act, so this writes a dated row naming who and why,
    and the next gated run judges from it. Refuses without a reason,
    for an unknown package, at or below the current mark, or when the
    marks cannot be read.
    """
    from livery.workshop._points import check_legs
    from livery.workshop._quality import _git_identity

    root = _root()
    packages = tuple(item.path for item in discover_packages(root))
    legs = check_legs(root)
    chosen = leg or next((item for item in legs if _speed.is_reference(item)), "")
    if not chosen:
        chosen = legs[0] if legs else ""
    if not chosen:
        fail("no check leg: the contract names no runner to judge on")
    for line in _speed.accept(
        root,
        package=package,
        leg=chosen,
        seconds=seconds,
        reason=reason,
        by=_git_identity(root),
        packages=packages,
    ):
        print(line)
