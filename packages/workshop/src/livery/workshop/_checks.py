"""The check registry: what the quality gate runs, one record per check.

A check is one tool's judgment over the workspace or over one
package: ruff's format pass, the render drift comparison, ctest
over a native member. Each is a [livery.workshop._checks.CheckRecord][]
registered through [livery.workshop._checks.register_check][], and
the gate is the walk over the registry: every applicable check
green, the exit code the verdict. The workshop registers the
builtin records at import, in the order the gate runs its
rewriters; a layer's plugin registers its own at mount, the way it
registers a kind, and re-registering a name replaces the record.

Kinds gate on roles, never on tools. A kind's
[livery.workshop._kinds.CiContract][] names the roles that apply to
it, a role is the set of checks that implement it, and a check
names the kinds it judges, so a C++ kind says ``format`` applies
and whether that means ruff over its recipe or clang-format over
its sources is the checks' business.

Every registered check is also a hidden task, `checks.<name>`, and
the gate schedules those tasks: the rewriters serially before any
judge reads the tree ([livery.workshop._checks.rewriters][]), then
every judge together ([livery.workshop._checks.judges][]), each with
its own report row. A check that must follow another on the same
package, a build after its configure, names it in ``after`` and the
scheduler orders them.
"""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from livery.footman import context, doc, fail, group, prog

if TYPE_CHECKING:
    from livery.workshop._packages import Package

#: A check's scope: the whole workspace in one run, or one package at a time.
WORKSPACE = "workspace"
PACKAGE = "package"

#: How a workspace check narrows under a package-scoped gate.
PATHS = "paths"
PACKAGES = "packages"
NONE = "none"

#: The layer the builtin records belong to.
BASE_LAYER = "livery.workshop"


@dataclass(frozen=True)
class GateContext:
    """One gate run's inputs, handed to every check.

    Attributes:
        root: The workspace root.
        packages: Every package the workspace carries.
        subset: The packages a scoped gate judges, or None for the
            whole gate.
        tests: Per package path, the test files that stand for the
            package's suite in this run.
        pages: Per package path, the docs pages an examples harness
            narrows to.
        fix: Whether the rewriters run in their fix mode.
        check_style: False when a rewrite pass already ran, so the
            style checks may skip re-judging what they just wrote.
        package: The package a per-package check is judging, else
            None.
        selection: The test files selected for that package, relative
            to it, else empty.
    """

    root: Path
    packages: tuple[Package, ...]
    subset: tuple[Package, ...] | None = None
    tests: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    pages: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    fix: bool = False
    check_style: bool = True
    package: Package | None = None
    selection: tuple[str, ...] = ()

    @property
    def scoped(self) -> bool:
        """Whether this is a package-scoped gate."""
        return self.subset is not None

    @property
    def judged(self) -> tuple[Package, ...]:
        """The packages under judgment: the subset, or every package."""
        return self.packages if self.subset is None else self.subset

    def for_package(self, package: Package) -> GateContext:
        """This context narrowed to one package and its selected tests."""
        files = self.tests.get(package.path, ())
        relative = tuple(path[len(package.path) + 1 :] for path in files)
        return replace(self, package=package, selection=relative)


@dataclass(frozen=True)
class CheckRecord:
    """One check, completely.

    Attributes:
        name: The check's name, what the gate output and a skip print.
        role: What the check implements: ``format``, ``lint``,
            ``typecheck``, ``typecomplete``, ``test``, ``build``,
            ``render``, ``provenance`` or ``layering``. A kind's CI
            contract names roles, and a role a kind does not carry
            skips by name.
        run: The judging callable; a refusal is its verdict.
        scope: ``workspace`` for a check that judges the whole in one
            run, ``package`` for one that judges one package at a
            time.
        narrowing: How a workspace check narrows under a scoped gate:
            ``paths`` (the subset's directories), ``packages`` (the
            subset's members) or ``none`` (the whole is always
            checked). A package check narrows by its packages.
        fix: The rewriting callable when the tool can rewrite, run
            serially before any judge under ``--fix``; None for a
            check that only judges.
        kinds: The package kinds a package check judges, matched
            against a package's kind chain, so a child kind takes its
            parent's checks. Empty for a workspace check.
        tests_only: Whether a package check still runs when the
            package's change is confined to its tests. A build and
            the tests do; a formatter does not.
        in_scoped: Whether a workspace check runs in a package-scoped
            gate at all. The render gate does not: its inputs are
            root answers a package-scoped change cannot touch.
        after: The checks this one runs after, by name: a build
            after its configure, a ctest after its build. Each runs
            through its task before this one's body, once per gate
            whoever asks first.
        layer: The layer that registered the check, named when a
            narrowing is printed.
    """

    name: str
    role: str
    run: Callable[[GateContext], None]
    scope: str = WORKSPACE
    narrowing: str = NONE
    fix: Callable[[GateContext], None] | None = None
    kinds: tuple[str, ...] = ()
    tests_only: bool = False
    in_scoped: bool = True
    after: tuple[str, ...] = ()
    layer: str = BASE_LAYER


