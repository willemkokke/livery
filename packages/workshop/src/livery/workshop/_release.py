"""The release train's verbs: prepare, verify, wheels, the driver, and replay.

``fm release.verify`` is the train's gate, run by the release
workflow before anything builds: the tag, the ``pyproject`` version,
the ``__version__``, and the changelog must all agree, and every
``[[depends]]`` floor must resolve to a tag that has actually been
released. ``fm release.prepare`` stamps a version into those same
places, idempotently, so the human act is one command plus one tag.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import livery.toolroom.tools.api as tools
from livery.footman.api import Context, doc, fail, group

# Registers the base's release notes provider, git-cliff into
# CHANGELOG.md, until the changelog extension ships it.
from livery.workshop import _cliff as _cliff
from livery.workshop._backends import backend_for
from livery.workshop._extensions import workspace_root
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package, discover_packages
from livery.workshop._release_notes import NO_PROVIDER, release_notes
from livery.workshop._versions import derive_version

release = group("release", help="The release train's CI entries")

_TAG_RE = re.compile(r"^(packages/[^/]+)/v(\d+\.\d+\.\d+)$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


def _root() -> Path:
    root = workspace_root()
    if root is None:
        fail("no workspace: no workshop.toml above the working directory")
    return root


@dataclass(frozen=True)
class ReleasePlan:
    """One verified release: the package and the version the tag names.

    Attributes:
        package: The workspace package being released.
        version: The semver the tag carries.
    """

    package: Package
    version: str


def verify_release(
    root: Path, tag: str, *, coreleased: frozenset[str] = frozenset()
) -> ReleasePlan:
    """Check *tag* against the tree; every finding fails verbatim.

    The agreements checked: the tag names an existing package; the
    kind's own version homes (pyproject and one ``__version__`` for
    a python kind, the recipe's ``version`` for conan) carry the
    tag's version; the release notes provider, when one is mounted,
    finds the version's notes sound; and
    every ``[[depends]]`` floor names a version whose release tag
    exists, so nothing ships depending on an unreleased floor.
    """
    from livery.workshop._kinds import requires_pyproject

    match = _TAG_RE.fullmatch(tag)
    if match is None:
        fail(f"tag {tag!r} does not match packages/<pkg>/v<semver>")
    path, version = match.group(1), match.group(2)
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"tag names {path}, which is not a workspace package")
    problems = []
    declared = backend_for(package).current_version(package)
    if declared != version:
        problems.append(f"tag says {version}, the package declares {declared}")
    notes = release_notes()
    if notes is not None:
        problems += notes.verify(package, version)
    if requires_pyproject(package.kind):
        inits = list((package.directory / "src").rglob("__init__.py"))
        stamp = f'__version__ = "{version}"'
        if not any(stamp in init.read_text("utf-8") for init in inits):
            problems.append(f"no __init__.py under src/ declares {stamp}")
    released = set(GitOps(root).tags())
    for edge in package.depends:
        if not edge.floor:
            continue
        wanted = f"{edge.path}/v{edge.floor}"
        if wanted in coreleased:
            # An atomic set's intra-set floor: the wave cuts the
            # dependency's receipt before this member publishes, so
            # the tag it names exists by the time any consumer looks.
            continue
        if wanted not in released:
            problems.append(
                f"the floor on {edge.path} is {edge.floor}, and no tag"
                f" {wanted} exists: floors must name released versions"
            )
    if problems:
        fail(f"release {tag} refused:\n  " + "\n  ".join(problems))
    return ReleasePlan(package=package, version=version)


@release.task(name="verify", hidden=True)
def release_verify(
    tag: Annotated[str, doc("the release tag, packages/<pkg>/v<semver>")],
) -> None:
    """Verify *tag* against the tree; the train's gate before building.

    In GitHub Actions the verified package name is appended to
    ``$GITHUB_OUTPUT`` as ``package=<name>`` for the build step.
    """
    plan = verify_release(_root(), tag)
    print(f"  verified: {plan.package.name} {plan.version} from {tag}")
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(f"package={plan.package.name}\n")


def _last_released(root: Path, package: Package) -> str:
    """The newest released version *package*'s tags carry, or empty.

    Tags are the release identity and its receipt; a stamped version
    without its tag is an unfinished release, not a finished one.
    """
    from livery.workshop._git_ops import GitOps
    from livery.workshop._update import latest_released

    return latest_released(GitOps(root).tags()).get(package.path, "")


def prepare_release(root: Path, path: str, version: str = "") -> list[str]:
    """Stamp a release into *path*'s places; what changed.

    Without *version*, it is derived from the conventional commits
    under the package's paths since its last release tag
    (livery.workshop._versions), and the release notes provider
    writes the entry the commits earn. A given *version* wins and
    gets an empty entry for the human to write. With no provider
    mounted, the version is stamped and no notes are written.
    Idempotent either way: a place already carrying the version is
    left alone.
    """
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"{path} is not a workspace package")
    entry_body = ""
    notes = release_notes()
    if not version:
        derived = derive_version(root, package)
        released = _last_released(root, package)
        if not derived or derived == released:
            # The derivation answers with the last tag's version when
            # nothing unreleased touches the package. The tag is the
            # receipt (never the stamped pyproject: a stamp can land
            # without its release cutting, and judging by it would
            # strand that release forever). Releasing again would
            # republish the same code under a version the index
            # already has.
            print(f"  nothing to release: no unreleased commits touch {path}")
            return []
        version = derived
        entry_body = notes.entry(root, package, version) if notes else ""
        since = released or "the beginning"
        print(f"  derived {version} from the commits since {since}")
    if not _SEMVER_RE.fullmatch(version):
        fail(f"version {version!r} is not <major>.<minor>.<patch>")
    if notes and not entry_body and version != _last_released(root, package):
        # An explicitly passed version regenerates a stranded entry
        # too: the driver hands prepare the derived version, and a
        # heading without its tag under-documents what actually
        # ships either way.
        entry_body = notes.entry(root, package, version)
    changed = backend_for(package).stamp_version(package).stamp(version)
    if notes is not None:
        changed += notes.record(package, version, entry_body)
    else:
        print(f"  {NO_PROVIDER}")
    lock = root / "uv.lock"
    if changed and lock.is_file():
        # The lock records every member's version, so a stamp without
        # a refresh leaves the lock claiming the old one: the first
        # sync after the release rewrites it, and the dirty tree then
        # blocks the train's own re-run. Refreshing here puts the lock
        # line inside the commit that stamps the version.
        before_lock = lock.read_bytes()
        result = tools.uv.opts(cwd=root, nofail=True, recorded=False)("lock")
        if result.code != 0:
            fail(
                f"uv lock after stamping {version} failed:"
                f"\n{result.stdout}{result.stderr}"
            )
        if lock.read_bytes() != before_lock:
            changed.append("uv.lock")
    return changed


@release.task(name="prepare", hidden=True)
def release_prepare(
    path: Annotated[str, doc("the package, e.g. packages/workshop")],
    version: Annotated[str, doc("the semver to stamp; empty derives it")] = "",
) -> None:
    """Stamp a release: version derived from the commits unless given.

    The derived path reads what the unreleased commits earn, and the
    release notes provider writes the entry they make, for review. A
    package with nothing unreleased is refused rather than given a new
    number. A given version wins and
    leaves the entry for the human. ``fm release.verify`` is the
    check that everything agrees before the tag is cut.
    """
    for name in prepare_release(_root(), path, version):
        print(f"  stamped: {name}")


@release.task(name="replay")
def release_replay(
    member: Annotated[str, doc("the member's directory under packages/")],
    *,
    python: Annotated[
        str, doc("the interpreter version for the plain environment")
    ] = "",
    extras: Annotated[str, doc("extras to install with the wheel, comma-joined")] = "",
    report: Annotated[
        bool, doc("file or extend the marker issue on red; the default inside CI")
    ] = False,
) -> None:
    """Replay a member's latest released wheel against its own tests.

    The pairing a consumer gets: the wheel the registry serves,
    installed into a plain environment beside the tests at the
    release tag, imported from site-packages. The nightly point
    schedules it through ``[[ci.schedule]]``; by hand it answers the
    same. A red replay fails, and inside CI (or with ``--report``)
    files or extends the marker issue through the forge first.
    """
    import sys

    from livery.forge.api import SimpleRegistry
    from livery.workshop._registries import resolve_registry
    from livery.workshop._replay import replay_flow
    from livery.workshop._state import run_context

    root = _root()
    packages = {p.directory.name: p for p in discover_packages(root)}
    package = packages.get(member)
    if package is None:
        fail(
            f"no member {member!r} under packages/; the members are"
            f" {', '.join(sorted(packages)) or 'none'}"
        )
    # The target's url is the read index itself, the wave's probe
    # reads it the same way; the install points uv at the same index.
    target = resolve_registry(root, "python")
    registry = SimpleRegistry(target.url, token=target.token)
    repo = None
    if report or run_context() is not None:
        from livery.workshop._forge_lane import this_repository

        repo = this_repository(root)
    replay_flow(
        root,
        GitOps(root),
        package,
        python=python or f"{sys.version_info.major}.{sys.version_info.minor}",
        registry=registry,
        index=target.url,
        extras=extras,
        repo=repo,
    )


@release.task(name="wheels", hidden=True)
def release_wheels(
    ctx: Context,
    ref: Annotated[str, doc("the release squash; empty means HEAD")] = "",
) -> None:
    """Build this platform's native artifacts for the squash's members.

    One per-platform matrix job runs this before the wave, in three
    steps. Each conan member is created into this leg's cache and
    saved beside its wheels, one file per host, which is how a forge
    with no conan registry carries the package. Then cibuildwheel
    builds every interpreter of the workspace's python matrix for
    this platform (CIBW_BUILD widened past the local
    one-interpreter narrowing, both linux libc flavours kept),
    resolving each conan member from the cache the first step
    filled. Then every declared floor on a conan member is proved
    with its own build. The artifact upload collects each
    ``dist/``, and the wave publishes the union with ``--prebuilt``.
    A squash with no native member prints so and builds nothing, so
    the matrix job stays green on a pure release.
    """
    from livery.workshop._backends import _cpp_conan, _python_nanobind, backend_for
    from livery.workshop._kinds import kind_for
    from livery.workshop._publish import discover_release
    from livery.workshop._pythons import python_matrix
    from livery.workshop._wheels import cibw_build_set

    root = _root()
    git = GitOps(root)
    resolved_ref = ref or git.head_sha()
    epoch = int(git._run("log", "-1", "--format=%ct", resolved_ref).strip() or "0")
    members = discover_release(root, git, resolved_ref)
    released = {package.path: version for package, version in members}
    conan_members = [
        (package, version)
        for package, version in members
        if kind_for(package.kind).artifact == "conan"
    ]
    native = [
        package
        for package, _version in members
        if kind_for(package.kind).wheel_identity == "platform"
    ]
    if not native and not conan_members:
        print("  no native members in this release; nothing to build")
        return
    for package, version in conan_members:
        # The workspace registers each conan member editable, which
        # points a consumer at the source tree. A release leg builds
        # the package the release ships, so the registration goes
        # first and the extension resolves the created package.
        _cpp_conan.forget_editable(package)
        backend_for(package).build(package, root, epoch=epoch)
        archive = _cpp_conan.save_cache(package, version, package.directory / "dist")
        print(f"  {package.name}: {archive.name}")
    # The full set for this platform: the python matrix's interpreters,
    # and both libc flavours kept (an empty CIBW_SKIP reads as no skip,
    # and its presence stops the local narrowing's setdefault). Set on
    # the task's own environment, which every build child inherits; a
    # write to os.environ here would be scoped the same way and is
    # refused as an environment write meant to travel sideways.
    ctx.env.setdefault("CIBW_BUILD", cibw_build_set(python_matrix(root)))
    ctx.env.setdefault("CIBW_SKIP", "")
    for package in native:
        dist = backend_for(package).build(package, root, epoch=epoch)
        wheels = ", ".join(sorted(w.name for w in dist.glob("*.whl")))
        print(f"  {package.name}: {wheels}")
    for package in native:
        _python_nanobind.floor_legs(package, root, released, epoch=epoch)


@release.task(name="driver", hidden=True)
def release_driver(
    workshop: Annotated[
        str,
        doc(
            "a released livery-workshop version to drive the wave; empty keeps"
            " the checkout's"
        ),
    ] = "",
) -> None:
    """Install the released workshop *workshop* names over the checkout's own.

    The wave's recovery for a run whose own workshop was the fault: a
    re-dispatch names a released driver, and this step installs it
    before the wave's verbs run, so they run under it. Empty, the
    checkout's workshop drives the wave and nothing is installed; a
    re-run then does what the first run did.
    """
    import livery.toolroom.tools.api as tools

    if not workshop:
        print("  driver: the checkout's own workshop drives the wave")
        return
    from livery.workshop._ci_generate import DRIVER_DIST

    tools.uv("pip", "install", f"{DRIVER_DIST}=={workshop}")
    print(f"  driver: {DRIVER_DIST} {workshop} installed over the checkout's")
