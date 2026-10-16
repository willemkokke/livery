"""The declared phases, the context a step receives, and the walk that runs steps.

A phase is declared, by the base in the ``[phases]`` section of its
``contract.toml`` and by an extension under ``[phases.<phase>]`` in its
``extension.toml``, in one grammar ([livery.workshop._phases.PhaseSpec][]):
the keys the engine running it gives its steps, typed; its level,
``package`` for a phase whose steps run once per package and
``workspace`` for one whose steps run once; whether ``fm check`` runs
it; the phases it follows in the train's order; and whether a verb is
generated for it. [livery.workshop._phases.declared_phases][] is every
declared phase by name, the base's and then each installed
extension's; a phase nobody declares refuses naming the declared ones,
and two extensions declaring one phase refuse naming both.

Every phase's steps run through the one walk,
[livery.workshop._phases.walk_steps][]: each step a footman step inside
one ``parallel()`` block, a workspace step once and a package step
once per package in scope, independent steps concurrently, a step
after the steps it names in ``after`` and after the providers of the
keys it ``reads``. The gate runs its phases through it with its own
engine around it ([livery.workshop._quality.walk][]); the release
train and ``fm run`` run a package's lifecycle phases through it
([livery.workshop._lifecycle][]), the steps those of the package's set
([livery.workshop._phases.phase_steps][]); ``fm sync`` runs the sync
phase's registered steps through it over the workspace, and
[livery.workshop.run_phase][] those of any phase the gate never runs,
first removing what a step that stopped running declared it writes.
Each step receives the one context, [livery.workshop.PhaseContext][].

A step table's ``deletes`` names steps of other extensions, each
``<owner>:<phase>.<step>`` with its reason, that never run for a
package whose set holds the deleting extension: the set's steps leave
them out, and the walk skips such a package for a registered step it
deletes, saying why. A package whose set does not hold the deleting
extension runs them.

The engine running a walk gives each phase its keys, by phase. The
train builds on the isolated side in the ``release`` configuration and
tests on the isolated side once per resolution it proves; the gate
builds on the tree side in the ``instrumented`` configuration, and
``fm run`` on the tree side in ``debug``. A phase whose declaration
holds ``isolated`` has two sides: the tree, and the isolated one, from
a copy of the sources in a fresh environment. A step that declares
``isolated`` serves the side it names alone, and one that declares
nothing serves both ([livery.workshop._phases.serves][]).

Two steps providing one key, a key nobody provides, a step declaring a
side of a phase that has one side, and an order with no start refuse
before any step runs ([livery.workshop._phases.phase_problems][]);
``layering.graph`` names them for each package.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from livery.footman import fail, parallel, step

if TYPE_CHECKING:
    from livery.workshop._composition import Lookup
    from livery.workshop._influence import Changes
    from livery.workshop._packages import Package
    from livery.workshop._steps import StepRecord

#: The levels a phase runs at: over one package at a time, or over the
#: workspace once.
PACKAGE_LEVEL = "package"
WORKSPACE_LEVEL = "workspace"
LEVELS = (PACKAGE_LEVEL, WORKSPACE_LEVEL)

#: The types an engine key or a context key may take.
TYPES = ("str", "int", "bool", "path", "strs", "paths", "table")

#: The extension that declares the base's phases.
BASE = "livery.workshop"


@dataclass(frozen=True)
class PhaseSpec:
    """A phase as its declarer declares it: one ``[phases.<phase>]`` table.

    Attributes:
        name: The phase's name, ``build``.
        keys: The keys the engine running the phase gives its steps,
            each to its type; a step reads one like any key, and no
            step provides one.
        level: ``package`` for a phase whose steps run once per
            package in scope; ``workspace`` for one whose steps run
            once over the workspace.
        gated: Whether ``fm check`` runs the phase.
        after: The phases this one follows in the train's order.
        verb: Whether a verb is generated for the phase, ``fm <phase>``
            with a sub-task per step.
        extension: The extension that declares the phase; the base for
            its own.
    """

    name: str
    keys: Mapping[str, str] = field(default_factory=dict[str, str])
    level: str = PACKAGE_LEVEL
    gated: bool = False
    after: tuple[str, ...] = ()
    verb: bool = False
    extension: str = BASE


class PhaseError(RuntimeError):
    """A phase whose steps cannot run as declared, or a step's misuse of the context.

    Raised before any step runs for the phase's own problems, and from
    [livery.workshop.PhaseContext.provide][] or
    [livery.workshop.PhaseContext.read][] for a key the running step's
    table does not declare, or a value of another type.
    """


@functools.cache
def base_phases() -> dict[str, PhaseSpec]:
    """The base's phases, read from the ``[phases]`` section of its contract file.

    In the file's order, each spec naming the base as its declarer.

    Raises:
        DeclarationError: when a section is off the shape a phase's
            declaration has.
    """
    from livery.workshop._contract_keys import BASE_FILE, base_contract
    from livery.workshop._declaration import phase_specs_of

    return phase_specs_of(base_contract(), BASE_FILE, extension=BASE)


@functools.cache
def declared_phases() -> dict[str, PhaseSpec]:
    """Every declared phase by name: the base's, then each installed extension's.

    An installed extension's declaration is read whether the workspace
    lists it or not, so a step into its phase reads the same anywhere;
    the mount refuses the step while the declarer is not listed. Each
    phase's ``after`` names declared phases, and the order they give
    has a start.

    Raises:
        DeclarationError: when two extensions declare one phase, a
            phase follows one nobody declares, or the order has no
            start.
    """
    from livery.footman import installed_entry_points
    from livery.workshop._declaration import DeclarationError, phase_declarations

    found = dict(base_phases())
    for entry in installed_entry_points("workshop.extensions"):
        for spec in phase_declarations(entry.name, entry.value.partition(":")[0]):
            held = found.get(spec.name)
            if held is not None and held.extension != spec.extension:
                both = " and ".join(sorted((held.extension, spec.extension)))
                raise DeclarationError(
                    f"the {spec.name} phase is declared by both {both}; one extension"
                    " declares a phase"
                )
            found[spec.name] = spec
    _judge_after(found)
    return found


def _judge_after(specs: Mapping[str, PhaseSpec]) -> None:
    """Refuse an ``after`` naming no declared phase, and an order with no start."""
    from livery.workshop._declaration import DeclarationError

    for spec in specs.values():
        for name in spec.after:
            if name not in specs:
                raise DeclarationError(
                    f"{spec.extension} declares the {spec.name} phase after {name},"
                    f" which no extension declares; the phases are"
                    f" {', '.join(specs)}"
                )
    done: set[str] = set()
    path: list[str] = []

    def visit(name: str) -> None:
        if name in done:
            return
        if name in path:
            cycle = path[path.index(name) :]
            raise DeclarationError(
                "the phases' order has no start: "
                + " after ".join((name, *reversed(cycle)))
                + "; remove an after among them"
            )
        path.append(name)
        for earlier in specs[name].after:
            visit(earlier)
        path.pop()
        done.add(name)

    for name in specs:
        visit(name)


def phase_spec(name: str) -> PhaseSpec:
    """The declared phase *name*; refuses naming the declared ones.

    Raises:
        PhaseError: when no extension declares *name*.
    """
    specs = declared_phases()
    found = specs.get(name)
    if found is None:
        raise PhaseError(
            f"{name} is not a declared phase; the phases are {', '.join(specs)}"
        )
    return found


def lifecycle_phases() -> tuple[str, ...]:
    """The package-level phases, in declaration order: a package's lifecycle."""
    return tuple(
        name for name, spec in declared_phases().items() if spec.level == PACKAGE_LEVEL
    )


