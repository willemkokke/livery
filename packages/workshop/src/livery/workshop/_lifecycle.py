"""What the engine asks of a package: build, versions, tests, publish, requirements.

The release train, the layering phase and the docs reach a package's
artifacts and manifests through these functions alone: its build, the
version its manifest declares, the files a version is written into,
writing a version, testing the built artifact in isolation, publishing
what it built and the registry it goes to, verifying a released
version, its declared requirements, writing one, the names other
packages reference its code by, and what its sources reference. A
question is a query ([livery.workshop._queries.Query][]) the package's
set answers, the extensions it lists and those they require; a build,
a stamp, an isolated test, a publish or a verification runs as the
package's phase ([livery.workshop._phases.run_phase][]), the steps its
set adds to it. So a caller never names an extension.

A question nothing in the set answers gets the query's empty answer:
no module roots, no requirements. The version is the one question an
empty answer cannot serve, and it refuses naming the query. A phase
the set adds no step to refuses the release, naming the phase. A
package that lists no extension holds none: every question gets its
empty answer, and every phase refuses.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from livery.forge import Registry
    from livery.workshop._packages import Package, SourceReferences
    from livery.workshop._phases import PhaseContext
    from livery.workshop._registries import RegistryTarget
    from livery.workshop._steps import StepRecord


def sync(packages: Iterable[Package], root: Path) -> None:
    """Run the sync phase over the workspace at *root* holding *packages*.

    The phase is what the workspace and its members need before
    ``uv sync`` installs them: the steps the listed extensions
    registered at mount, each once over the workspace, and a step that
    judges one package at a time once for each of *packages* whose set
    holds an extension it names. A workspace step that names
    extensions runs while one of *packages* holds one of them, and not
    at all while none does. conan's profile step is one: it runs once
    while a package holds conan, a package that lists nanobind among
    them, since its set holds conan. With no step registered, the phase
    runs nothing.
    """
    from livery.workshop._phases import PhaseContext, run_registered

    run_registered(PhaseContext(root=root, packages=tuple(packages), phase="sync"))


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

    Raises:
        Failed: naming the package, its set and the phase, when no
            extension of the set adds a step to the build phase.
    """
    from pathlib import Path

    ctx = _served(package, "build", root, {**RELEASE_BUILD, "epoch": epoch})
    return cast("Path", _provided(ctx, "dist", Path))


def current_version(package: Package) -> str:
    """The version *package*'s own manifest declares.

    Raises:
        Failed: naming the package, its set and the query, when nothing
            in the set answers the version, since a release reads and
            stamps it.
    """
    from livery.footman import fail
    from livery.workshop._queries import CURRENT_VERSION, answer

    answered = answer(package, CURRENT_VERSION)
    if not answered:
        held = ", ".join(_set_of(package)) or "none"
        fail(
            f"{package.path}: its extensions ({held}) answer no"
            f" {CURRENT_VERSION.name}: none of them reads a version from the"
            " package's manifests, so the release train has no version to read"
            " or stamp. List the extension whose manifest carries the version"
        )
    return answered


def version_files(package: Package) -> tuple[Path, ...]:
    """Every file a version may be written into.

    A caller that writes a version for one build keeps these files
    first, then restores them. Empty for a package whose set names
    none.
    """
    from livery.workshop._queries import VERSION_FILES, answer

    return tuple(answer(package, VERSION_FILES))


def stamp(package: Package, root: Path, version: str) -> list[str]:
    """Write *version* into *package*'s version files; the files it changed.

    Raises:
        Failed: naming the package, its set and the phase, when no
            extension of the set adds a step to the stamp phase.
    """
    ctx = _served(package, "stamp", root, {"version": version})
    return list(cast("list[str]", _provided(ctx, "changed", (list, tuple))))


def publish(
    package: Package, root: Path, *, version: str, target: RegistryTarget
) -> bool:
    """Upload *package*'s built artifacts to *target*; False when all were there.

    A re-run walks past what an earlier attempt uploaded, so False is
    a success that uploaded nothing.

    Raises:
        Failed: naming the package, its set and the phase, when no
            extension of the set adds a step to the publish phase.
    """
    from livery.workshop._registries import target_table

    inputs: dict[str, object] = {
        "version": version,
        "registry-target": target_table(target),
    }
    ctx = _served(package, "publish", root, inputs)
    return cast("bool", _provided(ctx, "published", bool))


def distributions(package: Package) -> tuple[tuple[str, str], ...]:
    """The distributions *package* releases, each as its ecosystem and its name.

    Its set's answers to the ``distributions`` query, combined.
    """
    from livery.workshop._queries import DISTRIBUTIONS, answer

    return answer(package, DISTRIBUTIONS)


