"""What the engine asks of a package: build, versions, tests, publish, requirements.

The release train, the layering phase and the docs reach a package's
artifacts and manifests through these functions alone: its build, the
version its manifest declares, the files a version is written into,
writing a version, testing the built artifact in isolation, publishing
what it built, verifying a released version, its declared
requirements, writing one, the names other packages reference its code
by, and what its sources reference. A question that is a query
([livery.workshop._queries.Query][]) is answered by the package's
extensions when one of them answers it, and a build, a stamp, an
isolated test, a publish or a verification runs as the package's phase
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
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from livery.workshop._packages import Package, SourceReferences
    from livery.workshop._phases import PhaseContext
    from livery.workshop._queries import Query
    from livery.workshop._registries import RegistryTarget
    from livery.workshop._steps import StepRecord

T = TypeVar("T")


def sync(packages: Iterable[Package], root: Path) -> None:
    """Run the sync phase over *packages*: what a member needs before ``uv sync``.

    The steps are the listed extensions' (conan's profile step among
    them), and a package that lists no extension runs the phase over
    what its kind stands for ([livery.workshop._kinds.kind_stands_for][]),
    the bridge while package kinds remain. The sync phase alone takes
    that bridge: the lifecycle phases read the listed extensions, so
    the kind's backend still builds such a package, and a nanobind
    package, which consumes conan and is never a recipe, gets conan's
    profile and none of its stamp, build or publish.
    """
    from dataclasses import replace

    from livery.workshop._kinds import kind_names, kind_stands_for

    for package in packages:
        held = package
        if not package.extensions and package.kind in kind_names():
            held = replace(package, extensions=kind_stands_for(package.kind))
        _run_steps(held, "sync", root, {})


#: What the release train's build gives the build phase beside the
#: epoch: the isolated side, in the release configuration.
RELEASE_BUILD: Mapping[str, object] = {"isolated": True, "configuration": "release"}


def build(package: Package, root: Path, *, epoch: int = 0) -> Path:
    """Build *package*'s release artifacts into its ``dist/``; that directory.

    The release train's build: the build phase on its isolated side,
    in the ``release`` configuration
    ([livery.workshop._lifecycle.RELEASE_BUILD][]). *epoch* is the
    build's source date, so two builds of one tree are the same bytes;
    0 takes the build tool's own.
    """
    from pathlib import Path

    ctx = _run_steps(package, "build", root, {**RELEASE_BUILD, "epoch": epoch})
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


def has_isolated_test(package: Package) -> bool:
    """Whether *package*'s release tests its built artifact on the isolated side.

    Through a step of its set serving the test phase's isolated side
    where one is added, and its kind otherwise; a kind that builds no
    wheels has no isolated test.
    """
    return bool(_isolated_tests(package)) or (
        _optional(package, "isolated_test") is not None
    )


def isolated_test(
    package: Package, root: Path, *, resolution: str, release_dirs: tuple[Path, ...]
) -> dict[str, str]:
    """Run *package*'s test phase on its isolated side at *resolution*; what it got.

    Each distribution the run installed, to its version. *resolution*
    is ``floor``, ``latest`` or ``head``; *release_dirs* are the
    co-released set's ``dist/`` directories, the only place the run
    finds a sibling's unpublished wheel. Ask
    [livery.workshop._lifecycle.has_isolated_test][] first: a package
    with no isolated test refuses here.
    """
    from livery.footman import fail

    inputs: dict[str, object] = {
        "isolated": True,
        "resolution": resolution,
        "release-dirs": list(release_dirs),
    }
    if _isolated_tests(package):
        ctx = _run_steps(package, "test", root, inputs)
        if ctx is not None:
            resolved = cast("dict[object, object]", _provided(ctx, "resolved", dict))
            return {str(name): str(version) for name, version in resolved.items()}
    reader = _optional(package, "isolated_test")
    if reader is None:
        fail(
            f"{package.path}: nothing tests its release isolated: its extensions"
            " add no step on the test phase's isolated side, and its kind has none"
        )
    return dict(reader(package, root, release_dirs=release_dirs, resolution=resolution))


def verify(
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
    ctx = _run_steps(package, "verify", root, inputs)
    if ctx is not None:
        return cast("int", _provided(ctx, "exit-code", int))
    reader = _optional(package, "verify")
    if reader is None:
        fail(
            f"{package.path}: nothing verifies its released version: its extensions"
            " add no verify step, and its kind verifies nothing"
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

    None for a package whose kind reads no manifest, which
    ``layering.graph`` names, since it cannot compare the package's edges.
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

    What a type-completeness step verifies. A package whose kind has
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
    from livery.workshop._queries import COMPILE_COMMANDS

    answered = _extensions_answer(package, COMPILE_COMMANDS)
    if answered is not None:
        return answered
    found = _optional(package, "compile_commands")
    return None if found is None else cast("Path | None", found(package))


def plugin_modules(package: Package) -> tuple[str, ...]:
    """The modules *package* declares as footman task entry points; empty for none."""
    reader = _optional(package, "plugin_modules")
    return () if reader is None else tuple(reader(package))


def references(package: Package) -> tuple[SourceReferences, ...]:
    """What each of *package*'s sources references, one record per source.

    The ``references`` answer of the package's extensions where one of
    them answers it, and its kind backend's otherwise; the answers
    combine as their union. Empty for a package nothing answers for,
    such as one whose language no extension reads sources of. Each
    record names a file inside the package, and one extension answers
    for each file.

    Raises:
        QueryError: when an answer names a file outside the package,
            naming the extension and the file, or when two extensions
            answer for one file, naming both.
    """
    from livery.workshop._queries import REFERENCES, QueryError, answers

    found: Mapping[str, Iterable[SourceReferences]]
    if _answered(package, REFERENCES):
        found = answers(package, REFERENCES)
    else:
        reader = _optional(package, "references")
        if reader is None:
            return ()
        found = {f"the {package.kind} kind": reader(package)}
    owners: dict[str, str] = {}
    union: dict[SourceReferences, None] = {}
    for name, answered in found.items():
        for source in answered:
            if not _inside(package, source.path):
                raise QueryError(
                    f"{package.path}: {REFERENCES.name}: {name} answers for"
                    f" {source.path}, a file outside the package; an answer"
                    " names the package's own sources"
                )
            held = owners.setdefault(source.path, name)
            if held != name:
                raise QueryError(
                    f"{package.path}: {REFERENCES.name}: {held} and {name} both"
                    f" answer for {source.path}; one extension of a package"
                    " answers for each source"
                )
            union[source] = None
    return tuple(union)


def _inside(package: Package, path: str) -> bool:
    """Whether *path*, relative to the workspace root, is a file inside *package*."""
    from pathlib import PurePosixPath

    return path.startswith(f"{package.path}/") and ".." not in PurePosixPath(path).parts


def _run_steps(
    package: Package, phase: str, root: Path, inputs: Mapping[str, object]
) -> PhaseContext | None:
    """Run *phase* over *package* with the engine's *inputs*; None without steps.

    None when no extension of the package's set adds steps to the phase,
    which leaves the call to the package's kind backend: the bridge
    while package kinds remain. The steps run through the one walk
    ([livery.workshop._phases.run_phase][]), the context naming the
    package and the phase.
    """
    steps = _phase_steps(package, phase)
    if not steps:
        return None
    from livery.workshop._phases import PhaseContext, run_phase

    ctx = PhaseContext(root=root, packages=(package,), package=package, phase=phase)
    return run_phase(ctx, steps, inputs={phase: inputs})


def _isolated_tests(package: Package) -> tuple[StepRecord, ...]:
    """The steps *package*'s set adds to the test phase that serve its isolated side."""
    from livery.workshop._phases import ISOLATED, serves

    return tuple(
        record
        for record in _phase_steps(package, "test")
        if serves(record, {ISOLATED: True})
    )