def _holds(kind: str, value: object) -> bool:
    """Whether *value* is of the context type *kind*."""
    if kind == "str":
        return isinstance(value, str)
    if kind == "int":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "bool":
        return isinstance(value, bool)
    if kind == "path":
        return isinstance(value, Path)
    if kind in ("strs", "paths"):
        member = str if kind == "strs" else Path
        return isinstance(value, (list, tuple)) and all(
            isinstance(item, member) for item in value
        )
    return isinstance(value, dict)


@dataclass
class _Shared:
    """What every view of one walk's context writes and reads.

    Attributes:
        values: The keys the steps provided, by the package they ran
            over (an empty path for a workspace step), each to its
            value.
        exit_code: The exit code the ``run`` phase's step recorded.
        lock: Held around a write, since the steps run concurrently.
    """

    values: dict[str, dict[str, object]] = field(
        default_factory=dict[str, dict[str, object]]
    )
    exit_code: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


@dataclass(frozen=True)
class PhaseContext:
    """What a run hands a step: the workspace, the run's scope, and the phase's keys.

    One context serves one walk, and every step receives its own view
    of it, with the step named in ``step`` and, for a package step,
    the package it runs over in ``package``. A step writes the keys its
    table ``provides`` with [livery.workshop.PhaseContext.provide][]
    and reads the ones it ``reads`` with
    [livery.workshop.PhaseContext.read][]; the views share what is
    written, so the engine reads a provided key back from the context
    the walk returns ([livery.workshop.PhaseContext.result][]). The
    ``run`` phase's step that starts the executable records its exit
    code with [livery.workshop.PhaseContext.exit][]. A view is frozen:
    a step that assigns an attribute changes a copy nobody reads, so
    the keys and the exit code go through the methods.

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
        point: The CI point whose tests the test phase selects; empty
            for the gate's own.
        catalogue: Every file the run may read, by unit, each with its
            category, listed once per walk by
            [livery.workshop._steps.catalogue][]; None when there is
            no listing, and then every step that applies runs.
        package: The package a package step runs over, else None.
        selection: The test files selected for that package, relative
            to it, else empty.
        changes: What changed since the tree the run measures from,
            which selects what each workspace step with declared
            inputs judges ([livery.workshop._influence.select][]); None
            when the run knows nothing of it, and then each judges
            everything.
        arguments: The words after ``--``, the same for every step of
            the run: on a step's own verb, which a step that takes
            them hands its tool (``StepRecord.arguments``, a
            template's ``{arguments}``), and on ``fm run``, which the
            step starting the executable passes it; empty in every
            other run.
        step: The running step's name, ``<phase>.<step>``, which the
            walk sets as it hands the context to the step; empty
            outside a step's run.
        phase: The phase the engine runs, ``build``, whose given keys
            [livery.workshop.PhaseContext.result][] reads back; on a
            step's view, the step's own phase, whose given keys the
            step reads. Empty for a walk over several phases, the
            gate's.
    """

    root: Path
    packages: tuple[Package, ...] = ()
    subset: tuple[Package, ...] | None = None
    tests: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict[str, tuple[str, ...]]
    )
    examples: tuple[str, ...] = ()
    fix: bool = False
    files: tuple[str, ...] = ()
    safe: bool = False
    point: str = ""
    catalogue: Mapping[str, tuple[tuple[str, str], ...]] | None = None
    package: Package | None = None
    selection: tuple[str, ...] = ()
    changes: Changes | None = None
    arguments: tuple[str, ...] = ()
    step: str = ""
    phase: str = ""
    _steps: Mapping[str, StepRecord] = field(
        default_factory=dict[str, "StepRecord"], repr=False
    )
    _given: Mapping[str, Mapping[str, object]] = field(
        default_factory=dict[str, Mapping[str, object]], repr=False
    )
    _shared: _Shared = field(default_factory=_Shared, repr=False)

    @property
    def scoped(self) -> bool:
        """Whether this is a package-scoped gate."""
        return self.subset is not None

    @property
    def judged(self) -> tuple[Package, ...]:
        """The packages under judgment: the subset, or every package."""
        return self.packages if self.subset is None else self.subset

    @property
    def member(self) -> Package:
        """The package the step runs over, for a step that runs once per package.

        Raises:
            Failed: when the run names none: a workspace step, or a
                context built outside a package step's run.
        """
        if self.package is None:
            fail(
                f"{self.step or 'this step'} runs over one package at a time, and"
                " this run names none"
            )
        return self.package

    @property
    def exit_code(self) -> int:
        """The exit code the ``run`` phase's step recorded; 0 until one does."""
        return self._shared.exit_code

    def exit(self, code: int) -> None:
        """Record *code* as the exit code ``fm run`` exits with.

        The ``run`` phase's step that starts the executable calls it
        with the executable's; the walk hands the code to the verb
        through the context it returns.
        """
        self._shared.exit_code = code

    def for_package(self, package: Package) -> PhaseContext:
        """This context narrowed to one package and its selected tests."""
        files = self.tests.get(package.path, ())
        relative = tuple(path[len(package.path) + 1 :] for path in files)
        return replace(self, package=package, selection=relative)

    def provide(self, key: str, value: object) -> None:
        """Write *value* under *key*, a key the running step's table provides.

        Raises:
            PhaseError: when the step's table does not provide *key*,
                or *value* is not of the type it declares.
        """
        record = self._running()
        provided = dict(record.provides)
        if key not in provided:
            raise PhaseError(
                f"{self.step} provides {key}, and its {self._table()} table does"
                " not name it in provides"
            )
        if not _holds(provided[key], value):
            raise PhaseError(
                f"{self.step} provides {key} as {type(value).__name__}, and its"
                f" {self._table()} table declares it {provided[key]}"
            )
        with self._shared.lock:
            self._shared.values.setdefault(self._unit(), {})[key] = value

    def read(self, key: str) -> object:
        """The value under *key*, a key the running step's table reads.

        Its type is the one its provider declares, or the one the
        phase's declaration gives an engine key.

        Raises:
            PhaseError: when the step's table does not read *key*, the
                engine did not give it, or its provider wrote nothing
                under it.
        """
        record = self._running()
        if key not in record.reads:
            raise PhaseError(
                f"{self.step} reads {key}, and its {self._table()} table does not"
                " name it in reads"
            )
        if key in self._engine():
            given = self._given.get(self.phase, {})
            if key not in given:
                raise PhaseError(
                    f"{self.step} reads {key}, which the engine running the"
                    f" {self.phase} phase did not give it"
                )
            return given[key]
        values = self._shared.values.get(self._unit(), {})
        if key not in values:
            provider = next(
                (
                    name
                    for name, held in self._steps.items()
                    if key in dict(held.provides)
                ),
                "its provider",
            )
            raise PhaseError(
                f"{self.step} reads {key}, which {provider} has not provided in"
                f" the {self.phase} phase"
            )
        return values[key]

    def result(self, key: str) -> object | None:
        """The value under *key* once the phase ran, for the engine that ran it.

        A key a step provided for the context's package, else one the
        engine gave the context's phase; None when neither wrote one.
        """
        values = self._shared.values.get(self._unit(), {})
        return values.get(key, self._given.get(self.phase, {}).get(key))

    def _running(self) -> StepRecord:
        """The running step's record; refuses outside a walk's step."""
        record = self._steps.get(self.step)
        if record is None:
            raise PhaseError(
                f"{self.step or 'this step'} is not a step of the walk that built"
                " this context, so the context has no table to judge its keys by"
            )
        return record

    def _table(self) -> str:
        """The running step's table, as the declaration spells it."""
        phase, _dot, name = self.step.partition(".")
        return f"[phases.{phase}.{name}]"

    def _engine(self) -> Mapping[str, str]:
        """The keys the engine gives the phase, when the context names one."""
        return phase_spec(self.phase).keys if self.phase else {}

    def _unit(self) -> str:
        """Where the keys this view writes and reads live: its package, or the root."""
        return self.package.path if self.package is not None else ""