def ecosystem(package: Package) -> str:
    """The one ecosystem *package* releases to, which its registry resolves by.

    The ecosystem its distributions name (``distributions``): the release
    train publishes the package to that ecosystem's registry and probes
    it there.

    Raises:
        Failed: naming the package and its set when no extension of the
            set releases a distribution of it, and naming the ecosystems
            when its distributions name more than one, since a package
            releases to one registry.
    """
    from livery.footman import fail

    # A list, not a tuple: basedpyright narrows a tuple by the length
    # checks below and then reads the last line's index as out of range.
    named = list(dict.fromkeys(found for found, _name in distributions(package)))
    if not named:
        held = ", ".join(_set_of(package)) or "none"
        fail(
            f"{package.path}: its extensions ({held}) answer no registry: none of"
            " them releases a distribution of it, so the release train has nowhere"
            " to publish it. List the extension that releases it, or set [release]"
            " publish = false to version and tag it without an upload"
        )
    if len(named) > 1:
        fail(
            f"{package.path}: its extensions answer more than one registry,"
            f" {', '.join(named[:-1])} and {named[-1]}: each releases a"
            " distribution of it, and a package releases to one registry. List"
            " the extensions that release it to one of them"
        )
    return named[0]


def registry(package: Package) -> Registry:
    """The registry *package* releases to, as a probe of the versions it serves.

    Its set's answer to the ``registry`` query, from the extension that
    releases its distribution. The release train polls the probe until
    a version it uploaded is served.

    Raises:
        Failed: naming the package when its extensions answer no
            registry or more than one (``ecosystem``), before any probe is
            built, and naming the ecosystem when the extensions that
            release it answer no probe of its registry.
    """
    from livery.footman import fail
    from livery.workshop._queries import REGISTRY, answer

    released = ecosystem(package)
    probe = answer(package, REGISTRY)
    if not probe:
        fail(
            f"{package.path}: its extensions release it to {released} and answer"
            " no probe of that registry, so the release train cannot see what it"
            " serves"
        )
    return probe


def has_isolated_test(package: Package) -> bool:
    """Whether *package*'s release tests its built artifact on the isolated side.

    Whether its set adds a step serving the test phase's isolated side,
    as python does for the wheel it builds.
    """
    return bool(_isolated_tests(package))


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

    Raises:
        Failed: naming the package when its set adds no step on the test
            phase's isolated side.
    """
    from livery.footman import fail

    if not _isolated_tests(package):
        fail(
            f"{package.path}: nothing tests its release isolated: its extensions"
            " add no step on the test phase's isolated side"
        )
    inputs: dict[str, object] = {
        "isolated": True,
        "resolution": resolution,
        "release-dirs": list(release_dirs),
    }
    ctx = _served(package, "test", root, inputs)
    resolved = cast("dict[object, object]", _provided(ctx, "resolved", dict))
    return {str(name): str(version) for name, version in resolved.items()}


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

    Raises:
        Failed: naming the package, its set and the phase, when no
            extension of the set adds a step to the verify phase.
    """
    inputs: dict[str, object] = {
        "version": version,
        "tree": tree,
        "interpreter": python,
        "index": index,
        "extras": extras,
    }
    ctx = _served(package, "verify", root, inputs)
    return cast("int", _provided(ctx, "exit-code", int))


def declared_requirements(package: Package) -> dict[str, str]:
    """What *package*'s native manifests require, each name to its constraint.

    Empty for a package whose set reads no manifest, which declares no
    requirement: ``layering.graph`` names each of its edges on a
    sibling as missing from its manifests.
    """
    from livery.workshop._queries import REQUIREMENTS, answer

    return dict(answer(package, REQUIREMENTS))


def declared_test_requirements(package: Package) -> dict[str, str] | None:
    """What *package*'s manifests require for its tests, each name to its constraint.

    None for a package whose set answers no ``test-requirements``: the
    package has no home for its test edges, and ``layering.graph``
    judges none of them.
    """
    from livery.workshop._queries import TEST_REQUIREMENTS, answer, answers

    if not answers(package, TEST_REQUIREMENTS):
        return None
    return dict(answer(package, TEST_REQUIREMENTS))


