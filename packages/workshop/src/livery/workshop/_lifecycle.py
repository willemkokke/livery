"""What the engine asks of a package: build, versions, publish, requirements.

The release train, the layering check and the docs reach a package's
artifacts and manifests through these functions alone: its build, the
version its manifest declares, the files a version is written into,
writing a version, publishing what it built, its declared requirements,
writing one, the names other packages reference its code by, and the
siblings its sources use. A question that is a query
([livery.workshop._queries.Query][]) is answered by the package's
extensions when one of them answers it, and a build, a stamp or a
publish runs as the package's lifecycle phase
([livery.workshop._phases.run_phase][]) when its extensions add steps
to it; anything else, and any package whose extensions do neither, is
answered by the package's kind backend
([livery.workshop._kinds.backend_for][]). So a caller never names an
extension or a backend. A function a backend may leave out answers None
or an empty tuple when the backend has none, and so does an abstract
kind, which has no backend.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, TypeVar, cast

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from livery.workshop._packages import Neighbours, Package
    from livery.workshop._phases import PhaseContext
    from livery.workshop._queries import Query
    from livery.workshop._registries import RegistryTarget

T = TypeVar("T")


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s artifacts into its ``dist/``; that directory.

    *epoch* is the build's source date, so two builds of one tree are
    the same bytes; 0 takes the build tool's own.
    """
    from pathlib import Path

    ctx = _run_steps(package, "build", root, {"epoch": epoch})
    if ctx is not None:
        return cast("Path", _provided(ctx, "dist", Path))
    from livery.workshop._kinds import backend_for

    return backend_for(package).build(package, root, epoch=epoch)


def current_version(package: Package) -> str:
    """The version *package*'s own manifest declares."""
    from livery.workshop._queries import CURRENT_VERSION

    answered = _extensions_answer(package, CURRENT_VERSION)
    if answered is not None:
        return answered
    from livery.workshop._kinds import backend_for

    return backend_for(package).current_version(package)


def version_files(package: Package) -> tuple[Path, ...]:
    """Every file a version may be written into.

    A caller that writes a version for one build keeps these files
    first, then restores them.
    """
    from livery.workshop._queries import VERSION_FILES

    answered = _extensions_answer(package, VERSION_FILES)
    if answered is not None:
        return answered
    from livery.workshop._kinds import backend_for

    return tuple(backend_for(package).stamp_version(package).homes())


def stamp(package: Package, root: Path, version: str) -> list[str]:
    """Write *version* into *package*'s version files; the files it changed."""
    ctx = _run_steps(package, "stamp", root, {"version": version})
    if ctx is not None:
        return list(cast("list[str]", _provided(ctx, "changed", (list, tuple))))
    from livery.workshop._kinds import backend_for

    return backend_for(package).stamp_version(package).stamp(version)


def publish(
    package: Package, root: Path, *, version: str, target: RegistryTarget
) -> bool:
    """Upload *package*'s built artifacts to *target*; False when all were there.

    A re-run walks past what an earlier attempt uploaded, so False is
    a success that uploaded nothing.
    """
    from livery.workshop._registries import target_table

    inputs: dict[str, object] = {
        "version": version,
        "registry-target": target_table(target),
    }
    ctx = _run_steps(package, "publish", root, inputs)
    if ctx is not None:
        return cast("bool", _provided(ctx, "published", bool))
    from livery.workshop._kinds import backend_for

    return backend_for(package).publish_artifact(
        package, root, version=version, target=target
    )


def proves(package: Package) -> bool:
    """Whether *package*'s release proves its artifacts in isolated legs.

    Through its extensions' ``prove`` steps where they add them, and its
    kind otherwise; a kind that builds no wheels proves nothing.
    """
    return _adds_steps(package, "prove") or _optional(package, "prove") is not None


def prove(
    package: Package, root: Path, *, resolution: str, release_dirs: tuple[Path, ...]
) -> dict[str, str]:
    """One isolated leg of *package*'s release at *resolution*; what it installed.

    Each distribution the leg installed, to its version. *release_dirs*
    are the co-released set's ``dist/`` directories, the only place the
    leg finds a sibling's unpublished wheel. Ask
    [livery.workshop._lifecycle.proves][] first: a package nothing
    proves refuses here.
    """
    from livery.footman import fail

    inputs: dict[str, object] = {
        "resolution": resolution,
        "release-dirs": list(release_dirs),
    }
    ctx = _run_steps(package, "prove", root, inputs)
    if ctx is not None:
        resolved = cast("dict[object, object]", _provided(ctx, "resolved", dict))
        return {str(name): str(version) for name, version in resolved.items()}
    reader = _optional(package, "prove")
    if reader is None:
        fail(
            f"{package.path}: nothing proves its release: its extensions add no"
            " prove step, and its kind proves nothing"
        )
    return dict(reader(package, root, release_dirs=release_dirs, resolution=resolution))


