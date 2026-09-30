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
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any

from livery.footman import context, doc, fail, group, prog
from livery.workshop import _fragments, _slots
from livery.workshop._fragments import Fragment

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
        examples: The package paths whose changed files are examples
            and nothing else: their examples run and no suite.
        fix: Whether the rewriters run in their fix mode.
        files: The files the run is limited to, root-relative, from
            `fm check <paths>`; empty when the scope decides.
        safe: Whether the fixers run in their in-flight mode, which
            removes no code: ``--safe-fix``.
        point: The CI point whose tests the test role selects; empty
            for the gate's own.
        catalogue: Every file the run may read, by unit, each with its
            category, listed once per walk by
            [livery.workshop._checks.catalogue][]; None when there is
            no listing, and then every check that applies runs.
        package: The package a per-package check is judging, else
            None.
        selection: The test files selected for that package, relative
            to it, else empty.
    """

    root: Path
    packages: tuple[Package, ...]
    subset: tuple[Package, ...] | None = None
    tests: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    examples: tuple[str, ...] = ()
    fix: bool = False
    files: tuple[str, ...] = ()
    safe: bool = False
    point: str = ""
    catalogue: Mapping[str, tuple[tuple[str, str], ...]] | None = None
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
class Option:
    """One option a package may set on a check, in its ``[checks.<name>]`` table.

    Attributes:
        name: The option's key, kebab-case.
        kind: ``bool``, ``str`` or ``int``; a value of another type
            refuses naming the option.
        default: What the check reads when the package says nothing.
        doc: One line saying what the option changes.
    """

    name: str
    kind: str
    default: object
    doc: str = ""


#: Every check carries this option: a package that turns a check off
#: is skipped by name in the gate's output.
ENABLED = Option("enabled", "bool", True, "whether the check judges this package")


@dataclass(frozen=True)
class Claim:
    """One category a check judges, and the rules it withholds there.

    Attributes:
        category: The category, ``source``, ``test``, ``configuration``.
        ignore: Rule codes the check does not apply to that category;
            a ruff-shaped tool renders them as its per-file ignores.
        suffixes: The file suffixes the check reads in the category,
            ``(".py", ".pyi")`` for a python tool; empty reads every
            file. A category names a role, not a language: a native
            package's ``source`` is C++ and its ``conanfile.py`` is
            configuration, and the suffix keeps a python tool's claim
            to the files it reads.
    """

    category: str
    ignore: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


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
        kinds: The package kinds the check judges, matched against a
            package's kind chain, so a child kind takes its parent's
            checks. A package check runs per member of these kinds; a
            workspace check names them so its tools and claims reach
            those kinds, or leaves them empty.
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
        tools: The tools the check runs, as the tool profile names
            them, ``("ruff",)``; each reaches the profile for every kind
            the check judges, naming ``check <name>`` as its site, and
            leaves it when the check is unregistered.
        options: The options a package may set under
            ``[checks.<name>]``; ``enabled`` is every check's.
        contributions: ``(slot, value)`` pairs the record puts into
            slots at registration, the dev group's lines say; withdrawn
            with the record.
        fragments: The configuration the render manages for the
            check, one per rendered file it has something to say in;
            composed in check-name order, judged by the drift gate,
            gone from the next render with the record.
        extension: The editor extension's marketplace id, when the
            record carries a verified one; the rendered
            recommendations list names these and nothing else.
        claims: The categories the check judges, each with the rules
            it withholds there and the suffixes it reads; a check
            judges every file its claims reach and no other, and the
            render derives a ruff-shaped tool's per-file ignores from
            them.
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
    tools: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()
    contributions: tuple[tuple[str, object], ...] = ()
    fragments: tuple[Fragment, ...] = ()
    extension: str = ""
    claims: tuple[Claim, ...] = ()


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
    try:
        _fragments.verify(record.fragments, record.name)
    except ValueError as error:
        fail(str(error))
    from livery.workshop._categories import known_categories

    known = known_categories()
    for claim in record.claims:
        if claim.category not in known:
            fail(
                f"check {record.name!r} claims {claim.category!r}, which no category"
                f" table knows; the categories are {', '.join(sorted(known))}"
            )
    _CHECKS[record.name] = record
    _WITHDRAWN.pop(record.name, None)
    _contribute(record)
    _ensure_task(record)


def _contribute(record: CheckRecord) -> None:
    _slots_withdraw(record.name)
    for slot, value in record.contributions:
        _slots.contribute(slot, value, layer=record.layer, by=record.name)


def _slots_withdraw(name: str) -> None:
    for slot in _slots.slots():
        _slots.withdraw(slot, by=name)


def _typed(option: Option, value: object) -> bool:
    expected = {"bool": bool, "str": str, "int": int}[option.kind]
    if option.kind == "int" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def option_value(record: CheckRecord, package: Package, name: str) -> object:
    """What *package* sets for *record*'s option *name*, or the option's default.

    Refuses an option the record does not declare and a value of the
    wrong type, naming the record's options.
    """
    declared = {option.name: option for option in (*record.options, ENABLED)}
    if name not in declared:
        fail(
            f"check {record.name!r} declares no option {name!r}; its options are"
            f" {', '.join(declared)}"
        )
    option = declared[name]
    for check, options in package.checks:
        if check != record.name:
            continue
        for key, value in options:
            if key != name:
                continue
            if not _typed(option, value):
                fail(
                    f"{package.path}/workshop.toml: [checks.{record.name}] {name} is"
                    f" {option.kind}, not {value!r}"
                )
            return value
    return option.default


def option_problems(packages: tuple[Package, ...]) -> list[str]:
    """Every ``[checks.<name>]`` entry no record declares, one line each.

    Read by the layering check, so a package that sets an option on a
    check that does not exist, or one the check does not declare, is
    refused with the vocabulary named.
    """
    problems: list[str] = []
    for package in packages:
        for check, options in package.checks:
            record = _CHECKS.get(check)
            if record is None:
                problems.append(
                    f"{package.path}/workshop.toml: [checks.{check}] names no"
                    f" registered check; the checks are {', '.join(_CHECKS)}"
                )
                continue
            declared = {option.name: option for option in (*record.options, ENABLED)}
            for key, value in options:
                option = declared.get(key)
                if option is None:
                    problems.append(
                        f"{package.path}/workshop.toml: [checks.{check}] declares no"
                        f" option {key!r}; its options are {', '.join(declared)}"
                    )
                elif not _typed(option, value):
                    problems.append(
                        f"{package.path}/workshop.toml: [checks.{check}] {key} is"
                        f" {option.kind}, not {value!r}"
                    )
    return problems


def enabled(name: str, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """*packages* minus those that turned the check *name* off, each skip printed."""
    record = check_for(name)
    kept: list[Package] = []
    for package in packages:
        if option_value(record, package, "enabled"):
            kept.append(package)
        else:
            print(
                f"  {name}: {package.path} skips (turned off in"
                f" {package.path}/workshop.toml)"
            )
    return tuple(kept)


def tools_for_kind(kind_name: str) -> tuple[tuple[str, str], ...]:
    """The tools the checks judging *kind_name* run, as ``(tool, check)`` pairs.

    A check judges a kind when the kind's chain meets the check's
    ``kinds``; a workspace check names the kinds its bodies judge the
    same way.
    """
    from livery.workshop._kinds import kind_chain, kind_names

    if kind_name not in kind_names():
        return ()
    chain = {record.name for record in kind_chain(kind_name)}
    found: list[tuple[str, str]] = []
    for record in _CHECKS.values():
        if any(kind in chain for kind in record.kinds):
            found.extend((tool, record.name) for tool in record.tools)
    return tuple(found)


def unregister_check(name: str, *, by: str = "") -> None:
    """Drop the check *name*; unknown names refuse naming the registry.

    *by* names the layer withdrawing a check it did not register, so
    the gate can print who narrowed it.
    """
    if name not in _CHECKS:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    record = _CHECKS.pop(name)
    _slots_withdraw(name)
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


def snapshot() -> tuple[dict[str, CheckRecord], dict[str, str]]:
    """The registry's state, for [livery.workshop._checks.restore][] to put back."""
    return dict(_CHECKS), dict(_WITHDRAWN)