_CHECKS: dict[str, CheckRecord] = {}

#: Builtin checks a layer withdrew, name to the layer that did: the
#: gate names them, so a lighter gate is a legible decision.
_WITHDRAWN: dict[str, str] = {}


def register_check(record: CheckRecord) -> None:
    """Register *record*; a name already registered is replaced.

    Layers call this from their plugin at mount. Replacing is how a
    layer swaps a tool under a role, and how a test injects a fake;
    a package check names at least one kind, since a check that
    judges packages and applies to none never runs.
    """
    if record.scope not in (WORKSPACE, PACKAGE):
        fail(
            f"check {record.name!r}: scope is {WORKSPACE!r} or {PACKAGE!r},"
            f" not {record.scope!r}"
        )
    if record.narrowing not in (PATHS, PACKAGES, NONE):
        fail(
            f"check {record.name!r}: narrowing is {PATHS!r}, {PACKAGES!r} or"
            f" {NONE!r}, not {record.narrowing!r}"
        )
    if record.scope == PACKAGE and not record.kinds:
        fail(
            f"check {record.name!r} judges packages and names no kind: a"
            " package check applies to the kinds it lists"
        )
    _CHECKS[record.name] = record
    _WITHDRAWN.pop(record.name, None)
    _ensure_task(record)


def unregister_check(name: str, *, by: str = "") -> None:
    """Drop the check *name*; unknown names refuse naming the registry.

    *by* names the layer withdrawing a check it did not register, so
    the gate can print who narrowed it.
    """
    if name not in _CHECKS:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    record = _CHECKS.pop(name)
    if by and record.layer != by:
        _WITHDRAWN[name] = by


def narrowings() -> tuple[str, ...]:
    """The gate's lines naming what a layer added, replaced or withdrew.

    A check the base did not register names its layer; a builtin a
    layer withdrew names the layer too. Empty for the base alone.
    """
    lines = [
        f"  {record.name}: registered by {record.layer}"
        for record in _CHECKS.values()
        if record.layer != BASE_LAYER
    ]
    lines += [f"  {name}: withdrawn by {layer}" for name, layer in _WITHDRAWN.items()]
    return tuple(lines)


def check_names() -> tuple[str, ...]:
    """Every registered check, in registration order."""
    return tuple(_CHECKS)


def check_for(name: str) -> CheckRecord:
    """The record named *name*; refusal names the registry."""
    record = _CHECKS.get(name)
    if record is None:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    return record


def roles() -> tuple[str, ...]:
    """The roles the registered checks implement, sorted."""
    return tuple(sorted({record.role for record in _CHECKS.values()}))


def verify_roles() -> None:
    """Refuse a kind whose CI contract names a role no check implements.

    A kind gates on roles, so a verb in its contract that no
    registered check answers would skip or run nothing in silence;
    the refusal names the vocabulary instead.
    """
    from livery.workshop._kinds import all_kinds

    known = set(roles())
    for kind in all_kinds():
        unknown = [verb for verb in kind.ci.check_verbs if verb not in known]
        if unknown:
            fail(
                f"kind {kind.name!r} gates on {', '.join(unknown)}, which no"
                f" registered check implements; the roles are"
                f" {', '.join(sorted(known))}"
            )


def _applies(record: CheckRecord, package: Package) -> bool:
    """Whether a package check judges *package*, by its kind chain."""
    from livery.workshop._kinds import kind_chain, kind_names

    if package.kind not in kind_names():
        return False
    chain = {kind.name for kind in kind_chain(package.kind)}
    return any(kind in chain for kind in record.kinds)


def _package_records(ctx: GateContext, package: Package) -> list[CheckRecord]:
    """The package checks that judge *package* in this run, in order."""
    tests_only = ctx.scoped and package.path in ctx.tests
    return [
        record
        for record in _CHECKS.values()
        if record.scope == PACKAGE
        and _applies(record, package)
        and (record.tests_only or not tests_only)
    ]


def _members(ctx: GateContext) -> tuple[Package, ...]:
    """The packages under judgment that a kind owns: the tests unit is none's."""
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    return tuple(package for package in ctx.judged if package.path != WORKSPACE_TESTS)


#: The gate run's context while its checks run, read by every check's
#: task. One gate runs per process, and the tasks it schedules run in
#: this process's threads, so a module value is the seam; the walk
#: sets it around each half and clears it after.
_CURRENT: GateContext | None = None

