"""The coverage extension's verbs, loaded through the extension's entry point.

Importing this module registers the ``coverage`` group: ``coverage.leg``,
the entry the extension adds to each check leg, and ``coverage.union``,
the judge it adds to the gate point's verdict job, both placed by the
extension's ``extension.toml``; and ``coverage.enforce`` and
``coverage.accept``, which a person runs. The base's plugin never
imports this module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from livery.footman import doc, fail, group
from livery.workshop import workspace_root

coverage_group = group("coverage", help="The measured union and its floors")


def _root() -> Path:
    """The workspace root; refuses outside a workspace."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    return root


@coverage_group.task(name="leg", hidden=True)
def coverage_leg() -> None:
    """Leave this leg's measured suites for its write, measuring the unrecorded first.

    The check leg's entry, after the gate and before the leg's own
    write, ``ci.leg``: the run left one data file per process. When the
    gate ran narrowed or skipped, the suites no record can supply at
    their current closure run first, measured. Then each suite the
    leg ran is split out and left, with the scope the leg measured, for
    the leg's write, which puts it on the leg's per-run ref beside the
    timing row; the parts combine into one ``.coverage``. Refuses when
    a leg that ran its gate left no data, naming the variable that arms
    the meter, and when the lines could not be left, so a leg that
    measured is never judged as an empty union.
    """
    from livery.extensions.coverage._leg import combine_leg, top_up
    from livery.workshop._quality import _packages
    from livery.workshop._verified import read_marker

    root = _root()
    packages = _packages()
    combine_leg(root, packages, marker=top_up(root, packages, read_marker(root)))


@coverage_group.task(name="union", hidden=True)
def coverage_union() -> None:
    """Union the run's legs' lines with the records and enforce the floors.

    The gate job's judge: every check leg's lines are read from its
    per-run ref, every suite no leg ran is carried from a record on
    that leg at the suite's current closure, and the union judges
    every package. On main's run the record is then written back.
    Refuses when no leg left its lines, when a leg that ran its gate
    left none, and when a suite neither the run nor the record can
    supply, so nothing passes as a smaller union; the report and the
    verdicts print, so the numbers on screen are the numbers enforced.
    The percentages are left as the coverage section of the run's row,
    which the collect after it folds into the run's file.
    """
    from contextlib import nullcontext

    from livery.extensions.coverage._floors import enforce_coverage
    from livery.extensions.coverage._union import combine_union
    from livery.git import remote_snapshot
    from livery.workshop._ci_context import run_context, state_store
    from livery.workshop._metrics import leave_section
    from livery.workshop._quality import _packages

    root = _root()
    run = run_context()
    # The legs' per-run refs and every record the union may carry
    # from: one listing and one fetch.
    scope = (
        remote_snapshot(state_store(root), fetch=(f"run/{run.run_id}/", "coverage/"))
        if run is not None
        else nullcontext()
    )
    with scope:
        judged = combine_union(root, _packages())
    if judged:
        measured = enforce_coverage(root, judged)
        rounded = {path: round(value, 2) for path, value in measured.items()}
        print(f"  {leave_section(root, 'coverage', rounded, unit='%')}")


@coverage_group.task(name="enforce")
def coverage_enforce() -> None:
    """Enforce every package's floor on the combined coverage data.

    Runs where a merged ``.coverage`` file already exists, the
    aggregating CI job after it combines every leg's data; the same
    floors `fm test` checks quickly on one machine, here judged on
    the cross-platform union.
    """
    from livery.extensions.coverage._floors import enforce_coverage
    from livery.workshop._quality import _packages

    enforce_coverage(_root(), _packages())


@coverage_group.task(name="accept")
def coverage_accept(
    package: Annotated[str, doc("the package path, as packages/forge")],
    value: Annotated[float, doc("the new mark, in percent")],
    reason: Annotated[str, doc("why the mark comes down; goes on the record")] = "",
) -> None:
    """Lower a package's coverage mark deliberately, with the reason on the record.

    The mark rises on its own when a run clears it; lowering it is a
    person's act, so this writes a dated row naming who and why, and
    the next gated run judges from it. Refuses without a reason, for
    a package not under auto-ratchet, at or above the current mark,
    or when the marks cannot be read.
    """
    from livery.extensions.coverage import _marks
    from livery.extensions.coverage._floors import coverage_policy
    from livery.workshop._quality import _git_identity, _packages

    root = _root()
    if not reason.strip():
        fail(
            "a reason is required: --reason=<why the mark comes down> goes on the"
            " record beside the new mark"
        )
    packages = _packages()
    found = next((item for item in packages if item.path == package), None)
    if found is None:
        fail(
            f"no package at {package!r}; the packages are "
            + ", ".join(item.path for item in packages)
        )
    policy = coverage_policy(found)
    if policy is None or not policy.ratchet:
        fail(
            f"{package} is not under auto-ratchet: its workshop.toml sets its"
            " floor under [coverage], so change it there"
        )
    if not 0 <= value <= 100:
        fail(f"the mark is a percentage; {value!r} is not")
    current, why = _marks.marks(root)
    if current is None:
        fail(f"refusing: {why}; a write from an unread store would erase its rows")
    mark = current.get(package)
    if mark is None:
        fail(
            f"{package} has no mark yet: the next gated run records one, and"
            " there is nothing to lower"
        )
    if value >= mark.value:
        fail(
            f"{package}'s mark is {mark.value:.2f}%; {value:.2f}% does not lower"
            " it. Raising is the ratchet's own move, when a run clears the mark."
        )
    who = _git_identity(root)
    written = _marks.write_mark(
        root,
        package=package,
        value=value,
        kind="accept",
        by=who,
        reason=reason.strip(),
    )
    if written:
        fail(f"the mark was not written: {written}")
    print(
        f"  coverage {package}: mark {mark.value:.2f}% -> {value:.2f}%"
        f" accepted by {who}"
    )
    print(f"    reason: {reason.strip()}")
