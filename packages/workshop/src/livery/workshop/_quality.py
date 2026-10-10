"""The quality verbs: the gate and its parts, dispatched by contract.

Each verb discovers the packages by their ``workshop.toml``, refuses
any package kind without a backend, and hands the work to the kind's backend
module. ``check`` is the whole local gate; CI runs the same command.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

if TYPE_CHECKING:
    from livery.workshop._gate_record import Plan
    from livery.workshop._influence import Changes
    from livery.workshop._verified import Verified

import livery.footman as footman
from livery.extensions.python import _backend as _python
from livery.footman import Forward, doc, fail, group, parallel, task
from livery.workshop import _checks
from livery.workshop._backends import require_backends
from livery.workshop._checks import GateContext
from livery.workshop._extensions import workspace_root
from livery.workshop._packages import Package, discover_packages
from livery.workshop._state import RunContext

# Re-exported by name: the render check's record looks it up on this
# module at run time, which is where the gate's characterisation tests
# patch it.
from livery.workshop._templates import drift_check as drift_check


def _packages() -> tuple[Package, ...]:
    """The workspace's packages, backends verified before anything runs."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    packages = discover_packages(root)
    require_backends(packages)
    return packages


def _context(
    *,
    subset: tuple[Package, ...] | None = None,
    fix: bool = False,
    files: tuple[str, ...] = (),
    safe: bool = False,
    point: str = "",
    tests: Mapping[str, tuple[str, ...]] | None = None,
    examples: tuple[str, ...] = (),
    changes: Changes | None = None,
    arguments: tuple[str, ...] = (),
) -> GateContext:
    """This workspace's gate context: the root, every package, and the run's scope."""
    root = workspace_root()
    if root is None:
        raise ValueError("no workspace: no workshop.toml above the working directory")
    return GateContext(
        root=root,
        packages=_packages(),
        subset=subset,
        tests=tests or {},
        examples=examples,
        fix=fix,
        files=files,
        safe=safe,
        point=point,
        changes=changes,
        arguments=arguments,
    )


def walk(
    ctx: GateContext,
    *,
    between: Callable[[], None] | None = None,
    judge: bool = True,
    only: frozenset[str] | None = None,
) -> None:
    """Walk the registry: the one place the gate's order lives.

    Every gate comes here, the whole one, CI's narrowed legs and a
    machine's narrowed step alike. What was narrowed and by whom is
    printed first. Under ``--fix`` every fixer that applies runs, one
    at a time in registration order, before any judge reads the tree,
    and a check that rewrote is not judged again in the run; then
    *between* runs, where a machine's run measures the tree the judges
    read; then every judge runs in one parallel block, and one refusal
    is the verdict. A check whose claims reach no file in scope is
    said and not started. Without *judge* the walk stops after the
    fixers, the post-edit hook's run. *only* keeps the named checks
    alone, a generated verb's run.
    """
    for line in _checks.narrowings():
        print(line)
    ctx = replace(ctx, catalogue=_checks.catalogue(ctx))

    def chosen(names: tuple[str, ...]) -> tuple[str, ...]:
        return names if only is None else tuple(n for n in names if n in only)

    with _checks.current(ctx):
        for name in _checks.with_files(chosen(_checks.rewriters(ctx)), ctx):
            _checks.task_for(name)(fix=True)
    if ctx.fix and between is not None:
        between()
    if not judge:
        return
    with _checks.current(ctx), parallel():
        for name in _checks.with_files(chosen(_checks.judges(ctx)), ctx):
            _checks.task_for(name)()


def _refuse_both(fix: bool, safe_fix: bool) -> None:
    if fix and safe_fix:
        fail(
            "--fix and --safe-fix are two answers to one question: --fix"
            " applies every safe fix, --safe-fix withholds the"
            " code-removing ones. Pass one."
        )


#: The contract key that lets CI's check legs run the scoped gate.
AFFECTED_LEGS_KEY = "affected-legs"


def affected_legs(root: Path) -> bool:
    """The contract's ``[ci] affected-legs``; false when undeclared.

    The contract's judge holds the value to a boolean: the legs either
    narrow or they do not, and a stray string would read as true.
    """
    from livery.workshop._contract import load_contract

    ci = load_contract(root / "workshop.toml").get("ci") or {}
    return bool(ci.get(AFFECTED_LEGS_KEY, False))


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


