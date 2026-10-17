"""The step registry: what the quality gate runs, one record per step.

A step is one tool's judgment over the workspace or over one
package: a formatter's pass, the render drift comparison, ctest
over a native member. Each is a [livery.workshop._steps.StepRecord][]
registered through [livery.workshop._steps.register_step][], and
the gate is the walk over the registry: every applicable step
green, the exit code the verdict. The workshop registers the
builtin records at import; an extension's records register at
mount, after them and in composition order, each extension after
the ones it requires, ties alphabetical, and re-registering a name
replaces the record.

A step names the extensions whose packages it judges, and a package
holding none of them skips it by name: a C++ member is formatted over
its recipe by a python formatter and over its sources by clang-format
because each step names an extension it holds. The phases the
registry knows are the ones its steps name, so they are what the
listed extensions register.

A step is named by its phase and its name in the phase,
``test.pytest``. The phase is a declared one
([livery.workshop._phases.declared_phases][]), and a step of a phase
nobody declares refuses naming the declared ones. A phase that
declares a verb is a verb and every step a sub-task of it, made by
[livery.workshop._steps.generate_verbs][] from the registry: ``fm
test`` runs the test phase's steps and ``fm test.pytest`` the one;
the gate runs the steps of the phases that declare themselves gated.

The gate runs the registered steps through the one walk
([livery.workshop._phases.walk_steps][]), each a footman step: the
rewriters one at a time before any judge reads the tree
([livery.workshop._steps.rewriters][]), then every judge in one
block ([livery.workshop._steps.judges][]), each with its own record.
A step that must follow another on the same package, a build after
its configure, names it in ``after``: the walk starts it once the
earlier one ran, and the gate brings the earlier one in
([livery.workshop._steps.with_earlier][]).
"""

from __future__ import annotations

import inspect
import weakref
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Annotated, cast

from livery import git
from livery.footman import Group, doc, fail, prog, root_group
from livery.workshop import _fragments
from livery.workshop._fragments import Fragment

if TYPE_CHECKING:
    from livery.workshop._categories import CategoryRule
    from livery.workshop._influence import Changes, Inputs, Selection
    from livery.workshop._packages import Package
    from livery.workshop._phases import PhaseContext
    from livery.workshop._words import Words

#: A step's scope: the whole workspace in one run, or one package at a time.
WORKSPACE = "workspace"
PACKAGE = "package"

#: How a workspace step narrows under a package-scoped gate.
PATHS = "paths"
PACKAGES = "packages"
NONE = "none"

#: The extension the builtin records belong to.
BASE_EXTENSION = "livery.workshop"


@dataclass(frozen=True)
class Option:
    """One option a package may set on a step, in the step's own table.

    The table is ``[phases.<phase>.<step>]``, the step's address; a
    ``[phases.<phase>]`` table sets an option on every step of the
    phase.

    Attributes:
        name: The option's key, kebab-case.
        kind: ``bool``, ``str``, ``int`` or ``strs``, a list of
            strings; a value of another type refuses naming the option.
        default: What the step reads when the package says nothing.
        doc: One line saying what the option changes.
    """

    name: str
    kind: str
    default: object
    doc: str = ""


#: Every step carries this option: a package that turns a step off
#: is skipped by name in the gate's output.
ENABLED = Option("enabled", "bool", True, "whether the step judges this package")


@dataclass(frozen=True)
class Claim:
    """One category a step judges, and the rules it withholds there.

    Attributes:
        category: The category, ``source``, ``test``, ``configuration``.
        ignore: Rule codes the step does not apply to that category;
            a tool with per-file ignores renders them as those.
        suffixes: The file suffixes the step reads in the category,
            ``(".py", ".pyi")`` for a python tool; empty reads every
            file. A category names what a file is for, not its
            language: a native package's ``source`` is C++ and its
            ``conanfile.py`` is configuration, and the suffix keeps a
            python tool's claim to the files it reads.
    """

    category: str
    ignore: tuple[str, ...] = ()
    suffixes: tuple[str, ...] = ()


