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
[livery.workshop._phases.run_phase][]: each step a footman step inside
one ``parallel()`` block, a workspace step once and a package step
once per package in scope, independent steps concurrently, a step
after the steps it names in ``after`` and after the providers of the
keys it ``reads``. The gate runs its phases through it with its own
engine around it ([livery.workshop._quality.walk][]); the release
train, ``fm sync`` and ``fm run`` run a package's lifecycle phases
through it ([livery.workshop._lifecycle][]), the steps those of the
package's set ([livery.workshop._phases.phase_steps][]). Each step
receives the one context, [livery.workshop.PhaseContext][].

Two steps providing one key, a key nobody provides and an order with
no start refuse before any step runs
([livery.workshop._phases.phase_problems][]); the layering check names
them for each package.
"""

from __future__ import annotations

import functools
import threading
from collections.abc import Callable, Generator, Iterable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from livery.footman import fail, parallel, step

if TYPE_CHECKING:
    from livery.workshop._checks import CheckRecord
    from livery.workshop._composition import Lookup
    from livery.workshop._influence import Changes
    from livery.workshop._packages import Package

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
    import tomllib

    from livery.workshop._contract_keys import BASE_FILE
    from livery.workshop._declaration import phase_specs_of

    data = tomllib.loads(BASE_FILE.read_text("utf-8"))
    return phase_specs_of(data, BASE_FILE, extension=BASE)


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
    of it, with the step named in ``check`` and, for a package step,
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
        point: The CI point whose tests the test role selects; empty
            for the gate's own.
        catalogue: Every file the run may read, by unit, each with its
            category, listed once per walk by
            [livery.workshop._checks.catalogue][]; None when there is
            no listing, and then every check that applies runs.
        package: The package a package step runs over, else None.
        selection: The test files selected for that package, relative
            to it, else empty.
        changes: What changed since the tree the run measures from,
            which selects what each workspace check with declared
            inputs judges ([livery.workshop._influence.select][]); None
            when the run knows nothing of it, and then each judges
            everything.
        arguments: The words after ``--``: on a check's own verb, which
            a check that takes them (``CheckRecord.arguments``) hands
            its tool, and on ``fm run``, which the step starting the
            executable passes it; empty in every other run.
        check: The running step's name, ``<phase>.<step>``, which the
            walk sets as it hands the context to the step; empty
            outside a step's run.
        phase: The phase whose keys the engine gives, ``build``, which
            the engine running the phase names; on a step's view, the
            step's own phase. Empty for a run that gives no keys.
        executable: The executable the ``run`` phase starts, as the
            ``executables`` query names it; empty in another phase.
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
    check: str = ""
    phase: str = ""
    executable: str = ""
    _steps: Mapping[str, CheckRecord] = field(
        default_factory=dict[str, "CheckRecord"], repr=False
    )
    _given: Mapping[str, object] = field(default_factory=dict[str, object], repr=False)
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
                f"{self.check or 'this step'} runs over one package at a time, and"
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
                f"{self.check} provides {key}, and its {self._table()} table does"
                " not name it in provides"
            )
        if not _holds(provided[key], value):
            raise PhaseError(
                f"{self.check} provides {key} as {type(value).__name__}, and its"
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
                f"{self.check} reads {key}, and its {self._table()} table does not"
                " name it in reads"
            )
        if key in self._engine():
            if key not in self._given:
                raise PhaseError(
                    f"{self.check} reads {key}, which the engine running the"
                    f" {self.phase} phase did not give it"
                )
            return self._given[key]
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
                f"{self.check} reads {key}, which {provider} has not provided in"
                f" the {self.phase} phase"
            )
        return values[key]

    def result(self, key: str) -> object | None:
        """The value under *key* once the phase ran, for the engine that ran it.

        A key a step provided for the context's package, else one the
        engine gave; None when neither wrote one.
        """
        values = self._shared.values.get(self._unit(), {})
        return values.get(key, self._given.get(key))

    def _running(self) -> CheckRecord:
        """The running step's record; refuses outside a walk's step."""
        record = self._steps.get(self.check)
        if record is None:
            raise PhaseError(
                f"{self.check or 'this step'} is not a step of the walk that built"
                " this context, so the context has no table to judge its keys by"
            )
        return record

    def _table(self) -> str:
        """The running step's table, as the declaration spells it."""
        phase, _dot, name = self.check.partition(".")
        return f"[phases.{phase}.{name}]"

    def _engine(self) -> Mapping[str, str]:
        """The keys the engine gives the phase, when the context names one."""
        return phase_spec(self.phase).keys if self.phase else {}

    def _unit(self) -> str:
        """Where the keys this view writes and reads live: its package, or the root."""
        return self.package.path if self.package is not None else ""


def phase_steps(
    phase: str, members: tuple[str, ...], lookup: Lookup
) -> tuple[CheckRecord, ...]:
    """The steps the extensions *members* add to *phase*, in their order.

    Each member's steps in declaration order, the members in the order
    given, which for a package's set is its composition order.
    """
    found: list[CheckRecord] = []
    for name in members:
        declared = lookup(name)
        if declared is not None:
            found += declared.phases.get(phase, ())
    return tuple(found)


