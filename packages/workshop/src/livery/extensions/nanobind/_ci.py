"""The wheels job's runners, the members it builds, and what each of its legs builds.

A package that holds nanobind names the runner labels that build its
platform wheels under ``[nanobind] wheel-platforms`` in its own
``workshop.toml``. The wheels job nanobind's declaration adds to the
release point names `wheel_runners` as its ``labels``: one leg per
label in the union over those packages. A workspace whose packages
hold no nanobind names no label, and its release renders no wheels
job.

Each leg builds the members `job_built_members` names: every package
that holds nanobind, and every package that releases a conan package.
It is nanobind's answer to the workspace's ``job-built-members``
query, which the release train reads to publish what the run
collected for those members, and the entry reads to install the ones
uv installs after the tools. `host_build` is what each leg builds; the
verb that runs it is in this extension's ``_tasks`` module.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from livery.footman import fail
from livery.workshop import (
    DISTRIBUTIONS,
    Package,
    answer,
    discover_packages,
    extension_set,
    read_contract,
)

#: The extension whose packages build platform wheels.
EXTENSION = "nanobind"

#: The package contract key naming the runner labels that build its wheels.
WHEEL_PLATFORMS_KEY = "wheel-platforms"

#: The release point's job that builds the members before the wave: the
#: name this extension's declaration gives it under ``[ci.jobs.release]``.
JOB = "wheels"

#: The ecosystem of a conan package, as the ``distributions`` query names it.
CONAN = "conan"


def holds_nanobind(package: Package) -> bool:
    """Whether *package*'s set holds nanobind: its list, or what the list requires."""
    return EXTENSION in extension_set(package)


def releases_a_conan_package(package: Package) -> bool:
    """Whether *package* releases a conan package, which each leg creates for its host.

    Its extensions answer the ``distributions`` query, which names a
    conan package for a recipe that names a reference and nothing for
    a consumer's recipe.
    """
    released = answer(package, DISTRIBUTIONS)
    return any(ecosystem == CONAN for ecosystem, _name in released)


def job_built_members(root: Path) -> dict[str, str]:
    """The members the wheels job builds before the wave, each path to the job.

    Every package that holds nanobind, whose platform wheels each leg
    builds, and every package that releases a conan package, which
    each leg creates for its host and saves beside the wheels, in
    discovery order. A workspace whose packages hold no nanobind has no
    wheels job, and names no member.
    """
    packages = discover_packages(root)
    if not any(holds_nanobind(package) for package in packages):
        return {}
    return {
        package.path: JOB
        for package in packages
        if holds_nanobind(package) or releases_a_conan_package(package)
    }


def declared_wheel_platforms(package: Package) -> list[str]:
    """The runner labels *package* declares under ``[nanobind] wheel-platforms``.

    Empty for a package that holds no nanobind and names no label.

    Raises:
        Failed: naming the package and the key, when a package that
            holds nanobind declares no labels, an empty list or a blank
            label, since the forge cannot schedule a leg on any of
            those; and when a package that holds no nanobind declares
            the key, since no leg builds its wheel. The labels are the
            package's own fact, never guessed from the check runners.
    """
    contract = read_contract(package.directory)
    table = contract.get(EXTENSION) or {}
    declared: Any = table.get(WHEEL_PLATFORMS_KEY) if isinstance(table, dict) else None
    where = f"{package.path}: [{EXTENSION}] {WHEEL_PLATFORMS_KEY}"
    if not holds_nanobind(package):
        if declared is not None:
            fail(
                f"{where} is declared on a package that holds no {EXTENSION},"
                " and no wheels job builds its wheel; remove the key"
            )
        return []
    if declared is None:
        fail(
            f"{where} is missing: a package that holds {EXTENSION} names the"
            ' runner labels that build its wheels, like ["ubuntu-latest",'
            ' "macos-latest", "windows-latest"]'
        )
    # The contract's judge holds it to a list of strings.
    if not declared:
        fail(
            f'{where} must be a non-empty list of runner labels, like ["ubuntu-latest"]'
        )
    for label in declared:
        if not label.strip():
            fail(f"{where} entry {label!r} is not a runner label")
    return [str(label) for label in declared]


def wheel_runners(root: Path) -> list[str]:
    """The wheels job's legs: every label the packages declare, once, in order.

    The packages are read in discovery order, and each package's labels
    in its own order. Empty when no package holds nanobind, so the
    release renders no wheels job.
    """
    labels: list[str] = []
    for package in discover_packages(root):
        for label in declared_wheel_platforms(package):
            if label not in labels:
                labels.append(label)
    return labels


