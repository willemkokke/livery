"""The quality verbs: the gate and its parts, dispatched by contract.

Each verb discovers the packages by their ``workshop.toml``, refuses
any type without a backend, and hands the work to the type's backend
module. ``check`` is the whole local gate; CI runs the same command.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from livery.workshop._verified import Verified

import livery.footman as footman
from livery.footman import Forward, doc, fail, group, parallel, task
from livery.workshop._backends import _python, require_backends
from livery.workshop._kinds import gated
from livery.workshop._layers import workspace_root
from livery.workshop._packages import Package, discover_packages
from livery.workshop._state import RunContext
from livery.workshop._templates import template_check


def _packages() -> tuple[Package, ...]:
    """The workspace's packages, backends verified before anything runs."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    packages = discover_packages(root)
    require_backends(packages)
    return packages


def run_kind_checks(packages: tuple[Package, ...], root: Path) -> None:
    """Run each kind's own per-package gate, in package order.

    Only kinds whose contract names ``kind_verbs`` take part; the
    announcement carries the package and the verbs, so the gate
    output says what ran beside what skipped.
    """
    from livery.workshop._kinds import CiContract, kind_for

    default_verbs = CiContract().check_verbs
    for package in packages:
        record = kind_for(package.type)
        if not record.ci.kind_verbs:
            continue
        skipped = [v for v in default_verbs if v not in record.ci.check_verbs]
        print(
            f"  {package.path} ({record.name}):"
            f" {', '.join(record.ci.kind_verbs)} run;"
            f" {', '.join(skipped)} skip"
        )
        record.backend.check(package, root)


def _refuse_both(fix: bool, safe_fix: bool) -> None:
    if fix and safe_fix:
        fail(
            "--fix and --safe-fix are two answers to one question: --fix"
            " applies every safe fix, --safe-fix withholds the"
            " code-removing ones. Pass one."
        )


@task
def lint(
    *paths: str,
    fix: Annotated[bool, doc("apply safe fixes in place")] = False,
    safe_fix: Annotated[
        bool, doc("apply fixes safe for in-progress edits (keeps imports)")
    ] = False,
) -> None:
    """Lint every package with its type's linter.

    With *paths*, lints exactly those files (foreign filetypes pass
    through); without, every package. ``--safe-fix`` is the
    edit-in-flight fix, documented as safe to apply mid-edit.
    """
    _refuse_both(fix, safe_fix)
    if not paths:
        _packages()
    _python.run_lint(fix=fix, safe_fix=safe_fix, paths=paths or _python.SRC)


@task
def format(
    *paths: str,
    fix: Annotated[bool, doc("rewrite instead of reporting")] = False,
    safe_fix: Annotated[
        bool, doc("rewrite; safe to apply to in-progress edits")
    ] = False,
) -> None:
    """Check every package's formatting; ``--fix`` rewrites.

    With *paths*, formats exactly those files (foreign filetypes
    pass through); without, every package.
    """
    _refuse_both(fix, safe_fix)
    if not paths:
        _packages()
    _python.run_format(check=not fix, safe_fix=safe_fix, paths=paths or _python.SRC)


@task
def typecheck() -> None:
    """Type-check every package with its type's gating checkers."""
    _packages()
    _python.run_typecheck()


@task
def typecomplete() -> None:
    """Verify every package's public API is 100% type-complete."""
    _python.run_typecomplete(gated(_packages(), "typecomplete"))


@task
def test(*pytest_args: str) -> None:
    """Run the test suite, coverage floors enforced.

    Args:
        pytest_args: forwarded to pytest verbatim
    """
    packages = gated(_packages(), "test")
    root = workspace_root()
    _python.run_test(*pytest_args, packages=packages, root=root)


@task
def kindcheck() -> None:
    """Run each non-python kind's own gate over its packages.

    Quiet in a pure-python workspace: no kind declares its own
    verbs, so nothing runs and nothing prints.
    """
    packages = _packages()
    root = workspace_root()
    assert root is not None
    run_kind_checks(packages, root)


#: The contract key that lets CI's check legs run the scoped gate.
AFFECTED_LEGS_KEY = "affected-legs"


def affected_legs(root: Path) -> bool:
    """The contract's ``[ci] affected-legs``; false when undeclared.

    Refuses a value that is not a boolean, naming the key: the legs
    either narrow or they do not, and a stray string would read as
    true by accident.
    """
    from livery.workshop._contract import load_contract

    ci = load_contract(root / "workshop.toml").get("ci") or {}
    declared = ci.get(AFFECTED_LEGS_KEY, False) if isinstance(ci, dict) else False
    if not isinstance(declared, bool):
        fail(f"[ci] {AFFECTED_LEGS_KEY} must be true or false, not {declared!r}")
    return declared