def phase_steps(
    phase: str, members: tuple[str, ...], lookup: Lookup
) -> tuple[StepRecord, ...]:
    """The steps the extensions *members* add to *phase*, in their order.

    Each member's steps in declaration order, the members in the order
    given, which for a package's set is its composition order. A step
    one of *members* deletes is left out, so it never runs for the set
    (`deletions`).
    """
    deleted = deletions(members, lookup)
    found: list[StepRecord] = []
    for name in members:
        declared = lookup(name)
        if declared is not None:
            found += [
                record
                for record in declared.phases.get(phase, ())
                if step_address(record) not in deleted
            ]
    return tuple(found)


def step_address(record: StepRecord) -> str:
    """*record*'s address among every extension's steps: ``<owner>:<phase>.<step>``."""
    return f"{record.extension}:{record.name}"


def deletions(members: Iterable[str], lookup: Lookup) -> dict[str, tuple[str, str]]:
    """The steps the extensions *members* delete, by address, to the deleter and why.

    An address is ``<owner>:<phase>.<step>``, as a step table's
    ``deletes`` names it; the first of *members* to delete a step is
    its deleter.
    """
    found: dict[str, tuple[str, str]] = {}
    for name in members:
        declared = lookup(name)
        if declared is None:
            continue
        for records in declared.phases.values():
            for record in records:
                for address, reason in record.deletes:
                    found.setdefault(address, (name, reason))
    return found


