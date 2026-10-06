"""The check registry: what the quality gate runs, one record per check.

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

A check is named by its role and its tool, ``test.pytest``. Every
role is a verb and every check a sub-task of it, made by
[livery.workshop._checks.generate_verbs][] from the registry: ``fm
test`` runs the test role's checks and ``fm test.pytest`` the one.

Every registered check is also a hidden task, ``checks.test-pytest``,
and the gate schedules those tasks: the rewriters serially before
any judge reads the tree ([livery.workshop._checks.rewriters][]),
then every judge together ([livery.workshop._checks.judges][]), each
with its own report row. A check that must follow another on the
same package, a build after its configure, names it in ``after`` and
the scheduler orders them.
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable, Generator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, Any

from livery.footman import context
from livery.footman.api import Group, doc, fail, group, prog
from livery.workshop import _fragments, _slots
from livery.workshop._fragments import Fragment

if TYPE_CHECKING:
    from livery.workshop._influence import Changes, Inputs, Selection
    from livery.workshop._packages import Package

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
        changes: What changed since the tree the run measures from,
            which selects what each workspace check with declared
            inputs judges ([livery.workshop._influence.select][]); None
            when the run knows nothing of it, and then each judges
            everything.
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
    changes: Changes | None = None

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
    """One option a package may set on a check, in its ``[checks.<role>.<tool>]`` table.

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
    verb ``fm test.pytest``, the ``[checks.test.pytest]`` table of its
    options, the gate's lines, and every lookup use it.

    Attributes:
        tool: The tool the check runs, as its sub-task names it under
            its role: ``pytest`` in ``fm test.pytest``.
        role: What the check implements: ``format``, ``lint``,
            ``typecheck``, ``typecomplete``, ``test``, ``build``,
            ``drift``, ``provenance`` or ``layering``, a string a
            verb is generated from. The roles that exist are those
            of the registered checks.
        run: The judging callable; a refusal is its verdict.
        scope: ``workspace`` for a check that judges the whole in one
            run, ``package`` for one that judges one package at a
            time.
        narrowing: How a workspace check narrows under a scoped gate:
            ``paths`` (the subset's directories), ``packages`` (the
            subset's members) or ``none`` (the whole is always
            checked). A package check narrows by its packages.
        transport: How a path list reaches the tool
            ([livery.workshop._invoke.TRANSPORTS][]): ``argv``, on its
            command line, split into the fewest calls the platform
            allows; ``file`` and ``config`` are named for the tools
            that read one.
        threshold: The share of affected units at or above which a
            narrowed check runs over its configured whole instead; 1
            narrows until every unit is affected.
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
        inputs: The files a workspace check reads, so a run judges it
            only when one of them, or what widens it, changed, and then
            only the changed ones when it judges per file
            ([livery.workshop._influence.Inputs][]). None for a check
            that narrows by its packages alone.
        after: The checks this one runs after, by name: a build
            after its configure, a ctest after its build. Each runs
            through its task before this one's body, once per gate
            whoever asks first.
        extension: The extension that registered the check, named when a
            narrowing is printed.
        tools: The tools the check runs, as the tool profile names
            them, ``("clang_format",)``; each reaches the profile for every kind
            the check judges, naming ``check <name>`` as its site, and
            leaves it when the check is unregistered.
        options: The options a package may set under
            ``[checks.<role>.<tool>]``; ``enabled`` is every check's.
        contributions: ``(slot, value)`` pairs the record puts into
            slots at registration, the dev group's lines say; withdrawn
            with the record.
        fragments: The configuration the render manages for the
            check, one per rendered file it has something to say in;
            composed in check-name order, judged by the drift gate,
            gone from the next render with the record.
        editor_extension: The editor extension's marketplace id, when the
            record carries a verified one; the rendered
            recommendations list names these and nothing else.
        claims: The categories the check judges, each with the rules
            it withholds there and the suffixes it reads; a check
            judges every file its claims reach and no other, and a
            tool with per-file ignores renders its ignores from them.
        roles: Further roles the check implements; it answers to
            ``<role>.<tool>`` under each, and its options stay in its
            own table, ``[checks.<role>.<tool>]`` for ``role``.
        flags: The command-line flags the check reads, from `FLAGS`:
            ``point`` for a check that selects tests by CI point. A
            generated verb offers the flags its checks declare, and
            ``--fix`` with ``--safe-fix`` where a check has a fix mode.
        listed_with: The option of its extension that registers the
            check, which a workspace turns on in its list,
            ``basedpyright[typecomplete]``; empty for a check that
            registers whenever its extension is listed. The extension
            declares the option in its ``OPTIONS``.
    """

    tool: str
    role: str
    run: Callable[[GateContext], None]
    scope: str = WORKSPACE
    narrowing: str = NONE
    transport: str = "argv"
    threshold: float = 1.0
    fix: Callable[[GateContext], None] | None = None
    kinds: tuple[str, ...] = ()
    tests_only: bool = False
    inputs: Inputs | None = None
    after: tuple[str, ...] = ()
    extension: str = BASE_EXTENSION
    tools: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()
    contributions: tuple[tuple[str, object], ...] = ()
    fragments: tuple[Fragment, ...] = ()
    editor_extension: str = ""
    claims: tuple[Claim, ...] = ()
    roles: tuple[str, ...] = ()
    flags: tuple[str, ...] = ()
    listed_with: str = ""

    @property
    def name(self) -> str:
        """The check's name: its role and its tool, ``test.pytest``."""
        return f"{self.role}.{self.tool}"


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
    judges packages and applies to none never runs. Each address the
    check answers to, ``<role>.<tool>`` under its role and each
    further one, is a task's address, so a dot inside the role or
    the tool refuses, a tool named ``default`` refuses (that address
    is the role verb's own), and an address another check answers to
    refuses: one address runs one check.
    """
    for part in (record.role, *record.roles, record.tool):
        if not part or "." in part:
            fail(
                f"check {record.name!r}: a role and a tool are one word each,"
                f" not {part!r}; the check answers to <role>.<tool>"
            )
    if record.tool == "default":
        fail(
            f"check {record.name!r}: {record.name} is the address of the"
            f" {record.role} verb itself; name the check by its tool"
        )
    for role in (record.role, *record.roles):
        address = f"{role}.{record.tool}"
        holder = answering(address)
        if holder is not None and holder != record.name:
            fail(
                f"check {record.name!r} answers to {address}, which the check"
                f" {holder!r} answers to already: one address runs one check"
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
    from livery.workshop._invoke import ARGV, TRANSPORTS

    if record.transport not in TRANSPORTS:
        fail(
            f"check {record.name!r}: transport is one of {', '.join(TRANSPORTS)},"
            f" not {record.transport!r}"
        )
    if record.transport != ARGV:
        fail(
            f"check {record.name!r}: the engine passes paths by {ARGV!r} alone"
            f" so far; {record.transport!r} waits for a tool that needs it"
        )
    if not 0 < record.threshold <= 1:
        fail(
            f"check {record.name!r}: threshold is a share of the units, above 0"
            f" and at most 1, not {record.threshold!r}"
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
        _slots.contribute(slot, value, extension=record.extension, by=record.name)


def _slots_withdraw(name: str) -> None:
    for slot in _slots.slots():
        _slots.withdraw(slot, by=name)


def _typed(option: Option, value: object) -> bool:
    expected = {"bool": bool, "str": str, "int": int}[option.kind]
    if option.kind == "int" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def option_addresses(record: CheckRecord) -> tuple[str, ...]:
    """The tables that reach *record*'s options, shallowest first.

    ``roles.<role>`` for each role the check answers to, then
    ``checks.<tool>``, then ``checks.<tool>.<role>`` for each role: a
    later table wins key by key. The tool's table outranks the role's,
    since it names fewer checks.
    """
    roles = (record.role, *record.roles)
    return (
        *(f"roles.{role}" for role in roles),
        f"checks.{record.tool}",
        *(f"checks.{record.tool}.{role}" for role in roles),
    )


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
    """The registered checks a table *address* reaches."""
    kind, _, rest = address.partition(".")
    if kind == "roles":
        return tuple(r for r in _CHECKS.values() if rest in (r.role, *r.roles))
    tool, _, role = rest.partition(".")
    return tuple(
        r
        for r in _CHECKS.values()
        if r.tool == tool and (not role or role in (r.role, *r.roles))
    )


def option_problems(packages: tuple[Package, ...]) -> list[str]:
    """Every option table or key no registered check takes, one line each.

    Read by the layering check, so a package that addresses a tool, a
    check or a role that does not exist, or sets an option none of the
    checks it reaches declares, is refused with the vocabulary named.
    """
    problems: list[str] = []
    tools = sorted({r.tool for r in _CHECKS.values()})
    roles = sorted({role for r in _CHECKS.values() for role in (r.role, *r.roles)})
    for package in packages:
        where = f"{package.path}/workshop.toml"
        for address, options in package.checks:
            reached = _reached(address)
            if not reached:
                kind, _, rest = address.partition(".")
                first, _, second = rest.partition(".")
                if kind == "checks" and first in roles and second in tools:
                    problems.append(
                        f"{where}: [{address}] names a role then a tool; a check's"
                        f" own table is [checks.{second}.{first}]"
                    )
                elif kind == "checks" and first in roles and not second:
                    problems.append(
                        f"{where}: [{address}] names a role; a role's options are"
                        f" [roles.{first}]"
                    )
                elif kind == "roles":
                    problems.append(
                        f"{where}: [{address}] names no role a registered check"
                        f" has; the roles are {', '.join(roles)}"
                    )
                else:
                    problems.append(
                        f"{where}: [{address}] reaches no registered check; the"
                        f" tools are {', '.join(tools)}"
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

    *by* names the extension withdrawing a check it did not register, so
    the gate can print who narrowed it.
    """
    if name not in _CHECKS:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    record = _CHECKS.pop(name)
    _slots_withdraw(name)
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
    import livery.toolroom.tools.api as tools

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
    patterns in the kind's table, a package's under ``packages/**/``,
    which reaches a member in a group directory too, and the root's as
    they are; two checks claiming one category under different rules
    land in one entry with both sets.
    """
    from livery.workshop._categories import WORKSPACE, category_rules
    from livery.workshop._kinds import kind_chain, kind_names

    table: dict[str, set[str]] = {}
    units = [(kind, "packages/**/") for kind in kinds if kind in kind_names()]
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


def editor_extensions() -> tuple[str, ...]:
    """The editor extension ids the registered checks carry, sorted, each once."""
    return tuple(
        sorted({r.editor_extension for r in _CHECKS.values() if r.editor_extension})
    )


def answering(address: str) -> str | None:
    """The check answering to *address*, by its name or a further role."""
    if address in _CHECKS:
        return address
    role, _, tool = address.partition(".")
    for record in _CHECKS.values():
        if record.tool == tool and role in record.roles:
            return record.name
    return None


def check_for(name: str) -> CheckRecord:
    """The check named *name*, ``test.pytest``, a further role's name included.

    Refusal names the registry.
    """
    holder = answering(name)
    if holder is None:
        fail(f"{name!r} is not a registered check; checks: {', '.join(_CHECKS)}")
    return _CHECKS[holder]


def roles() -> tuple[str, ...]:
    """The roles the registered checks implement, sorted."""
    return tuple(
        sorted({role for r in _CHECKS.values() for role in (r.role, *r.roles)})
    )


def judges_kind(record: CheckRecord, kind: str) -> bool:
    """Whether *record* judges a package of *kind*: its kinds meet the kind's chain.

    A check that names no kinds judges every package; a kind no record
    registers (the workspace's own tests unit) is judged by such a
    check alone.
    """
    from livery.workshop._kinds import kind_chain, kind_names

    if not record.kinds:
        return True
    if kind not in kind_names():
        return False
    return any(link.name in record.kinds for link in kind_chain(kind))


def judged_by(
    record: CheckRecord, packages: tuple[Package, ...], *, quiet: bool = False
) -> tuple[Package, ...]:
    """The *packages* *record* judges, by its kinds; each other one skips by name.

    A package outside the check's kinds prints a skip naming the check,
    the package and its kind, so a narrowed gate is visible in the
    output and never passes silently. *quiet* counts without printing.
    """
    kept: list[Package] = []
    for package in packages:
        if judges_kind(record, package.kind):
            kept.append(package)
        elif not quiet:
            print(f"  {record.name}: {package.path} skips ({package.kind} kind)")
    return tuple(kept)


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
#: its prints reach the console. The body reads the record by name
#: when it runs, so re-registering a name replaces the record and
#: keeps the task.
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
            f"a check's task ran outside the gate: `{prog()} check` runs it,"
            f" and `{prog()} <role>.<tool>` runs one check alone"
        )
    return _CURRENT


def _ensure_task(record: CheckRecord) -> None:
    """Register the hidden task for *record*'s name, once."""
    if record.name in _TASKS:
        return
    name = record.name

    def body(fix: Annotated[bool, doc("run the check's fix mode")] = False) -> None:
        run_check(name, _context_now(), fix=fix)

    body.__name__ = name.replace("-", "_").replace(".", "_")
    body.__doc__ = f"Run the {name} check over the gate's context."
    _TASKS[name] = checks.task(name=name.replace(".", "-"))(body)


def task_for(name: str) -> Any:
    """The hidden task that runs the check *name*; refusal names the registry."""
    check_for(name)
    return _TASKS[name]


def verb_tree() -> dict[str, dict[str, CheckRecord]]:
    """Each role's checks by tool, sorted: the verbs the registry generates."""
    tree: dict[str, dict[str, CheckRecord]] = {}
    for record in _CHECKS.values():
        for role in (record.role, *record.roles):
            tree.setdefault(role, {})[record.tool] = record
    return {role: dict(sorted(tools.items())) for role, tools in sorted(tree.items())}


#: The verb functions the generator made. Footman shares a task's
#: function when it copies a tree into a project, so the function is
#: how a later generation knows a verb as its own: its own it remakes
#: or removes, and any other task at an address serves that address.
_MADE: weakref.WeakSet[Callable[..., None]] = weakref.WeakSet()


def _run_named(
    names: tuple[str, ...],
    paths: tuple[str, ...],
    *,
    fix: bool = False,
    safe_fix: bool = False,
    point: str = "",
) -> None:
    from livery.workshop import _quality

    _quality.run_checks(names, paths, fix=fix, safe_fix=safe_fix, point=point)


#: A generated verb's flags, each with its help text. They live at
#: module level because footman reads a task's annotations in its
#: module's namespace: a name local to the function making the verb
#: does not resolve there, and the flag's value would arrive as text.
_Fix = Annotated[bool, doc("rewrite what the checks can, then judge the rest")]
_SafeFix = Annotated[bool, doc("fix, removing no code: safe for an edit in flight")]
_Point = Annotated[
    str, doc("select this CI point's tests: gate, merge, nightly, release")
]


def _verb(
    select: Callable[[], tuple[str, ...]], *, fixes: bool, reads_point: bool, what: str
) -> Callable[..., None]:
    """A verb over the checks *select* names, offering exactly the flags they read.

    ``--fix`` and ``--safe-fix`` where a check can fix, ``--point``
    where one reads the point; the paths narrow the run to those files.
    """

    def with_fix_and_point(
        *paths: str, fix: _Fix = False, safe_fix: _SafeFix = False, point: _Point = ""
    ) -> None:
        _run_named(select(), paths, fix=fix, safe_fix=safe_fix, point=point)

    def with_fix(*paths: str, fix: _Fix = False, safe_fix: _SafeFix = False) -> None:
        _run_named(select(), paths, fix=fix, safe_fix=safe_fix)

    def with_point(*paths: str, point: _Point = "") -> None:
        _run_named(select(), paths, point=point)

    def bare(*paths: str) -> None:
        _run_named(select(), paths)

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
        return tuple(
            record.name
            for record in _CHECKS.values()
            if role in (record.role, *record.roles)
        )

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
    from livery.footman import registry

    target = into if into is not None else registry.root
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
                    " or the workspace.",
                ),
            )


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
        if record.inputs is not None:
            # Listed, so the walk says when no file it reads changed.
            return True
        # A check without declared inputs narrows by its packages, so a
        # scope that holds none leaves it nothing to judge. Running it
        # would read the empty scope as the whole: the test check would
        # start the whole suite. Named files are a scope of their own,
        # which the check's claims judge.
        return bool(ctx.files) or not (ctx.scoped and not ctx.subset)
    return any(record in _package_records(ctx, p) for p in _members(ctx))


