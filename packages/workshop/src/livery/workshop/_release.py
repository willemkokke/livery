"""The release train's verbs: prepare, the tag check, the driver, and verify.

``fm release.check-tag`` checks a release tag against the tree: the
tag, the ``pyproject`` version, the ``__version__``, and the changelog
must all agree, and every ``[[depends]]`` floor must resolve to a tag
that has actually been released; the release wave runs the same check
for every member before anything uploads. ``fm release.prepare``
stamps a version into those same places, idempotently.
``fm release.verify`` runs a member's verify phase: its latest
released version installed alone and tested at the release tag.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated

import livery.toolroom.tools as tools
from livery import git
from livery.footman import doc, fail, group
from livery.workshop import _lifecycle, _release_notes
from livery.workshop._extensions import workspace_root
from livery.workshop._packages import (
    Package,
    discover_packages,
    holds_runtime_language,
    release_tag,
)
from livery.workshop._versions import derive_version

release = group("release", help="The release train's CI entries")

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
    version homes its set answers (pyproject, and one ``__version__``
    for a package holding the runtime's language with a root module,
    the recipe's ``version`` for conan) carry the tag's version; the
    release notes provider, when one is mounted, finds the version's
    notes sound; and every ``[[depends]]`` floor names a version whose
    release tag exists, so nothing ships depending on an unreleased
    floor.
    """
    found = release_tag(tag)
    if found is None:
        fail(f"tag {tag!r} does not match packages/<member>/v<semver>")
    path, version = found
    packages = {package.path: package for package in discover_packages(root)}
    package = packages.get(path)
    if package is None:
        fail(f"tag names {path}, which is not a workspace package")
    problems = []
    declared = _lifecycle.current_version(package)
    if declared != version:
        problems.append(f"tag says {version}, the package declares {declared}")
    notes = _release_notes.release_notes(root)
    if notes is not None:
        problems += notes.verify(package, version)
    if holds_runtime_language(package):
        # The modules the set's stamp writes the version into: a
        # package's __init__.py. A package with none, a namespace with
        # no public names, carries its version in its manifest alone.
        modules = [
            home
            for home in _lifecycle.version_files(package)
            if home.suffix == ".py" and home.is_file()
        ]
        stamp = f'__version__ = "{version}"'
        if modules and not any(
            stamp in module.read_text("utf-8") for module in modules
        ):
            problems.append(f"no __init__.py under src/ declares {stamp}")
    released = set(git.tags(root))
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


@release.task(name="check-tag", hidden=True)
def release_check_tag(
    tag: Annotated[str, doc("the release tag, packages/<member>/v<semver>")],
) -> None:
    """Check *tag* against the tree: the release wave's check, run by hand.

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
    from livery.workshop._update import latest_released

    return latest_released(git.tags(root)).get(package.path, "")


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
    notes = _release_notes.release_notes(root)
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
    changed = _lifecycle.stamp(package, root, version)
    if notes is not None:
        changed += notes.record(package, version, entry_body)
    else:
        print(f"  {_release_notes.NO_PROVIDER}")
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
    leaves the entry for the human. ``fm release.check-tag`` is the
    check that everything agrees before the tag is cut.
    """
    for name in prepare_release(_root(), path, version):
        print(f"  stamped: {name}")


@release.task(name="verify")
def release_verify(
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
    """Verify a member's latest released wheel against its own tests.

    The member's verify phase: the pairing a consumer gets, the wheel
    the registry serves installed into a plain environment beside the
    tests at the release tag, imported from site-packages. The nightly
    point schedules it through ``[[ci.schedule]]``; by hand it answers
    the same. A red verification fails, and inside CI (or with
    ``--report``) files or extends the marker issue through the forge
    first.
    """
    import sys

    from livery.forge import SimpleRegistry
    from livery.workshop._ci_context import run_context
    from livery.workshop._registries import resolve_registry
    from livery.workshop._verify import verify_flow

    root = _root()
    packages = {p.member: p for p in discover_packages(root)}
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
    verify_flow(
        root,
        package,
        python=python or f"{sys.version_info.major}.{sys.version_info.minor}",
        registry=registry,
        index=target.url,
        extras=extras,
        repo=repo,
    )


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
    import livery.toolroom.tools as tools

    if not workshop:
        print("  driver: the checkout's own workshop drives the wave")
        return
    from livery.workshop._ci_generate import DRIVER_DIST

    tools.uv("pip", "install", f"{DRIVER_DIST}=={workshop}")
    print(f"  driver: {DRIVER_DIST} {workshop} installed over the checkout's")