@dataclass(frozen=True)
class StepRecord:
    """One step, completely.

    A step's name is its phase and its name in the phase,
    ``test.pytest``: the verb ``fm test.pytest``, the
    ``[phases.test.pytest]`` table of its options, the gate's lines,
    and every lookup use it. An extension declares the step in its
    ``[phases.<phase>.<step>]`` table.

    Attributes:
        step: The step's name in its phase, as its sub-task names it
            under the phase's verb: ``pytest`` in ``fm test.pytest``.
        phase: The phase the step belongs to: ``format``, ``lint``,
            ``typecheck``, ``typecomplete``, ``test``, ``build``,
            ``drift``, ``provenance`` or ``layering``, a declared
            phase a verb is generated from.
        run: The judging callable, called with the run's
            [livery.workshop.PhaseContext][]: the engine running the
            step's words or a reference to its code; a refusal is its
            verdict.
        scope: ``workspace`` for a step that judges the whole in one
            run, ``package`` for one that judges one package at a
            time.
        narrowing: How a workspace step narrows under a scoped gate:
            ``paths`` (the subset's directories), ``packages`` (the
            subset's members) or ``none`` (the whole is always
            judged). A package step narrows by its packages. No
            declaration names it: the reader derives it, ``paths``
            for a template with ``{paths}``, ``packages`` for a
            workspace step in code with no ``inputs``, ``none``
            otherwise ([livery.workshop._declaration.read][]).
        fix: The rewriting callable when the tool can rewrite, run
            serially before any judge under ``--fix``; None for a
            step that only judges.
        extensions: The extensions whose packages the step judges: a
            package whose set holds one of them
            ([livery.workshop.extension_set][]). A package step
            runs per such member; a workspace step names them so its
            tools and claims reach those packages, or leaves them empty.
        tests_only: Whether a package step still runs when the
            package's change is confined to its tests. A build and
            the tests do; a formatter does not.
        inputs: The files a workspace step reads, so a run judges it
            only when one of them, or what widens it, changed, and then
            only the changed ones when it judges per file
            ([livery.workshop._influence.Inputs][]). None for a step
            that narrows by its packages alone.
        after: The steps this one runs after, by name: a build
            after its configure, a ctest after its build. The walk
            starts this one once each of them ran for the same
            package, and the gate's walk brings them in
            ([livery.workshop._steps.with_earlier][]).
        extension: The extension that registered the step, named when a
            narrowing is printed.
        tools: The tools the step runs, as the tool profile names
            them, ``("clang_format",)``; each reaches the profile while a
            package the step judges exists, naming ``step <name>`` as its
            site, and leaves it when the step is unregistered.
        options: The options a package may set under
            ``[phases.<phase>.<step>]``; ``enabled`` is every step's.
        fragments: The configuration the render manages for the
            step, one per rendered file it has something to say in;
            composed in step-name order, judged by the drift gate,
            gone from the next render with the record.
        claims: The categories the step judges, each with the rules
            it withholds there and the suffixes it reads; a step
            judges every file its claims reach and no other, and a
            tool with per-file ignores renders its ignores from them.
        flags: The command-line flags the step reads, from `FLAGS`:
            ``point`` for a step that selects tests by CI point,
            ``shard`` for a step that runs its share of a sharded
            check leg, and so runs on every shard; a step without it
            runs on the first shard alone
            ([livery.workshop._steps.on_this_shard][]). A generated
            verb offers the flags its steps declare, and ``--fix``
            with ``--safe-fix`` where a step has a fix mode.
        arguments: Whether the step hands the tool it wraps the words
            after ``--`` on its own verb (``fm test.pytest -- -k name``),
            which it reads from [livery.workshop.PhaseContext][]'s
            ``arguments``. A step that wraps no tool leaves it off, and
            its verb refuses them. No declaration names it: the reader
            derives it, on for a template with ``{arguments}`` and for
            a step in code that names ``tools``.
        words: The step's words when it is declared in words, which
            ``fm explain`` prints and the conformance kit judges; None
            for a step whose ``main`` is code.
        provides: The context keys the step writes inside its phase,
            each to its type name; a step of one phase reads what
            another provides.
        reads: The context keys the step reads inside its phase.
        isolated: Whether the step serves the phase's isolated side
            (True), from a copy of the sources in a fresh environment,
            or its tree side (False); None serves both. The walk runs
            the step on the side the engine gives the phase
            ([livery.workshop._phases.serves][]), and the gate gives
            the tree side.
        enabled: Whether the step registers while its extension is
            listed. An extension declares False for a step a workspace
            turns on at the step's address in the root contract,
            ``[phases.<phase>.<step>] enabled = true``.
        replaces: The steps of the base or of an extension this one
            requires that it replaces, each ``<owner>:<phase>.<step>``
            with the reason; naming the step registered at this one's
            address is what lets a second extension take the address.
        deletes: The steps of the base or of an extension this one
            requires that it deletes, each ``<owner>:<phase>.<step>``
            with the reason.
        outputs: The paths the step writes, each relative to the root
            or ``package/<path>`` for the path in each package. When
            [livery.workshop.run_phase][] runs the step's phase, an
            output no running step declares is removed first, so a step
            that stops running leaves nothing behind wherever it wrote.

    """

    step: str
    phase: str
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
    outputs: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        """The step's name: its phase and its name in the phase, ``test.pytest``."""
        return f"{self.phase}.{self.step}"


_STEPS: dict[str, StepRecord] = {}


#: The command-line flags a step may read, beyond the fix modes:
#: ``point``, the CI point whose tests the step selects, and ``shard``,
#: the shard of a sharded check leg the step runs its share of.
FLAGS = ("point", "shard")


def register_step(record: StepRecord) -> None:
    """Register *record*; a name already registered is replaced.

    The mount registers each step a listed extension declares.
    Replacing is how an extension swaps the step at an address, and
    how a test injects a fake; a package step names at least one
    extension, since a step that judges packages and applies to none
    never runs. The step's address, ``<phase>.<step>``, is a task's
    address, so a dot inside the phase or the step's name refuses, and
    a step named ``default`` refuses (that address is the phase verb's
    own).
    """
    from livery.workshop import _phases

    for part in (record.phase, record.step):
        if not part or "." in part:
            fail(
                f"step {record.name!r}: a phase and a step's name are one word"
                f" each, not {part!r}; the step answers to <phase>.<step>"
            )
    declared = _phases.declared_phases()
    if record.phase not in declared:
        fail(
            f"step {record.name!r}: {record.phase} is not a declared phase; the"
            f" phases are {', '.join(declared)}"
        )
    if record.step == "default":
        fail(
            f"step {record.name!r}: {record.name} is the address of the"
            f" {record.phase} verb itself; name the step by its tool"
        )
    held = _STEPS.get(record.name)
    if held is not None and held.extension != record.extension:
        # A second extension takes an address by saying so: the step it
        # replaces or deletes, named with its owner, and why.
        named = {address for address, _reason in (*record.replaces, *record.deletes)}
        if f"{held.extension}:{held.name}" not in named:
            fail(
                f"step {record.name} is declared by both {held.extension} and"
                f" {record.extension}; two extensions declare one step unless"
                f" {record.extension} names {held.extension}:{held.name} in"
                " replaces or deletes, with a reason"
            )
    if record.scope not in (WORKSPACE, PACKAGE):
        fail(
            f"step {record.name!r}: scope is {WORKSPACE!r} or {PACKAGE!r},"
            f" not {record.scope!r}"
        )
    unknown = [flag for flag in record.flags if flag not in FLAGS]
    if unknown:
        fail(
            f"step {record.name!r} reads {', '.join(unknown)}, which no verb"
            f" offers; the flags are {', '.join(FLAGS)}"
        )
    if record.narrowing not in (PATHS, PACKAGES, NONE):
        fail(
            f"step {record.name!r}: narrowing is {PATHS!r}, {PACKAGES!r} or"
            f" {NONE!r}, not {record.narrowing!r}"
        )
    if record.scope == PACKAGE and not record.extensions:
        fail(
            f"step {record.name!r} judges packages and names no extension: a"
            " package step judges the packages whose set holds one it names"
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
                f"step {record.name!r} claims {claim.category!r}, which no category"
                f" table knows; the categories are {', '.join(sorted(known))}"
            )
    _STEPS[record.name] = record


def _typed(option: Option, value: object) -> bool:
    if option.kind == "strs":
        return isinstance(value, list) and all(
            isinstance(item, str) for item in cast("list[object]", value)
        )
    expected = {"bool": bool, "str": str, "int": int}[option.kind]
    if option.kind == "int" and isinstance(value, bool):
        return False
    return isinstance(value, expected)


def option_addresses(record: StepRecord) -> tuple[str, ...]:
    """The tables that reach *record*'s options, shallowest first.

    ``phases.<phase>``, the phase's table, which reaches every step of
    the phase, then ``phases.<phase>.<step>``, the step's own: the
    later table wins key by key, since it names fewer steps.
    """
    return (f"phases.{record.phase}", f"phases.{record.phase}.{record.step}")


