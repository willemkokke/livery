"""Each package's coverage policy, its measured figure, the preview and the floors.

A package states its policy in its ``[coverage]`` table, a table this
extension declares: a floor, the mark on the store, or no verdict at
all (`coverage_policy`). The figure is one computation, `figures`, over
merged parts in the line format: each source file's measurer judges
what it names, its lines alone or its lines and branches. The gate job
judges it over the union of every leg (`enforce_coverage`), and a
machine's run of the tests prints it over its own run, beside each
floor, as a preview that judges nothing (`report_coverage`); both read
their data through `measured_coverage`. The coverage pages state each
package's floor in the words `floor_status` gives.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING

import livery.footman as footman
from livery.extensions.coverage import _marks
from livery.extensions.coverage._format import (
    Merged,
    OtherVersion,
    Part,
    PartError,
    decode,
    digest,
    merge,
    numbers,
)
from livery.extensions.coverage._leg import suites_of
from livery.extensions.coverage._marks import AUTO_RATCHET, DEFAULT_EPSILON
from livery.extensions.coverage._parts import (
    MEASURERS,
    native,
    part_files,
    read_part,
    read_union,
    reduced,
)
from livery.footman import fail
from livery.workshop._contract import load_contract
from livery.workshop._packages import WORKSPACE_TESTS

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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


def floor_status(
    package: Package, marks: Mapping[str, _marks.Mark] | None = None
) -> str:
    """The line a package's coverage pages state about its floor.

    The floor its contract names, or the default one; under auto-ratchet
    the mark it ratchets to, from *marks*, the marks read from the state
    store, and the mode alone when they were not read (None); or that
    nothing judges it: on purpose, ``required = false``, or for want of
    tests.
    """
    policy = coverage_policy(package)
    if policy is None:
        return "Not judged: the package has no tests and names no floor."
    if not policy.required:
        return f"{UNJUDGED[0].upper()}{UNJUDGED[1:]}."
    epsilon = f"with an epsilon of {policy.epsilon}%"
    if not policy.ratchet:
        assert policy.floor is not None
        default = " by default" if policy.default else ""
        return (
            f"Floor: {policy.floor:.1f}%{default}, {epsilon}; the gate job judges"
            " the union of every leg against it."
        )
    if marks is None:
        return f"Floor: the mark on the state store, {epsilon}."
    mark = marks.get(package.path)
    if mark is None:
        return "Floor: the mark on the state store, which the next CI run records."
    return (
        f"Floor: the mark on the state store, {mark.value:.1f}% ({mark.kind} by"
        f" {mark.by}), {epsilon}."
    )


#: The category of a package's files its figure counts.
SOURCE = "source"


def figures(
    root: Path,
    files: Mapping[str, Merged],
    units: Iterable[str],
    packages: tuple[Package, ...],
) -> dict[str, float]:
    """Each measured package's figure over its source files in *files*.

    A file counts in the package that holds it when its category there
    is ``source`` and the checkout holds it, so a test's own lines and a
    build script never dilute the figure, and a module a test built
    under its temporary directory counts nowhere. Each file counts what
    its measurer judges (`numbers`): coverage.py's statements and
    branches, a native measurer's lines alone, the figure being what is
    covered over what can be.

    *units* are the units whose parts *files* merged. A native
    package is measured only when its own suite's parts are among them,
    since its suite's measurer leaves them; any other package is
    measured when any unit that is not a native suite's is, and a
    package measured with no source file reached is 100. A package not
    measured has no figure.
    """
    from livery.workshop._categories import category_of

    held = set(units)
    natives = {package.path for package in packages if native(package)}
    others = any(unit not in natives for unit in held)
    found: dict[str, float] = {}
    for package in packages:
        if (package.path not in held) if package.path in natives else not others:
            continue
        prefix = f"{package.path}/"
        covered = coverable = 0
        for name, reading in files.items():
            if not name.startswith(prefix) or not (root / name).is_file():
                continue
            if category_of(package, name[len(prefix) :]).name != SOURCE:
                continue
            counted = numbers(reading.entry, reading.measurer)
            covered += counted.covered
            coverable += counted.coverable
        found[package.path] = 100.0 * covered / coverable if coverable else 100.0
    return found


def measured_coverage(
    root: Path, packages: tuple[Package, ...], *, none_ok: bool = False
) -> dict[str, float]:
    """Each measured package's figure (`figures`) over the measured data at *root*.

    In the gate job the data is the union every leg's parts and the
    records' made, which its judge wrote at *root*. Anywhere else it is
    what the last run on this machine left: the parts a native suite's
    measurer left, and the parts the listed reductions make of the rest
    of the data. A machine's data is the whole run's, which names every
    source under the meter's directory, one nothing imported among
    them with no line run; the leg's units hold the files their suites
    reached alone, so such a file is left out here too, and the preview
    and the union agree on every file both measured. A run that left no
    data for a package that is not native is empty under *none_ok*, and
    refuses otherwise.
    """
    try:
        unioned = read_union(root)
    except PartError as error:
        fail(f"coverage: {error}")
    if unioned is not None:
        return figures(root, unioned.files, unioned.units, packages)
    files, units = _left_files(root)
    if not units and not none_ok and not all(native(item) for item in packages):
        fail(
            "coverage: nothing measured here: no union was written at"
            f" {root}, and no measured run on this machine left data; run the"
            " tests measured first"
        )
    return figures(root, files, units, packages)


def _left_files(root: Path) -> tuple[Mapping[str, Merged], tuple[str, ...]]:
    """The files the last run on this machine measured, merged; and their units.

    A part of another format version is named and left out; a part off
    the format refuses, naming its source and the key.
    """
    from livery.workshop import answer

    parts: list[Part] = []
    for path in part_files(root):
        try:
            parts.append(read_part(path, root))
        except OtherVersion as error:
            print(f"  coverage: {error}; the figure reads without it")
        except PartError as error:
            fail(f"coverage: {error}")
    for name, reduction in sorted(answer(root, MEASURERS).items()):
        source = f"the {name} measurer's data on this machine"
        for item in reduced(partial(reduction.left, root), source):
            try:
                part = decode(item, source=source)
            except OtherVersion as error:
                print(f"  coverage: {error}; the figure reads without it")
                continue
            except PartError as error:
                fail(f"coverage: {error}")
            ran = {file: entry for file, entry in part.files.items() if entry.counts}
            parts.append(replace(part, files=ran))
    names = sorted({name for part in parts for name in part.files})
    current = {
        name: digest((root / name).read_bytes())
        for name in names
        if (root / name).is_file()
    }
    return (
        merge(parts, current, contexts=False).files,
        tuple(part.unit for part in parts),
    )


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
    for package in packages:
        if package.path == WORKSPACE_TESTS:
            continue  # a unit of the union, never a package with a floor
        policy = coverage_policy(package)
        if policy is None:
            continue
        if package.path not in measured:
            if native(package):
                print(
                    f"  coverage {package.path}: not measured here (its tests did"
                    " not run, or ran without their measurer)"
                )
            continue
        percent = measured[package.path]
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
        if package.path not in measured and native(package):
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