def restore(state: tuple[dict[str, CheckRecord], dict[str, str]]) -> None:
    """Put the registry back to *state*, the slot contributions with it.

    A test that replaces a record with one that contributes nothing
    would otherwise leave the slots short of the real record's lines
    for every test after it on the same worker.
    """
    records, withdrawn = state
    for name in list(_CHECKS):
        _slots_withdraw(name)
    _CHECKS.clear()
    _CHECKS.update(records)
    _WITHDRAWN.clear()
    _WITHDRAWN.update(withdrawn)
    for record in records.values():
        _contribute(record)


def _reaches(claim: Claim, relative: str) -> bool:
    """Whether the claim admits the file's suffix; no suffixes admits every file."""
    return not claim.suffixes or PurePosixPath(relative).suffix in claim.suffixes


def _admits(claim: Claim, pattern: str) -> bool:
    """Whether a category pattern can name a file the claim reads.

    A pattern ending in a literal suffix outside the claim's, a
    native kind's ``tests/**/*.cpp`` against a python claim, renders
    no ignore; a directory pattern renders as it is, since the tool
    reads only its own files under it.
    """
    return not PurePosixPath(pattern).suffix or _reaches(claim, pattern)


def _package_files(package: Package) -> tuple[str, ...]:
    """The files git holds under the package: tracked, or untracked and not ignored.

    Sorted, relative to the package. A build tree or a cache under the
    package is ignored, so no claim reaches it; a tracked file deleted
    from the tree is left out.
    """
    from livery.toolroom import tools

    listing = tools.git.opts(cwd=package.directory, recorded=False)(
        "ls-files", "--cached", "--others", "--exclude-standard", "-z"
    )
    names = sorted({name for name in listing.stdout.split("\0") if name})
    return tuple(name for name in names if (package.directory / name).is_file())