def host_build(root: Path) -> None:
    """Build this host's native artifacts for the release at *root*'s HEAD.

    Each leg of the release's wheels job runs this before the wave,
    over the members of the release that `job_built_members` names, in
    three steps. Each of them that releases a conan package is created
    into this leg's cache and saved beside its wheels, one file per
    host, which is how a forge with no conan registry carries the
    package. Then each package that holds nanobind builds through its
    set's build phase, for the interpreters the caller's ``CIBW_BUILD``
    names, resolving each conan member from the cache the first step
    filled. Then every declared floor on a conan member is proved with
    its own build (`floor_legs`). The publish job collects each leg's
    ``dist/``, and the wave publishes the union. A release with no such
    member prints so and builds nothing, so the leg stays green on a
    pure release.
    """
    from livery import git
    from livery.extensions.conan import _package as _conan
    from livery.workshop import _lifecycle
    from livery.workshop._publish import discover_release
    from livery.workshop._queries import JOB_BUILT_MEMBERS

    resolved_ref = git.head_sha(root)
    epoch = int(git.run(root, "log", "-1", "--format=%ct", resolved_ref).strip() or "0")
    members = discover_release(root, resolved_ref)
    released = {package.path: version for package, version in members}
    built = answer(root, JOB_BUILT_MEMBERS)
    conan_members: list[tuple[Package, str]] = []
    native: list[Package] = []
    for package, version in members:
        if package.path not in built:
            continue
        if releases_a_conan_package(package):
            conan_members.append((package, version))
        else:
            native.append(package)
    if not native and not conan_members:
        print("  no native members in this release; nothing to build")
        return
    # A release leg builds the packages the release ships, so a sibling
    # resolves to the package this leg created, never to its source
    # tree: the conan workspace is set aside for every build.
    with _conan.workspace_aside(root):
        for package, version in conan_members:
            _lifecycle.build(package, root, epoch=epoch)
            dist = package.directory / "dist"
            archive = _conan.save_cache(package, version, dist)
            print(f"  {package.name}: {archive.name}")
        for package in native:
            dist = _lifecycle.build(package, root, epoch=epoch)
            wheels = ", ".join(sorted(w.name for w in dist.glob("*.whl")))
            print(f"  {package.name}: {wheels}")
        for package in native:
            floor_legs(package, root, released, epoch=epoch)


def floor_legs(
    package: Package, root: Path, released: dict[str, str], *, epoch: int = 0
) -> None:
    """Prove every floor *package* declares on a conan package, one leg each.

    A floor is a claim: the extension module compiles and its tests
    pass against that version of the library, so a consumer who
    resolves the floor gets a working package. The claim is proved
    here. Per dependency that releases a conan package: the floor's
    saved cache is restored from its own release, one wheel is built
    for this machine with the requirement replaced by the floor, and
    the package's tests run against that wheel in a fresh venv. The pin
    is a conan profile holding a `[replace_requires]` line, added to the
    ones the dependency provider already passes, so the range the
    recipe declares resolves to that version alone.

    A floor equal to the version this wave releases is the package
    the main build already linked, so that leg says so and does
    nothing. *released* is the wave's manifest, member path to
    version.

    Raises:
        Failed: when the floor's cache is not on its release, when
            the pinned build fails, or when the tests fail against
            it. A floor whose header lacks a symbol the extension
            module calls fails at the compile.
    """
    import shutil

    from livery.extensions.conan import _package as _conan
    from livery.extensions.nanobind import _build
    from livery.extensions.python import _backend as _python

    by_path = {member.path: member for member in discover_packages(root)}
    for edge in package.depends:
        dependency = by_path.get(edge.path)
        if dependency is None or not edge.floor:
            continue
        if not releases_a_conan_package(dependency):
            continue
        floor = edge.floor
        if released.get(edge.path) == floor:
            print(
                f"  {package.name}: the floor on {dependency.name} is"
                f" {floor}, the version this wave releases; the build"
                " above already linked it"
            )
            continue
        _conan.restore_from_releases(
            root, dependency.name, floor, cwd=package.directory
        )
        dist = package.directory / "build" / "floor"
        shutil.rmtree(dist, ignore_errors=True)
        profile = package.directory / "build" / "floor-profile.txt"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile.write_text(
            f"[replace_requires]\n{dependency.name}/*: {dependency.name}/{floor}\n",
            encoding="utf-8",
        )
        # A posix path: the string rides a CMake list, where a
        # Windows separator would read as an escape.
        pinned = f"{_build.conan_install_args(package)};-pr:h;{profile.as_posix()}"
        _build.build_wheels(
            package,
            root,
            dist,
            epoch=epoch,
            conan_install_args=pinned,
            one_host_wheel=True,
        )
        _python.run_isolated_test(package, root, wheels_dir=dist)
        print(f"  {package.name}: floor leg green against {dependency.name} {floor}")