def option_value(record: StepRecord, package: Package, name: str) -> object:
    """What *package* sets for *record*'s option *name*, or the option's default.

    The deepest table that sets it wins
    ([livery.workshop._steps.option_addresses][]). Refuses an option
    the record does not declare and a value of the wrong type, naming
    the record's options.
    """
    declared = {option.name: option for option in (*record.options, ENABLED)}
    if name not in declared:
        fail(
            f"step {record.name!r} declares no option {name!r}; its options are"
            f" {', '.join(declared)}"
        )
    option = declared[name]
    tables = dict(package.step_options)
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


def _reached(address: str) -> tuple[StepRecord, ...]:
    """The registered steps a table *address* reaches.

    ``phases.<phase>`` reaches every step of the phase, and
    ``phases.<phase>.<step>`` the one step.
    """
    _kind, _, rest = address.partition(".")
    phase, _, step = rest.partition(".")
    return tuple(
        r for r in _STEPS.values() if r.phase == phase and (not step or r.step == step)
    )


def option_problems(packages: tuple[Package, ...]) -> list[str]:
    """Every option table or key no registered step takes, one line each.

    Read by ``layering.graph``, so a package that addresses a phase or
    a step that does not exist, or sets an option none of the steps it
    reaches declares, is refused with the vocabulary named.
    """
    problems: list[str] = []
    known = phases()
    for package in packages:
        where = f"{package.path}/workshop.toml"
        for address, options in package.step_options:
            reached = _reached(address)
            if not reached:
                _kind, _, rest = address.partition(".")
                if "." in rest:
                    problems.append(
                        f"{where}: [{address}] reaches no registered step; the"
                        f" steps are {', '.join(sorted(_STEPS))}"
                    )
                else:
                    problems.append(
                        f"{where}: [{address}] names no phase a registered step"
                        f" has; the phases are {', '.join(known)}"
                    )
                continue
            declared: dict[str, Option] = {ENABLED.name: ENABLED}
            for record in reached:
                declared.update({option.name: option for option in record.options})
            for key, value in options:
                option = declared.get(key)
                if option is None:
                    problems.append(
                        f"{where}: [{address}] sets {key!r}, which no step it"
                        f" reaches declares; their options are"
                        f" {', '.join(sorted(declared))}"
                    )
                elif not _typed(option, value):
                    problems.append(
                        f"{where}: [{address}] {key} is {option.kind}, not {value!r}"
                    )
    return problems


def enabled(name: str, packages: tuple[Package, ...]) -> tuple[Package, ...]:
    """*packages* minus those that turned the step *name* off, each skip printed."""
    record = step_for(name)
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
    """The tools the steps judging packages that hold *extensions* run.

    Each as a ``(tool, step)`` pair. A step judges such a package
    when it names one of *extensions*; a workspace step names the
    extensions its bodies judge the same way.
    """
    held = set(extensions)
    found: list[tuple[str, str]] = []
    for record in _STEPS.values():
        if held & set(record.extensions):
            found.extend((tool, record.name) for tool in record.tools)
    return tuple(found)


def unregister_step(name: str) -> None:
    """Drop the step *name*; unknown names refuse naming the registry."""
    if name not in _STEPS:
        fail(f"{name!r} is not a registered step; steps: {', '.join(_STEPS)}")
    del _STEPS[name]


def narrowings() -> tuple[str, ...]:
    """The gate's lines naming each step an extension registered.

    A step the base did not register names its extension. Empty for the
    base alone.
    """
    return tuple(
        f"  {record.name}: registered by {record.extension}"
        for record in _STEPS.values()
        if record.extension != BASE_EXTENSION
    )


def step_names() -> tuple[str, ...]:
    """Every registered step, in registration order."""
    return tuple(_STEPS)


def snapshot(*, initial: bool = False) -> dict[str, StepRecord]:
    """The registry's state, for [livery.workshop._steps.restore][] to put back.

    With *initial*, the state importing this module leaves instead: the
    builtin steps alone, as a process starts with them.
    """
    return dict(_BUILTIN_STATE if initial else _STEPS)


def restore(state: dict[str, StepRecord]) -> None:
    """Put the registry back to *state*."""
    _STEPS.clear()
    _STEPS.update(state)


def _reaches(claim: Claim, relative: str) -> bool:
    """Whether the claim admits the file's suffix; no suffixes admits every file."""
    return not claim.suffixes or PurePosixPath(relative).suffix in claim.suffixes


def _admits(claim: Claim, pattern: str) -> bool:
    """Whether a category pattern can name a file the claim reads.

    A pattern ending in a literal suffix outside the claim's, cpp's
    ``tests/**/*.cpp`` against a python claim, renders no ignore; a
    directory pattern renders as it is, since the tool reads only its
    own files under it.
    """
    return not PurePosixPath(pattern).suffix or _reaches(claim, pattern)


def _package_files(package: Package) -> tuple[str, ...]:
    """The files git holds under the package: tracked, or untracked and not ignored.

    Sorted, relative to the package. A build tree or a cache under the
    package is ignored, so no claim reaches it; a tracked file deleted
    from the tree is left out.
    """
    names = git.ls_files(package.directory, others=True)
    return tuple(name for name in names if (package.directory / name).is_file())