def judged_files(record: CheckRecord, package: Package) -> tuple[str, ...]:
    """The files of *package* the check's claims reach, relative to it, sorted.

    Every file git holds under the package directory is categorised
    and kept when a claim names its category and admits its suffix; a
    record without claims judges nothing by this measure, which is
    what a check that runs a tool over directories looks like from
    here.
    """
    from livery.workshop._categories import category_of

    if not record.claims or not package.directory.is_dir():
        return ()
    found: list[str] = []
    for relative in _package_files(package):
        category = category_of(package, relative).name
        if any(c.category == category and _reaches(c, relative) for c in record.claims):
            found.append(relative)
    return tuple(found)


def claimants(package: Package, path: str) -> tuple[str, ...]:
    """The checks whose claims reach *path* in *package*, by name, sorted."""
    from livery.workshop._categories import category_of

    category = category_of(package, path).name
    names = []
    for record in _CHECKS.values():
        if not any(c.category == category and _reaches(c, path) for c in record.claims):
            continue
        if record.kinds and not _applies(record, package):
            continue
        names.append(record.name)
    return tuple(sorted(names))


def per_file_ignores(kinds: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    """The per-file ignores the claims render, pattern to rule codes, sorted.

    For each present kind and the workspace's own unit, every claim
    that withholds rules on a category maps to that category's
    patterns in the kind's table, a package's under ``packages/*/``
    and the root's as they are; two checks claiming one category
    under different rules land in one entry with both sets.
    """
    from livery.workshop._categories import WORKSPACE, category_rules
    from livery.workshop._kinds import kind_chain, kind_names

    table: dict[str, set[str]] = {}
    units = [(kind, "packages/*/") for kind in kinds if kind in kind_names()]
    units.append((WORKSPACE, ""))
    for kind, prefix in units:
        chain = (
            {record.name for record in kind_chain(kind)}
            if kind != WORKSPACE
            else {WORKSPACE}
        )
        for record in _CHECKS.values():
            if (
                kind != WORKSPACE
                and record.kinds
                and not any(k in chain for k in record.kinds)
            ):
                continue
            for claim in record.claims:
                if not claim.ignore:
                    continue
                for rule in category_rules(kind):
                    if rule.category != claim.category or rule.pattern == "**":
                        continue
                    if not _admits(claim, rule.pattern):
                        continue
                    table.setdefault(prefix + rule.pattern, set()).update(claim.ignore)
    return [(pattern, tuple(sorted(codes))) for pattern, codes in sorted(table.items())]


def checks_by_name() -> dict[str, CheckRecord]:
    """Every registered record by name, in registration order."""
    return dict(_CHECKS)


def extensions() -> tuple[str, ...]:
    """The editor extension ids the registered checks carry, sorted, each once."""
    return tuple(sorted({r.extension for r in _CHECKS.values() if r.extension}))


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
            if not reads_files(record, ctx, (package.path,)):
                print(f"  {record.name}: {package.path} has no file it reads; not run")
                continue
            if not option_value(record, package, "enabled"):
                print(
                    f"  {record.name}: {package.path} skips (turned off in"
                    f" {package.path}/workshop.toml)"
                )
                continue
            kind = kind_for(package.kind).name
            print(f"  {record.name}: {package.path} runs ({kind} kind)")
            body(ctx.for_package(package))


def _applies_now(record: CheckRecord, ctx: GateContext) -> bool:
    if record.scope == WORKSPACE:
        return record.in_scoped or not ctx.scoped
    return any(record in _package_records(ctx, p) for p in _members(ctx))


#: The unit of every file under no package: the root's own files,
#: ``tasks.py`` and the workspace's tests among them.
ROOT_UNIT = "."


def catalogue(ctx: GateContext) -> dict[str, tuple[tuple[str, str], ...]] | None:
    """Every file git holds under the root, by unit, each with its category.

    Listed once per walk: tracked files and untracked ones git does not
    ignore, as the tools see them, or the run's named files alone. A
    package's files are relative to it
    and categorised by its kind's table; a file under no package
    belongs to [livery.workshop._checks.ROOT_UNIT], relative to the
    root and categorised by the workspace's table. None when the root
    is not a git checkout, and then every check that applies runs.
    """
    from livery.toolroom import tools
    from livery.workshop._categories import category_of
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._provenance import unit_of

    if ctx.files:
        names: set[str] = set(ctx.files)
    else:
        listing = tools.git.opts(cwd=ctx.root, recorded=False, nofail=True)(
            "ls-files", "--cached", "--others", "--exclude-standard", "-z"
        )
        if listing.code != 0:
            return None
        names = {name for name in listing.stdout.split("\0") if name}
    packages = tuple(p for p in ctx.packages if p.path != WORKSPACE_TESTS)
    found: dict[str, list[tuple[str, str]]] = {}
    for name in sorted(names):
        if not (ctx.root / name).is_file():
            continue
        unit, inside = unit_of(ctx.root, packages, name)
        if unit is None:
            continue
        key = unit.path if unit in packages else ROOT_UNIT
        found.setdefault(key, []).append((inside, category_of(unit, inside).name))
    return {key: tuple(files) for key, files in found.items()}


def _scope_units(record: CheckRecord, ctx: GateContext) -> tuple[str, ...]:
    """The units *record* reads in this run.

    A package check reads the packages it judges. A workspace check
    reads every unit in a whole gate; in a scoped one, the subset's
    packages and the root's own files, since the workspace's tests
    ride every scoped run.
    """
    if record.scope == PACKAGE:
        return tuple(
            package.path
            for package in _members(ctx)
            if record in _package_records(ctx, package)
        )
    if ctx.subset is None or ctx.catalogue is None:
        return tuple(ctx.catalogue or ())
    return (*(package.path for package in _members(ctx)), ROOT_UNIT)


def reads_files(
    record: CheckRecord, ctx: GateContext, units: tuple[str, ...] | None = None
) -> bool:
    """Whether *record*'s claims reach a file of *units*, or of its scope in this run.

    A check without claims reads what its own body decides, and a run
    without a catalogue has no listing to judge by: both answer yes,
    except over named files, which a check without claims cannot say
    it reads.
    """
    if not record.claims:
        return not ctx.files
    if ctx.catalogue is None:
        return True
    for unit in units if units is not None else _scope_units(record, ctx):
        for relative, category in ctx.catalogue.get(unit, ()):
            if any(
                c.category == category and _reaches(c, relative) for c in record.claims
            ):
                return True
    return False


def claimed_files(
    record: CheckRecord, ctx: GateContext, unit: str | None = None
) -> tuple[str, ...]:
    """The files of the run's catalogue *record*'s claims reach, as absolute paths.

    For a run over named files: the ones a check takes. *unit* keeps
    one package's alone, for a package check.
    """
    found: list[str] = []
    for key, files in (ctx.catalogue or {}).items():
        if unit is not None and key != unit:
            continue
        base = ctx.root if key == ROOT_UNIT else ctx.root / key
        for relative, category in files:
            if any(
                c.category == category and _reaches(c, relative) for c in record.claims
            ):
                found.append(str(base / relative))
    return tuple(sorted(found))


def with_files(names: tuple[str, ...], ctx: GateContext) -> tuple[str, ...]:
    """*names* less the checks with no file to read in this run, each skip said.

    No process starts for a check whose claims reach nothing in scope.
    """
    kept: list[str] = []
    where = (
        "the named files"
        if ctx.files
        else "the affected packages"
        if ctx.scoped
        else "the workspace"
    )
    for name in names:
        if reads_files(_CHECKS[name], ctx):
            kept.append(name)
        else:
            print(f"  {name}: no file it reads in {where}; not run")
    return tuple(kept)


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
        if record.fix is not None and ctx.fix:
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

    def python_kinds(ctx: GateContext) -> tuple[Package, ...]:
        return tuple(p for p in _members(ctx) if is_python_kind(p.kind))

    def python_members(ctx: GateContext, role: str, name: str) -> tuple[Package, ...]:
        """The python members the check *name* judges: by role, then by its option."""
        judged = tuple(p for p in gated(_members(ctx), role) if is_python_kind(p.kind))
        return enabled(name, judged)

    def claimed(name: str, natives: tuple[Package, ...]) -> tuple[str, ...]:
        """The files of the native members the check *name* claims, by path.

        A native member's directories are C++ and not the check's to
        walk, so its files come by name: its ``conanfile.py`` to ruff.
        """
        record = check_for(name)
        return tuple(f"{p.path}/{f}" for p in natives for f in judged_files(record, p))

    def paths(ctx: GateContext, name: str) -> tuple[str, ...]:
        """The paths a check judges: the named files, the tree, or the members'."""
        if ctx.files:
            return claimed_files(check_for(name), ctx)
        gated_members = gated(_members(ctx), name)
        members = enabled(name, gated_members)
        pythons = tuple(p for p in members if is_python_kind(p.kind))
        natives = tuple(p for p in members if not is_python_kind(p.kind))
        if ctx.subset is None:
            if len(members) == len(gated_members):
                return _python.SRC
            chosen = _python.package_paths(pythons + unit(ctx)) + claimed(name, natives)
            return chosen or _python.SRC
        judged = tuple(p for p in ctx.subset if p in pythons or p in unit(ctx))
        present = tuple(p for p in natives if p in ctx.subset)
        return _python.package_paths(judged) + claimed(name, present)

    def format_run(ctx: GateContext) -> None:
        _python.run_format(check=True, paths=paths(ctx, "format"))

    def format_fix(ctx: GateContext) -> None:
        _python.run_format(check=False, safe_fix=ctx.safe, paths=paths(ctx, "format"))

    def lint_run(ctx: GateContext) -> None:
        _python.run_lint(fix=False, paths=paths(ctx, "lint"))

    def lint_fix(ctx: GateContext) -> None:
        if ctx.safe:
            _python.run_lint(safe_fix=True, paths=paths(ctx, "lint"))
            return
        _python.run_lint(fix=True, paths=paths(ctx, "lint"))

    def typecheck_run(ctx: GateContext) -> None:
        if ctx.files:
            _python.run_typecheck(paths=claimed_files(check_for("typecheck"), ctx))
            return
        judged = python_members(ctx, "typecheck", "typecheck") + unit(ctx)
        whole = len(judged) == len(python_kinds(ctx)) + len(unit(ctx))
        if not ctx.scoped and whole:
            _python.run_typecheck()
            return
        _python.run_typecheck(paths=_python.package_paths(judged))

    def typecomplete_run(ctx: GateContext) -> None:
        _python.run_typecomplete(python_members(ctx, "typecomplete", "typecomplete"))

    def test_run(ctx: GateContext) -> None:
        point = (f"--workshop-point={ctx.point}",) if ctx.point else ()
        if ctx.files:
            # A named test or source file runs its package's whole suite;
            # the workspace's tests unit reads the root's own files.
            record = check_for("test")
            named = tuple(
                p
                for p in (*python_members(ctx, "test", "test"), *unit(ctx))
                if claimed_files(
                    record, ctx, ROOT_UNIT if p.path == WORKSPACE_TESTS else p.path
                )
            )
            _python.run_test(*point, packages=named, root=ctx.root, scoped=True)
            return
        # A package whose examples alone changed runs them, not its suite.
        judged = tuple(
            p for p in python_members(ctx, "test", "test") if p.path not in ctx.examples
        )
        record = check_for("test")
        serial = tuple(p for p in judged if not option_value(record, p, "parallel"))
        parallel = tuple(p for p in judged if p not in serial) + unit(ctx)
        # A package whose suite is not worker-safe runs in an invocation
        # of its own under -n 0; the rest share one run across cores,
        # which runs whatever the members are, since the workspace's
        # own tests ride every run.
        for members, extra in ((parallel, ()), (serial, ("-n", "0"))):
            if extra and not members:
                continue
            if not ctx.scoped:
                _python.run_test(*extra, *point, packages=members, root=ctx.root)
                continue
            _python.run_test(
                *extra,
                *point,
                packages=members,
                root=ctx.root,
                scoped=True,
                selection=ctx.tests,
            )

    def examples_run(ctx: GateContext) -> None:
        from livery.workshop._kinds import kind_examples

        for package in python_members(ctx, "examples", "examples"):
            if (
                ctx.scoped
                and package.path in ctx.tests
                and package.path not in ctx.examples
            ):
                continue  # its tests alone changed: the examples did not move
            runner = kind_examples(package.kind)
            if runner is None:
                print(
                    f"  examples: {package.path} skips ({package.kind} kind runs none)"
                )
                continue
            named = claimed_files(check_for("examples"), ctx, package.path)
            if ctx.files and not named:
                continue
            runner(package, ctx.root, named)

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
        from livery.workshop._ast_rules import RuleContext, ast_rules, parsed_modules
        from livery.workshop._layers import write_layers
        from livery.workshop._packages import verify_workspace
        from livery.workshop._uv import run_uv

        for line in write_layers(ctx.root):
            print(line)
        # Every rule's fix runs here, inside the one rewrite and over
        # the one parse; the judgments follow in the check's judge.
        context = RuleContext(root=ctx.root, packages=ctx.packages)
        modules = parsed_modules(ctx.root, ctx.packages)
        written: list[str] = []
        for rule in ast_rules():
            if rule.fix is not None:
                written += rule.fix(modules, context)
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

    def named_sources(ctx: GateContext, name: str) -> tuple[Path, ...] | None:
        """The named files a package check takes, or None for the package's own set."""
        if not ctx.files:
            return None
        package = package_of(ctx)
        return tuple(Path(p) for p in claimed_files(check_for(name), ctx, package.path))

    def clang_format_run(ctx: GateContext) -> None:
        files = named_sources(ctx, "clang-format")
        _cpp_conan.format_check(package_of(ctx), fix=False, files=files)

    def clang_format_fix(ctx: GateContext) -> None:
        files = named_sources(ctx, "clang-format")
        _cpp_conan.format_check(package_of(ctx), fix=True, files=files)

    def configure_run(ctx: GateContext) -> None:
        _cpp_conan.configure(package_of(ctx))

    def build_run(ctx: GateContext) -> None:
        _cpp_conan.compile(package_of(ctx))

    def ctest_run(ctx: GateContext) -> None:
        package = package_of(ctx)
        _cpp_conan.test(package, ctx.root, selection=ctx.selection)

    def clang_tidy_run(ctx: GateContext) -> None:
        files = named_sources(ctx, "clang-tidy")
        _cpp_conan.lint(package_of(ctx), ctx.root, files=files)

    # The slots the python records fill: the dev group's tool lines
    # and pytest's options, lines the base template no longer writes
    # by hand. The python layer declares both once it exists.
    _slots.register_slot("python.dev-group")
    _slots.register_slot("python.test.addopts")

    native = ("cpp-conan", "python-nanobind")
    python = ("python",)
    # ruff judges a native package's conanfile.py beside clang-format on
    # its sources, so its records apply to the native kind as well.
    ruffed = ("python", "cpp-conan")
    py = _python.PY_SUFFIXES
    cpp = _cpp_conan.SOURCE_SUFFIXES
    for record in (
        CheckRecord(
            "format",
            "format",
            format_run,
            narrowing=PATHS,
            fix=format_fix,
            kinds=ruffed,
            tools=("ruff",),
            fragments=(
                Fragment("pyproject.toml", _fragments.RUFF_BASE),
                Fragment(".vscode/settings.json", _fragments.RUFF_SETTINGS),
            ),
            extension="charliermarsh.ruff",
            claims=tuple(
                Claim(category, suffixes=py)
                for category in (
                    "source",
                    "test",
                    "test-support",
                    "configuration",
                )
            ),
        ),
        CheckRecord(
            "clang-format",
            "format",
            clang_format_run,
            scope=PACKAGE,
            fix=clang_format_fix,
            kinds=native,
            tools=("clang_format",),
            fragments=tuple(
                Fragment(".clang-format", _fragments.CLANG_FORMAT, kind=kind)
                for kind in native
            ),
            claims=tuple(
                Claim(category, suffixes=cpp)
                for category in ("source", "test", "test-support")
            ),
        ),
        CheckRecord(
            "lint",
            "lint",
            lint_run,
            narrowing=PATHS,
            fix=lint_fix,
            kinds=ruffed,
            tools=("ruff",),
            fragments=(Fragment("pyproject.toml", _fragments.RUFF_LINT),),
            extension="charliermarsh.ruff",
            # Test bodies explain themselves by name and assertion, so
            # the docstring rules stop at the tests.
            claims=(
                Claim("source", suffixes=py),
                Claim("test", ignore=("D1",), suffixes=py),
                # An example keeps the layout its page shows; ruff judges
                # its names, as the page harness did, and nothing of style.
                Claim(
                    "example",
                    ignore=(
                        "D",
                        "E",
                        "I",
                        "UP",
                        "B",
                        "SIM",
                        "C4",
                        "RUF",
                        "F401",
                        "F811",
                        "F841",
                    ),
                    suffixes=py,
                ),
                Claim("test-support", ignore=("D1",), suffixes=py),
                Claim("configuration", suffixes=py),
            ),
        ),
        CheckRecord(
            "typecheck",
            "typecheck",
            typecheck_run,
            narrowing=PATHS,
            kinds=python,
            tools=("basedpyright", "mypy", "ty", "pyrefly"),
            fragments=(Fragment("pyproject.toml", _fragments.TYPECHECKERS),),
            extension="detachedfork.basedpyright",
            claims=tuple(
                Claim(category, suffixes=py)
                for category in ("source", "test", "test-support")
            ),
            # mypy reads the members' own stubs from the venv, so it
            # rides the dev group beside the store's copy.
            contributions=(("python.dev-group", "mypy>=1.14"),),
        ),
        CheckRecord(
            "typecomplete",
            "typecomplete",
            typecomplete_run,
            narrowing=PACKAGES,
            kinds=python,
            tools=("basedpyright",),
            claims=(Claim("source", suffixes=py),),
        ),
        CheckRecord(
            "test",
            "test",
            test_run,
            narrowing=PACKAGES,
            kinds=python,
            # pytest is the record's tool in the store, for the typed
            # handle the runner calls, and its venv copy below is the
            # one that imports the project's environment.
            tools=("pytest",),
            fragments=(Fragment("pyproject.toml", _fragments.TESTS),),
            # A package's tests measure its source, so a source change
            # is one the test check reads, and runs the suite for.
            claims=(
                Claim("test"),
                Claim("test-support"),
                Claim("source", suffixes=py),
            ),
            options=(
                Option(
                    "parallel",
                    "bool",
                    True,
                    "run the package's suite across cores; false runs it under -n 0",
                ),
            ),
            # pytest and coverage import the project's environment, so
            # they live in the venv: coverage 7.10 for its subprocess
            # patch, xdist for -n auto, the .pth that meters every
            # python the tests start.
            contributions=(
                ("python.dev-group", "pytest>=8.0"),
                ("python.dev-group", "pytest-cov>=5"),
                ("python.dev-group", "coverage[toml]>=7.10"),
                ("python.dev-group", "coverage-enable-subprocess>=1.0"),
                ("python.dev-group", "pytest-xdist>=3.6"),
                ("python.test.addopts", "-q"),
                ("python.test.addopts", "-n auto"),
                ("python.test.addopts", "--dist=worksteal"),
                ("python.test.addopts", "--import-mode=importlib"),
            ),
        ),
        CheckRecord(
            "examples",
            "examples",
            examples_run,
            narrowing=PACKAGES,
            kinds=python,
            tools=("pytest",),
            claims=(Claim("example", suffixes=py),),
        ),
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
            claims=(Claim("test", suffixes=cpp), Claim("source", suffixes=cpp)),
        ),
        CheckRecord(
            "clang-tidy",
            "lint",
            clang_tidy_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            after=("configure",),
            tools=("clang_tidy",),
            fragments=tuple(
                Fragment(".clang-tidy", _fragments.CLANG_TIDY, kind=kind)
                for kind in native
            ),
            claims=(Claim("source", suffixes=cpp), Claim("test", suffixes=cpp)),
        ),
    ):
        register_check(record)


_register_builtin()