def workspace_selected(ctx: GateContext) -> tuple[str, ...]:
    """The workspace checks with declared inputs that this run's changes select."""
    return tuple(
        record.name
        for record in _CHECKS.values()
        if record.scope == WORKSPACE
        and record.inputs is not None
        and selected(record, ctx).runs
    )


def selected_files(record: CheckRecord, ctx: GateContext) -> frozenset[str] | None:
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
    from livery.workshop._extensions import extension_names, extension_package
    from livery.workshop._influence import provider_of
    from livery.workshop._packages import discover_packages

    packages = discover_packages(root)
    providers = {
        provider_of(extension_package(name), packages) for name in extension_names(root)
    }
    return (
        "workshop.toml",
        *package_files(root),
        "uv.lock",
        "tools.lock",
        *(f"{provider}/src/**" for provider in sorted(providers) if provider),
    )


def selected(record: CheckRecord, ctx: GateContext) -> Selection:
    """What *record* judges in this run, by its declared inputs.

    Everything for a check that declares none. The run's changes
    select, or the named files of `fm check <paths>` when there are no
    changes; a run with neither judges everything.
    """
    from livery.workshop._influence import WHOLE, Changes, provider_of, select

    if record.inputs is None:
        return WHOLE
    changes = ctx.changes
    if changes is None and ctx.files:
        changes = Changes(ctx.root, ctx.files)
    return select(
        record.inputs, changes, provider=provider_of(record.extension, ctx.packages)
    )


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
    import livery.toolroom.tools.api as tools
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