def ci_affected_base(root: Path, run: RunContext | None) -> str:
    """The base branch a CI check leg narrows against, or empty for the full gate.

    Empty outside CI, when the contract does not declare
    ``[ci] affected-legs``, on any event but a pull request (the merge
    point runs the full gate until the verified-tree record exists),
    and when the payload names no base; the last two print why, so a
    full leg is never a silent fallback.
    """
    if run is None or not affected_legs(root):
        return ""
    if run.event != "pull_request":
        print(
            f"  affected-legs: a {run.event or 'non pull request'} run pays"
            " the full gate"
        )
        return ""
    if not run.base_ref:
        print("  affected-legs: the event payload names no base branch; full gate")
        return ""
    return run.base_ref


def verified_already(root: Path) -> Verified | None:
    """The record's full row for this checkout's tree, or ``None`` to run the gate.

    Prints the run that proved it when it does, and the reason when
    the record could not decide (an unreadable store, an entry of
    another shape); an absent entry or a narrowed scope is the
    ordinary case and stays quiet. Never skips on anything but a
    full entry for this exact tree. The row names the branch whose
    run proved it, the coverage record main's run copies.
    """
    from livery.workshop import _verified
    from livery.workshop._git_ops import GitError, GitOps

    try:
        tree = _verified.tree_id(GitOps(root))
    except GitError as error:
        print(f"  verified: this checkout has no tree id ({error}); running the gate")
        return None
    found, why = _verified.record(root, tree)
    if why:
        print(f"  verified: {why}; running the gate")
        return None
    if found is None or found.scope != _verified.FULL:
        return None
    basis = f" on top of tree {found.base_tree[:12]}" if found.base_tree else ""
    print(
        f"  verified: tree {tree[:12]} proved green by run {found.run}"
        f" at {found.sha[:12]}{basis}; skipping the gate"
    )
    return found


def _affected(base: str = "main") -> tuple[Package, ...] | None:
    """The affected subset for the gate; None means everything.

    A merge base git cannot compute (a shallow checkout, a base the
    fetch did not bring) falls open to everything with git's words
    printed, the same way an unregistered kind does.
    """
    from livery.workshop._git_ops import GitError, GitOps
    from livery.workshop._graph import affected_packages

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    git = GitOps(root)
    git.fetch()
    try:
        return affected_packages(root, git, base=base)
    except GitError as error:
        print(
            f"  affected: no merge base with origin/{base}; failing open to"
            f" everything ({error})"
        )
        return None


