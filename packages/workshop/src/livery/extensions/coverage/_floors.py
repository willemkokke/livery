"""Each package's coverage policy, its measured figure, the preview and the floors.

A package states its policy in its ``[coverage]`` table, a table this
extension declares: a floor, the mark on the store, or no verdict at
all ([livery.extensions.coverage._floors.coverage_policy][]). The
figure is one computation,
[livery.extensions.coverage._floors.measured_coverage][]: the gate
job judges it over the union of every leg
([livery.extensions.coverage._floors.enforce_coverage][]), and a
machine's run of the tests prints it over its own run, beside each
floor, as a preview that judges nothing
([livery.extensions.coverage._floors.report_coverage][]).
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePath
from typing import TYPE_CHECKING

import livery.footman as footman
import livery.toolroom.tools as tools
from livery.extensions.coverage import _marks
from livery.extensions.coverage._leg import suites_of, workspace_name
from livery.extensions.coverage._lines import measures_lines, percent, read_parts
from livery.extensions.coverage._marks import AUTO_RATCHET, DEFAULT_EPSILON
from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._packages import WORKSPACE_TESTS

if TYPE_CHECKING:
    from collections.abc import Callable

    from livery.workshop._packages import Package

#: The floor a package with tests is judged at when its contract names none.
DEFAULT_FLOOR = 100.0

#: What a coverage line says of a package its contract leaves unjudged.
UNJUDGED = "not judged: its [coverage] table sets required = false"


@dataclass(frozen=True)
class FloorPolicy:
    """How a package's coverage is judged: a floor, the ratchet, or not at all.

    Attributes:
        floor: The percentage, or ``None`` under auto-ratchet and when
            nothing judges the package.
        ratchet: Whether the mark on the store is the floor.
        epsilon: The tolerance in percentage points, in both modes.
        required: Whether anything judges the package; ``False`` gives
            no verdict and records no mark.
        default: Whether the floor is the default, the contract naming none.
    """

    floor: float | None
    ratchet: bool
    epsilon: float
    required: bool = True
    default: bool = False


def coverage_policy(package: Package) -> FloorPolicy | None:
    """The package's ``[coverage]`` policy, or ``None`` when it has none.

    The floor is a percentage, or the mode ``"auto-ratchet"``; anything
    else refuses by name. A package with tests that names no floor is
    judged at the default floor, 100, and one without tests that names
    none has no policy. ``required = false`` leaves the package unjudged
    on purpose, and a floor beside it refuses. ``epsilon`` is a number of
    percentage points, 0.5 when absent; a negative one refuses.
    """
    table = load_contract(package.directory / "workshop.toml").get("coverage") or {}
    raw = table.get("floor")
    # The contract's judge holds the epsilon to a number and required
    # to a boolean.
    epsilon = float(table.get("epsilon", DEFAULT_EPSILON))
    if epsilon < 0:
        fail(f"{package.path}: [coverage] epsilon must not be negative")
    if not table.get("required", True):
        if raw is not None:
            fail(
                f"{package.path}: [coverage] floor is {raw!r} beside required ="
                " false; a package nothing judges keeps no floor, so remove one"
                " of the two"
            )
        return FloorPolicy(None, False, epsilon, required=False)
    if raw is None:
        if not suites_of((package,)):
            return None
        return FloorPolicy(DEFAULT_FLOOR, False, epsilon, default=True)
    if raw == AUTO_RATCHET:
        return FloorPolicy(None, True, epsilon)
    if isinstance(raw, str):
        fail(
            f"{package.path}: [coverage] floor is a percentage or"
            f' "{AUTO_RATCHET}"; found {raw!r}'
        )
    return FloorPolicy(float(raw), False, epsilon)


def coverage_floor(package: Package) -> float | None:
    """The floor the package's coverage is judged at, or None.

    ``None`` under auto-ratchet too, whose floor is the mark on the
    store, and when nothing judges the package; ``coverage_policy``
    tells them apart.
    """
    policy = coverage_policy(package)
    return None if policy is None else policy.floor


def _floor_words(policy: FloorPolicy) -> str:
    """The floor as a coverage line names it, saying when it is the default."""
    assert policy.floor is not None
    return f"floor {policy.floor:.1f}%" + (" by default" if policy.default else "")


#: What coverage.py says when the run left no data: a selection of
#: tests that reached no source measures nothing, which a preview
#: reports and a judged leg refuses.
NO_DATA = "No data to report"


def measured_coverage(
    root: Path, packages: tuple[Package, ...], *, none_ok: bool = False
) -> dict[str, float]:
    """Per-package coverage from the run's ``.coverage`` data, statements and branches.

    A python package's percentage is the statements and branches of
    its files the data reached, over all of them, the figure
    coverage.py reports as a file's total; a package with none to
    reach is 100. A native package's is its lines part's, the lines
    hit over the instrumentable lines of its sources, and a native
    package with no part left is absent, never a number. A run that
    left no python data (`NO_DATA`) is empty under *none_ok* and a
    refusal otherwise. A measured file the disk no longer has, a module
    a test built under its temporary directory, is skipped: no package
    owns it, so it changes no number. A file the data names with no
    line run, a source nothing imported, is left out too, as the leg's
    split leaves it out of every unit
    ([livery.extensions.coverage._leg.suite_arcs_by_context][]): the
    preview a machine prints over its own run and the figure the gate
    job judges over the union are this one computation, and agree on
    every file both measured.
    """
    lines_parts = read_parts(root)
    measured: dict[str, float] = {}
    for package in packages:
        if measures_lines(package) and package.path in lines_parts:
            reached = percent(lines_parts[package.path], package)
            measured[package.path] = 100.0 if reached is None else reached
    arcs_packages = tuple(p for p in packages if not measures_lines(p))
    if not arcs_packages:
        return measured
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as handle:
        report = handle.name
    # The data read is *root*'s, by contract. Under a metered gate the
    # ambient COVERAGE_*/COV_CORE_* variables re-point the coverage CLI
    # at the outer run's live data file, whose parallel parts are still
    # being written; scrubbing them keeps the read on the file the
    # caller named.
    scrubbed = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("COVERAGE_", "COV_CORE_"))
    }
    # nofail: the exit code is read here, so a run that left no data
    # is told apart from a broken read instead of ending the gate.
    # --ignore-errors: the data names every livery.* module the tests
    # imported, a fake one a test built under its tmp_path included,
    # and a passing test deletes that directory at teardown. A file
    # outside the workspace is no package's, so the sums below never
    # read it, and a source the disk no longer has must not stop them.
    result = tools.coverage.opts(cwd=root, env=scrubbed, nofail=True)(
        "json", "--ignore-errors", "-o", report
    )
    if result.code != 0:
        if none_ok and NO_DATA in result.stdout + result.stderr:
            return measured
        fail(f"coverage json exited {result.code}:\n{result.stdout}{result.stderr}")
    data = json.loads(Path(report).read_text("utf-8"))
    Path(report).unlink(missing_ok=True)
    lined = run_files(root)
    totals: dict[str, list[int]] = {package.path: [0, 0] for package in arcs_packages}
    for filename, entry in data.get("files", {}).items():
        # coverage spells a relative path with the platform's separator;
        # a package's path is spelled with slashes everywhere.
        spelled = PurePath(filename).as_posix()
        if spelled not in lined:
            continue
        for package in arcs_packages:
            if spelled.startswith(f"{package.path}/src/"):
                summary = entry.get("summary", {})
                totals[package.path][0] += int(summary.get("covered_lines", 0)) + int(
                    summary.get("covered_branches", 0)
                )
                totals[package.path][1] += int(summary.get("num_statements", 0)) + int(
                    summary.get("num_branches", 0)
                )
                break
    measured.update(
        {
            path: (100.0 * covered / statements if statements else 100.0)
            for path, (covered, statements) in totals.items()
        }
    )
    return measured


def report_coverage(root: Path, packages: tuple[Package, ...]) -> None:
    """Print each package's local coverage beside its floor, no verdict.

    One machine's run misses the platform branches and the task
    shells only the measured CI union reaches, so the local number is
    a low-biased preview: it informs, and the aggregating CI job's
    union is what the floors gate.
    """
    measured = measured_coverage(root, packages, none_ok=True)
    if not measured:
        print(
            "  coverage: nothing measured by this run (its tests reached no"
            " source); the CI union judges the floors"
        )
    arcs_measured = any(
        not measures_lines(package) and package.path in measured for package in packages
    )
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            continue  # a unit of the union, never a package with a floor
        policy = coverage_policy(package)
        if policy is None:
            continue
        if measures_lines(package):
            if package.path not in measured:
                print(
                    f"  coverage {package.path}: not measured here (its tests did"
                    " not run, or ran without their measurer)"
                )
                continue
        elif not arcs_measured:
            continue
        percent = measured.get(package.path, 0.0)
        if not policy.required:
            judged = UNJUDGED
        elif policy.ratchet:
            judged = "the mark on the store judges the CI union"
        else:
            judged = f"{_floor_words(policy)} judges the CI union"
        print(f"  coverage {package.path}: {percent:.1f}% here ({judged})")


def previewer(root: Path) -> Callable[[Path, tuple[Package, ...]], None]:
    """Answer ``python.test.preview`` with the function that prints the preview.

    The python extension asks with the workspace root, which the answer
    does not depend on, after a machine's run of the suites, and calls
    the answer with the root and the packages the run tested.
    """
    del root
    return report_coverage


def run_files(root: Path) -> set[str]:
    """The files *root*'s coverage data names with a line run, workspace-relative.

    A file the meter names with no line run, a source under the
    measured directory that nothing imported, is not among them, and
    neither is a file outside *root*. Empty without a data file.
    """
    from coverage import CoverageData

    path = root / ".coverage"
    if not path.is_file():
        return set()
    data = CoverageData(basename=str(path))
    data.read()
    named = (
        workspace_name(filename, root)
        for filename in data.measured_files()
        if data.lines(filename)
    )
    return {name for name in named if name is not None}


def enforce_coverage(root: Path, packages: tuple[Package, ...]) -> dict[str, float]:
    """Fail any package measurably below its floor; the measured percentages.

    A floor, the package's own or the default, passes at the floor
    minus the package's epsilon. Under auto-ratchet the floor is the
    mark on the store minus epsilon: a run with no mark records one and
    passes, a run that clears the mark by more than epsilon raises it
    (a CI run writes; a local run says it would), and a store that
    cannot be read falls open with its reason and writes nothing. A
    package whose contract sets ``required = false`` gets no verdict and
    no mark, and its line says so. Every verdict prints with its
    numbers, so the numbers on screen are the numbers enforced.
    """
    from livery.workshop._ci_context import run_context

    measured = measured_coverage(root, packages)
    policies = {package.path: coverage_policy(package) for package in packages}
    ratcheted = [
        p for p in packages if (policies[p.path] or FloorPolicy(None, False, 0)).ratchet
    ]
    current: dict[str, _marks.Mark] | None = {}
    marks_why = ""
    if ratcheted:
        current, marks_why = _marks.marks(root)
    run = run_context()
    problems: list[str] = []
    for package in packages:
        policy = policies[package.path]
        if policy is None:
            continue
        if not policy.required:
            reached = measured.get(package.path)
            figure = "not measured" if reached is None else f"{reached:.1f}%"
            print(f"  coverage {package.path}: {figure} ({UNJUDGED})")
            continue
        if package.path not in measured and measures_lines(package):
            print(f"  coverage {package.path}: not measured")
            problems.append(
                f"{package.path}: not measured; its tests ran without their"
                " measurer, or never ran, and an unmeasured suite never passes"
            )
            continue
        percent = measured.get(package.path, 0.0)
        if not policy.ratchet:
            assert policy.floor is not None
            print(
                f"  coverage {package.path}: {percent:.1f}%"
                f" ({_floor_words(policy)}, epsilon {policy.epsilon}%)"
            )
            if percent < policy.floor - policy.epsilon:
                which = "default" if policy.default else "committed"
                hint = (
                    "; [coverage] floor in its workshop.toml names another"
                    if policy.default
                    else ""
                )
                problems.append(
                    f"{package.path}: {percent:.1f}% is below the {which} floor of"
                    f" {policy.floor:.1f}% by more than the {policy.epsilon}%"
                    f" epsilon{hint}"
                )
            continue
        if current is None:
            print(
                f"  coverage {package.path}: {percent:.1f}% (auto-ratchet:"
                f" {marks_why}; the gate falls open and records nothing)"
            )
            continue
        (verdict,) = _marks.judge(
            {package.path: percent}, current, {package.path: policy.epsilon}
        )
        print(_marks.render(verdict))
        if not verdict.ok:
            assert verdict.mark is not None
            problems.append(
                f"{package.path}: {percent:.1f}% is below its mark of"
                f" {verdict.mark.value:.1f}% by more than the {policy.epsilon}%"
                f" epsilon; raise the code, or lower the mark deliberately with"
                f" `{footman.prog()} coverage.accept {package.path} <value>"
                " --reason=...`"
            )
            continue
        if verdict.raises:
            if run is None:
                print("    (a CI run records the mark; this run does not)")
                continue
            why = _marks.write_mark(
                root,
                package=package.path,
                value=percent,
                kind="first" if verdict.mark is None else "ratchet",
                by=f"run {run.run_id}",
            )
            print(f"    {'recorded' if not why else 'not recorded: ' + why}")
    if problems:
        fail(
            "coverage fell below the floors:\n  "
            + "\n  ".join(problems)
            + "\n  raise the code, or lower a floor deliberately"
        )
    return measured