def set_deletions(package: Package) -> dict[str, tuple[str, str]]:
    """The steps *package*'s set deletes, by address, to the deleter and why."""
    from livery.workshop._composition import extension_set
    from livery.workshop._extensions import installed_declaration

    return deletions(extension_set(package), installed_declaration)


#: The engine key that names a phase's side: true for the isolated one.
ISOLATED = "isolated"


def serves(record: StepRecord, given: Mapping[str, object]) -> bool:
    """Whether *record* runs on the side of its phase the engine's *given* keys name.

    *given* are the keys the engine gives the step's phase. A step
    declaring ``isolated = true`` runs when the engine gives
    ``isolated`` true; one declaring false runs when the engine gives
    false or nothing; one declaring neither runs on both sides.
    """
    if record.isolated is None:
        return True
    return record.isolated == (given.get(ISOLATED) is True)


def _providers(steps: Iterable[StepRecord]) -> dict[str, list[str]]:
    """Each key the steps provide, to the steps that provide it."""
    found: dict[str, list[str]] = {}
    for record in steps:
        for key, _kind in record.provides:
            found.setdefault(key, []).append(record.name)
    return found


def _earlier(
    record: StepRecord,
    by_name: Mapping[str, StepRecord],
    providers: Mapping[str, list[str]],
    *,
    after: bool,
) -> set[str]:
    """The steps among *by_name* that *record* runs after.

    The providers of the keys it reads, in its own phase, and, with
    *after*, the steps it names in ``after``; a name outside
    *by_name* orders nothing.
    """
    found = {
        provider
        for key in record.reads
        for provider in providers.get(key, ())
        if by_name[provider].phase == record.phase
    }
    if after:
        found.update(other for other in record.after if other in by_name)
    found.discard(record.name)
    return found