@task
def check(
    affected: Annotated[
        bool, doc("scope the gate to the branch's affected packages")
    ] = False,
    fix: Forward[bool] = False,
    base: Annotated[str, doc("the branch --affected narrows against")] = "main",
) -> None:
    """Run the gate: format, lint, types, tests, render gate, in parallel.

    ``--fix`` runs format and lint in their fix modes: each prints
    what it found, rewrites what is mechanical, and still fails on
    what is not. The two rewrite the same files, so under ``--fix``
    they run one after the other before the rest of the gate.

    ``--affected`` narrows every verb to the packages this branch's
    changes can influence (their dependents' closure). A change
    outside the packages configures every gate, so the narrowing
    falls back to everything; ty and pyrefly always check their
    configured whole either way. ``--base`` names the branch the
    narrowing compares against, ``main`` when absent; inside CI a
    pull request's own base wins.

    ``--fix`` refuses inside CI: a runner's checkout is judged,
    never rewritten, because a fix there mutates a copy nobody
    keeps and hides the finding from the verdict. Run the fix
    locally and push the result.
    """
    import os

    if fix and os.environ.get("CI"):
        fail(
            "check --fix inside CI: the runner's checkout is judged,"
            " never rewritten. A fix here would mutate a copy nobody"
            " keeps and hide the finding from the verdict. Run"
            f" `{footman.prog()} check --fix` locally and push the result."
        )
    from livery.workshop import _verified
    from livery.workshop._state import run_context

    root_for_ci = workspace_root()
    run = run_context()
    # The nightly point pays the whole gate: the record it would skip on
    # was stamped by a run that selected the gate's tests, not its own,
    # and a narrowed nightly would be no nightly.
    nightly = _current_point() == "nightly"
    if nightly:
        print(
            "  nightly: the whole gate, the verified record and the narrowing set aside"
        )
    if not nightly and root_for_ci is not None and run is not None:
        from livery.workshop._state import remote_snapshot

        # The verified record and the records a skip reads: one listing.
        with remote_snapshot(root_for_ci, fetch=("verified", "coverage/")):
            proved = verified_already(root_for_ci)
            if proved is not None:
                _measure_unrecorded(root_for_ci, run, bases=record_bases(proved.branch))
                return
    ci_base = (
        ci_affected_base(root_for_ci, run)
        if not affected and not nightly and root_for_ci is not None
        else ""
    )
    if ci_base:
        print(f"  affected-legs: the scoped gate against origin/{ci_base}")
        affected = True
    subset = _affected(ci_base or base) if affected else None
    if affected and subset is not None:
        packages = _packages()
        if root_for_ci is not None and run is not None:
            subset = _with_unstored_suites(root_for_ci, run, packages, subset)
        if not subset:
            print(f"  nothing affected: {_nothing_reason(ci_base or base)}")
            if root_for_ci is not None and run is not None:
                _verified.write_marker(root_for_ci, _verified.NOTHING, leg=run.leg)
            return
        # Narrowed when some package's gate is skipped: the workspace's
        # own tests in the subset are a unit beside the packages, not
        # one of them, and a subset of every package plus that unit is
        # the whole gate, which runs those tests anyway.
        from livery.workshop._coverage_store import WORKSPACE_TESTS

        members = [package for package in subset if package.path != WORKSPACE_TESTS]
        if len(members) < len(packages):
            names = ", ".join(package.path for package in subset)
            print(f"  affected: {names}")
            if root_for_ci is not None and run is not None:
                _verified.write_marker(
                    root_for_ci,
                    _verified.AFFECTED,
                    tuple(package.path for package in subset),
                    leg=run.leg,
                )
            _scoped_check(subset, fix=fix)
            # The render and provenance checks are the gate job's in CI,
            # once per run; a local narrowed gate runs them too, since a
            # new module changes the generated site configuration and the
            # drift would otherwise surface only in CI's gate job.
            if run is None:
                from livery.workshop._provenance import provenance_check

                template_check()
                provenance_check()
            _remember_local(
                root_for_ci,
                run,
                packages=tuple(package.path for package in subset),
                base=ci_base or base,
            )
            return
    # The marker is a CI leg's fact for its metrics row and the stamp;
    # a local run leaves none, since an untracked root file would read
    # as a root change on the next affected gate.
    if root_for_ci is not None and run is not None:
        _verified.write_marker(root_for_ci, _verified.FULL, leg=run.leg)
    from livery.workshop._provenance import provenance_check

    if fix:
        format(fix=True)
        lint(fix=True)
        provenance_check(fix=True)
        with parallel():
            typecheck()
            typecomplete()
            test()
            kindcheck()
            template_check()
        _remember_local(root_for_ci, run, packages=None, base=base)
        return
    with parallel():
        format()
        lint()
        typecheck()
        typecomplete()
        test()
        kindcheck()
        template_check()
        provenance_check()
    _remember_local(root_for_ci, run, packages=None, base=base)


def _remember_local(
    root: Path | None,
    run: RunContext | None,
    *,
    packages: tuple[str, ...] | None,
    base: str,
) -> None:
    """Record a green local gate on this machine's gate record; CI never writes it."""
    if root is None or run is not None:
        return
    from livery.workshop import _gate_record
    from livery.workshop._git_ops import GitOps

    print(_gate_record.remember(root, GitOps(root), packages=packages, base=base))


def _current_point() -> str:
    """The point this process runs at, as the job runner named it; gate outside CI."""
    import os

    from livery.workshop._pytest_points import POINT_VARIABLE

    return os.environ.get(POINT_VARIABLE, "gate")


def _nothing_reason(base: str) -> str:
    """Why nothing is affected: only prose and site files changed, or nothing."""
    from livery.workshop._git_ops import GitError, GitOps
    from livery.workshop._graph import is_prose, is_site

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    try:
        changed = GitOps(root).changed_paths(base)
    except GitError:
        return "the branch changes no files"
    quiet = [path for path in changed if is_prose(path) or is_site(path)]
    if changed and len(quiet) == len(changed):
        return (
            f"only prose and site files changed ({len(quiet)} file(s) under"
            " notes/, markdown, the root docs/ tree, or zensical.toml); the gate"
            " skips, the site build judges them"
        )
    return "the branch changes no files"