def judged_files(record: StepRecord, package: Package) -> tuple[str, ...]:
    """The files of *package* the step's claims reach, relative to it, sorted.

    Every file git holds under the package directory is categorised
    and kept when a claim names its category and admits its suffix; a
    record without claims judges nothing by this measure, which is
    what a step that runs a tool over directories looks like from
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
    """The steps whose claims reach *path* in *package*, by name, sorted."""
    from livery.workshop._categories import category_of

    category = category_of(package, path).name
    names = []
    for record in _STEPS.values():
        if not any(c.category == category and _reaches(c, path) for c in record.claims):
            continue
        if record.extensions and not _applies(record, package):
            continue
        names.append(record.name)
    return tuple(sorted(names))


def per_file_ignores(
    held: Iterable[Iterable[str]],
) -> list[tuple[str, tuple[str, ...]]]:
    """The per-file ignores the claims render, pattern to rule codes, sorted.

    *held* is each set of extensions a present package holds. For
    each set, every claim of a step that judges a package
    holding it and withholds rules on a category maps to that
    category's patterns in the tables that answer for such a package,
    each under ``packages/**/``, which reaches a member in a group
    directory too. For the workspace's own unit, every step's claims map
    to its table's patterns as they are. Two steps claiming one category
    under different rules land in one entry with both sets.
    """
    from livery.workshop._categories import category_rules, workspace_rules

    table: dict[str, set[str]] = {}
    units: list[tuple[frozenset[str] | None, tuple[CategoryRule, ...], str]] = []
    for listed in held:
        names = tuple(listed)
        units.append((frozenset(names), category_rules(names), "packages/**/"))
    units.append((None, workspace_rules(), ""))
    for holding, rules, prefix in units:
        for record in _STEPS.values():
            if holding is not None and not judges_extensions(record, holding):
                continue
            for claim in record.claims:
                if not claim.ignore:
                    continue
                for rule in rules:
                    if rule.category != claim.category or rule.pattern == "**":
                        continue
                    if not _admits(claim, rule.pattern):
                        continue
                    table.setdefault(prefix + rule.pattern, set()).update(claim.ignore)
    return [(pattern, tuple(sorted(codes))) for pattern, codes in sorted(table.items())]


def steps_by_name() -> dict[str, StepRecord]:
    """Every registered record by name, in registration order."""
    return dict(_STEPS)


def answering(address: str) -> str | None:
    """The step answering to *address*, its name; None when none is registered."""
    return address if address in _STEPS else None


def step_for(name: str) -> StepRecord:
    """The step named *name*, ``test.pytest``.

    Refusal names the registry.
    """
    if name not in _STEPS:
        fail(f"{name!r} is not a registered step; steps: {', '.join(_STEPS)}")
    return _STEPS[name]


def phases() -> tuple[str, ...]:
    """The phases the registered steps belong to, sorted."""
    return tuple(sorted({r.phase for r in _STEPS.values()}))


def judges_extensions(record: StepRecord, extensions: Iterable[str]) -> bool:
    """Whether *record* judges a package holding *extensions*.

    A step that names no extensions judges every package; one that
    names some judges a package holding one of them.
    """
    return not record.extensions or bool(set(record.extensions) & set(extensions))


def judges_unit(record: StepRecord, unit: Package) -> bool:
    """Whether *record* judges *unit*, by the extensions the unit holds.

    A member holds its set, and the workspace's own tests hold the
    root's set (`extension_set`). A step that names no extension judges
    every unit; one that names some judges a unit holding one of them.
    """
    from livery.workshop._composition import extension_set

    return judges_extensions(record, extension_set(unit))


def judged_by(
    record: StepRecord, packages: tuple[Package, ...], *, quiet: bool = False
) -> tuple[Package, ...]:
    """The *packages* *record* judges, by the extensions they hold; each other skips.

    A package holding none of the step's extensions prints a skip
    naming the step, the package and the extensions, so a narrowed gate
    is visible in the output and never passes silently. *quiet* counts
    without printing.
    """
    kept: list[Package] = []
    for package in packages:
        if judges_unit(record, package):
            kept.append(package)
        elif not quiet:
            wanted = " or ".join(record.extensions)
            print(f"  {record.name}: {package.path} skips (not a {wanted} package)")
    return tuple(kept)


def _applies(record: StepRecord, package: Package) -> bool:
    """Whether a package step judges *package*, by the extensions it holds."""
    from livery.workshop._composition import extension_set

    return bool(set(record.extensions) & set(extension_set(package)))


def judges_package(record: StepRecord, ctx: PhaseContext, package: Package) -> bool:
    """Whether the package step *record* runs over *package* in this run.

    The package holds an extension the step names; a change confined
    to the package's tests leaves the step out unless it is tests-only
    ([livery.workshop._steps.StepRecord][]).
    """
    tests_only = ctx.scoped and package.path in ctx.tests
    return (
        record.scope == PACKAGE
        and _applies(record, package)
        and (record.tests_only or not tests_only)
    )


def members(ctx: PhaseContext) -> tuple[Package, ...]:
    """The packages under judgment that are members: every one but the tests unit."""
    from livery.workshop._packages import WORKSPACE_TESTS

    return tuple(package for package in ctx.judged if package.path != WORKSPACE_TESTS)


def _makes_verb(phase: str) -> bool:
    """Whether *phase* gets a verb: it declares one."""
    from livery.workshop import _phases

    spec = _phases.declared_phases().get(phase)
    return spec is None or spec.verb


def verb_tree() -> dict[str, dict[str, StepRecord]]:
    """Each phase's steps by name, sorted: the verbs the registry generates.

    A phase that declares no verb is left out.
    """
    tree: dict[str, dict[str, StepRecord]] = {}
    for record in _STEPS.values():
        if _makes_verb(record.phase):
            tree.setdefault(record.phase, {})[record.step] = record
    return {phase: dict(sorted(steps.items())) for phase, steps in sorted(tree.items())}


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
    shard: str = "",
) -> None:
    from livery.workshop import _quality

    paths, arguments = split_arguments(words)
    if arguments and refusal:
        fail(refusal)
    _quality.run_steps(
        names,
        paths,
        fix=fix,
        safe_fix=safe_fix,
        point=point,
        shard=shard,
        arguments=arguments,
    )


#: A generated verb's paths and flags, each with its help text. They live at
#: module level because footman reads a task's annotations in its
#: module's namespace: a name local to the function making the verb
#: does not resolve there, and the flag's value would arrive as text.
_Paths = Annotated[
    str, doc("the files or directories to check; the workspace when none")
]
_Fix = Annotated[bool, doc("rewrite what the steps can, then judge the rest")]
_SafeFix = Annotated[bool, doc("fix, removing no code: safe for an edit in flight")]
_Point = Annotated[
    str, doc("select this CI point's tests: gate, merge, nightly, release")
]
_Shard = Annotated[
    str, doc("run this shard's share of a sharded check leg: <i>of<n>, such as 2of4")
]


def _verb(
    select: Callable[[], tuple[str, ...]],
    *,
    fixes: bool,
    flags: frozenset[str],
    what: str,
    refusal: str = "",
) -> Callable[..., None]:
    """A verb over the steps *select* names, offering exactly the flags they read.

    ``--fix`` and ``--safe-fix`` where a step can fix, ``--point`` and
    ``--shard`` where one reads them (*flags*, from `FLAGS`); the
    paths narrow the run to those files. The words after ``--`` reach
    the step's tool, unless *refusal* says why this verb takes none.
    One body per set of flags, because footman reads a verb's flags
    from its signature, and a signature is written, not computed.
    """

    def run(
        paths: tuple[str, ...],
        *,
        fix: bool = False,
        safe_fix: bool = False,
        point: str = "",
        shard: str = "",
    ) -> None:
        _run_named(
            select(),
            paths,
            refusal=refusal,
            fix=fix,
            safe_fix=safe_fix,
            point=point,
            shard=shard,
        )

    def bare(*paths: _Paths) -> None:
        run(paths)

    def with_point(*paths: _Paths, point: _Point = "") -> None:
        run(paths, point=point)

    def with_shard(*paths: _Paths, shard: _Shard = "") -> None:
        run(paths, shard=shard)

    def with_point_and_shard(
        *paths: _Paths, point: _Point = "", shard: _Shard = ""
    ) -> None:
        run(paths, point=point, shard=shard)

    def with_fix(*paths: _Paths, fix: _Fix = False, safe_fix: _SafeFix = False) -> None:
        run(paths, fix=fix, safe_fix=safe_fix)

    def with_fix_and_point(
        *paths: _Paths,
        fix: _Fix = False,
        safe_fix: _SafeFix = False,
        point: _Point = "",
    ) -> None:
        run(paths, fix=fix, safe_fix=safe_fix, point=point)

    def with_fix_and_shard(
        *paths: _Paths,
        fix: _Fix = False,
        safe_fix: _SafeFix = False,
        shard: _Shard = "",
    ) -> None:
        run(paths, fix=fix, safe_fix=safe_fix, shard=shard)

    def with_fix_point_and_shard(
        *paths: _Paths,
        fix: _Fix = False,
        safe_fix: _SafeFix = False,
        point: _Point = "",
        shard: _Shard = "",
    ) -> None:
        run(paths, fix=fix, safe_fix=safe_fix, point=point, shard=shard)

    bodies: dict[tuple[bool, bool, bool], Callable[..., None]] = {
        (False, False, False): bare,
        (False, True, False): with_point,
        (False, False, True): with_shard,
        (False, True, True): with_point_and_shard,
        (True, False, False): with_fix,
        (True, True, False): with_fix_and_point,
        (True, False, True): with_fix_and_shard,
        (True, True, True): with_fix_point_and_shard,
    }
    chosen = bodies[(fixes, "point" in flags, "shard" in flags)]
    chosen.__doc__ = what
    _MADE.add(chosen)
    return chosen


def _steps_of(phase: str) -> Callable[[], tuple[str, ...]]:
    """Every registered step of *phase*, read when the verb runs."""

    def select() -> tuple[str, ...]:
        return tuple(record.name for record in _STEPS.values() if record.phase == phase)

    return select


def _one_step(name: str) -> Callable[[], tuple[str, ...]]:
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
    step registered later that fixes or reads the point reaches its
    phase's verb; any other task at the address is left alone.
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


def _prune(target: Group, tree: Mapping[str, Mapping[str, StepRecord]]) -> None:
    """Remove the verbs the generator made whose steps are gone.

    A phase with no step left loses its verb, and its group when that
    leaves the group empty; a task another extension put there stays.
    """
    for phase, parent in list(target.groups.items()):
        steps = tree.get(phase, {})
        removed = False
        for key, task in list(parent.tasks.items()):
            kept = bool(steps) if key == "default" else key in steps
            if _is_made(task) and not kept:
                del parent.tasks[key]
                removed = True
        if removed and not parent.tasks and not parent.groups:
            del target.groups[phase]


def generate_verbs(into: Group | None = None) -> None:
    """Make a verb per phase and a sub-task per step, from the registry.

    ``fm test`` runs every step of the test phase and ``fm test.pytest``
    the one, each offering exactly the flags its steps read. A phase
    or a sub-task whose address a verb already holds is served by that
    verb, ``fm provenance``, and nothing is
    made there. Run again after extensions registered or withdrew steps,
    it makes what is new, remakes a verb whose flags changed, and
    removes a verb whose step is gone.
    """
    target = into if into is not None else root_group
    tree = verb_tree()
    _prune(target, tree)
    for phase, steps in tree.items():
        if phase in target.tasks:
            continue
        parent = target.groups.get(phase)
        default = None if parent is None else parent.default_task
        # The generator owns the phase's verb in a group it made; a
        # group of another verb's gets no default.
        owned = parent is None or (default is not None and _is_made(default))
        if parent is None:
            parent = target.group(phase, help=f"The {phase} steps, one task each")
        if owned:
            _place(
                parent,
                "default",
                _verb(
                    _steps_of(phase),
                    fixes=any(r.fix is not None for r in steps.values()),
                    flags=frozenset(f for r in steps.values() for f in r.flags),
                    what=f"Run every {phase} step, over the named paths or the"
                    " workspace.",
                    refusal=arguments_refusal(f"{prog()} {phase}", steps.values()),
                ),
            )
        for step, record in steps.items():
            if step in parent.groups:
                continue
            _place(
                parent,
                step,
                _verb(
                    _one_step(record.name),
                    fixes=record.fix is not None,
                    flags=frozenset(record.flags),
                    what=f"Run the {record.name} step alone, over the named paths"
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


def arguments_refusal(verb: str, records: Iterable[StepRecord]) -> str:
    """Why *verb*, which runs several steps, takes no arguments after ``--``.

    The words after ``--`` are one tool's, so they go to a step's own
    verb; the refusal names those of *records* that take them.
    """
    takers = [f"{prog()} {r.phase}.{r.step}" for r in records if r.arguments]
    named = f": {', '.join(takers)}" if takers else ""
    return (
        f"`{verb}` runs several steps, and the words after -- are one tool's;"
        f" give them to the step's own verb{named}"
    )


def run_step(name: str, ctx: PhaseContext, *, fix: bool = False) -> None:
    """Run the step *name* over *ctx* through the walk: its judge, or its fix mode.

    One step alone ([livery.workshop._phases.walk_steps][]): a workspace
    step runs once, a package step once per package it judges, and
    a step without a fix mode runs nothing in fix mode. The steps
    named in ``after`` are not run first: a caller running several
    steps by hand orders them itself, and the gate's walk brings them
    in ([livery.workshop._steps.with_earlier][]).
    """
    from livery.workshop._phases import walk_steps

    walk_steps(ctx, (step_for(name),), fixing=fix)


def with_earlier(names: tuple[str, ...]) -> tuple[StepRecord, ...]:
    """The records of *names*, and of every step one of them runs after.

    Followed through ``after`` transitively, in registration order,
    so a build's configure joins the walk that judges the build. An
    ``after`` naming a step an installed extension declares and the
    mount did not register orders nothing: clang-tidy's lint runs after
    cmake's configure in a workspace whose packages hold cmake, and
    alone in one whose packages do not.

    Raises:
        Failed: when an ``after`` names a step that neither the base nor
            an installed extension declares, naming the registry.
    """
    wanted = set(names)
    queue = list(names)
    while queue:
        for earlier in step_for(queue.pop()).after:
            if earlier in wanted:
                continue
            if earlier not in _STEPS and installed_step(earlier):
                continue
            # A name nothing declares joins the queue, and its lookup
            # refuses naming the registry.
            wanted.add(earlier)
            queue.append(earlier)
    return tuple(record for record in _STEPS.values() if record.name in wanted)


def installed_step(name: str) -> bool:
    """Whether an installed extension or the base declares the step *name*.

    *name* is ``<phase>.<step>``. Read from the declarations, so a step
    the mount did not register, one of an extension the workspace does
    not mount, is declared all the same.
    """
    from livery.workshop._declaration import installed_steps

    return any(address.partition(":")[2] == name for address in installed_steps())


def _applies_now(record: StepRecord, ctx: PhaseContext) -> bool:
    if record.scope == WORKSPACE:
        if record.inputs is not None:
            # Listed, so the walk says when no file it reads changed.
            return True
        # A step without declared inputs narrows by its packages, so a
        # scope that holds none leaves it nothing to judge. Running it
        # would read the empty scope as the whole: the test step would
        # start the whole suite. Named files are a scope of their own,
        # which the step's claims judge.
        return bool(ctx.files) or not (ctx.scoped and not ctx.subset)
    return any(judges_package(record, ctx, p) for p in members(ctx))


def workspace_selected(ctx: PhaseContext) -> tuple[str, ...]:
    """The workspace steps with declared inputs that this run's changes select."""
    return tuple(
        record.name
        for record in _STEPS.values()
        if record.scope == WORKSPACE
        and record.inputs is not None
        and selected(record, ctx).runs
    )