def replay(
    package: Package,
    root: Path,
    *,
    tree: Path,
    version: str,
    python: str,
    index: str,
    extras: str,
) -> int:
    """Install *package*'s released *version* alone and run its tests; the exit code.

    *tree* is checked out at the release's receipt tag, *python* is the
    interpreter, *index* where the version installs from and *extras*
    the extras installed with it, comma-joined.
    """
    from livery.footman import fail

    inputs: dict[str, object] = {
        "version": version,
        "tree": tree,
        "interpreter": python,
        "index": index,
        "extras": extras,
    }
    ctx = _run_steps(package, "replay", root, inputs)
    if ctx is not None:
        return cast("int", _provided(ctx, "exit-code", int))
    reader = _optional(package, "replay")
    if reader is None:
        fail(
            f"{package.path}: nothing replays its released version: its extensions"
            " add no replay step, and its kind replays nothing"
        )
    return int(
        reader(
            package,
            tree=tree,
            version=version,
            python=python,
            index=index,
            extras=extras,
        )
    )


def declared_requirements(package: Package) -> dict[str, str] | None:
    """What *package*'s native manifest requires, each name to its constraint.

    None for a package whose kind reads no manifest, which the layering
    check names, since it cannot compare the package's edges.
    """
    from livery.workshop._queries import REQUIREMENTS

    answered = _extensions_answer(package, REQUIREMENTS)
    if answered is not None:
        return dict(answered)
    reader = _optional(package, "declared_requirements")
    return None if reader is None else reader(package)


def declare_requirement(package: Package, dependency: Package, floor: str) -> list[str]:
    """Write *dependency* at *floor* into *package*'s manifest; the files changed.

    Empty when the manifest names the dependency already.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).declare_requirement(package, dependency, floor)


def module_roots(package: Package) -> tuple[str, ...]:
    """The names other packages reference *package*'s code by; empty for none."""
    from livery.workshop._queries import MODULE_ROOTS

    answered = _extensions_answer(package, MODULE_ROOTS)
    if answered is not None:
        return answered
    roots = _optional(package, "module_roots")
    return () if roots is None else tuple(roots(package))


def public_modules(package: Package) -> tuple[str, ...]:
    """The modules that declare *package*'s public API, by import path.

    What a type-completeness check verifies. A package whose kind has
    no importable API answers nothing.
    """
    from livery.workshop._queries import PUBLIC_MODULES

    answered = _extensions_answer(package, PUBLIC_MODULES)
    if answered is not None:
        return answered
    from livery.workshop._kinds import backend_for

    return backend_for(package).public_modules(package)


def compile_commands(package: Package) -> Path | None:
    """Where *package*'s gate build writes its compilation database.

    None for a package whose build writes none. The file exists once
    the build is configured.
    """
    from livery.workshop._kinds import backend_for

    return backend_for(package).compile_commands(package)


def plugin_modules(package: Package) -> tuple[str, ...]:
    """The modules *package* declares as footman task entry points; empty for none."""
    reader = _optional(package, "plugin_modules")
    return () if reader is None else tuple(reader(package))


def referenced_siblings(package: Package, around: Neighbours) -> dict[str, str] | None:
    """The siblings *package*'s sources use and nothing accounts for, by area.

    The area is ``src`` or ``tests``, which tells a runtime edge from
    a test one. None for a package whose kind reads no references.
    """
    reader = _optional(package, "referenced_siblings")
    return None if reader is None else reader(package, around)


def _run_steps(
    package: Package, phase: str, root: Path, inputs: Mapping[str, object]
) -> PhaseContext | None:
    """Run *phase* over *package* with the engine's *inputs*; None without steps.

    None when no extension of the package's set adds steps to the phase,
    which leaves the call to the package's kind backend: the bridge
    while package kinds remain.
    """
    if not _adds_steps(package, phase):
        return None
    from livery.workshop._phases import run_phase

    return run_phase(phase, package, root, inputs=inputs)


def _adds_steps(package: Package, phase: str) -> bool:
    """Whether an extension of *package*'s set adds steps to *phase*."""
    if not package.extensions:
        return False
    from livery.workshop._composition import package_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._phases import phase_steps

    members = package_set(package.extensions, installed_declaration)
    return bool(phase_steps(phase, members, installed_declaration))


def _provided(ctx: PhaseContext, key: str, kind: type | tuple[type, ...]) -> object:
    """What *ctx*'s steps provided under *key*; refuses when none of them did."""
    from livery.footman import fail

    value = ctx.result(key)
    if not isinstance(value, kind):
        fail(
            f"{ctx.package.path}: the {ctx.phase} phase's steps provide no {key},"
            " which the release train reads from the phase"
        )
    return value


def _extensions_answer(package: Package, query: Query[T]) -> T | None:
    """*package*'s extensions' answer to *query*; None when none of them answers.

    The bridge while package kinds remain: a package whose extensions
    answer a query takes their answer, and any other takes its kind
    backend's. Only the declarations are read to decide, so no answer
    is computed twice.
    """
    if not package.extensions:
        return None
    from livery.workshop._composition import package_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._queries import answer

    for name in package_set(package.extensions, installed_declaration):
        declared = installed_declaration(name)
        if declared is not None and query.name in declared.queries:
            return answer(package, query)
    return None


def _optional(package: Package, name: str) -> Any:
    """The backend function *name* of *package*'s kind; None when it has none."""
    from livery.workshop._kinds import kind_for

    return getattr(kind_for(package.kind).backend, name, None)