def record_bases(branch: str) -> tuple[str, ...]:
    """The records a leg reads, in order: the branch's own if any, then main's."""
    from livery.workshop._coverage_store import MAIN

    return (branch, MAIN) if branch and branch != MAIN else (MAIN,)


def _unrecorded(
    root: Path,
    run: RunContext,
    packages: tuple[Package, ...],
    units: tuple[Package, ...],
    *,
    bases: tuple[str, ...],
) -> tuple[Package, ...]:
    """Of *units*, those no record in *bases* can supply on this leg; each says why.

    One read per record. A unit a record holds at its current closure
    identity is supplied, the branch's record asked before main's; a
    unit every record lacks or holds at another closure, an unreadable
    record, a leg without a label, or a closure git cannot identify
    runs the suite fresh and says why, so the union never lacks a
    suite and the records need no backfill.
    """
    from livery.workshop._coverage_store import closure_id, recorded
    from livery.workshop._git_ops import GitError, GitOps

    if not units:
        return ()
    if not run.leg:
        for unit in units:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (this leg"
                " has no label)"
            )
        return units
    held = {base: recorded(root, leg=run.leg, base=base) for base in bases}
    git = GitOps(root)
    out: list[Package] = []
    for unit in units:
        try:
            key = closure_id(git, packages, unit)
        except GitError as error:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (its closure"
                f" has no identity: {error})"
            )
            out.append(unit)
            continue
        states: list[str] = []
        for base in bases:
            record = held[base]
            if record.failed:
                states.append(f"{base}'s record could not be read ({record.reason})")
                continue
            row = record.units.get(unit.path)
            if row is not None and row.closure == key:
                break
            states.append(
                f"{base}'s record holds it at closure {row.closure[:12]}, not"
                f" {key[:12]}"
                if row is not None
                else f"{base}'s record holds no measurement of it"
            )
        else:
            print(
                f"  coverage store: {unit.path} runs, nothing to reuse (on this leg"
                f" {', '.join(states)})"
            )
            out.append(unit)
    return tuple(out)


def _with_unstored_suites(
    root: Path,
    run: RunContext,
    packages: tuple[Package, ...],
    subset: tuple[Package, ...],
) -> tuple[Package, ...]:
    """*subset* plus every suite no record can supply for this leg.

    A leg skips a suite only when its branch's record or main's holds
    the suite's lines at its current closure on this leg; the rest run
    fresh, and the line says why.
    """
    from livery.workshop._backends._python import suites_of
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    kept = {package.path for package in subset}
    skipped = tuple(
        package for package in suites_of(packages) if package.path not in kept
    )
    bases = record_bases(run.head_ref)
    extra = {
        unit.path for unit in _unrecorded(root, run, packages, skipped, bases=bases)
    }
    if not extra:
        return subset
    # The workspace's own tests ride the subset as a unit of their own,
    # outside the packages, and stay in it.
    unit = tuple(package for package in subset if package.path == WORKSPACE_TESTS)
    return tuple(package for package in packages if package.path in kept | extra) + unit


def _measure_unrecorded(root: Path, run: RunContext, *, bases: tuple[str, ...]) -> None:
    """On a proved tree, run the units no record in *bases* can supply, for their lines.

    The gate's checks already passed for this tree, so none reruns.
    The coverage union still needs every unit at its current closure;
    the branch whose run proved the tree recorded them, so a tree the
    records supply in full leaves the ``verified`` scope and runs
    nothing. A unit neither record holds, a row written before the
    record named branches, say, runs metered, and nothing else.
    """
    from livery.workshop import _verified
    from livery.workshop._backends._python import units_of

    packages = _packages()
    units = _unrecorded(root, run, packages, units_of(root, packages), bases=bases)
    if not units:
        _verified.write_marker(root, _verified.VERIFIED, leg=run.leg)
        return
    names = ", ".join(unit.path for unit in units)
    print(f"  measuring: {names} run for their lines alone; the gate is proved")
    _verified.write_marker(
        root, _verified.MEASURED, tuple(unit.path for unit in units), leg=run.leg
    )
    _python.run_test(packages=units, root=root, scoped=True)


def _scoped_check(subset: tuple[Package, ...], *, fix: bool = False) -> None:
    """The gate over *subset* only: this routes, the backends compose.

    The render gate is skipped: its inputs are the root answers and
    the template source, which a package-scoped change cannot touch
    (touching them makes the change root-scoped, and the full gate
    runs instead). ``fix`` runs every kind's rewriters serially
    before any check reads the tree, exactly as the whole gate does;
    what each kind checks, and in what parallel shape, is its
    backend's knowledge, not this router's.
    """
    from livery.footman import step

    root = workspace_root()
    assert root is not None
    if fix:
        _python.scoped_rewrite(subset)
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    # The workspace's own tests are a unit of the python gate alone:
    # no kind owns them.
    members = tuple(package for package in subset if package.path != WORKSPACE_TESTS)
    with parallel() as p:
        p(
            step(_python.scoped_gate, title="python")(
                subset, root=root, check_style=not fix
            )
        )
        run_kind_checks(members, root)