def selected_files(ctx: PhaseContext) -> frozenset[str] | None:
    """The files the running step judges in this run; None for every file it reads.

    A workspace step that declares ``inputs`` judges, in a run that
    knows what changed, the changed files among its inputs, and what
    its ``widen`` reference adds; in a run that knows nothing of the
    changes, it judges every file it reads, and this is None. The
    context names the running step, so a step asks with the context
    its run was handed.

    Raises:
        Failed: when *ctx* names no step: one built by hand, outside
            a step's run.
    """
    if not ctx.step:
        fail(
            "selected_files() answers for the running step, and this"
            " PhaseContext names none: call it with the context a step's"
            " run is handed, which sets PhaseContext.step"
        )
    return _selected_files(step_for(ctx.step), ctx)


def _selected_files(record: StepRecord, ctx: PhaseContext) -> frozenset[str] | None:
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


def graph_files(root: Path) -> tuple[str, ...]:
    """What the graph is made of: the contracts, the native manifests, a root src/.

    The layering steps over the sources widen on them: a change there
    moves what the rules read, so every source is judged again.
    """
    return ("workshop.toml", "src/**", *package_files(root))


def graph_changed(changes: Changes) -> bool:
    """Tell whether a contract, a native manifest or a root ``src/`` changed.

    A step over a language's sources widens on it through its
    ``widen`` reference, a function of its own extension that calls
    this one: the members, their module roots and what they declare
    come from these files, so a change to one judges every source
    again, as the base's own layering steps do. Prose and the licence
    beside a manifest never count.

    Args:
        changes: What the run changed, as a step's ``widen`` reference
            receives it.

    Returns:
        True when a changed path is the root contract, a file directly
        in a member's directory, or a file under ``src/`` at the root.
    """
    from livery.workshop._categories import matches

    patterns = graph_files(changes.root)
    return any(
        matches(pattern, path)
        for path in changes.paths
        if not any(matches(ignored, path) for ignored in NOT_INPUTS)
        for pattern in patterns
    )


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