def phase_order(
    steps: Iterable[StepRecord], *, after: bool = True
) -> tuple[StepRecord, ...]:
    """*steps* in the order the walk starts them.

    Each step after the providers of the keys it reads, in its own
    phase, and, with *after*, after the steps it names in ``after``;
    otherwise in the given order. An ``after`` naming a step outside
    *steps* orders nothing.

    Raises:
        OrderCycle: when the order has no start, naming the members
            of one cycle.
    """
    from livery.workshop._composition import OrderCycle, cycle_of

    by_name = {record.name: record for record in steps}
    providers = _providers(by_name.values())
    waiting = {
        name: _earlier(record, by_name, providers, after=after)
        for name, record in by_name.items()
    }
    done: list[str] = []
    while len(done) < len(by_name):
        ready = next(
            (
                name
                for name in by_name
                if name not in done and not waiting[name].difference(done)
            ),
            None,
        )
        if ready is None:
            left = {name: waiting[name] for name in by_name if name not in done}
            raise OrderCycle(cycle_of(left))
        done.append(ready)
    return tuple(by_name[name] for name in done)


def phase_problems(phase: str, steps: Iterable[StepRecord]) -> list[str]:
    """Why *steps* cannot run as *phase*; empty when they can.

    Two extensions declaring one step, two steps providing one key, a
    step providing a key the phase's engine gives, a step declaring a
    side of a phase whose declaration gives no ``isolated``, a key a
    step reads and no step provides, and an order with no start.

    Raises:
        PhaseError: when no extension declares *phase*.
    """
    from livery.workshop._composition import OrderCycle

    records = tuple(steps)
    problems: list[str] = []
    engine = phase_spec(phase).keys
    owners: dict[str, list[str]] = {}
    for record in records:
        owners.setdefault(record.name, []).append(record.extension)
    for name, extensions in owners.items():
        if len(extensions) > 1:
            problems.append(
                f"the {phase} phase: {' and '.join(sorted(extensions))} both declare"
                f" {name}; one extension of a package declares a step"
            )
    providers = _providers(records)
    for key, names in sorted(providers.items()):
        if key in engine:
            verb = "provides" if len(names) == 1 else "provide"
            problems.append(
                f"the {phase} phase: {' and '.join(sorted(names))} {verb} {key},"
                " which the engine running the phase provides; a step reads it"
            )
        elif len(names) > 1:
            problems.append(
                f"the {phase} phase: {' and '.join(sorted(names))} both provide"
                f" {key}; one step of a package provides a key"
            )
    if ISOLATED not in engine:
        problems += [
            f"the {phase} phase: {record.name} declares isolated, and the phase"
            " has one side: its declaration gives no isolated key"
            for record in records
            if record.isolated is not None
        ]
    for record in records:
        for key in record.reads:
            if key not in providers and key not in engine:
                problems.append(
                    f"the {phase} phase: {record.name} reads {key}, which no step of"
                    " the package provides"
                )
    try:
        phase_order(records)
    except OrderCycle as error:
        cycle = " before ".join((*error.members, error.members[0]))
        problems.append(
            f"the {phase} phase: its steps' order has no start: {cycle}; remove a"
            " key read or an after among them"
        )
    return problems


class _Unit:
    """One footman step of a walk: a step record over one package, or over the whole.

    Attributes:
        record: The step.
        package: The package it runs over; None for a workspace step.
        title: What footman shows for it: the step's name, then the
            package's path.
        done: Set once the unit ran, whatever happened.
        failed: Whether it failed; a unit that waits on it refuses.
        needs: The units that run before it.
    """

    __slots__ = ("done", "failed", "needs", "package", "record", "title")

    def __init__(self, record: StepRecord, package: Package | None) -> None:
        self.record = record
        self.package = package
        self.title = record.name if package is None else f"{record.name} {package.path}"
        self.done = threading.Event()
        self.failed = False
        self.needs: list[_Unit] = []


#: How long a waiting step looks at the unit it waits on before it
#: yields to footman again.
_POLL_SECONDS = 0.05