coverage = group("coverage", help="The measured union and its floors")


@coverage.task(name="leg", hidden=True)
def coverage_leg(
    *,
    job: Annotated[str, doc("the job's name as the forge lists it")] = "",
    trace: Annotated[Path, doc("the trace the profiled gate wrote")] = Path(
        "fm-profile.json"
    ),
) -> None:
    """Put this leg's measured suites and its timing row on its per-run ref, once.

    Runs at the end of a check leg whose tests ran metered: the run
    left one data file per process; each suite the leg ran is split
    out and put on the leg's per-run ref with the scope the gate ran,
    and the parts combine into one ``.coverage``. With ``--job`` the
    leg's timing row, read from the trace the profiled gate wrote,
    rides the same write for the gate job to collect; a trace that
    cannot be read prints its reason and the write goes on without
    the row. Refuses when a leg that ran its gate left no data, naming
    the variable that arms the meter, and when the lines could not be
    put, so a leg that measured is never judged as an empty union.
    """
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    timing = None
    if job:
        from livery.workshop._metrics import leg_row

        timing, why = leg_row(trace if trace.is_absolute() else root / trace, job=job)
        if timing is None:
            print(f"  {job}: {why}; the timing row stays unwritten")
    _python.combine_leg(root, _packages(), timing=timing)
    if timing is not None:
        print(f"  {job}: timing row on the leg's ref")


@coverage.task(name="union", hidden=True)
def coverage_union() -> None:
    """Union the run's legs' lines with main's record and enforce the floors.

    Runs in the gate job: every check leg's lines are read from its
    per-run ref, every suite no leg ran is carried from main's record
    on that leg at the suite's current closure, and the union judges
    every package. On main's run the record is then written back.
    Refuses when no leg left its lines, when a leg that ran its gate
    left none, and when a suite neither the run nor the record can
    supply, so nothing passes as a smaller union; the report and the
    verdicts print, so the numbers on screen are the numbers enforced.
    """
    from contextlib import nullcontext

    from livery.workshop._state import remote_snapshot, run_context

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    run = run_context()
    # The legs' per-run refs and every record the union may carry
    # from: one listing and one fetch.
    scope = (
        remote_snapshot(root, fetch=(f"run/{run.run_id}/", "coverage/"))
        if run is not None
        else nullcontext()
    )
    with scope:
        judged = _python.combine_union(root, _packages())
    if judged:
        from livery.workshop._metrics import write_coverage_row

        measured = _python.enforce_coverage(root, judged)
        print(f"  {write_coverage_row(root, measured)}")


@coverage.task(name="enforce")
def coverage_enforce() -> None:
    """Enforce every package's floor on the combined coverage data.

    Runs where a merged ``.coverage`` file already exists, the
    aggregating CI job after it combines every leg's data; the same
    floors `fm test` checks quickly on one machine, here judged on
    the cross-platform union.
    """
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    _python.enforce_coverage(root, _packages())


@coverage.task(name="accept")
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
    from livery.workshop import _coverage_marks

    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
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
    policy = _python.coverage_policy(found)
    if policy is None or not policy.ratchet:
        fail(
            f"{package} is not under auto-ratchet: its floor is committed in its"
            " workshop.toml, so lower it there"
        )
    if not 0 <= value <= 100:
        fail(f"the mark is a percentage; {value!r} is not")
    current, why = _coverage_marks.marks(root)
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
    written = _coverage_marks.write_mark(
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


def _git_identity(root: Path) -> str:
    """Who is accepting: the git identity, or the user name the shell has."""
    import os

    import livery.toolroom as toolroom

    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "config", "user.name"
    )
    name = result.stdout.strip() if result.code == 0 else ""
    return name or os.environ.get("USER", "unknown")


caches = group("caches", help="The workspace's derived caches")


@caches.task(name="clear")
def caches_clear() -> None:
    """Remove build artifacts and checker caches."""
    import shutil

    root = workspace_root()
    if root is None:
        return
    for name in ("dist", ".pytest_cache", ".ruff_cache", ".mypy_cache"):
        shutil.rmtree(root / name, ignore_errors=True)