def selected(record: StepRecord, ctx: PhaseContext) -> Selection:
    """What *record* judges in this run, by its declared inputs.

    Everything for a step that declares none. The run's changes
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
    package's files are relative to it and categorised by the tables of
    the extensions it holds; a file under no package belongs to
    [livery.workshop._steps.ROOT_UNIT], relative to the root and
    categorised by the workspace's table. None when the root is not a
    git checkout, and then every step that applies runs.
    """
    from livery.workshop._categories import category_of
    from livery.workshop._packages import WORKSPACE_TESTS
    from livery.workshop._provenance import unit_of

    if ctx.files:
        names: set[str] = set(ctx.files)
    else:
        try:
            names = set(git.ls_files(ctx.root, others=True))
        except git.GitError:
            return None
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


def _scope_units(record: StepRecord, ctx: PhaseContext) -> tuple[str, ...]:
    """The units *record* reads in this run.

    A package step reads the packages it judges. A workspace step
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
    record: StepRecord, ctx: PhaseContext, units: tuple[str, ...] | None = None
) -> bool:
    """Whether *record*'s claims reach a file of *units*, or of its scope in this run.

    A step without claims reads what its own body decides, and a run
    without a catalogue has no listing to judge by: both answer yes,
    except over named files, which a step without claims cannot say
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
    record: StepRecord, ctx: PhaseContext, unit: str | None = None
) -> tuple[str, ...]:
    """The files of the run's catalogue *record*'s claims reach, as absolute paths.

    For a run over named files: the ones a step takes. *unit* keeps
    one package's alone, for a package step.
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
    """*names* less the steps with no file to read in this run, each skip said.

    No process starts for a step whose claims reach nothing in scope.
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
        record = _STEPS[name]
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


def on_this_shard(names: tuple[str, ...], ctx: PhaseContext) -> tuple[str, ...]:
    """*names* less the steps this shard leaves to the first shard, each said.

    A step that declares ``shard`` among its flags runs on every shard
    of a sharded check leg and runs its share, the shard in its
    context's ``shard``. A step that does not runs whole, so it runs
    on the first shard alone: on any other shard it is left out, and a
    line says so. On the first shard, and on a run that is one shard
    of one, every step stays and nothing is said.
    """
    if ctx.shard.first:
        return names
    kept: list[str] = []
    for name in names:
        if "shard" in _STEPS[name].flags:
            kept.append(name)
            continue
        print(f"  {name}: left to the first shard (this leg is {ctx.shard})")
    return tuple(kept)


def _gated(record: StepRecord) -> bool:
    """Whether the gate runs *record*: its phase declares itself gated."""
    from livery.workshop import _phases

    spec = _phases.declared_phases().get(record.phase)
    return spec is None or spec.gated


def rewriters(ctx: PhaseContext) -> tuple[str, ...]:
    """The steps whose fix mode runs, serially and in order, under --fix.

    Those of the gated phases; empty unless the gate runs in its fix
    mode. They run in registration order, the base's first and then
    each extension's in composition order, with each step after the
    ones it names in ``after`` and the providers of the keys it reads.

    Raises:
        OrderCycle: when the rewriters' order has no start, naming the
            members of one cycle.
    """
    if not ctx.fix:
        return ()
    from livery.workshop._phases import phase_order

    records = [
        record
        for record in _STEPS.values()
        if record.fix is not None and _gated(record) and _applies_now(record, ctx)
    ]
    return tuple(record.name for record in phase_order(records))