def walk_steps(
    ctx: PhaseContext,
    steps: Iterable[StepRecord],
    *,
    inputs: Mapping[str, Mapping[str, object]] | None = None,
    fixing: bool = False,
) -> PhaseContext:
    """Run *steps* as footman steps inside one ``parallel()`` block.

    A step runs on the side of its phase the engine gives
    ([livery.workshop._phases.serves][]), and a step serving the other
    side is not run. A workspace step runs once. A package step runs
    once per package in scope (the context's ``judged``) it judges:
    one whose set holds an extension the step names and none that
    deletes the step, that has not turned the step off, that has a
    file the step's claims reach, and whose change is not confined to
    its tests unless the step is tests-only; each other package is
    said and not run. Independent
    steps run concurrently, and a step starts once the steps it names
    in ``after`` and the providers of the keys it reads have run, for
    the same package. A step whose earlier step failed is not run and
    says so; the earlier failure is the verdict, and the steps that
    depend on nothing failed still run, as footman runs a block.
    *inputs* are the keys the engine gives each phase, by phase, each
    phase's judged against its declaration before any step runs.
    *fixing* runs each step's fix body instead of its main, a step
    without one running nothing, in the given order: a fix rewrites
    what its own tool reads, and the gate runs its fixers one at a
    time, so ``after`` orders nothing there.

    Returns:
        The context the steps shared, which the engine reads a provided
        key or the exit code back from.

    Raises:
        PhaseError: before any step runs, when the context or *inputs*
            names a phase nobody declares, when *inputs* gives a phase
            a key it does not take or a value of another type, or when
            the steps cannot run as declared
            ([livery.workshop._phases.phase_problems][]).
        Exception: the first failing step's, raised again once every
            step ran.
    """
    from livery.workshop._steps import (
        PACKAGE,
        judges_package,
        members,
        option_value,
        reads_files,
    )

    records = tuple(steps)
    where = f"{ctx.package.path}: " if ctx.package is not None else ""
    given = {phase: dict(keys) for phase, keys in (inputs or {}).items()}
    if ctx.phase:
        # A context naming a phase nobody declares refuses here, before
        # any step runs, as an input to such a phase does.
        phase_spec(ctx.phase)
    _judge_inputs(given, where)
    problems: list[str] = []
    for phase in dict.fromkeys(record.phase for record in records):
        problems += phase_problems(
            phase, (record for record in records if record.phase == phase)
        )
    if problems:
        raise PhaseError(where + "; ".join(problems))
    ordered = phase_order(records, after=not fixing)
    held = replace(
        ctx, _steps={record.name: record for record in records}, _given=given
    )
    units: list[_Unit] = []
    removed: dict[str, dict[str, tuple[str, str]]] = {}
    for record in ordered:
        if (record.fix if fixing else record.run) is None:
            continue
        if not serves(record, given.get(record.phase, {})):
            continue
        if record.scope != PACKAGE:
            units.append(_Unit(record, None))
            continue
        for package in members(held):
            if not judges_package(record, held, package):
                continue
            if package.path not in removed:
                removed[package.path] = set_deletions(package)
            deleted = removed[package.path].get(step_address(record))
            if deleted is not None:
                deleter, reason = deleted
                print(
                    f"  {record.name}: {package.path} skips ({deleter} deletes it:"
                    f" {reason})"
                )
                continue
            if not reads_files(record, held, (package.path,)):
                print(f"  {record.name}: {package.path} has no file it reads; not run")
                continue
            if not option_value(record, package, "enabled"):
                print(
                    f"  {record.name}: {package.path} skips (turned off in"
                    f" {package.path}/workshop.toml)"
                )
                continue
            units.append(_Unit(record, package))
    _link(units, ordered, after=not fixing)
    bodies = [_resolved(unit, fixing) for unit in units]
    with parallel() as block:
        for unit, body in zip(units, bodies, strict=True):
            built = step(_body(held, unit, body), title=unit.title)
            block(built.opts(capture=False)())
    return held


def run_registered(ctx: PhaseContext) -> PhaseContext:
    """Run the steps registered for the phase *ctx* names, through the walk.

    The steps the mount registered for ``ctx.phase``, in registration
    order: each listed extension's own, and those of the
    ``[for.<target>]`` tables that apply. They run as `walk_steps` runs
    any steps, so a phase the gate never runs, one declared with
    ``gated = false``, runs this way over the workspace its context
    names. A workspace step that names extensions runs once while a
    package the context judges holds one of them; while none does, a
    line says so and the step does not run. A phase with no registered
    step runs nothing.

    Returns:
        The context the steps shared.

    Raises:
        PhaseError: when *ctx* names no phase, or one nobody declares.
        Exception: the first failing step's, raised again once every
            step ran.
    """
    running, _idle = _registered(ctx)
    return walk_steps(ctx, running)


def _registered(
    ctx: PhaseContext,
) -> tuple[tuple[StepRecord, ...], tuple[StepRecord, ...]]:
    """The steps registered for ``ctx.phase``: those that run, and those that do not.

    A workspace step that names extensions runs while a package the
    context judges holds one of them, and a line says so for each one
    that does not.

    Raises:
        PhaseError: when *ctx* names no phase.
    """
    from livery.workshop._steps import PACKAGE, judges_unit, steps_by_name

    if not ctx.phase:
        raise PhaseError(
            "a run of the registered steps names its phase, and this context names none"
        )
    running: list[StepRecord] = []
    idle: list[StepRecord] = []
    for record in steps_by_name().values():
        if record.phase != ctx.phase:
            continue
        named = record.scope != PACKAGE and bool(record.extensions)
        if named and not any(judges_unit(record, unit) for unit in ctx.judged):
            wanted = " or ".join(record.extensions)
            print(f"  {record.name}: no package holds {wanted}; not run")
            idle.append(record)
            continue
        running.append(record)
    return tuple(running), tuple(idle)