def ci_changes(root: Path) -> Changes | None:
    """What this CI run changed against the base it measures from; None for everything.

    A pull request's run in a workspace that declares ``[ci]
    affected-legs`` measures from its base branch: the paths changed
    since the merge base with ``origin/<base>``, committed or not, with
    that merge base, so a ``widen`` reference can read a file as it
    was. None at a desk, on any other event, without
    ``affected-legs``, and when git cannot answer; the run then judges
    everything, and the line printed says why.
    """
    from livery.workshop._git_ops import GitError, GitOps
    from livery.workshop._influence import Changes
    from livery.workshop._state import run_context

    run = run_context()
    base = ci_affected_base(root, run) if run is not None else ""
    if not base:
        return None
    git = GitOps(root)
    try:
        git.fetch()
        paths = tuple(git.changed_paths(base))
        before = git.merge_base(base)
    except GitError as error:
        print(
            f"  affected-legs: no diff against origin/{base}; everything is"
            f" judged ({error})"
        )
        return None
    return Changes(root, paths, before)


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


def _leg_plan(root: Path, base: str, run: RunContext) -> Plan:
    """Where this CI leg measures its change from, said in one line.

    The reflex's rule over CI's record ([livery.workshop._gate_record.leg_plan][]):
    the proved tree nearest HEAD's, an earlier push's merge included,
    or the merge base with *base* when the record proves none. A merge
    base git cannot compute (a shallow checkout, a base the fetch did
    not bring) falls open to everything, with git's words.
    """
    from livery.workshop import _gate_record
    from livery.workshop._git_ops import GitOps

    git = GitOps(root)
    git.fetch()
    step = _gate_record.leg_plan(root, git, base=base, branch=run.head_ref)
    count = f"{len(step.paths)} path(s) changed"
    if step.mode == _gate_record.MERGE_BASE:
        print(f"  from the merge base, tree {step.base_tree[:12]}: {step.why}; {count}")
    elif step.mode == "step":
        print(f"  since tree {step.base_tree[:12]} ({step.why}): {count}")
    else:
        print(f"  affected: {step.why}; failing open to everything")
    return step


def _affected(root: Path, step: Plan) -> tuple[Package, ...] | None:
    """The packages *step*'s paths reach; None means everything."""
    from livery.workshop._git_ops import GitOps
    from livery.workshop._graph import affected_by

    if step.mode == "full":
        return None
    scope = affected_by(root, GitOps(root), step.paths, before=step.base_tree)
    return None if scope is None else scope.packages


@task
def check(
    *paths: str,
    full: Annotated[bool, doc("run everything, whatever the record proves")] = False,
    fix: Forward[bool] = False,
    safe_fix: Annotated[
        bool, doc("fix, removing no code: safe for an edit in flight")
    ] = False,
    base: Annotated[str, doc("the branch the chain's root is taken from")] = "main",
    point: Annotated[str, doc("run the tests of this CI point, nightly say")] = "",
) -> None:
    """Run the gate: every registered check, the rewriters first under --fix.

    With *paths*, exactly those files are checked, or the files under
    those directories: every check whose claims reach one of them runs
    over them and no other, a check without claims does not run, and
    nothing is recorded as proved. ``--safe-fix`` runs the fixers in
    their in-flight mode, which removes no code, and refuses beside
    ``--fix``. ``--point`` hands a CI point to the test role, which
    selects that point's tests instead of the gate's.

    On a machine the gate is the reflex: it runs what the working
    tree changed since the nearest tree this checkout's own green
    gates proved, the packages that delta can influence (their
    dependents' closure), and records the working tree as proved on
    the local gate record ([livery.workshop._gate_record][]). A tree
    the record already proves runs nothing. The chain of records
    rests on a full gate here or on CI's record of the merge base
    with ``--base``, so a fresh branch off main starts proved. A
    change outside the packages configures every gate, so it runs
    everything and roots a new chain; ``--full`` runs everything
    whatever the record says. ty and pyrefly always check their
    configured whole either way.

    Inside CI, when the contract declares ``[ci] affected-legs``, a
    pull request's legs measure by the same rule from CI's own record:
    from the proved tree nearest the checkout's, an earlier push's
    merge among them, or from the merge base with the pull request's
    base when the record proves none. Without the key they run the
    whole workspace. The local record is never read or written there.

    ``--fix`` runs format and lint in their fix modes: each prints
    what it found, rewrites what is mechanical, and still fails on
    what is not. The two rewrite the same files, so under ``--fix``
    they run one after the other before the rest of the gate.

    ``--fix`` refuses inside CI: a runner's checkout is judged,
    never rewritten, because a fix there mutates a copy nobody
    keeps and hides the finding from the verdict. Run the fix
    locally and push the result.

    On a machine the state store is read from what the last ``sync``
    or ``start`` fetched ([livery.workshop._state.fetched_snapshot][]):
    nothing the gate runs reaches origin, and a checkout the store was
    never fetched into roots its chain on a full gate and says so.
    Inside CI the legs read the store as the run's own snapshot.
    """
    from contextlib import nullcontext

    from livery.workshop._checks import arguments_refusal, split_arguments
    from livery.workshop._state import fetched_snapshot, run_context

    _refuse_both(fix, safe_fix)
    paths, arguments = split_arguments(paths)
    if arguments:
        verb = f"{footman.prog()} check"
        fail(arguments_refusal(verb, _checks.checks_by_name().values()))
    if paths:
        _check_files(paths, fix=fix or safe_fix, safe=safe_fix, point=point)
        return
    root = workspace_root()
    scope = (
        fetched_snapshot(root)
        if root is not None and run_context() is None
        else nullcontext()
    )
    with scope:
        _run_check(
            full=full, fix=fix or safe_fix, base=base, point=point, safe=safe_fix
        )