#: The registered checks as hidden tasks, one per name: the task is
#: what the gate schedules, so each check gets its own report row and
#: its prints reach the console, and `fm checks.<name>` runs one
#: check by hand. The body reads the record by name when it runs, so
#: re-registering a name replaces the record and keeps the task.
checks = group("checks", help="The registered checks, one task each", hidden=True)
_TASKS: dict[str, Any] = {}


@contextmanager
def current(ctx: GateContext) -> Generator[None, None, None]:
    """Make *ctx* the context the checks' tasks read, for the block."""
    global _CURRENT
    before = _CURRENT
    _CURRENT = ctx
    try:
        yield
    finally:
        _CURRENT = before


def _context_now() -> GateContext:
    if _CURRENT is None:
        fail(
            f"a check's task ran outside the gate: `{prog()} checks.<name>` is"
            f" the gate's own spelling and runs inside `{prog()} check`"
        )
    return _CURRENT


def _ensure_task(record: CheckRecord) -> None:
    """Register the hidden task for *record*'s name, once."""
    if record.name in _TASKS:
        return
    name = record.name

    def body(fix: Annotated[bool, doc("run the check's fix mode")] = False) -> None:
        run_check(name, _context_now(), fix=fix)

    body.__name__ = name.replace("-", "_")
    body.__doc__ = f"Run the {name} check over the gate's context."
    _TASKS[name] = checks.task(name=name)(body)


def task_for(name: str) -> Any:
    """The hidden task that runs the check *name*; refusal names the registry."""
    check_for(name)
    return _TASKS[name]


def run_check(name: str, ctx: GateContext, *, fix: bool = False) -> None:
    """Run the check *name* over *ctx*: its judge, or its fix mode.

    A workspace check runs once; a package check runs once per
    package it judges, in package order, each announced by name. A
    check without a fix mode does nothing in fix mode, and is judged
    after the rewriters like every other judge. The checks named in
    ``after`` run first, through their tasks: a call from a task body
    runs the earlier check here or waits on the copy the block
    already started, so a build never reads a directory its
    configure has not made.
    """
    from livery.workshop._kinds import kind_for

    record = check_for(name)
    body = record.fix if fix else record.run
    if body is None:
        return
    if not fix and context.current().in_task:
        # Inside a run the scheduler dedups a task call, so the earlier
        # check runs once for the whole gate; outside one (a test
        # driving a check by hand) there is no run to dedup against,
        # and the caller orders the checks itself.
        for earlier in record.after:
            task_for(earlier)()
    if record.scope == WORKSPACE:
        body(ctx)
        return
    for package in _members(ctx):
        if record in _package_records(ctx, package):
            kind = kind_for(package.kind).name
            print(f"  {record.name}: {package.path} runs ({kind} kind)")
            body(ctx.for_package(package))


def _applies_now(record: CheckRecord, ctx: GateContext) -> bool:
    if record.scope == WORKSPACE:
        return record.in_scoped or not ctx.scoped
    return any(record in _package_records(ctx, p) for p in _members(ctx))


def rewriters(ctx: GateContext) -> tuple[str, ...]:
    """The checks whose fix mode runs, serially and in order, under --fix.

    Empty unless the gate runs in its fix mode.
    """
    if not ctx.fix:
        return ()
    return tuple(
        record.name
        for record in _CHECKS.values()
        if record.fix is not None and _applies_now(record, ctx)
    )


def judges(ctx: GateContext) -> tuple[str, ...]:
    """The checks the gate judges together, in registration order.

    A check that rewrote in this run is not judged again: re-judging
    the style it just wrote would only spend time agreeing.
    """
    names = []
    for record in _CHECKS.values():
        if not _applies_now(record, ctx):
            continue
        if record.fix is not None and (ctx.fix or not ctx.check_style):
            continue
        names.append(record.name)
    return tuple(names)