#: The record of what each phase's last run through `run_phase` declared
#: it writes: the checkout's own state, never committed.
OUTPUTS_RECORD = ".workshop/state/outputs.json"

#: The prefix of an output that names a path in each package.
IN_EACH_PACKAGE = "package/"


def output_refusal(output: str) -> str:
    """Why *output* cannot name what a step writes, or ``""`` when it can.

    An output is a path relative to the workspace root, or
    ``package/<path>`` for that path in each package; a directory ends
    in ``/``. It lies below the root, or below the package, and it is
    never ``docs/`` itself, the root's or a package's, which holds the
    authored pages. The text completes a sentence about the output.
    """
    import re

    inner = output.removeprefix(IN_EACH_PACKAGE)
    where = "each package" if inner != output else "the root"
    parts = tuple(part for part in inner.split("/") if part not in ("", "."))
    if (
        "\\" in output
        or inner.startswith("/")
        or re.match(r"[A-Za-z]:", inner)
        or ".." in parts
    ):
        return (
            f"names {output!r}, which leaves the root; an output is a path below"
            " the root, or package/<path> for the path in each package"
        )
    if not parts:
        return f"names {output!r}, {where} itself; an output is a path below it"
    if parts == ("docs",):
        tree = "each package's" if inner != output else "the root's"
        return (
            f"names {output!r}, {tree} docs/ itself, which holds the authored"
            " pages; an output is a path below it"
        )
    return ""


def _outputs_held(root: Path) -> dict[str, tuple[str, ...]]:
    """Each phase's outputs, as its last run through `run_phase` recorded them.

    A record that cannot be read, or holds another shape, records
    nothing: the next run writes it again.
    """
    import json

    try:
        held = json.loads((root / OUTPUTS_RECORD).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(held, dict) or held.get("schema") != 1:
        return {}
    phases = held.get("phases")
    if not isinstance(phases, dict):
        return {}
    return {
        phase: tuple(output for output in listed if isinstance(output, str))
        for phase, listed in cast("dict[str, object]", phases).items()
        if isinstance(listed, list)
    }


def _remove_outputs(
    root: Path,
    phase: str,
    running: tuple[str, ...],
    idle: tuple[str, ...],
    packages: tuple[Package, ...],
) -> None:
    """Remove what no running step of *phase* writes, and record what they do.

    Each output the phase's last run recorded, or a registered step
    that does not run declares, goes unless a running step declares
    it; one line names each path removed. The record then holds the
    running steps' outputs.
    """
    import json
    import shutil

    held = _outputs_held(root)
    kept = set(running)
    stale = [
        output
        for output in dict.fromkeys((*held.get(phase, ()), *idle))
        if output not in kept and not output_refusal(output)
    ]
    for output in stale:
        inner = output.removeprefix(IN_EACH_PACKAGE)
        homes = [p.directory for p in packages] if inner != output else [root]
        for home in homes:
            path = home / inner
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                continue
            shown = path.relative_to(root).as_posix() + (
                "/" if output.endswith("/") else ""
            )
            print(f"  removed {shown}: no running step of the {phase} phase writes it")
    recorded = tuple(sorted(kept))
    if held.get(phase, ()) == recorded:
        return
    held[phase] = recorded
    path = root / OUTPUTS_RECORD
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "phases": {name: list(held[name]) for name in sorted(held) if held[name]},
    }
    path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def run_phase(root: Path, phase: str, *, outputs: Iterable[str] = ()) -> PhaseContext:
    """Run a phase the gate never runs over the workspace at *root*; the shared context.

    The steps the listed extensions register for *phase*, their own and
    those of the ``[for.<target>]`` tables that apply, run side by side
    through the walk every phase runs through, each workspace step once
    and each package step once per package whose set holds an
    extension it names. A workspace step that names extensions runs
    while a package holds one of them; while none does, a line says so
    and the step does not run. A phase with no registered step runs
    nothing.

    Before the steps run, the paths a step declares under ``outputs``
    are settled: each one the phase's last run recorded, or a step that
    does not run now declares, is removed unless a step that runs now
    declares it, and a line names each path removed. So a step that
    stops running, its extension unlisted or no longer installed or the
    step turned off, leaves nothing behind wherever it wrote, and a
    running step finds its own output as it left it. The record lives
    in the checkout's ``.workshop/state/``.

    Args:
        root: The workspace root.
        phase: The phase, one declared with ``gated = false``.
        outputs: Paths this run writes that no step declares, such as
            those of the verbs a step runs: each in the shape a step
            declares one, and removed and recorded as a running step's
            are.

    Returns:
        The context the steps shared.

    Raises:
        PhaseError: when nobody declares *phase*, naming the declared
            phases; when the gate runs it; or when an output in
            *outputs* is not a path below the root, or names ``docs/``
            itself.
        Exception: the first failing step's, raised again once every
            step ran.
    """
    from livery.workshop._packages import discover_packages

    spec = phase_spec(phase)
    if spec.gated:
        raise PhaseError(
            f"the gate runs the {phase} phase; run_phase runs a phase declared"
            " with gated = false, and run_phase_over runs a gated phase's steps"
            " over named packages"
        )
    given = tuple(outputs)
    for output in given:
        refusal = output_refusal(output)
        if refusal:
            raise PhaseError(f"an output of the {phase} phase {refusal}")
    ctx = PhaseContext(root=root, packages=discover_packages(root), phase=phase)
    running, idle = _registered(ctx)
    _remove_outputs(
        root,
        phase,
        (*(o for record in running for o in record.outputs), *given),
        tuple(o for record in idle for o in record.outputs),
        ctx.packages,
    )
    return walk_steps(ctx, running)