def _providers(steps: Iterable[CheckRecord]) -> dict[str, list[str]]:
    """Each key the steps provide, to the steps that provide it."""
    found: dict[str, list[str]] = {}
    for record in steps:
        for key, _kind in record.provides:
            found.setdefault(key, []).append(record.name)
    return found


def _earlier(
    record: CheckRecord,
    by_name: Mapping[str, CheckRecord],
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
        if by_name[provider].role == record.role
    }
    if after:
        found.update(other for other in record.after if other in by_name)
    found.discard(record.name)
    return found


def phase_order(
    steps: Iterable[CheckRecord], *, after: bool = True
) -> tuple[CheckRecord, ...]:
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


def phase_problems(phase: str, steps: Iterable[CheckRecord]) -> list[str]:
    """Why *steps* cannot run as *phase*; empty when they can.

    Two extensions declaring one step, two steps providing one key, a
    step providing a key the phase's engine gives, a key a step reads
    and no step provides, and an order with no start.

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

    def __init__(self, record: CheckRecord, package: Package | None) -> None:
        self.record = record
        self.package = package
        self.title = record.name if package is None else f"{record.name} {package.path}"
        self.done = threading.Event()
        self.failed = False
        self.needs: list[_Unit] = []


#: How long a waiting step looks at the unit it waits on before it
#: yields to footman again.
_POLL_SECONDS = 0.05


def run_phase(
    ctx: PhaseContext,
    steps: Iterable[CheckRecord],
    *,
    inputs: Mapping[str, object] | None = None,
    fixing: bool = False,
) -> PhaseContext:
    """Run *steps* as footman steps inside one ``parallel()`` block.

    A workspace step runs once. A package step runs once per package
    in scope (the context's ``judged``) it judges: one whose set holds
    an extension the step names, that has not turned the step off,
    that has a file the step's claims reach, and whose change is not
    confined to its tests unless the step is tests-only; each other
    package is said and not run. Independent steps run concurrently,
    and a step starts once the steps it names in ``after`` and the
    providers of the keys it reads have run, for the same package. A
    step whose earlier step failed is not run and says so; the earlier
    failure is the verdict, and the steps that depend on nothing
    failed still run, as footman runs a block. *inputs* are the keys
    the engine gives the phase the context names, judged against the
    phase's declaration before any step runs. *fixing* runs each step's
    fix body instead of its main, a step without one running nothing,
    in the given order: a fix rewrites what its own tool reads, and the
    gate runs its fixers one at a time, so ``after`` orders nothing
    there.

    Returns:
        The context the steps shared, which the engine reads a provided
        key or the exit code back from.

    Raises:
        PhaseError: before any step runs, when the context names no
            declared phase, when *inputs* holds a key the phase does
            not take or a value of another type, or when the steps
            cannot run as declared
            ([livery.workshop._phases.phase_problems][]).
        Exception: the first failing step's, raised again once every
            step ran.
    """
    from livery.workshop._checks import (
        PACKAGE,
        judges_package,
        members,
        option_value,
        reads_files,
    )

    records = tuple(steps)
    where = f"{ctx.package.path}: " if ctx.package is not None else ""
    given = dict(inputs or {})
    _judge_inputs(ctx.phase, given, where)
    problems: list[str] = []
    for phase in dict.fromkeys(record.role for record in records):
        problems += phase_problems(
            phase, (record for record in records if record.role == phase)
        )
    if problems:
        raise PhaseError(where + "; ".join(problems))
    ordered = phase_order(records, after=not fixing)
    held = replace(
        ctx, _steps={record.name: record for record in records}, _given=given
    )
    units: list[_Unit] = []
    for record in ordered:
        if (record.fix if fixing else record.run) is None:
            continue
        if record.scope != PACKAGE:
            units.append(_Unit(record, None))
            continue
        for package in members(held):
            if not judges_package(record, held, package):
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
    with parallel() as block:
        for unit in units:
            built = step(_body(held, unit, fixing), title=unit.title)
            block(built.opts(capture=False)())
    return held


def _judge_inputs(phase: str, given: Mapping[str, object], where: str) -> None:
    """Refuse an input the phase does not take, or one of another type."""
    if not phase:
        if given:
            raise PhaseError(
                f"{where}the engine gives {', '.join(sorted(given))} and names no"
                " phase; the context's phase names the phase whose keys it gives"
            )
        return
    engine = phase_spec(phase).keys
    for key, value in given.items():
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


def _link(units: list[_Unit], ordered: tuple[CheckRecord, ...], *, after: bool) -> None:
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


def _body(
    ctx: PhaseContext, unit: _Unit, fixing: bool
) -> Callable[[], Generator[None, Any, None]]:
    """The footman step's body for *unit*: wait for what it needs, then run it.

    A generator, so each wait is a checkpoint where footman cancels
    the step; the unit is marked done however the body ends, so a unit
    waiting on it never waits past its end.
    """
    from livery.workshop._kinds import extension_set

    record = unit.record
    body = record.fix if fixing else record.run
    assert body is not None  # a unit is built for a step with a body alone

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
            view = replace(view, check=record.name, phase=record.role)
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