def _phase_steps(package: Package, phase: str) -> tuple[StepRecord, ...]:
    """The steps *package*'s set adds to *phase*; empty for a package listing none."""
    if not package.extensions:
        return ()
    from livery.workshop._composition import package_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._phases import phase_steps

    members = package_set(package.extensions, installed_declaration)
    return phase_steps(phase, members, installed_declaration)


def _provided(ctx: PhaseContext, key: str, kind: type | tuple[type, ...]) -> object:
    """What *ctx*'s steps provided under *key*; refuses when none of them did."""
    from livery.footman import fail

    value = ctx.result(key)
    if not isinstance(value, kind):
        fail(
            f"{ctx.member.path}: the {ctx.phase} phase's steps provide no {key},"
            " which the release train reads from the phase"
        )
    return value


def _extensions_answer(package: Package, query: Query[T]) -> T | None:
    """*package*'s extensions' answer to *query*; None when none of them answers.

    The bridge while package kinds remain: a package whose extensions
    answer a query in a place asked now, at the top level or in a
    ``[for.<target>]`` table whose target is listed, takes their
    answer, and any other takes its kind backend's.
    """
    if not _answered(package, query):
        return None
    from livery.workshop._queries import answer

    return answer(package, query)


def _answered(package: Package, query: Query[T]) -> bool:
    """Whether an extension of *package*'s set answers *query* in a place asked now.

    The places are the ones the query asks (``answered_at``), read from
    the declarations alone, so deciding computes no answer.
    """
    if not package.extensions:
        return False
    from livery.workshop._composition import package_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._queries import answered_at

    for name in package_set(package.extensions, installed_declaration):
        declared = installed_declaration(name)
        if declared is not None and answered_at(package, name, declared, query.name):
            return True
    return False


def _optional(package: Package, name: str) -> Any:
    """The backend function *name* of *package*'s kind; None when it has none."""
    from livery.workshop._kinds import kind_for

    return getattr(kind_for(package.kind).backend, name, None)
