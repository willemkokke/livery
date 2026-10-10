"""Run a lifecycle phase over a package: each extension's pre, main and post steps.

A package-level extension adds steps to a package's lifecycle phases in
its ``extension.toml``, ``[phases.<phase>]``: ``pre``, ``main`` and
``post``, each a function called with the phase's
[livery.workshop.PhaseContext][], and the context keys the steps
``provides`` and ``reads``. [livery.workshop._phases.run_phase][] walks
the extensions of a package's set that add steps to the phase: every
``pre`` in order, then every ``main`` in order, then every ``post`` in
reverse. A ``post`` runs once its extension's ``pre`` ran, whatever
failed, and the context says what did.

The engine that runs a phase provides keys of its own
([livery.workshop._phases.ENGINE_KEYS][]): a step reads one like any
key, and no extension provides one.

The order puts a reader after the provider of each key it reads, then
follows each extension's ``before`` and ``after``, ties alphabetical
([livery.workshop._composition.order][]). Two providers of one key, a
key nobody provides and an order with no start refuse before any step
runs; the layering check names them for each package.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from livery.workshop._composition import Lookup
    from livery.workshop._declaration import DeclaredPhase, Reference
    from livery.workshop._packages import Package

#: The lifecycle phases, in the order a package lives through them.
PHASES = (
    "create",
    "sync",
    "stamp",
    "build",
    "prove",
    "publish",
    "replay",
    "clean",
    "run",
)


#: The keys the engine that runs a phase provides, with their types:
#: ``stamp`` the version to write, ``build`` the build's source date (0
#: takes the build tool's own), ``prove`` the leg's resolution and the
#: co-released set's dist directories, ``publish`` the version and the
#: resolved registry target, a table of its fields, and ``replay`` the
#: released version, the tree checked out at its receipt tag, the
#: interpreter, the index to install from and the extras.
ENGINE_KEYS: Mapping[str, Mapping[str, str]] = {
    "stamp": {"version": "str"},
    "build": {"epoch": "int"},
    "prove": {"resolution": "str", "release-dirs": "paths"},
    "publish": {"version": "str", "registry-target": "table"},
    "replay": {
        "version": "str",
        "tree": "path",
        "interpreter": "str",
        "index": "str",
        "extras": "str",
    },
}


class PhaseError(RuntimeError):
    """A phase its extensions declare in a way that cannot run, or a step's misuse.

    Raised before any step runs for the phase's own problems, and from
    [livery.workshop.PhaseContext.provide][] or
    [livery.workshop.PhaseContext.read][] for a key the running step's
    extension does not declare, or a value of another type.
    """


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
class PhaseContext:
    """What a phase's steps share: the package, the workspace, the keys and the failure.

    A step writes the keys its extension ``provides`` with
    [livery.workshop.PhaseContext.provide][] and reads the ones it
    ``reads`` with [livery.workshop.PhaseContext.read][]. A ``post``
    reads ``failed``, ``failure`` and ``failed_extension`` to learn
    whether the steps before it went through. The ``run`` phase's main
    starts ``executable`` with ``arguments`` and sets ``exit_code``.

    Attributes:
        phase: The phase that runs: ``build``.
        package: The package it runs for.
        root: The workspace root.
        failed: Whether a step failed.
        failure: The first step's exception; None while nothing failed.
        failed_extension: The extension whose step failed first; empty
            while nothing failed.
        executable: The executable the ``run`` phase starts, as the
            ``executables`` query names it; empty in another phase.
        arguments: The words ``fm run`` passes the executable.
        exit_code: The executable's exit code, which ``fm run`` exits
            with; the ``run`` phase's main sets it.
    """

    phase: str
    package: Package
    root: Path
    failed: bool = False
    failure: Exception | None = None
    failed_extension: str = ""
    executable: str = ""
    arguments: tuple[str, ...] = ()
    exit_code: int = 0
    _steps: Mapping[str, DeclaredPhase] = field(
        default_factory=dict[str, "DeclaredPhase"], repr=False
    )
    _values: dict[str, object] = field(default_factory=dict[str, object], repr=False)
    _running: str = field(default="", repr=False)

    def provide(self, key: str, value: object) -> None:
        """Write *value* under *key*, a key the running step's extension provides.

        Raises:
            PhaseError: when the extension does not provide *key*, or
                *value* is not of the type it declares.
        """
        provided = self._steps[self._running].provides
        if key not in provided:
            raise PhaseError(
                f"{self._running} provides {key} in the {self.phase} phase, and its"
                " [phases] table does not name it in provides"
            )
        if not _holds(provided[key], value):
            raise PhaseError(
                f"{self._running} provides {key} as {type(value).__name__}, and its"
                f" [phases] table declares it {provided[key]}"
            )
        self._values[key] = value

    def read(self, key: str) -> object:
        """The value under *key*, a key the running step's extension reads.

        Its type is the one its provider declares.

        Raises:
            PhaseError: when the extension does not read *key*, or its
                provider wrote nothing under it.
        """
        if key not in self._steps[self._running].reads:
            raise PhaseError(
                f"{self._running} reads {key} in the {self.phase} phase, and its"
                " [phases] table does not name it in reads"
            )
        if key not in self._values:
            if key in ENGINE_KEYS.get(self.phase, {}):
                raise PhaseError(
                    f"{self._running} reads {key}, which the engine running the"
                    f" {self.phase} phase did not give it"
                )
            provider = next(
                (name for name, step in self._steps.items() if key in step.provides),
                "its provider",
            )
            raise PhaseError(
                f"{self._running} reads {key}, which {provider} has not provided in"
                f" the {self.phase} phase"
            )
        return self._values[key]

    def result(self, key: str) -> object | None:
        """The value under *key* once the phase ran, for the engine that ran it.

        None when neither a step nor the engine wrote one.
        """
        return self._values.get(key)


def phase_steps(
    phase: str, members: tuple[str, ...], lookup: Lookup
) -> dict[str, DeclaredPhase]:
    """Each of *members* that adds steps to *phase*, to its steps, in their order."""
    found: dict[str, DeclaredPhase] = {}
    for name in members:
        declared = lookup(name)
        if declared is not None and phase in declared.phases:
            found[name] = declared.phases[phase]
    return found


def _providers(steps: Mapping[str, DeclaredPhase]) -> dict[str, list[str]]:
    """Each key the steps provide, to the extensions that provide it."""
    found: dict[str, list[str]] = {}
    for name, step in steps.items():
        for key in step.provides:
            found.setdefault(key, []).append(name)
    return found


def phase_order(steps: Mapping[str, DeclaredPhase], lookup: Lookup) -> tuple[str, ...]:
    """The extensions of *steps* in the order the phase walks them.

    A reader after the provider of each key it reads, then ``before``
    and ``after``, ties alphabetical; requirements order nothing here.

    Raises:
        OrderCycle: when the order has no start.
    """
    from livery.workshop._composition import order

    providers = _providers(steps)
    edges = [
        (provider, reader)
        for reader, step in steps.items()
        for key in step.reads
        for provider in providers.get(key, ())
    ]
    return order(tuple(steps), lookup, requires=False, edges=edges)


def phase_problems(
    phase: str, steps: Mapping[str, DeclaredPhase], lookup: Lookup
) -> list[str]:
    """Why *steps* cannot run as *phase*; empty when they can.

    Two extensions providing one key, a key a step reads and nobody
    provides, and an order with no start.
    """
    from livery.workshop._composition import OrderCycle

    problems: list[str] = []
    engine = ENGINE_KEYS.get(phase, {})
    providers = _providers(steps)
    for key, names in sorted(providers.items()):
        if key in engine:
            verb = "provides" if len(names) == 1 else "provide"
            problems.append(
                f"the {phase} phase: {' and '.join(sorted(names))} {verb} {key},"
                " which the engine running the phase provides; an extension reads it"
            )
        elif len(names) > 1:
            problems.append(
                f"the {phase} phase: {' and '.join(sorted(names))} both provide"
                f" {key}; one extension of a package provides a key"
            )
    for name, step in steps.items():
        for key in step.reads:
            if key not in providers and key not in engine:
                problems.append(
                    f"the {phase} phase: {name} reads {key}, which no extension of"
                    " the package provides"
                )
    try:
        phase_order(steps, lookup)
    except OrderCycle as error:
        cycle = " before ".join((*error.members, error.members[0]))
        problems.append(
            f"the {phase} phase: its steps' order has no start: {cycle}; remove a"
            " key read, a before or an after among them"
        )
    return problems


def run_phase(
    phase: str,
    package: Package,
    root: Path,
    *,
    lookup: Lookup | None = None,
    mains: frozenset[str] | None = None,
    executable: str = "",
    arguments: tuple[str, ...] = (),
    inputs: Mapping[str, object] | None = None,
) -> PhaseContext:
    """Run *phase* over *package*: each pre, then each main, then each post reversed.

    The steps are those of the package's set
    ([livery.workshop._composition.package_set][]), each extension's
    declaration read through *lookup*, the installed ones when absent.
    *mains* names the extensions whose main runs, every one when None:
    the ``run`` phase's main is the one extension's that owns the
    executable. *executable* and *arguments* reach the steps on the
    context, and so do *inputs*, the keys the engine provides for the
    phase ([livery.workshop._phases.ENGINE_KEYS][]); a key the engine
    does not give refuses when a step reads it. A failing step stops
    the pres or the mains; every post
    whose extension's pre ran still runs, and reads the failure from
    the context. A post that fails after another failure is named on
    stderr.

    Returns:
        The context the steps shared.

    Raises:
        PhaseError: when the phase's steps cannot run as declared, or
            *inputs* holds a key the engine does not provide for the
            phase or a value of another type, before any step runs.
        Exception: the first step's exception, raised again after every
            post that was due ran.
    """
    from livery.workshop._composition import package_set

    if lookup is None:
        from livery.workshop._extensions import installed_declaration

        lookup = installed_declaration
    given = dict(inputs or {})
    engine = ENGINE_KEYS.get(phase, {})
    for key, value in given.items():
        if key not in engine:
            known = ", ".join(sorted(engine)) or "nothing"
            raise PhaseError(
                f"{package.path}: the engine gives {key} to the {phase} phase, which"
                f" takes {known} from it"
            )
        if not _holds(engine[key], value):
            raise PhaseError(
                f"{package.path}: the engine gives {key} to the {phase} phase as"
                f" {type(value).__name__}, which the phase takes as {engine[key]}"
            )
    members = package_set(package.extensions, lookup)
    steps = phase_steps(phase, members, lookup)
    problems = phase_problems(phase, steps, lookup)
    if problems:
        raise PhaseError(f"{package.path}: " + "; ".join(problems))
    walk = phase_order(steps, lookup)
    ctx = PhaseContext(
        phase,
        package,
        root,
        executable=executable,
        arguments=arguments,
        _steps=steps,
        _values=given,
    )
    entered: list[str] = []
    for name in walk:
        entered.append(name)
        if not _step(ctx, name, steps[name].pre):
            break
    if not ctx.failed:
        for name in walk:
            if mains is not None and name not in mains:
                continue
            if not _step(ctx, name, steps[name].main):
                break
    for name in reversed(entered):
        _step(ctx, name, steps[name].post)
    if ctx.failure is not None:
        raise ctx.failure
    return ctx


def _step(ctx: PhaseContext, name: str, reference: Reference | None) -> bool:
    """Call *name*'s step *reference* with *ctx*; whether it went through.

    The first failure lands on the context; a later one, a post's after
    another step failed, is named on stderr so it is never lost.
    """
    if reference is None:
        return True
    ctx._running = name
    try:
        reference(ctx)
    except Exception as error:
        if ctx.failure is None:
            ctx.failed = True
            ctx.failure = error
            ctx.failed_extension = name
        else:
            print(
                f"  note: {name}'s step {reference} failed as well, after"
                f" {ctx.failed_extension}'s: {error}",
                file=sys.stderr,
            )
        return False
    finally:
        ctx._running = ""
    return True