def declare_requirement(
    package: Package, dependency: Package, floor: str, *, kind: str = "runtime"
) -> list[str]:
    """Write *dependency* at *floor* into *package*'s manifests; the files changed.

    *kind* is the edge's: a build or runtime requirement is written by
    the writers the package's set answers ``requirement-writers`` with,
    and a test requirement by those it answers
    ``test-requirement-writers`` with. Every writer is called. Each
    takes a sibling of its own ecosystem alone, so the requirement
    lands in the manifest of the dependency's ecosystem: python's
    ``pyproject.toml`` for a sibling that holds python, conan's recipe
    for a sibling that is a conan package. The files are relative to
    the package; empty when the manifests name the dependency already,
    and for a test requirement in a set with no writer of one, which
    has no home for it.

    Raises:
        Failed: when no writer of the set takes *dependency*, naming
            both sets; nothing is written then.
    """
    from livery.footman import fail
    from livery.workshop._composition import extension_set
    from livery.workshop._queries import (
        REQUIREMENT_WRITERS,
        TEST_REQUIREMENT_WRITERS,
        answer,
    )

    writers = answer(
        package, TEST_REQUIREMENT_WRITERS if kind == "test" else REQUIREMENT_WRITERS
    )
    if kind == "test" and not writers:
        return []
    taken = [
        written
        for writer in writers
        if (written := writer(package, dependency, floor)) is not None
    ]
    if not taken:
        own = ", ".join(extension_set(package)) or "no extension"
        theirs = ", ".join(extension_set(dependency)) or "no extension"
        what = "test requirement" if kind == "test" else "requirement"
        fail(
            f"{package.path} holds {own}, and no writer of its set takes a"
            f" {what} on {dependency.path}, which holds {theirs}: a writer"
            " takes a sibling of its own ecosystem alone. List an extension"
            f" whose manifest takes {dependency.path}, or drop the dependency"
        )
    return list(dict.fromkeys(name for written in taken for name in written))


def module_roots(package: Package) -> tuple[str, ...]:
    """The names other packages reference *package*'s code by; empty for none."""
    from livery.workshop._queries import MODULE_ROOTS, answer

    return tuple(answer(package, MODULE_ROOTS))


def references(package: Package) -> tuple[SourceReferences, ...]:
    """What each of *package*'s sources references, one record per source.

    The ``references`` answers of the package's set, combined as their
    union. Empty for a package nothing answers for, such as one whose
    language no extension reads sources of. Each record names a file
    inside the package, and one extension answers for each file.

    Raises:
        QueryError: when an answer names a file outside the package,
            naming the extension and the file, or when two extensions
            answer for one file, naming both.
    """
    from livery.workshop._queries import REFERENCES, QueryError, answers

    found: Mapping[str, Iterable[SourceReferences]] = answers(package, REFERENCES)
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

    None when no extension of the package's set adds steps to the
    phase. A step an extension of the set deletes is none of them. The
    steps run through the one walk
    ([livery.workshop._phases.run_phase][]), the context naming the
    package and the phase.
    """
    steps = _phase_steps(package, phase)
    if not steps:
        return None
    from livery.workshop._phases import PhaseContext, run_phase

    ctx = PhaseContext(root=root, packages=(package,), package=package, phase=phase)
    return run_phase(ctx, steps, inputs={phase: inputs})


def _served(
    package: Package, phase: str, root: Path, inputs: Mapping[str, object]
) -> PhaseContext:
    """Run *phase* over *package* as `_run_steps` does; refuse a phase with no step.

    Raises:
        Failed: naming the package, its set and the phase, when no
            extension of the set adds a step to the phase, so nothing
            does what the release asks of it.
    """
    ctx = _run_steps(package, phase, root, inputs)
    if ctx is None:
        from livery.footman import fail

        held = ", ".join(_set_of(package)) or "none"
        fail(
            f"{package.path}: its extensions ({held}) add no step to the {phase}"
            " phase, so the release has nothing to run there. List the extension"
            f" whose steps serve the {phase} phase"
        )
    return ctx


def _isolated_tests(package: Package) -> tuple[StepRecord, ...]:
    """The steps *package*'s set adds to the test phase that serve its isolated side."""
    from livery.workshop._phases import ISOLATED, serves

    return tuple(
        record
        for record in _phase_steps(package, "test")
        if serves(record, {ISOLATED: True})
    )


def _phase_steps(package: Package, phase: str) -> tuple[StepRecord, ...]:
    """The steps *package*'s set adds to *phase*, less those the set deletes.

    Empty for a package listing none.
    """
    if not package.extensions:
        return ()
    from livery.workshop._composition import extension_set
    from livery.workshop._extensions import installed_declaration
    from livery.workshop._phases import phase_steps

    return phase_steps(phase, extension_set(package), installed_declaration)


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


def _set_of(package: Package) -> tuple[str, ...]:
    """The extensions *package*'s set holds, as its queries ask them."""
    from livery.workshop._composition import extension_set

    return extension_set(package)
