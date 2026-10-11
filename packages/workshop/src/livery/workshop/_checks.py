"""The check registry: what the quality gate runs, one record per step.

A check is one tool's judgment over the workspace or over one
package: a formatter's pass, the render drift comparison, ctest
over a native member. Each is a [livery.workshop._checks.CheckRecord][]
registered through [livery.workshop._checks.register_check][], and
the gate is the walk over the registry: every applicable check
green, the exit code the verdict. The workshop registers the
builtin records at import, in the order the gate runs its
rewriters; a listed extension's records register at mount, after
them and in list order, and re-registering a name replaces the
record.

A check names the kinds it judges, and a package of another kind
skips it by name: a C++ member is formatted over its recipe by a
python formatter and over its sources by clang-format because both
checks name its kind. A role is the set of checks that implement it,
so the roles that exist are what the listed extensions register.

A check is named by its role and its tool, ``test.pytest``. A role is
a declared phase ([livery.workshop._phases.declared_phases][]), and a
check of a phase nobody declares refuses naming the declared ones. A
role whose phase declares a verb is a verb and every check a sub-task
of it, made by [livery.workshop._checks.generate_verbs][] from the
registry: ``fm test`` runs the test role's checks and ``fm
test.pytest`` the one; the gate runs the checks of the phases that
declare themselves gated.

The gate runs the registered checks through the one walk
([livery.workshop._phases.run_phase][]), each a footman step: the
rewriters one at a time before any judge reads the tree
([livery.workshop._checks.rewriters][]), then every judge in one
block ([livery.workshop._checks.judges][]), each with its own record.
A check that must follow another on the same package, a build after
its configure, names it in ``after``: the walk starts it once the
earlier one ran, and the gate brings the earlier one in
([livery.workshop._checks.with_earlier][]).
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated

from livery.footman import Group, doc, fail, prog, root_group
from livery.workshop import _fragments
from livery.workshop._fragments import Fragment

if TYPE_CHECKING:
    from livery.workshop._influence import Inputs, Selection
    from livery.workshop._packages import Package
    from livery.workshop._phases import PhaseContext
    from livery.workshop._words import Words

#: A check's scope: the whole workspace in one run, or one package at a time.
WORKSPACE = "workspace"
PACKAGE = "package"

#: How a workspace check narrows under a package-scoped gate.
PATHS = "paths"
PACKAGES = "packages"
NONE = "none"

#: The extension the builtin records belong to.
BASE_EXTENSION = "livery.workshop"


@dataclass(frozen=True)
class Option:
    """One option a package may set on a check, in the check's own table.

    The table is ``[phases.<phase>.<step>]``, the step's address; a
    ``[phases.<phase>]`` table sets an option on every step of the
    phase.

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
            a tool with per-file ignores renders them as those.
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

    A check's name is its role and its tool, ``test.pytest``: the
    verb ``fm test.pytest``, the ``[phases.test.pytest]`` table of its
    options, the gate's lines, and every lookup use it. A step of a
    phase is the same record, its phase the role and its step the
    tool, read from its ``[phases.<phase>.<step>]`` table.

    Attributes:
        tool: The tool the check runs, as its sub-task names it under
            its role: ``pytest`` in ``fm test.pytest``.
        role: What the check implements: ``format``, ``lint``,
            ``typecheck``, ``typecomplete``, ``test``, ``build``,
            ``drift``, ``provenance`` or ``layering``, a string a
            verb is generated from. The roles that exist are those
            of the registered checks.
        run: The judging callable, called with the run's
            [livery.workshop.PhaseContext][]: the engine running the
            check's words or a reference to its code; a refusal is its
            verdict.
        scope: ``workspace`` for a check that judges the whole in one
            run, ``package`` for one that judges one package at a
            time.
        narrowing: How a workspace check narrows under a scoped gate:
            ``paths`` (the subset's directories), ``packages`` (the
            subset's members) or ``none`` (the whole is always
            checked). A package check narrows by its packages. No
            declaration names it: the reader derives it, ``paths``
            for a template with ``{paths}``, ``packages`` for a
            workspace step in code with no ``inputs``, ``none``
            otherwise ([livery.workshop._declaration.read][]).
        fix: The rewriting callable when the tool can rewrite, run
            serially before any judge under ``--fix``; None for a
            check that only judges.
        extensions: The package-level extensions whose packages the
            check judges: a package whose set holds one of them
            ([livery.workshop._kinds.extension_set][]). A package check
            runs per such member; a workspace check names them so its
            tools and claims reach those packages, or leaves them empty.
        tests_only: Whether a package check still runs when the
            package's change is confined to its tests. A build and
            the tests do; a formatter does not.
        inputs: The files a workspace check reads, so a run judges it
            only when one of them, or what widens it, changed, and then
            only the changed ones when it judges per file
            ([livery.workshop._influence.Inputs][]). None for a check
            that narrows by its packages alone.
        after: The checks this one runs after, by name: a build
            after its configure, a ctest after its build. The walk
            starts this one once each of them ran for the same
            package, and the gate's walk brings them in
            ([livery.workshop._checks.with_earlier][]).
        extension: The extension that registered the check, named when a
            narrowing is printed.
        tools: The tools the check runs, as the tool profile names
            them, ``("clang_format",)``; each reaches the profile for every kind
            the check judges, naming ``check <name>`` as its site, and
            leaves it when the check is unregistered.
        options: The options a package may set under
            ``[phases.<phase>.<step>]``; ``enabled`` is every check's.
        fragments: The configuration the render manages for the
            check, one per rendered file it has something to say in;
            composed in check-name order, judged by the drift gate,
            gone from the next render with the record.
        claims: The categories the check judges, each with the rules
            it withholds there and the suffixes it reads; a check
            judges every file its claims reach and no other, and a
            tool with per-file ignores renders its ignores from them.
        flags: The command-line flags the check reads, from `FLAGS`:
            ``point`` for a check that selects tests by CI point. A
            generated verb offers the flags its checks declare, and
            ``--fix`` with ``--safe-fix`` where a check has a fix mode.
        arguments: Whether the check hands the tool it wraps the words
            after ``--`` on its own verb (``fm test.pytest -- -k name``),
            which it reads from [livery.workshop.PhaseContext][]'s
            ``arguments``. A check that wraps no tool leaves it off, and
            its verb refuses them. No declaration names it: the reader
            derives it, on for a template with ``{arguments}`` and for
            a step in code that names ``tools``.
        words: The check's words when it is declared in words, which
            ``fm explain`` prints and the conformance kit judges; None
            for a check whose ``main`` is code.
        provides: The context keys the step writes inside its phase,
            each to its type name; a step of one phase reads what
            another provides.
        reads: The context keys the step reads inside its phase.
        isolated: Whether the step serves the phase's isolated side
            (True), from a copy of the sources in a fresh environment,
            or its tree side (False); None serves both.
        enabled: Whether the step registers while its extension is
            listed. An extension declares False for a step a workspace
            turns on at the step's address in the root contract,
            ``[phases.<phase>.<step>] enabled = true``.
        replaces: The steps of earlier extensions this one replaces,
            each ``<owner>:<phase>.<step>`` with the reason; naming the
            step registered at this one's address is what lets a
            second extension take the address.
        deletes: The steps of earlier extensions this one deletes,
            each ``<owner>:<phase>.<step>`` with the reason.

    """

    tool: str
    role: str
    run: Callable[[PhaseContext], None]
    scope: str = WORKSPACE
    narrowing: str = NONE
    fix: Callable[[PhaseContext], None] | None = None
    extensions: tuple[str, ...] = ()
    tests_only: bool = False
    inputs: Inputs | None = None
    after: tuple[str, ...] = ()
    extension: str = BASE_EXTENSION
    tools: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()
    fragments: tuple[Fragment, ...] = ()
    claims: tuple[Claim, ...] = ()
    flags: tuple[str, ...] = ()
    arguments: bool = False
    words: Words | None = None
    provides: tuple[tuple[str, str], ...] = ()
    reads: tuple[str, ...] = ()
    isolated: bool | None = None
    enabled: bool = True
    replaces: tuple[tuple[str, str], ...] = ()
    deletes: tuple[tuple[str, str], ...] = ()

    @property
    def name(self) -> str:
        """The check's name: its role and its tool, ``test.pytest``."""
        return f"{self.role}.{self.tool}"

    @property
    def phase(self) -> str:
        """The phase the step belongs to: the role, as the step record names it."""
        return self.role

    @property
    def step(self) -> str:
        """The step's name inside its phase: the tool, as the step record names it."""
        return self.tool