def _judge_inputs(given: Mapping[str, Mapping[str, object]], where: str) -> None:
    """Refuse an undeclared phase, a key it does not take, or a mistyped value."""
    for phase, keys in given.items():
        engine = phase_spec(phase).keys
        for key, value in keys.items():
            if key not in engine:
                known = ", ".join(sorted(engine)) or "nothing"
                raise PhaseError(
                    f"{where}the engine gives {key} to the {phase} phase, which takes"
                    f" {known} from it"
                )
            if not _holds(engine[key], value):
                raise PhaseError(
                    f"{where}the engine gives {key} to the {phase} phase as"
                    f" {type(value).__name__}, which the phase takes as {engine[key]}"
                )


def _link(units: list[_Unit], ordered: tuple[StepRecord, ...], *, after: bool) -> None:
    """Give each of *units* the units it waits on: its earlier steps, same package.

    A package unit waits on an earlier step's unit of its own package,
    and on a workspace unit; a workspace unit waits on every unit of
    an earlier step.
    """
    by_name = {record.name: record for record in ordered}
    providers = _providers(ordered)
    by_record: dict[str, list[_Unit]] = {}
    for unit in units:
        by_record.setdefault(unit.record.name, []).append(unit)
    for unit in units:
        for name in sorted(_earlier(unit.record, by_name, providers, after=after)):
            for other in by_record.get(name, ()):
                same = (
                    unit.package is None
                    or other.package is None
                    or unit.package.path == other.package.path
                )
                if same:
                    unit.needs.append(other)


def _resolved(unit: _Unit, fixing: bool) -> Callable[[PhaseContext], None]:
    """The body *unit* runs, its module imported now, on the calling thread.

    A declared step names its function, imported when it is first
    called; the walk imports every step's module before the block
    starts, so no two steps import at once, and a module that fails to
    import names its step before any step runs.
    """
    from livery.workshop._declaration import Reference

    body = unit.record.fix if fixing else unit.record.run
    assert body is not None  # a unit is built for a step with a body alone
    if isinstance(body, Reference):
        return cast("Callable[[PhaseContext], None]", body.resolve())
    return body


def _body(
    ctx: PhaseContext, unit: _Unit, body: Callable[[PhaseContext], None]
) -> Callable[[], Generator[None, Any, None]]:
    """The footman step's body for *unit*: wait for what it needs, then run *body*.

    A generator, so each wait is a checkpoint where footman cancels
    the step; the unit is marked done however the body ends, so a unit
    waiting on it never waits past its end.
    """
    from livery.workshop._composition import extension_set

    record = unit.record

    def run() -> Generator[None, Any, None]:
        try:
            for need in unit.needs:
                while not need.done.wait(_POLL_SECONDS):
                    yield
            failed = [need for need in unit.needs if need.failed]
            if failed:
                # Said here as well as refused: the block raises the
                # earlier failure, and this reason rides the record.
                reason = f"{unit.title}: not run, since {failed[0].title} failed"
                print(f"  {reason}")
                fail(reason)
            view = ctx if unit.package is None else ctx.for_package(unit.package)
            view = replace(view, step=record.name, phase=record.phase)
            if unit.package is not None:
                held = extension_set(unit.package)
                matched = " and ".join(n for n in record.extensions if n in held)
                print(
                    f"  {record.name}: {unit.package.path} runs (a {matched} package)"
                )
            body(view)
        except BaseException:
            unit.failed = True
            raise
        finally:
            unit.done.set()

    return run