def judges(ctx: PhaseContext) -> tuple[str, ...]:
    """The steps the gate judges together, in registration order.

    Those of the gated phases. A step that rewrote in this run is not
    judged again: re-judging the style it just wrote would only spend
    time agreeing.
    """
    names = []
    for record in _STEPS.values():
        if not _gated(record) or not _applies_now(record, ctx):
            continue
        if record.fix is not None and ctx.fix:
            continue
        names.append(record.name)
    return tuple(names)


#: The whole repository, as a step that walks it from the root reads it.
#: [livery.workshop._steps.scoped_paths][] answers it when a step
#: judges its configured whole; a tool that reads ``.`` as more than
#: its configuration names takes no paths then.
WHOLE = (".",)


def _workspace_unit(ctx: PhaseContext) -> tuple[Package, ...]:
    """The workspace's own tests, when this run judges them as a unit."""
    from livery.workshop._packages import WORKSPACE_TESTS

    return tuple(p for p in ctx.judged if p.path == WORKSPACE_TESTS)


def _claimed(name: str, natives: tuple[Package, ...]) -> tuple[str, ...]:
    """The files of the native members the step *name* claims, by path.

    A native member's directories are not a python step's to walk, so
    its files come by name: its ``conanfile.py``, say.
    """
    record = step_for(name)
    return tuple(f"{p.path}/{f}" for p in natives for f in judged_files(record, p))


def _every_unit_reached(ctx: PhaseContext, record: StepRecord, chosen: int) -> bool:
    """Whether a scoped run over *chosen* units reaches every unit *record* judges.

    Every member the step judges, the workspace's tests unit among
    them while the step judges it, with none turned off anywhere: the
    whole would reach the same, so the step runs over its configured
    whole instead of a list naming every unit.
    """
    from livery.workshop._packages import WORKSPACE_TESTS

    everyone = tuple(p for p in ctx.packages if p.path != WORKSPACE_TESTS)
    gated_all = judged_by(record, everyone, quiet=True)
    # The workspace's tests unit rides in the scope, not always among
    # the packages: counted once from either.
    suites = tuple(p for p in (*ctx.packages, *ctx.judged) if p.path == WORKSPACE_TESTS)
    tests_unit = {p.path for p in judged_by(record, suites, quiet=True)}
    units = len(gated_all) + len(tests_unit)
    on = all(option_value(record, p, "enabled") for p in gated_all)
    return on and 0 < units <= chosen


def scoped_paths(ctx: PhaseContext, name: str) -> tuple[str, ...]:
    """The paths the path-narrowing step *name* judges in this run.

    The files the run names, when it names any. Otherwise the whole
    tree, ``.``, when the run reaches every member the step judges;
    else the ``src`` and ``tests`` directories of the reached members
    holding the runtime's language, the workspace's own tests when the
    run and the step judge them, and the files the step's claims give
    it in the other reached members.
    """
    from livery.workshop._packages import holds_runtime_language, package_paths

    record = step_for(name)
    if ctx.files:
        return claimed_files(record, ctx)
    gated_members = judged_by(record, members(ctx))
    chosen_members = enabled(name, gated_members)
    pythons = tuple(p for p in chosen_members if holds_runtime_language(p))
    natives = tuple(p for p in chosen_members if not holds_runtime_language(p))
    unit = judged_by(record, _workspace_unit(ctx), quiet=True)

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


def _holds_claimed(record: StepRecord, directory: Path) -> bool:
    """Whether *directory* holds a file with a suffix *record*'s claims read.

    A step with no claim, or a claim that names no suffix, reads every
    file, so any directory holds one.
    """
    if not record.claims or any(not claim.suffixes for claim in record.claims):
        return True
    suffixes = sorted({suffix for claim in record.claims for suffix in claim.suffixes})
    return any(next(directory.rglob(f"*{suffix}"), None) for suffix in suffixes)


def scoped_files(
    ctx: PhaseContext, name: str, package: Package | None = None
) -> tuple[Path, ...]:
    """The files of *package* the step *name* judges in this run, absolute.

    *package* is the run's own package unless named: a package step
    names none, and a step that narrows by packages names each one
    [livery.workshop.scoped_packages][] answers. The files the run
    names, when it names any; else every file the step's claims reach
    in the package, an untracked new one among them.

    Raises:
        Failed: when neither *package* nor the run names a package.
    """
    record = step_for(name)
    package = package or ctx.package
    if package is None:
        fail(f"{name} judges one package at a time, and this run names none")
    if ctx.files:
        return tuple(Path(path) for path in claimed_files(record, ctx, package.path))
    return tuple(package.directory / path for path in judged_files(record, package))


def scoped_packages(ctx: PhaseContext, name: str) -> tuple[Package, ...]:
    """The members the package-narrowing step *name* judges in this run.

    The members holding an extension it names, in a scoped run those
    of the subset, less each one that turned the step off in its
    contract. Each skip is printed, so a narrowed gate names what it
    left out. A run over named files keeps the members holding a named
    file the step claims. The workspace's own tests are not a member.
    """
    record = step_for(name)
    chosen = enabled(name, judged_by(record, members(ctx)))
    if not ctx.files:
        return chosen
    return tuple(p for p in chosen if claimed_files(record, ctx, p.path))


def scoped_suite(ctx: PhaseContext, name: str) -> Package | None:
    """The workspace's own tests, when the step *name* judges them.

    The unit ([livery.workshop.workspace_suite][]) holds the root's
    set, so the step judges it while that set holds an extension the
    step names. Its tests reach any package, so every run of the step
    runs them then. None when the workspace has no ``tests``
    directory. None as well when the step does not judge the unit, and
    then a line says why.
    """
    from livery.workshop._packages import workspace_suite

    record = step_for(name)
    unit = workspace_suite(ctx.root)
    if unit is None or judges_unit(record, unit):
        return unit
    wanted = " or ".join(record.extensions)
    which = "it" if len(record.extensions) == 1 else "one"
    print(
        f"  {name}: {unit.path} skips (the root's set holds no {wanted};"
        f" list {which} in [workspace] extensions to run them)"
    )
    return None


def declared_step(name: str) -> StepRecord:
    """The step *name*: its registered record, else the installed declaration's.

    A process that mounted nothing holds the base's own steps alone,
    so an extension's step is read from the installed extension that
    declares it, the first by name (`installed_record` in the
    extensions module).

    Raises:
        Failed: when no installed extension declares the step, naming it.
    """
    record = _STEPS.get(name)
    if record is not None:
        return record
    from livery.workshop._extensions import installed_record

    found = installed_record(name)
    if found is None:
        fail(
            f"no installed extension declares the step {name!r}; install the"
            f" extension that declares it (`{prog()} sync` installs the ones the"
            " workspace lists), or name a step an installed extension declares"
        )
    return found