def named_files(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    """*paths* as the files they name, root-relative: a file, or a directory's files.

    A directory stands for the files git holds under it, tracked or
    untracked and not ignored, or every file under it outside a git
    checkout. A path outside the root, or naming nothing, is left out.
    """
    import livery.toolroom.tools as tools

    found: set[str] = set()
    for path in paths:
        target = Path(path).resolve()
        try:
            relative = target.relative_to(root.resolve()).as_posix()
        except ValueError:
            continue
        if target.is_file():
            found.add(relative)
        elif target.is_dir():
            listing = tools.git.opts(cwd=root, recorded=False, nofail=True)(
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
                "--",
                relative,
            )
            if listing.code == 0:
                found.update(name for name in listing.stdout.split("\0") if name)
            else:
                found.update(
                    p.relative_to(root.resolve()).as_posix()
                    for p in target.rglob("*")
                    if p.is_file()
                )
    return tuple(sorted(name for name in found if (root / name).is_file()))


def _in_workspace(root: Path, path: str) -> bool:
    """Whether *path* is a file or a directory under *root*."""
    target = Path(path).resolve()
    return target.is_relative_to(root.resolve()) and target.exists()


def _check_files(
    paths: tuple[str, ...],
    *,
    fix: bool,
    safe: bool,
    point: str,
    judge: bool = True,
    only: frozenset[str] | None = None,
    arguments: tuple[str, ...] = (),
) -> None:
    """The gate over the named files: the walk narrowed to them, nothing recorded.

    A person's path that names nothing in the workspace refuses, since
    running nothing there would pass: a typo, or a tool's own argument
    after ``--``, which the checks never pass through. The post-edit
    hook, which judges nothing, may name a file already deleted.
    """
    import os

    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    if fix and os.environ.get("CI"):
        fail(
            "check --fix inside CI: the runner's checkout is judged, never rewritten."
            f" Run `{footman.prog()} check --fix` locally and push the result."
        )
    if judge:
        unknown = [path for path in paths if not _in_workspace(root, path)]
        if unknown:
            fail(
                "not a file or directory in the workspace:"
                f" {', '.join(unknown)}. Name files or directories; a check's"
                " own verb hands its tool the words after --:"
                f" `{footman.prog()} <role>.<tool> <paths> -- <words>`."
            )
    files = named_files(root, paths)
    if not files:
        print("  nothing to check: no file under the named paths")
        return
    from livery.workshop._coverage_store import workspace_suite
    from livery.workshop._provenance import unit_of

    packages = _packages()
    holders = {unit_of(root, packages, name)[0] for name in files}
    # The packages holding a named file, and the workspace's tests unit
    # when a root file is named; the bare root is a unit of no kind.
    subset = [package for package in packages if package in holders]
    suite = workspace_suite(root)
    if suite is not None and suite in holders:
        subset.append(suite)
    ctx = _context(
        subset=tuple(subset),
        fix=fix,
        files=files,
        safe=safe,
        point=point,
        arguments=arguments,
    )
    walk(ctx, judge=judge, only=only)


def run_checks(
    names: tuple[str, ...],
    paths: tuple[str, ...] = (),
    *,
    fix: bool = False,
    safe_fix: bool = False,
    point: str = "",
    arguments: tuple[str, ...] = (),
) -> None:
    """Run the checks *names* through the walk: a generated verb's body.

    Over the named paths, or the whole workspace; nothing is recorded
    as proved, since a part of the gate ran. *arguments*, the words
    after ``--`` on a check's own verb, reach that check's tool.
    """
    import os

    _refuse_both(fix, safe_fix)
    if (fix or safe_fix) and os.environ.get("CI"):
        fail(
            "a fix inside CI: the runner's checkout is judged, never rewritten."
            f" Run `{footman.prog()} check --fix` locally and push the result."
        )
    only = frozenset(names)
    if paths:
        _check_files(
            paths,
            fix=fix or safe_fix,
            safe=safe_fix,
            point=point,
            only=only,
            arguments=arguments,
        )
        return
    context = _context(
        fix=fix or safe_fix, safe=safe_fix, point=point, arguments=arguments
    )
    walk(context, only=only)


def run_on_packages(names: tuple[str, ...], packages: tuple[Package, ...]) -> None:
    """Run the checks *names* over *packages* whole; nothing recorded as proved.

    Each package check judges the packages it applies to, files and
    claims notwithstanding: the development build ``fm run`` starts
    with ([livery.workshop._run.develop][]).
    """
    walk(_context(subset=packages), only=frozenset(names))


def fix_files(paths: tuple[str, ...], *, safe: bool = True) -> None:
    """Run the gate's fixers over *paths*, judging nothing.

    Every check whose claims reach a file fixes it, and a file no claim
    reaches is left alone. *safe* keeps to the fixes a check marks
    safe, which never remove code an edit in flight still needs; an
    agent's post-edit hook runs it after each edit.
    """
    _check_files(paths, fix=True, safe=safe, point="", judge=False)


def _run_check(
    full: bool, fix: bool, base: str, point: str = "", safe: bool = False
) -> None:
    """The gate itself; `check` opens the state store's snapshot around it."""
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
    # and a narrowed nightly would be no nightly. A dispatched run sets
    # the record aside too: a person asked for this tree to be proved
    # now, and a skip would answer with what an earlier run said. Its
    # narrowing is the affected-legs decision's, which pays the full
    # gate on every event but a pull request and says so.
    nightly = _current_point() == "nightly"
    dispatched = run is not None and run.event == "workflow_dispatch"
    if nightly:
        print(
            "  nightly: the whole gate, the verified record and the narrowing set aside"
        )
    elif dispatched:
        print("  dispatched: the whole gate, the verified record set aside")
    if not (nightly or dispatched) and root_for_ci is not None and run is not None:
        from livery.workshop._state import remote_snapshot

        # The verified record and the records a skip reads: one listing.
        with remote_snapshot(root_for_ci, fetch=("verified", "coverage/")):
            proved = verified_already(root_for_ci)
            if proved is not None:
                _measure_unrecorded(root_for_ci, run, bases=record_bases(proved.branch))
                return
    ci_base = (
        ci_affected_base(root_for_ci, run)
        if not nightly and root_for_ci is not None and run is not None
        else ""
    )
    if ci_base and root_for_ci is not None and run is not None:
        from livery.workshop._influence import Changes

        print(f"  affected-legs: the scoped gate against origin/{ci_base}")
        step = _leg_plan(root_for_ci, ci_base, run)
        subset = _affected(root_for_ci, step)
        if subset is not None:
            packages = _packages()
            changes = Changes(root_for_ci, step.paths, step.base_tree)
            subset = _with_unstored_suites(root_for_ci, run, packages, subset)
            if not subset:
                # The same walk as a local run: the workspace checks whose
                # inputs changed judge, and no package's checks run.
                if _checks.workspace_selected(_context(subset=(), changes=changes)):
                    print("  no package affected: the workspace checks run")
                    _scoped_check((), fix=fix, point=point, changes=changes)
                else:
                    say_skipped(f"nothing affected: {_nothing_reason(step.paths)}")
                _verified.write_marker(
                    root_for_ci,
                    _verified.NOTHING,
                    leg=run.leg,
                    base_tree=step.base_tree,
                )
                return
            from livery.workshop._coverage_store import WORKSPACE_TESTS

            members = [p for p in subset if p.path != WORKSPACE_TESTS]
            if len(members) < len(packages):
                names = ", ".join(package.path for package in subset)
                print(f"  affected: {names}")
                _verified.write_marker(
                    root_for_ci,
                    _verified.AFFECTED,
                    tuple(package.path for package in subset),
                    leg=run.leg,
                    base_tree=step.base_tree,
                )
                _scoped_check(subset, fix=fix, point=point, changes=changes)
                return
    proved_tree = ""
    if run is None and root_for_ci is not None:
        from livery.workshop import _gate_record
        from livery.workshop._git_ops import GitOps
        from livery.workshop._graph import affected_from_paths

        git = GitOps(root_for_ci)
        if full:
            proved_tree = git.working_tree_id()
            print("  full: everything runs, whatever the record proves")
        else:
            reflex = _gate_record.plan(root_for_ci, git, base=base)
            proved_tree = reflex.tree
            if reflex.mode == "proved":
                say_skipped(
                    f"proved: tree {reflex.tree[:12]} is green already"
                    f" ({reflex.why}); nothing to run"
                )
                return
            if reflex.mode == "step":
                packages = _packages()
                print(
                    f"  since tree {reflex.base_tree[:12]}: {len(reflex.paths)}"
                    " path(s) changed"
                )
                scope = affected_from_paths(
                    root_for_ci,
                    packages,
                    reflex.paths,
                    git=git,
                    before=reflex.base_tree,
                )
                if scope is not None:
                    from livery.workshop._coverage_store import WORKSPACE_TESTS
                    from livery.workshop._influence import Changes

                    subset = scope.packages
                    members = [p for p in subset if p.path != WORKSPACE_TESTS]
                    changes = Changes(
                        root_for_ci, tuple(reflex.paths), reflex.base_tree
                    )
                    if not subset:
                        # No package's checks: the workspace checks whose
                        # inputs changed still judge what changed.
                        if not _checks.workspace_selected(
                            _context(subset=(), changes=changes)
                        ):
                            say_skipped(
                                "nothing affected: no file a check reads changed"
                                " since the proved tree"
                            )
                        else:
                            print("  no package affected: the workspace checks run")
                            _scoped_check((), fix=fix, safe=safe, changes=changes)
                        _remember_local(
                            root_for_ci,
                            run,
                            tree=reflex.tree,
                            packages=(),
                            base_tree=reflex.base_tree,
                        )
                        return
                    if len(members) < len(packages) or scope.tests:
                        names = ", ".join(package.path for package in subset)
                        print(f"  affected: {names}")
                        for path, files in sorted(scope.tests.items()):
                            print(
                                f"  {path}: {len(files)} test file(s) changed and"
                                " nothing else; they run alone"
                            )
                        for path in scope.examples:
                            print(
                                f"  {path}: its examples changed and nothing else;"
                                " they run and no suite"
                            )
                        tree = reflex.tree

                        def measure_step() -> None:
                            nonlocal tree
                            tree = _rewritten_tree(root_for_ci, run, tree)

                        _scoped_check(
                            subset,
                            fix=fix,
                            tests=scope.tests,
                            examples=scope.examples,
                            between=measure_step,
                            point=point,
                            safe=safe,
                            changes=changes,
                        )
                        _remember_local(
                            root_for_ci,
                            run,
                            tree=tree,
                            packages=tuple(package.path for package in subset),
                            base_tree=reflex.base_tree,
                        )
                        return
                    print("  every package is affected: everything runs")
                else:
                    print("  everything runs, and roots a new chain")
            else:
                print(f"  full: {reflex.why}")
    # The marker is a CI leg's fact for its metrics row and the stamp;
    # a local run leaves none, since an untracked root file would read
    # as a root change on the next affected gate.
    if root_for_ci is not None and run is not None:
        _verified.write_marker(root_for_ci, _verified.FULL, leg=run.leg)

    # The whole gate is the registry's walk, the tree measured between
    # the fixers and the judges so the row names what the judges read.
    def measure_whole() -> None:
        nonlocal proved_tree
        proved_tree = _rewritten_tree(root_for_ci, run, proved_tree)

    walk(_context(fix=fix, safe=safe, point=point), between=measure_whole)
    _remember_local(root_for_ci, run, tree=proved_tree, packages=None)


def _rewritten_tree(root: Path | None, run: RunContext | None, tree: str) -> str:
    """The working tree's id after the rewriters ran: the tree the judges read.

    The plan measures the tree before a fix run rewrites files, and a
    row naming that tree would leave the proved tree one rewrite
    behind the commit that follows. Measured between the rewriters and
    the judges, so an edit made while the judges run stays unproved.
    Outside a local run there is no row, and *tree* stands as given.
    """
    if root is None or run is not None or not tree:
        return tree
    from livery.workshop._git_ops import GitOps

    return GitOps(root).working_tree_id()


def _remember_local(
    root: Path | None,
    run: RunContext | None,
    *,
    tree: str,
    packages: tuple[str, ...] | None,
    base_tree: str = "",
) -> None:
    """Record a green local gate on this machine's gate record; CI never writes it."""
    if root is None or run is not None or not tree:
        return
    from livery.workshop import _gate_record
    from livery.workshop._git_ops import GitOps

    print(
        _gate_record.remember(
            root, GitOps(root), tree=tree, packages=packages, base_tree=base_tree
        )
    )


def _current_point() -> str:
    """The point this process runs at, as the job runner named it; gate outside CI."""
    import os

    from livery.workshop._state import POINT_VARIABLE

    return os.environ.get(POINT_VARIABLE, "gate")


def say_skipped(text: str) -> None:
    """Print a skip, and put it on the timeline as an event.

    Work left undone because something already proves it is the one thing
    an account of a run must not show as merely fast: a gate that skipped
    and a gate that flew look identical from outside. The printed line is
    unchanged, so a log reads as it always did, and the mark is what makes
    the picture honest about why a leg was quick.

    Marks only inside a task, since that is where a timeline exists; a
    plain call outside a run prints and nothing more.
    """
    print(f"  {text}")
    if footman.current().in_task:
        footman.mark(f"skipped: {text}")


def _nothing_reason(changed: tuple[str, ...]) -> str:
    """Why nothing is affected: only prose and site files changed, or nothing."""
    from livery.workshop._graph import is_prose, is_site

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
    from livery.extensions.python._backend import suites_of
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
    from livery.extensions.python._backend import units_of
    from livery.workshop import _verified

    packages = _packages()
    # Only a suite a listed test check runs has a record to miss: a
    # workspace that lists no python test check measures nothing here.
    tested = _checks.tested(units_of(root, packages))
    units = _unrecorded(root, run, packages, tested, bases=bases)
    if not units:
        _verified.write_marker(root, _verified.VERIFIED, leg=run.leg)
        return
    names = ", ".join(unit.path for unit in units)
    print(f"  measuring: {names} run for their lines alone; the gate is proved")
    _verified.write_marker(
        root, _verified.MEASURED, tuple(unit.path for unit in units), leg=run.leg
    )
    _python.run_test(packages=units, root=root)


def _scoped_check(
    subset: tuple[Package, ...],
    *,
    fix: bool = False,
    tests: Mapping[str, tuple[str, ...]] | None = None,
    examples: tuple[str, ...] = (),
    between: Callable[[], None] | None = None,
    point: str = "",
    safe: bool = False,
    changes: Changes | None = None,
) -> None:
    """The gate over *subset* only: the registry's walk, narrowed.

    A workspace check with declared inputs judges what *changes*
    touched of them, and nothing when they touched none
    ([livery.workshop._checks.selected][]). *tests* names, per package
    path, the test files that stand for the package's suite in this
    run; *examples* the packages whose examples alone changed;
    *between* runs after the fixers under ``--fix``, as
    [livery.workshop._quality.walk][] says.
    """
    walk(
        _context(
            subset=subset,
            fix=fix,
            tests=tests,
            examples=examples,
            point=point,
            safe=safe,
            changes=changes,
        ),
        between=between,
    )


coverage = group("coverage", help="The measured union and its floors")


@coverage.task(name="leg", hidden=True)
def coverage_leg(
    *,
    job: Annotated[str, doc("the job's name as the forge lists it")] = "",
    trace: Annotated[Path, doc("the trace the gate wrote")] = Path(
        "fm-profile-check.json"
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

    import livery.toolroom.tools as toolroom

    result = toolroom.git.opts(cwd=root, nofail=True, recorded=False)(
        "config", "user.name"
    )
    name = result.stdout.strip() if result.code == 0 else ""
    return name or os.environ.get("USER", "unknown")


caches = group("caches", help="The workspace's derived caches")


@caches.task(name="clear")
def caches_clear() -> None:
    """Remove build artifacts and checker caches.

    A tool that is a check of its own keeps its cache under
    ``.workshop/.cache/<tool>/``, which goes whole; the build
    artifacts are named.
    """
    import shutil

    root = workspace_root()
    if root is None:
        return
    for name in ("dist", ".workshop/.cache"):
        shutil.rmtree(root / name, ignore_errors=True)