#: The whole repository, as a check that walks it from the root reads it.
#: [livery.workshop._checks.scoped_paths][] answers it when a check
#: judges its configured whole; a tool that reads ``.`` as more than
#: its configuration names takes no paths then.
WHOLE = (".",)


def _workspace_unit(ctx: GateContext) -> tuple[Package, ...]:
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


def whole_reached(ctx: GateContext, name: str, chosen: int) -> bool:
    """Whether the scoped check *name* over *chosen* units should run its whole.

    At the check's threshold share of every unit it judges, provided
    no member anywhere turned it off: the whole would reach that
    member too.
    """
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._invoke import runs_whole

    record = check_for(name)
    everyone = tuple(p for p in ctx.packages if p.path != WORKSPACE_TESTS)
    gated_all = judged_by(record, everyone, quiet=True)
    # The workspace's tests unit rides in the scope, not always among
    # the packages: counted once from either.
    tests_unit = {
        p.path for p in (*ctx.packages, *ctx.judged) if p.path == WORKSPACE_TESTS
    }
    units = len(gated_all) + len(tests_unit)
    on = all(option_value(record, p, "enabled") for p in gated_all)
    return on and runs_whole(chosen, units, record.threshold)


def scoped_paths(ctx: GateContext, name: str) -> tuple[str, ...]:
    """The paths the path-narrowing check *name* judges in this run.

    The files the run names, when it names any. Otherwise the whole
    tree, ``.``, when the check reaches every member it judges or its
    threshold says so; else the reached python members' ``src`` and
    ``tests`` directories, the workspace's own tests when the run
    judges them, and the files the check's claims give it in the
    reached native members.
    """
    from livery.workshop._kinds import is_python_kind
    from livery.workshop._packages import package_paths

    record = check_for(name)
    if ctx.files:
        return claimed_files(record, ctx)
    gated_members = judged_by(record, _members(ctx))
    members = enabled(name, gated_members)
    pythons = tuple(p for p in members if is_python_kind(p.kind))
    natives = tuple(p for p in members if not is_python_kind(p.kind))
    unit = _workspace_unit(ctx)
    if ctx.subset is None:
        if len(members) == len(gated_members):
            return WHOLE
        chosen = package_paths(pythons + unit) + _claimed(name, natives)
        return chosen or WHOLE
    judged = tuple(p for p in ctx.subset if p in pythons or p in unit)
    present = tuple(p for p in natives if p in ctx.subset)
    if whole_reached(ctx, name, len(judged) + len(present)):
        return WHOLE
    return package_paths(judged) + _claimed(name, present)