def step_option(name: str, package: Package, option: str) -> object:
    """What *package* sets for the step *name*'s *option*, or the option's default.

    The deepest of the package's tables that sets it wins. A step the
    mount registered answers from its registration. A step it did not
    register, as in a process that mounted nothing, answers from the
    installed extension that declares it.

    Raises:
        Failed: when no installed extension declares the step, naming
            it; when the step declares no *option*, naming its options;
            or when the package sets a value of the wrong type, naming
            the table.
    """
    return option_value(declared_step(name), package, option)


def step_extensions(name: str) -> tuple[str, ...]:
    """The extensions the step *name* names in its ``extensions`` list.

    The step judges a package whose set holds one of them
    ([livery.workshop.extension_set][]), and every package when it
    names none. The step is read as [livery.workshop.step_option][]
    reads it: registered, else installed.

    Raises:
        Failed: when no installed extension declares the step, naming it.
    """
    return declared_step(name).extensions


def step_enabled(root: Path, name: str) -> bool:
    """Whether the step *name* is on in the workspace at *root*.

    A step its extension declares on is always on. A step it declares
    ``enabled = false`` is on while the root contract turns it on, with
    ``enabled = true`` in the step's ``[phases.<phase>.<step>]`` table,
    and registers only then. A package turns a step off for itself
    with the step's ``enabled`` option, which
    [livery.workshop.step_option][] reads.

    Raises:
        Failed: when no installed extension declares the step, naming it.
    """
    if declared_step(name).enabled:
        return True
    from livery.workshop._extensions import enabled_steps

    return name in enabled_steps(root)


def tested(units: tuple[Package, ...]) -> tuple[Package, ...]:
    """Keep the *units* whose suites a registered step of the test phase runs.

    A step runs a unit's suite while the unit's set holds an extension
    the step names, and every unit's when it names none: a member holds
    its own set, and the workspace's own tests
    ([livery.workshop.WORKSPACE_TESTS][]) hold the root's set. A step
    of package scope runs over one member at a time and never over the
    workspace's own tests.

    Returns:
        The units of *units* some test step runs, in *units* order.
    """
    from livery.workshop._packages import WORKSPACE_TESTS

    tests = [record for record in _STEPS.values() if record.phase == "test"]
    return tuple(
        unit
        for unit in units
        if any(
            judges_unit(record, unit)
            and not (unit.path == WORKSPACE_TESTS and record.scope == PACKAGE)
            for record in tests
        )
    )


def _register_builtin() -> None:
    """Register the workshop's own steps, in the order the rewriters run.

    Each judges the whole tree: the render's drift, the provenance of
    the shipped content, the graph and the base's rules over the
    sources. A step that judges one package at a time is an
    extension's, and registers at mount.
    """
    from livery.workshop._influence import Inputs
    from livery.workshop._packages import RULES

    def render_run(ctx: PhaseContext) -> None:
        from livery.workshop import _quality

        _quality.drift_check(files=_selected_files(step_for("drift.check"), ctx))

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
            files=_selected_files(step_for("provenance.check"), ctx)
        )

    def provenance_fix(ctx: PhaseContext) -> None:
        from livery.workshop import _provenance

        _provenance.check_content(
            fix=True, files=_selected_files(step_for("provenance.check"), ctx)
        )

    def graph_run(ctx: PhaseContext) -> None:
        from livery.workshop._packages import verify_graph

        verify_graph(ctx.root)

    def relocked(root: Path, written: list[str]) -> None:
        """Print each line a fix *written*, and lock again when one moved a manifest."""
        from livery.workshop._uv import run_uv

        for line in written:
            print(line)
        if any("pyproject.toml" in line for line in written):
            # Each line names the files written. A requirement written
            # into a uv member's manifest moves the uv workspace's lock,
            # so the fix locks again and leaves the tree consistent, as
            # a person would after editing by hand.
            run_uv("lock", root=root)

    def graph_fix(ctx: PhaseContext) -> None:
        from livery.workshop._extensions import write_extensions
        from livery.workshop._packages import verify_graph, write_test_requirements

        for line in write_extensions(ctx.root):
            print(line)
        relocked(ctx.root, write_test_requirements(ctx.root))
        verify_graph(ctx.root)

    def rule_run(step: str) -> Callable[[PhaseContext], None]:
        """The judge of the base's layering step *step*, over the files it reaches."""

        def run(ctx: PhaseContext) -> None:
            from livery.workshop._packages import rule_problems

            files = _selected_files(step_for(f"layering.{step}"), ctx)
            problems = rule_problems(step, ctx.root, ctx.packages, files)
            if problems:
                fail("the workspace breaks its layering:\n  " + "\n  ".join(problems))

        return run

    def sibling_fix(ctx: PhaseContext) -> None:
        from dataclasses import replace

        from livery.workshop._packages import discover_packages, write_edges

        written = write_edges(ctx.root)
        relocked(ctx.root, written)
        if written:
            # The run read the packages before the fix wrote their
            # contracts, and the judge reads them as the fix left them.
            ctx = replace(ctx, packages=discover_packages(ctx.root))
        # A rewriter is not judged again under --fix, so the fix judges
        # what it leaves.
        rule_run("sibling-references")(ctx)

    for record in (
        StepRecord(
            "check",
            "drift",
            render_run,
            fix=render_fix,
            inputs=Inputs(reads=_drift_reads, widens=_drift_widens, ignores=NOT_INPUTS),
        ),
        StepRecord(
            "check",
            "provenance",
            provenance_run,
            fix=provenance_fix,
            inputs=Inputs(reads=("packages/**/src/**/content/**",)),
        ),
        StepRecord(
            "graph",
            "layering",
            graph_run,
            fix=graph_fix,
            inputs=Inputs(reads=graph_files, per_file=False, ignores=NOT_INPUTS),
        ),
        # The base's rules over the sources, one step each; the sibling
        # step's fix is the one rewrite among them.
        *(
            StepRecord(
                step,
                "layering",
                rule_run(step),
                fix=sibling_fix if step == "sibling-references" else None,
                inputs=Inputs(
                    reads=("tasks.py", "packages/**/*.py"),
                    widens=graph_files,
                    ignores=NOT_INPUTS,
                ),
            )
            for step in RULES
        ),
    ):
        register_step(record)


_register_builtin()

#: The registry as the import leaves it, for `snapshot(initial=True)`.
_BUILTIN_STATE = snapshot()