_CHECKS: dict[str, CheckRecord] = {}

#: Builtin checks an extension withdrew, name to the extension that did: the
#: gate names them, so a lighter gate is a legible decision.
_WITHDRAWN: dict[str, str] = {}


#: The command-line flags a check may read, beyond the fix modes.
FLAGS = ("point",)


def register_check(record: CheckRecord) -> None:
    """Register *record*; a name already registered is replaced.

    Extensions call this from their plugin at mount. Replacing is how a
    extension swaps a tool under a role, and how a test injects a fake;
    a package check names at least one kind, since a check that
    judges packages and applies to none never runs. The check's
    address, ``<role>.<tool>``, is a task's address, so a dot inside
    the role or the tool refuses, and a tool named ``default`` refuses
    (that address is the role verb's own).
    """
    from livery.workshop import _phases

    for part in (record.role, record.tool):
        if not part or "." in part:
            fail(
                f"check {record.name!r}: a role and a tool are one word each,"
                f" not {part!r}; the check answers to <role>.<tool>"
            )
    declared = _phases.declared_phases()
    if record.role not in declared:
        fail(
            f"check {record.name!r}: {record.role} is not a declared phase; the"
            f" phases are {', '.join(declared)}"
        )
    if record.tool == "default":
        fail(
            f"check {record.name!r}: {record.name} is the address of the"
            f" {record.role} verb itself; name the check by its tool"
        )
    held = _CHECKS.get(record.name)
    if held is not None and held.extension != record.extension:
        # A second extension takes an address by saying so: the step it
        # replaces or deletes, named with its owner, and why.
        named = {address for address, _reason in (*record.replaces, *record.deletes)}
        if f"{held.extension}:{held.name}" not in named:
            fail(
                f"step {record.name} is declared by both {held.extension} and"
                f" {record.extension}; two extensions declare one step unless the"
                f" later names {held.extension}:{held.name} in replaces or deletes,"
                " with a reason"
            )
    if record.scope not in (WORKSPACE, PACKAGE):
        fail(
            f"check {record.name!r}: scope is {WORKSPACE!r} or {PACKAGE!r},"
            f" not {record.scope!r}"
        )
    unknown = [flag for flag in record.flags if flag not in FLAGS]
    if unknown:
        fail(
            f"check {record.name!r} reads {', '.join(unknown)}, which no verb"
            f" offers; the flags are {', '.join(FLAGS)}"
        )
    if record.narrowing not in (PATHS, PACKAGES, NONE):
        fail(
            f"check {record.name!r}: narrowing is {PATHS!r}, {PACKAGES!r} or"
            f" {NONE!r}, not {record.narrowing!r}"
        )
    if record.scope == PACKAGE and not record.extensions:
        fail(
            f"check {record.name!r} judges packages and names no extension: a"
            " package check judges the packages whose set holds one it names"
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


def _typed(option: Option, value: object) -> bool:
    expected = {"bool": bool, "str": str, "int": int}[option.kind]
    if option.kind == "int" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def option_addresses(record: CheckRecord) -> tuple[str, ...]:
    """The tables that reach *record*'s options, shallowest first.

    ``phases.<phase>``, the phase's table, which reaches every step of
    the phase, then ``phases.<phase>.<step>``, the step's own: the
    later table wins key by key, since it names fewer steps.
    """
    return (f"phases.{record.role}", f"phases.{record.role}.{record.tool}")


def option_value(record: CheckRecord, package: Package, name: str) -> object:
    """What *package* sets for *record*'s option *name*, or the option's default.

    The deepest table that sets it wins
    ([livery.workshop._checks.option_addresses][]). Refuses an option
    the record does not declare and a value of the wrong type, naming
    the record's options.
    """
    declared = {option.name: option for option in (*record.options, ENABLED)}
    if name not in declared:
        fail(
            f"check {record.name!r} declares no option {name!r}; its options are"
            f" {', '.join(declared)}"
        )
    option = declared[name]
    tables = dict(package.checks)
    found: object = option.default
    for address in option_addresses(record):
        for key, value in tables.get(address, ()):
            if key != name:
                continue
            if not _typed(option, value):
                fail(
                    f"{package.path}/workshop.toml: [{address}] {name} is"
                    f" {option.kind}, not {value!r}"
                )
            found = value
    return found


def _reached(address: str) -> tuple[CheckRecord, ...]:
    """The registered checks a table *address* reaches.

    ``phases.<phase>`` reaches every step of the phase, and
    ``phases.<phase>.<step>`` the one step.
    """
    _kind, _, rest = address.partition(".")
    phase, _, step = rest.partition(".")
    return tuple(
        r for r in _CHECKS.values() if r.role == phase and (not step or r.tool == step)
    )


def option_problems(packages: tuple[Package, ...]) -> list[str]:
    """Every option table or key no registered check takes, one line each.

    Read by the layering check, so a package that addresses a phase or
    a step that does not exist, or sets an option none of the steps it
    reaches declares, is refused with the vocabulary named.
    """
    problems: list[str] = []
    phases = sorted({r.role for r in _CHECKS.values()})
    for package in packages:
        where = f"{package.path}/workshop.toml"
        for address, options in package.checks:
            reached = _reached(address)
            if not reached:
                _kind, _, rest = address.partition(".")
                if "." in rest:
                    problems.append(
                        f"{where}: [{address}] reaches no registered step; the"
                        f" steps are {', '.join(sorted(_CHECKS))}"
                    )
                else:
                    problems.append(
                        f"{where}: [{address}] names no phase a registered step"
                        f" has; the phases are {', '.join(phases)}"
                    )
                continue
            declared: dict[str, Option] = {ENABLED.name: ENABLED}
            for record in reached:
                declared.update({option.name: option for option in record.options})
            for key, value in options:
                option = declared.get(key)
                if option is None:
                    problems.append(
                        f"{where}: [{address}] sets {key!r}, which no check it"
                        f" reaches declares; their options are"
                        f" {', '.join(sorted(declared))}"
                    )
                elif not _typed(option, value):
                    problems.append(
                        f"{where}: [{address}] {key} is {option.kind}, not {value!r}"
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


def tools_for(extensions: Iterable[str]) -> tuple[tuple[str, str], ...]:
    """The tools the checks judging packages that hold *extensions* run.

    Each as a ``(tool, check)`` pair. A check judges such a package
    when it names one of *extensions*; a workspace check names the
    extensions its bodies judge the same way.
    """
    held = set(extensions)
    found: list[tuple[str, str]] = []
    for record in _CHECKS.values():
        if held & set(record.extensions):
            found.extend((tool, record.name) for tool in record.tools)
    return tuple(found)


def unregister_check(name: str, *, by: str = "") -> None:
    """Drop the check *name*; unknown names refuse naming the registry.

    *by* names the extension withdrawing a check it did not register, so
    the gate can print who narrowed it.
    """
    if name not in _CHECKS:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    record = _CHECKS.pop(name)
    if by and record.extension != by:
        _WITHDRAWN[name] = by


def narrowings() -> tuple[str, ...]:
    """The gate's lines naming what an extension added, replaced or withdrew.

    A check the base did not register names its extension; a builtin a
    extension withdrew names the extension too. Empty for the base alone.
    """
    lines = [
        f"  {record.name}: registered by {record.extension}"
        for record in _CHECKS.values()
        if record.extension != BASE_EXTENSION
    ]
    lines += [
        f"  {name}: withdrawn by {extension}" for name, extension in _WITHDRAWN.items()
    ]
    return tuple(lines)


def check_names() -> tuple[str, ...]:
    """Every registered check, in registration order."""
    return tuple(_CHECKS)


def snapshot() -> tuple[dict[str, CheckRecord], dict[str, str]]:
    """The registry's state, for [livery.workshop._checks.restore][] to put back."""
    return dict(_CHECKS), dict(_WITHDRAWN)


def restore(state: tuple[dict[str, CheckRecord], dict[str, str]]) -> None:
    """Put the registry back to *state*."""
    records, withdrawn = state
    _CHECKS.clear()
    _CHECKS.update(records)
    _WITHDRAWN.clear()
    _WITHDRAWN.update(withdrawn)


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
    import livery.toolroom.tools as tools

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
        if record.extensions and not _applies(record, package):
            continue
        names.append(record.name)
    return tuple(sorted(names))


def per_file_ignores(kinds: tuple[str, ...]) -> list[tuple[str, tuple[str, ...]]]:
    """The per-file ignores the claims render, pattern to rule codes, sorted.

    For each present kind and the workspace's own unit, every claim
    that withholds rules on a category maps to that category's
    patterns in the kind's table, a package's under ``packages/**/``,
    which reaches a member in a group directory too, and the root's as
    they are; two checks claiming one category under different rules
    land in one entry with both sets.
    """
    from livery.workshop._categories import WORKSPACE, category_rules
    from livery.workshop._kinds import kind_names, kind_stands_for

    table: dict[str, set[str]] = {}
    units = [(kind, "packages/**/") for kind in kinds if kind in kind_names()]
    units.append((WORKSPACE, ""))
    for kind, prefix in units:
        held = set(kind_stands_for(kind))
        for record in _CHECKS.values():
            if (
                kind != WORKSPACE
                and record.extensions
                and not held & set(record.extensions)
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


def answering(address: str) -> str | None:
    """The check answering to *address*, its name; None when none is registered."""
    return address if address in _CHECKS else None


def check_for(name: str) -> CheckRecord:
    """The check named *name*, ``test.pytest``.

    Refusal names the registry.
    """
    if name not in _CHECKS:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    return _CHECKS[name]


def roles() -> tuple[str, ...]:
    """The roles the registered checks implement, sorted."""
    return tuple(sorted({r.role for r in _CHECKS.values()}))


def judges_extensions(record: CheckRecord, extensions: Iterable[str]) -> bool:
    """Whether *record* judges a package holding *extensions*.

    A check that names no extensions judges every package; one that
    names some judges a package holding one of them.
    """
    return not record.extensions or bool(set(record.extensions) & set(extensions))


def judged_by(
    record: CheckRecord, packages: tuple[Package, ...], *, quiet: bool = False
) -> tuple[Package, ...]:
    """The *packages* *record* judges, by the extensions they hold; each other skips.

    A package holding none of the check's extensions prints a skip
    naming the check, the package and the extensions, so a narrowed gate
    is visible in the output and never passes silently. *quiet* counts
    without printing.
    """
    from livery.workshop._kinds import extension_set

    kept: list[Package] = []
    for package in packages:
        if judges_extensions(record, extension_set(package)):
            kept.append(package)
        elif not quiet:
            wanted = " or ".join(record.extensions)
            print(f"  {record.name}: {package.path} skips (not a {wanted} package)")
    return tuple(kept)


def _applies(record: CheckRecord, package: Package) -> bool:
    """Whether a package check judges *package*, by the extensions it holds."""
    from livery.workshop._kinds import extension_set

    return bool(set(record.extensions) & set(extension_set(package)))


def judges_package(record: CheckRecord, ctx: PhaseContext, package: Package) -> bool:
    """Whether the package step *record* runs over *package* in this run.

    The package holds an extension the step names; a change confined
    to the package's tests leaves the step out unless it is tests-only
    ([livery.workshop._checks.CheckRecord][]).
    """
    tests_only = ctx.scoped and package.path in ctx.tests
    return (
        record.scope == PACKAGE
        and _applies(record, package)
        and (record.tests_only or not tests_only)
    )


def _package_records(ctx: PhaseContext, package: Package) -> list[CheckRecord]:
    """The registered package checks that judge *package* in this run, in order."""
    return [
        record for record in _CHECKS.values() if judges_package(record, ctx, package)
    ]


def members(ctx: PhaseContext) -> tuple[Package, ...]:
    """The packages under judgment that a kind owns: the tests unit is none's."""
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    return tuple(package for package in ctx.judged if package.path != WORKSPACE_TESTS)


def _makes_verb(role: str) -> bool:
    """Whether *role* gets a verb: its phase declares one."""
    from livery.workshop import _phases

    spec = _phases.declared_phases().get(role)
    return spec is None or spec.verb


def verb_tree() -> dict[str, dict[str, CheckRecord]]:
    """Each role's checks by tool, sorted: the verbs the registry generates.

    A role whose phase declares no verb is left out.
    """
    tree: dict[str, dict[str, CheckRecord]] = {}
    for record in _CHECKS.values():
        if _makes_verb(record.role):
            tree.setdefault(record.role, {})[record.tool] = record
    return {role: dict(sorted(tools.items())) for role, tools in sorted(tree.items())}


#: The verb functions the generator made. Footman shares a task's
#: function when it copies a tree into a project, so the function is
#: how a later generation knows a verb as its own: its own it remakes
#: or removes, and any other task at an address serves that address.
_MADE: weakref.WeakSet[Callable[..., None]] = weakref.WeakSet()


def split_arguments(paths: tuple[str, ...]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """A verb's positional words as its paths, then the words after ``--``.

    footman hands a task's variadic parameter the words after ``--``
    too, last, and names them through ``passthrough()``.
    """
    from livery.footman import passthrough

    after = tuple(passthrough())
    if after and paths[-len(after) :] == after:
        return paths[: -len(after)], after
    return paths, after


def _run_named(
    names: tuple[str, ...],
    words: tuple[str, ...],
    *,
    refusal: str,
    fix: bool = False,
    safe_fix: bool = False,
    point: str = "",
) -> None:
    from livery.workshop import _quality

    paths, arguments = split_arguments(words)
    if arguments and refusal:
        fail(refusal)
    _quality.run_checks(
        names, paths, fix=fix, safe_fix=safe_fix, point=point, arguments=arguments
    )


#: A generated verb's paths and flags, each with its help text. They live at
#: module level because footman reads a task's annotations in its
#: module's namespace: a name local to the function making the verb
#: does not resolve there, and the flag's value would arrive as text.
_Paths = Annotated[
    str, doc("the files or directories to check; the workspace when none")
]
_Fix = Annotated[bool, doc("rewrite what the checks can, then judge the rest")]
_SafeFix = Annotated[bool, doc("fix, removing no code: safe for an edit in flight")]
_Point = Annotated[
    str, doc("select this CI point's tests: gate, merge, nightly, release")
]


def _verb(
    select: Callable[[], tuple[str, ...]],
    *,
    fixes: bool,
    reads_point: bool,
    what: str,
    refusal: str = "",
) -> Callable[..., None]:
    """A verb over the checks *select* names, offering exactly the flags they read.

    ``--fix`` and ``--safe-fix`` where a check can fix, ``--point``
    where one reads the point; the paths narrow the run to those files.
    The words after ``--`` reach the check's tool, unless *refusal*
    says why this verb takes none.
    """

    def with_fix_and_point(
        *paths: _Paths,
        fix: _Fix = False,
        safe_fix: _SafeFix = False,
        point: _Point = "",
    ) -> None:
        _run_named(
            select(), paths, refusal=refusal, fix=fix, safe_fix=safe_fix, point=point
        )

    def with_fix(*paths: _Paths, fix: _Fix = False, safe_fix: _SafeFix = False) -> None:
        _run_named(select(), paths, refusal=refusal, fix=fix, safe_fix=safe_fix)

    def with_point(*paths: _Paths, point: _Point = "") -> None:
        _run_named(select(), paths, refusal=refusal, point=point)

    def bare(*paths: _Paths) -> None:
        _run_named(select(), paths, refusal=refusal)

    chosen: Callable[..., None] = (
        with_fix_and_point
        if fixes and reads_point
        else with_fix
        if fixes
        else with_point
        if reads_point
        else bare
    )
    chosen.__doc__ = what
    _MADE.add(chosen)
    return chosen


def _role_checks(role: str) -> Callable[[], tuple[str, ...]]:
    """Every registered check of *role*, read when the verb runs."""

    def select() -> tuple[str, ...]:
        return tuple(record.name for record in _CHECKS.values() if record.role == role)

    return select


def _one_check(name: str) -> Callable[[], tuple[str, ...]]:
    def select() -> tuple[str, ...]:
        return (name,)

    return select


def _is_made(task: Callable[..., object]) -> bool:
    """Whether the generator made *task*, through the function footman wraps."""
    return inspect.unwrap(task) in _MADE


def _flags_of(task: Callable[..., object]) -> frozenset[str]:
    """The flags a verb offers: its keyword parameters."""
    return frozenset(
        name
        for name, parameter in inspect.signature(task).parameters.items()
        if parameter.kind is inspect.Parameter.KEYWORD_ONLY
    )


def _place(parent: Group, key: str, verb: Callable[..., None]) -> None:
    """Put *verb* at *key* in *parent*, unless a verb that serves it is there.

    A verb the generator made is remade when its flags changed, so a
    check registered later that fixes or reads the point reaches its
    role's verb; any other task at the address is left alone.
    """
    existing = parent.tasks.get(key)
    if existing is not None:
        if not _is_made(existing) or _flags_of(existing) == _flags_of(verb):
            return
        del parent.tasks[key]
    if key == "default":
        parent.default(verb)
    else:
        parent.task(name=key)(verb)


def _prune(target: Group, tree: Mapping[str, Mapping[str, CheckRecord]]) -> None:
    """Remove the verbs the generator made whose checks are gone.

    A role with no check left loses its verb, and its group when that
    leaves the group empty; a task another extension put there stays.
    """
    for role, parent in list(target.groups.items()):
        tools = tree.get(role, {})
        removed = False
        for key, task in list(parent.tasks.items()):
            kept = bool(tools) if key == "default" else key in tools
            if _is_made(task) and not kept:
                del parent.tasks[key]
                removed = True
        if removed and not parent.tasks and not parent.groups:
            del target.groups[role]


def generate_verbs(into: Group | None = None) -> None:
    """Make a verb per role and a sub-task per check, from the registry.

    ``fm test`` runs every check of the test role and ``fm test.pytest``
    the one, each offering exactly the flags its checks read. A role
    or a sub-task whose address a verb already holds is served by that
    verb, ``fm provenance``, and nothing is
    made there. Run again after extensions registered or withdrew checks,
    it makes what is new, remakes a verb whose flags changed, and
    removes a verb whose check is gone.
    """
    target = into if into is not None else root_group
    tree = verb_tree()
    _prune(target, tree)
    for role, tools in tree.items():
        if role in target.tasks:
            continue
        parent = target.groups.get(role)
        default = None if parent is None else parent.default_task
        # The generator owns the role's verb in a group it made; a
        # group of another verb's gets no default.
        owned = parent is None or (default is not None and _is_made(default))
        if parent is None:
            parent = target.group(role, help=f"The {role} checks, one task each")
        if owned:
            _place(
                parent,
                "default",
                _verb(
                    _role_checks(role),
                    fixes=any(r.fix is not None for r in tools.values()),
                    reads_point=any("point" in r.flags for r in tools.values()),
                    what=f"Run every {role} check, over the named paths or the"
                    " workspace.",
                    refusal=arguments_refusal(f"{prog()} {role}", tools.values()),
                ),
            )
        for tool, record in tools.items():
            if tool in parent.groups:
                continue
            _place(
                parent,
                tool,
                _verb(
                    _one_check(record.name),
                    fixes=record.fix is not None,
                    reads_point="point" in record.flags,
                    what=f"Run the {record.name} check alone, over the named paths"
                    " or the workspace"
                    + (
                        "; the words after -- go to its tool."
                        if record.arguments
                        else "."
                    ),
                    refusal=""
                    if record.arguments
                    else f"{record.name} wraps no tool, so it takes no arguments"
                    " after --",
                ),
            )


def arguments_refusal(verb: str, records: Iterable[CheckRecord]) -> str:
    """Why *verb*, which runs several checks, takes no arguments after ``--``.

    The words after ``--`` are one tool's, so they go to a check's own
    verb; the refusal names those of *records* that take them.
    """
    takers = [f"{prog()} {r.role}.{r.tool}" for r in records if r.arguments]
    named = f": {', '.join(takers)}" if takers else ""
    return (
        f"`{verb}` runs several checks, and the words after -- are one tool's;"
        f" give them to the check's own verb{named}"
    )


def run_check(name: str, ctx: PhaseContext, *, fix: bool = False) -> None:
    """Run the check *name* over *ctx* through the walk: its judge, or its fix mode.

    One step alone ([livery.workshop._phases.run_phase][]): a workspace
    check runs once, a package check once per package it judges, and
    a check without a fix mode runs nothing in fix mode. The checks
    named in ``after`` are not run first: a caller running several
    checks by hand orders them itself, and the gate's walk brings them
    in ([livery.workshop._checks.with_earlier][]).
    """
    from livery.workshop._phases import run_phase

    run_phase(ctx, (check_for(name),), fixing=fix)


def with_earlier(names: tuple[str, ...]) -> tuple[CheckRecord, ...]:
    """The records of *names*, and of every step one of them runs after.

    Followed through ``after`` transitively, in registration order,
    so a build's configure joins the walk that judges the build.

    Raises:
        Failed: when an ``after`` names no registered check.
    """
    wanted = set(names)
    queue = list(names)
    while queue:
        for earlier in check_for(queue.pop()).after:
            if earlier not in wanted:
                wanted.add(earlier)
                queue.append(earlier)
    return tuple(record for record in _CHECKS.values() if record.name in wanted)


def _applies_now(record: CheckRecord, ctx: PhaseContext) -> bool:
    if record.scope == WORKSPACE:
        if record.inputs is not None:
            # Listed, so the walk says when no file it reads changed.
            return True
        # A check without declared inputs narrows by its packages, so a
        # scope that holds none leaves it nothing to judge. Running it
        # would read the empty scope as the whole: the test check would
        # start the whole suite. Named files are a scope of their own,
        # which the check's claims judge.
        return bool(ctx.files) or not (ctx.scoped and not ctx.subset)
    return any(judges_package(record, ctx, p) for p in members(ctx))


def workspace_selected(ctx: PhaseContext) -> tuple[str, ...]:
    """The workspace checks with declared inputs that this run's changes select."""
    return tuple(
        record.name
        for record in _CHECKS.values()
        if record.scope == WORKSPACE
        and record.inputs is not None
        and selected(record, ctx).runs
    )


def selected_files(ctx: PhaseContext) -> frozenset[str] | None:
    """The files the running check judges in this run; None for every file it reads.

    A workspace check that declares ``inputs`` judges, in a run that
    knows what changed, the changed files among its inputs, and what
    its ``widen`` reference adds; in a run that knows nothing of the
    changes, it judges every file it reads, and this is None. The
    context names the running check, so a check asks with the context
    its run was handed.

    Raises:
        Failed: when *ctx* names no check: one built by hand, outside
            a check's run.
    """
    if not ctx.check:
        fail(
            "selected_files() answers for the running check, and this"
            " PhaseContext names none: call it with the context a check's"
            " run is handed, which sets PhaseContext.check"
        )
    return _selected_files(check_for(ctx.check), ctx)


def _selected_files(record: CheckRecord, ctx: PhaseContext) -> frozenset[str] | None:
    """The files *record* judges in this run; None for every file it reads."""
    selection = selected(record, ctx)
    return None if selection.whole else frozenset(selection.files)


#: What never counts as a manifest or a composed file's input, though it
#: sits beside them: prose, and the licence.
NOT_INPUTS = ("**/*.md", "LICENSE", "**/LICENSE")


def package_files(root: Path) -> tuple[str, ...]:
    """The files directly in each package's directory, as patterns.

    The contract and the native manifests are among them, whatever the
    package's kind calls its manifest, so the base names none of them.
    """
    from livery.workshop._packages import package_directories

    return tuple(
        f"{directory.relative_to(root).as_posix()}/*"
        for directory in package_directories(root)
    )


def _graph_files(root: Path) -> tuple[str, ...]:
    """What the graph is made of: the contracts, the native manifests, a root src/."""
    return ("workshop.toml", "src/**", *package_files(root))


#: The files the generated outputs land in, for every forge: the CI
#: files, the entry script and the code-owners file.
GENERATED = (
    ".github/workflows/*.yml",
    ".gitea/workflows/*.yml",
    ".gitlab-ci.yml",
    "setup.sh",
    ".github/CODEOWNERS",
    ".gitlab/CODEOWNERS",
    ".gitea/CODEOWNERS",
    "CODEOWNERS",
    "docs/CODEOWNERS",
)


def _drift_reads(root: Path) -> tuple[str, ...]:
    """The tracked outputs drift judges: the receipted composed ones and the generated.

    Read from the render's receipts, never by rendering: listing what
    is composed must cost less than composing it.
    """
    from livery.workshop._fragment_engine import read_rendered
    from livery.workshop._packages import package_directories

    found = list(GENERATED)
    found += list(read_rendered(root))
    for directory in package_directories(root):
        home = directory.relative_to(root).as_posix()
        found += [f"{home}/{name}" for name in read_rendered(directory)]
    return tuple(found)


def _drift_widens(root: Path) -> tuple[str, ...]:
    """What every tracked output is made from: contracts, manifests, locks, extensions.

    A listed extension whose sources live in a member package composes
    from them, so a change under them may move any output.
    """
    from livery.workshop._extensions import extension_names, extension_provider
    from livery.workshop._packages import discover_packages

    packages = discover_packages(root)
    providers = {extension_provider(name, packages) for name in extension_names(root)}
    return (
        "workshop.toml",
        *package_files(root),
        "uv.lock",
        "toolroom.lock",
        *(f"{provider}/src/**" for provider in sorted(providers) if provider),
    )


def selected(record: CheckRecord, ctx: PhaseContext) -> Selection:
    """What *record* judges in this run, by its declared inputs.

    Everything for a check that declares none. The run's changes
    select, or the named files of `fm check <paths>` when there are no
    changes; a run with neither judges everything.
    """
    from livery.workshop._extensions import extension_provider
    from livery.workshop._influence import WHOLE, Changes, select

    if record.inputs is None:
        return WHOLE
    changes = ctx.changes
    if changes is None and ctx.files:
        changes = Changes(ctx.root, ctx.files)
    return select(
        record.inputs,
        changes,
        provider=extension_provider(record.extension, ctx.packages),
    )


#: The unit of every file under no package: the root's own files,
#: ``tasks.py`` and the workspace's tests among them.
ROOT_UNIT = "."


def catalogue(ctx: PhaseContext) -> dict[str, tuple[tuple[str, str], ...]] | None:
    """Every file git holds under the root, by unit, each with its category.

    Listed once per walk: tracked files and untracked ones git does not
    ignore, as the tools see them, or the run's named files alone. A
    package's files are relative to it
    and categorised by its kind's table; a file under no package
    belongs to [livery.workshop._checks.ROOT_UNIT], relative to the
    root and categorised by the workspace's table. None when the root
    is not a git checkout, and then every check that applies runs.
    """
    import livery.toolroom.tools as tools
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


def _scope_units(record: CheckRecord, ctx: PhaseContext) -> tuple[str, ...]:
    """The units *record* reads in this run.

    A package check reads the packages it judges. A workspace check
    reads every unit in a whole gate; in a scoped one, the subset's
    packages and the root's own files, since the workspace's tests
    ride every scoped run.
    """
    if record.scope == PACKAGE:
        return tuple(
            package.path
            for package in members(ctx)
            if judges_package(record, ctx, package)
        )
    if ctx.subset is None or ctx.catalogue is None:
        return tuple(ctx.catalogue or ())
    return (*(package.path for package in members(ctx)), ROOT_UNIT)


def reads_files(
    record: CheckRecord, ctx: PhaseContext, units: tuple[str, ...] | None = None
) -> bool:
    """Whether *record*'s claims reach a file of *units*, or of its scope in this run.

    A check without claims reads what its own body decides, and a run
    without a catalogue has no listing to judge by: both answer yes,
    except over named files, which a check without claims cannot say
    it reads.
    """
    if record.inputs is not None:
        return selected(record, ctx).runs
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
    record: CheckRecord, ctx: PhaseContext, unit: str | None = None
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


def with_files(names: tuple[str, ...], ctx: PhaseContext) -> tuple[str, ...]:
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
        record = _CHECKS[name]
        if reads_files(record, ctx):
            kept.append(name)
            continue
        said = (
            "the changed files"
            if record.inputs is not None and ctx.changes is not None
            else where
        )
        print(f"  {name}: no file it reads in {said}; not run")
    return tuple(kept)


def _gated(record: CheckRecord) -> bool:
    """Whether the gate runs *record*: its phase declares itself gated."""
    from livery.workshop import _phases

    spec = _phases.declared_phases().get(record.role)
    return spec is None or spec.gated


def rewriters(ctx: PhaseContext) -> tuple[str, ...]:
    """The checks whose fix mode runs, serially and in order, under --fix.

    Those of the gated phases; empty unless the gate runs in its fix
    mode.
    """
    if not ctx.fix:
        return ()
    return tuple(
        record.name
        for record in _CHECKS.values()
        if record.fix is not None and _gated(record) and _applies_now(record, ctx)
    )


def judges(ctx: PhaseContext) -> tuple[str, ...]:
    """The checks the gate judges together, in registration order.

    Those of the gated phases. A check that rewrote in this run is not
    judged again: re-judging the style it just wrote would only spend
    time agreeing.
    """
    names = []
    for record in _CHECKS.values():
        if not _gated(record) or not _applies_now(record, ctx):
            continue
        if record.fix is not None and ctx.fix:
            continue
        names.append(record.name)
    return tuple(names)


#: The whole repository, as a check that walks it from the root reads it.
#: [livery.workshop._checks.scoped_paths][] answers it when a check
#: judges its configured whole; a tool that reads ``.`` as more than
#: its configuration names takes no paths then.
WHOLE = (".",)


def _workspace_unit(ctx: PhaseContext) -> tuple[Package, ...]:
    """The workspace's own tests, when this run judges them as a unit."""
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    return tuple(p for p in ctx.judged if p.path == WORKSPACE_TESTS)


def _claimed(name: str, natives: tuple[Package, ...]) -> tuple[str, ...]:
    """The files of the native members the check *name* claims, by path.

    A native member's directories are not a python check's to walk, so
    its files come by name: its ``conanfile.py``, say.
    """
    record = check_for(name)
    return tuple(f"{p.path}/{f}" for p in natives for f in judged_files(record, p))


def _every_unit_reached(ctx: PhaseContext, record: CheckRecord, chosen: int) -> bool:
    """Whether a scoped run over *chosen* units reaches every unit *record* judges.

    Every member the check judges, the workspace's tests unit among
    them, with none turned off anywhere: the whole would reach the
    same, so the check runs over its configured whole instead of a
    list naming every unit.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS

    everyone = tuple(p for p in ctx.packages if p.path != WORKSPACE_TESTS)
    gated_all = judged_by(record, everyone, quiet=True)
    # The workspace's tests unit rides in the scope, not always among
    # the packages: counted once from either.
    tests_unit = {
        p.path for p in (*ctx.packages, *ctx.judged) if p.path == WORKSPACE_TESTS
    }
    units = len(gated_all) + len(tests_unit)
    on = all(option_value(record, p, "enabled") for p in gated_all)
    return on and 0 < units <= chosen


def scoped_paths(ctx: PhaseContext, name: str) -> tuple[str, ...]:
    """The paths the path-narrowing check *name* judges in this run.

    The files the run names, when it names any. Otherwise the whole
    tree, ``.``, when the run reaches every member the check judges;
    else the reached python members' ``src`` and ``tests``
    directories, the workspace's own tests when the run judges them,
    and the files the check's claims give it in the reached native
    members.
    """
    from livery.workshop._kinds import is_python_kind
    from livery.workshop._packages import package_paths

    record = check_for(name)
    if ctx.files:
        return claimed_files(record, ctx)
    gated_members = judged_by(record, members(ctx))
    chosen_members = enabled(name, gated_members)
    pythons = tuple(p for p in chosen_members if is_python_kind(p.kind))
    natives = tuple(p for p in chosen_members if not is_python_kind(p.kind))
    unit = _workspace_unit(ctx)

    def holding(packages: tuple[Package, ...]) -> tuple[str, ...]:
        # A directory with no file the claims reach has nothing to
        # judge, and a tool such as mypy refuses one it is handed.
        return tuple(
            path
            for path in package_paths(packages)
            if _holds_claimed(record, ctx.root / path)
        )

    if ctx.subset is None:
        if len(chosen_members) == len(gated_members):
            return WHOLE
        chosen = holding(pythons + unit) + _claimed(name, natives)
        return chosen or WHOLE
    judged = tuple(p for p in ctx.subset if p in pythons or p in unit)
    present = tuple(p for p in natives if p in ctx.subset)
    if _every_unit_reached(ctx, record, len(judged) + len(present)):
        return WHOLE
    return holding(judged) + _claimed(name, present)


def _holds_claimed(record: CheckRecord, directory: Path) -> bool:
    """Whether *directory* holds a file with a suffix *record*'s claims read.

    A check with no claim, or a claim that names no suffix, reads every
    file, so any directory holds one.
    """
    if not record.claims or any(not claim.suffixes for claim in record.claims):
        return True
    suffixes = sorted({suffix for claim in record.claims for suffix in claim.suffixes})
    return any(next(directory.rglob(f"*{suffix}"), None) for suffix in suffixes)


def scoped_files(
    ctx: PhaseContext, name: str, package: Package | None = None
) -> tuple[Path, ...]:
    """The files of *package* the check *name* judges in this run, absolute.

    *package* is the run's own package unless named: a package check
    names none, and a check that narrows by packages names each one
    [livery.workshop.scoped_packages][] answers. The files the run
    names, when it names any; else every file the check's claims reach
    in the package, an untracked new one among them.

    Raises:
        Failed: when neither *package* nor the run names a package.
    """
    record = check_for(name)
    package = package or ctx.package
    if package is None:
        fail(f"{name} judges one package at a time, and this run names none")
    if ctx.files:
        return tuple(Path(path) for path in claimed_files(record, ctx, package.path))
    return tuple(package.directory / path for path in judged_files(record, package))


def scoped_packages(ctx: PhaseContext, name: str) -> tuple[Package, ...]:
    """The members the package-narrowing check *name* judges in this run.

    The members of its kinds, in a scoped run those of the subset,
    less each one that turned the check off in its contract. Each
    skip is printed, so a narrowed gate names what it left out. A run
    over named files keeps the members holding a named file the
    check claims. The workspace's own tests are not a member.
    """
    record = check_for(name)
    chosen = enabled(name, judged_by(record, members(ctx)))
    if not ctx.files:
        return chosen
    return tuple(p for p in chosen if claimed_files(record, ctx, p.path))


def check_option(name: str, package: Package, option: str) -> object:
    """What *package* sets for the check *name*'s *option*, or the option's default.

    The deepest of the package's tables that sets it wins. Refuses an
    option the check does not declare and a value of the wrong type,
    naming the check's options.
    """
    return option_value(check_for(name), package, option)


def tested(units: tuple[Package, ...]) -> tuple[Package, ...]:
    """The *units* whose suites a registered check of the test role runs.

    A package by the extensions it holds. The workspace's own tests
    are the root's suite, run when a test check names an extension the
    root's kind stands for ([livery.workshop._tools.ROOT_KIND][]).
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._kinds import kind_stands_for
    from livery.workshop._tools import ROOT_KIND

    tests = [record for record in _CHECKS.values() if record.role == "test"]
    root = set(kind_stands_for(ROOT_KIND))
    workspace = any(root & set(record.extensions) for record in tests)

    def runs(unit: Package) -> bool:
        if unit.path == WORKSPACE_TESTS:
            return workspace
        return any(_applies(record, unit) for record in tests)

    return tuple(unit for unit in units if runs(unit))


def _register_builtin() -> None:
    """Register the workshop's own checks, in the order the rewriters run.

    The workspace checks judge the whole tree; the cmake build and test
    checks judge one package at a time, each package holding the cmake
    extension. The base registers those three until the package kinds
    go, since a package that lists no extension holds cmake through its
    kind alone and never mounts the extension. A body resolves its
    backend function on the module when it runs, never at registration,
    so a test that patches ``_cpp_conan.test`` sees its fake run.
    """
    from livery.workshop._backends import _cpp_conan
    from livery.workshop._influence import Inputs

    def render_run(ctx: PhaseContext) -> None:
        from livery.workshop import _quality

        _quality.drift_check(files=_selected_files(check_for("drift.check"), ctx))

    def render_fix(ctx: PhaseContext) -> None:
        # A rewriter is not judged again under --fix, so the fix judges
        # what it leaves, as the provenance fix does.
        from livery.workshop import _quality
        from livery.workshop._shipped_files import deliver, relocate
        from livery.workshop._templates import apply_generated

        for line in relocate(ctx.root) + deliver(ctx.root):
            print(line)
        for path in apply_generated(ctx.root):
            print(f"  generated: {path}")
        _quality.drift_check()

    def provenance_run(ctx: PhaseContext) -> None:
        from livery.workshop import _provenance

        _provenance.check_content(
            files=_selected_files(check_for("provenance.check"), ctx)
        )

    def provenance_fix(ctx: PhaseContext) -> None:
        from livery.workshop import _provenance

        _provenance.check_content(
            fix=True, files=_selected_files(check_for("provenance.check"), ctx)
        )

    def graph_run(ctx: PhaseContext) -> None:
        from livery.workshop._packages import verify_graph

        verify_graph(ctx.root)

    def graph_fix(ctx: PhaseContext) -> None:
        from livery.workshop._extensions import write_extensions
        from livery.workshop._packages import verify_graph

        for line in write_extensions(ctx.root):
            print(line)
        verify_graph(ctx.root)

    def imports_run(ctx: PhaseContext) -> None:
        from livery.workshop._packages import verify_imports

        verify_imports(ctx.root, _selected_files(check_for("layering.imports"), ctx))

    def imports_fix(ctx: PhaseContext) -> None:
        from livery.workshop._ast_rules import RuleContext, ast_rules, parsed_modules
        from livery.workshop._packages import verify_imports
        from livery.workshop._uv import run_uv

        files = _selected_files(check_for("layering.imports"), ctx)
        # Every rule's fix runs here, inside the one rewrite and over
        # the one parse; the judgments follow in the check's judge.
        context = RuleContext(root=ctx.root, packages=ctx.packages, files=files)
        modules = parsed_modules(ctx.root, ctx.packages, files)
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
        verify_imports(ctx.root, files)

    def configure_run(ctx: PhaseContext) -> None:
        _cpp_conan.configure(ctx.member, ctx.arguments)

    def build_run(ctx: PhaseContext) -> None:
        _cpp_conan.compile(ctx.member, ctx.arguments)

    def ctest_run(ctx: PhaseContext) -> None:
        _cpp_conan.test(
            ctx.member, ctx.root, selection=ctx.selection, arguments=ctx.arguments
        )

    for record in (
        CheckRecord(
            "check",
            "drift",
            render_run,
            fix=render_fix,
            inputs=Inputs(reads=_drift_reads, widens=_drift_widens, ignores=NOT_INPUTS),
        ),
        CheckRecord(
            "check",
            "provenance",
            provenance_run,
            fix=provenance_fix,
            inputs=Inputs(reads=("packages/**/src/**/content/**",)),
        ),
        CheckRecord(
            "graph",
            "layering",
            graph_run,
            fix=graph_fix,
            inputs=Inputs(reads=_graph_files, per_file=False, ignores=NOT_INPUTS),
        ),
        CheckRecord(
            "imports",
            "layering",
            imports_run,
            fix=imports_fix,
            inputs=Inputs(
                reads=("tasks.py", "packages/**/*.py"),
                widens=_graph_files,
                ignores=NOT_INPUTS,
            ),
        ),
        CheckRecord(
            "configure",
            "build",
            configure_run,
            scope=PACKAGE,
            extensions=("cmake",),
            tests_only=True,
            arguments=True,
        ),
        CheckRecord(
            "compile",
            "build",
            build_run,
            scope=PACKAGE,
            extensions=("cmake",),
            tests_only=True,
            after=("build.configure",),
            arguments=True,
        ),
        # A change to any test or source file reaches ctest, whatever the
        # language the project builds.
        CheckRecord(
            "ctest",
            "test",
            ctest_run,
            scope=PACKAGE,
            extensions=("cmake",),
            tests_only=True,
            after=("build.compile",),
            claims=(Claim("test"), Claim("source")),
            arguments=True,
        ),
    ):
        register_check(record)


_register_builtin()