def scoped_packages(ctx: GateContext, name: str) -> tuple[Package, ...]:
    """The members the package-narrowing check *name* judges in this run.

    The members of its kinds, in a scoped run those of the subset,
    less each one that turned the check off in its contract. Each
    skip is printed, so a narrowed gate names what it left out. The
    workspace's own tests are not a member.
    """
    return enabled(name, judged_by(check_for(name), _members(ctx)))


def _register_builtin() -> None:
    """Register the workshop's own checks, in the order the rewriters run.

    The python checks judge the whole tree or the scoped subset's
    directories and gate each package by its kind's roles; the native
    checks judge one package at a time. A body resolves its backend
    function on the module when it runs, never at registration, so a
    test that patches ``_python.run_typecheck`` sees its fake run.
    """
    from livery.workshop._backends import _cpp_conan, _python
    from livery.workshop._coverage_store import WORKSPACE_TESTS
    from livery.workshop._influence import Inputs

    def unit(ctx: GateContext) -> tuple[Package, ...]:
        return tuple(p for p in ctx.judged if p.path == WORKSPACE_TESTS)

    # pyrefly checks its configured whole whatever the scope.
    def pyrefly_run(ctx: GateContext) -> None:
        del ctx
        _python.run_typecheck()

    def test_run(ctx: GateContext) -> None:
        point = (f"--workshop-point={ctx.point}",) if ctx.point else ()
        if ctx.files:
            # A named test or source file runs its package's whole suite;
            # the workspace's tests unit reads the root's own files.
            record = check_for("test.pytest")
            named = tuple(
                p
                for p in (*scoped_packages(ctx, "test.pytest"), *unit(ctx))
                if claimed_files(
                    record, ctx, ROOT_UNIT if p.path == WORKSPACE_TESTS else p.path
                )
            )
            _python.run_test(*point, packages=named, root=ctx.root, scoped=True)
            return
        # A package whose examples alone changed runs them, not its suite.
        judged = tuple(
            p for p in scoped_packages(ctx, "test.pytest") if p.path not in ctx.examples
        )
        record = check_for("test.pytest")
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

        for package in scoped_packages(ctx, "examples.pytest"):
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
            # Only a run over named files narrows the examples; a whole
            # walk runs the package's directory.
            named: tuple[str, ...] = ()
            if ctx.files:
                named = claimed_files(check_for("examples.pytest"), ctx, package.path)
                if not named:
                    continue
            runner(package, ctx.root, named)

    def render_run(ctx: GateContext) -> None:
        from livery.workshop import _quality

        _quality.drift_check(files=selected_files(check_for("drift.check"), ctx))

    def render_fix(ctx: GateContext) -> None:
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

    def provenance_run(ctx: GateContext) -> None:
        from livery.workshop import _provenance

        _provenance.check_content(
            files=selected_files(check_for("provenance.check"), ctx)
        )

    def provenance_fix(ctx: GateContext) -> None:
        from livery.workshop import _provenance

        _provenance.check_content(
            fix=True, files=selected_files(check_for("provenance.check"), ctx)
        )

    def graph_run(ctx: GateContext) -> None:
        from livery.workshop._packages import verify_graph

        verify_graph(ctx.root)

    def graph_fix(ctx: GateContext) -> None:
        from livery.workshop._extensions import write_extensions
        from livery.workshop._packages import verify_graph

        for line in write_extensions(ctx.root):
            print(line)
        verify_graph(ctx.root)

    def imports_run(ctx: GateContext) -> None:
        from livery.workshop._packages import verify_imports

        verify_imports(ctx.root, selected_files(check_for("layering.imports"), ctx))

    def imports_fix(ctx: GateContext) -> None:
        from livery.workshop._ast_rules import RuleContext, ast_rules, parsed_modules
        from livery.workshop._packages import verify_imports
        from livery.workshop._uv import run_uv

        files = selected_files(check_for("layering.imports"), ctx)
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
        files = named_sources(ctx, "format.clang-format")
        _cpp_conan.format_check(package_of(ctx), fix=False, files=files)

    def clang_format_fix(ctx: GateContext) -> None:
        files = named_sources(ctx, "format.clang-format")
        _cpp_conan.format_check(package_of(ctx), fix=True, files=files)

    def configure_run(ctx: GateContext) -> None:
        _cpp_conan.configure(package_of(ctx))

    def build_run(ctx: GateContext) -> None:
        _cpp_conan.compile(package_of(ctx))

    def ctest_run(ctx: GateContext) -> None:
        package = package_of(ctx)
        _cpp_conan.test(package, ctx.root, selection=ctx.selection)

    def clang_tidy_run(ctx: GateContext) -> None:
        files = named_sources(ctx, "lint.clang-tidy")
        _cpp_conan.lint(package_of(ctx), ctx.root, files=files)

    # The slots the python records fill: the dev group's tool lines
    # and pytest's options, lines the base template no longer writes
    # by hand. The python extension declares both once it exists.
    _slots.register_slot("python.dev-group")
    _slots.register_slot("python.test.addopts")

    native = ("cpp-conan", "python-nanobind")
    python = ("python",)
    py = _python.PY_SUFFIXES
    typed_claims = tuple(
        Claim(category, suffixes=py) for category in ("source", "test", "test-support")
    )
    cpp = _cpp_conan.SOURCE_SUFFIXES
    for record in (
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
            "pyrefly",
            "typecheck",
            pyrefly_run,
            kinds=python,
            tools=("pyrefly",),
            fragments=(Fragment("pyproject.toml", _fragments.PYREFLY),),
            claims=typed_claims,
        ),
        CheckRecord(
            "pytest",
            "test",
            test_run,
            flags=("point",),
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
            # they live in the venv: coverage 7.13 for the .pth it
            # installs, which starts the meter in every python the tests
            # start once the runner arms it and imports nothing
            # otherwise, and xdist for -n auto.
            contributions=(
                ("python.dev-group", "pytest>=8.0"),
                ("python.dev-group", "pytest-cov>=5"),
                ("python.dev-group", "coverage[toml]>=7.13"),
                ("python.dev-group", "pytest-xdist>=3.6"),
                ("python.test.addopts", "-q"),
                ("python.test.addopts", "-n auto"),
                ("python.test.addopts", "--dist=worksteal"),
                ("python.test.addopts", "--import-mode=importlib"),
            ),
        ),
        CheckRecord(
            "pytest",
            "examples",
            examples_run,
            narrowing=PACKAGES,
            kinds=python,
            tools=("pytest",),
            claims=(Claim("example", suffixes=py),),
        ),
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
            kinds=("cpp-conan",),
            tests_only=True,
        ),
        CheckRecord(
            "compile",
            "build",
            build_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            tests_only=True,
            after=("build.configure",),
        ),
        CheckRecord(
            "ctest",
            "test",
            ctest_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            tests_only=True,
            after=("build.compile",),
            claims=(Claim("test", suffixes=cpp), Claim("source", suffixes=cpp)),
        ),
        CheckRecord(
            "clang-tidy",
            "lint",
            clang_tidy_run,
            scope=PACKAGE,
            kinds=("cpp-conan",),
            after=("build.configure",),
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