def _register_builtin() -> None:
    """Register the workshop's own checks, in the order the rewriters run.

    The python checks judge the whole tree or the scoped subset's
    directories and gate each package by its kind's roles; the native
    checks judge one package at a time. A body resolves its backend
    function on the module when it runs, never at registration, so a
    test that patches ``_python.run_format`` sees its fake run.
    """
    from livery.workshop._backends import _cpp_conan, _python
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._kinds import gated, is_python_kind

    def unit(ctx: GateContext) -> tuple[Package, ...]:
        return tuple(p for p in ctx.judged if p.path == WORKSPACE_TESTS)

    def paths(ctx: GateContext) -> tuple[str, ...]:
        if ctx.subset is None:
            return _python.SRC
        return _python.package_paths(ctx.subset)

    def python_members(ctx: GateContext, role: str) -> tuple[Package, ...]:
        return tuple(p for p in gated(_members(ctx), role) if is_python_kind(p.kind))

    def format_run(ctx: GateContext) -> None:
        _python.run_format(check=True, paths=paths(ctx))

    def format_fix(ctx: GateContext) -> None:
        _python.run_format(check=False, paths=paths(ctx))

    def lint_run(ctx: GateContext) -> None:
        _python.run_lint(fix=False, paths=paths(ctx))

    def lint_fix(ctx: GateContext) -> None:
        _python.run_lint(fix=True, paths=paths(ctx))

    def typecheck_run(ctx: GateContext) -> None:
        if not ctx.scoped:
            _python.run_typecheck()
            return
        judged = python_members(ctx, "typecheck") + unit(ctx)
        _python.run_typecheck(paths=_python.package_paths(judged))

    def typecomplete_run(ctx: GateContext) -> None:
        _python.run_typecomplete(python_members(ctx, "typecomplete"))

    def test_run(ctx: GateContext) -> None:
        tested = python_members(ctx, "test") + unit(ctx)
        if not ctx.scoped:
            _python.run_test(packages=tested, root=ctx.root)
            return
        narrowed = [page for found in ctx.pages.values() for page in found]
        _python.run_test(
            *_python.page_arguments(narrowed),
            packages=tested,
            root=ctx.root,
            scoped=True,
            selection=ctx.tests,
        )

    def render_run(ctx: GateContext) -> None:
        from livery.workshop import _quality

        _quality.template_check()

    def provenance_run(ctx: GateContext) -> None:
        from livery.workshop import _provenance

        _provenance.provenance_check()

    def provenance_fix(ctx: GateContext) -> None:
        from livery.workshop import _provenance

        _provenance.provenance_check(fix=True)

    def layering_run(ctx: GateContext) -> None:
        from livery.workshop._packages import verify_workspace

        verify_workspace(ctx.root)

    def layering_fix(ctx: GateContext) -> None:
        from livery.workshop._layers import write_layers
        from livery.workshop._packages import verify_workspace, write_edges
        from livery.workshop._uv import run_uv

        for line in write_layers(ctx.root):
            print(line)
        written = write_edges(ctx.root)
        for line in written:
            print(line)
        if any("pyproject.toml" in line for line in written):
            # A new requirement moves the lock; the fix leaves the tree
            # consistent, as a person would after editing by hand.
            run_uv("lock", root=ctx.root)
        verify_workspace(ctx.root)

    def package_of(ctx: GateContext) -> Package:
        assert ctx.package is not None
        return ctx.package

    def clang_format_run(ctx: GateContext) -> None:
        _cpp_conan.format_check(package_of(ctx), fix=False)

    def clang_format_fix(ctx: GateContext) -> None:
        _cpp_conan.format_check(package_of(ctx), fix=True)

    def configure_run(ctx: GateContext) -> None:
        _cpp_conan.configure(package_of(ctx))

    def build_run(ctx: GateContext) -> None:
        _cpp_conan.compile(package_of(ctx))

    def ctest_run(ctx: GateContext) -> None:
        package = package_of(ctx)
        _cpp_conan.test(
            package,
            ctx.root,
            selection=ctx.selection,
            pages=ctx.pages.get(package.path, ()),
        )

    def clang_tidy_run(ctx: GateContext) -> None:
        _cpp_conan.lint(package_of(ctx), ctx.root)

    native = ("cpp-conan", "python-nanobind")
    for record in (
        CheckRecord("format", "format", format_run, narrowing=PATHS, fix=format_fix),
        CheckRecord(
            "clang-format",
            "format",
            clang_format_run,
            scope=PACKAGE,
            fix=clang_format_fix,
            kinds=native,
        ),
        CheckRecord("lint", "lint", lint_run, narrowing=PATHS, fix=lint_fix),
        CheckRecord("typecheck", "typecheck", typecheck_run, narrowing=PATHS),
        CheckRecord(
            "typecomplete", "typecomplete", typecomplete_run, narrowing=PACKAGES
        ),
        CheckRecord("test", "test", test_run, narrowing=PACKAGES),
        CheckRecord("template_check", "render", render_run, in_scoped=False),
        CheckRecord(
            "provenance_check",
            "provenance",
            provenance_run,
            fix=provenance_fix,
            in_scoped=False,
        ),
        CheckRecord("layering", "layering", layering_run, fix=layering_fix),
        CheckRecord(
            "configure",
            "build",
            configure_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            tests_only=True,
        ),
        CheckRecord(
            "build",
            "build",
            build_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            tests_only=True,
            after=("configure",),
        ),
        CheckRecord(
            "ctest",
            "test",
            ctest_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            tests_only=True,
            after=("build",),
        ),
        CheckRecord(
            "clang-tidy",
            "lint",
            clang_tidy_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            after=("configure",),
        ),
    ):
        register_check(record)


_register_builtin()
