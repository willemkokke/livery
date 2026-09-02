"""Dev releases: the branch decides, the wheel comes from the branch.

``workflow.release`` on a main-family branch runs the release
train; on any other branch it is the dev act, this module: a wheel
built straight from the branch at a dev version, no reserved
branch, no PR, no tags. Publishing goes only to the configured
custom index; without one the run is local, the wheel in ``dist/``
and the publish skipped with the teaching. PyPI is never the
fallback.
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import footman
from footman import fail

from livery.workshop import _cliff
from livery.workshop._backends import _python
from livery.workshop._brand import runner_prog
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package
from livery.workshop._publish import publish_wheels
from livery.workshop._update import latest_released

#: The environment variable naming the custom index dev wheels go to.
INDEX_VAR = "LIVERY_PUBLISH_INDEX"

# The dev grammar's display form: 1.2.3-dev.branch.name.N+sha.date
_SEMVER_DEV = re.compile(
    r"^(?P<base>\d+\.\d+\.\d+)"
    r"-dev\.(?P<branch>.+?)\.(?P<distance>\d+)"
    r"(?:\+(?P<local>.+))?$"
)


@dataclass(frozen=True)
class DevPlan:
    """One package's derived dev release: the package and its version."""

    package: Package
    version: str


def sanitise_branch(branch: str) -> str:
    """The branch name as version-segment identifiers.

    ``/`` becomes ``.``; every character outside ``[0-9A-Za-z-.]``
    is dropped. ASCII only: PEP 440's local segment accepts nothing
    wider, and ``str.isalnum`` alone would wave Unicode through into
    a version the build backend refuses untaught.
    """
    return "".join(
        c
        for c in branch.replace("/", ".")
        if (c.isalnum() and c.isascii()) or c in "-."
    )


def semver_to_pep440(semver: str) -> str:
    """The dev grammar's PEP 440 form; anything else passes through.

    ``1.2.3-dev.feat.x.4+abc1234.20260901`` becomes
    ``1.2.3.dev4+feat.x.abc1234.20260901``: the branch moves into
    the local segment, because PEP 440's public half accepts only
    ``devN``. A build backend refuses the display form outright, so
    every stamp goes through this.
    """
    match = _SEMVER_DEV.fullmatch(semver)
    if match is None:
        return semver
    base = match.group("base")
    branch = match.group("branch")
    distance = match.group("distance")
    local = match.group("local")
    tail = f"{branch}.{local}" if local else branch
    return f"{base}.dev{distance}+{tail}"


def describe_distance(git: GitOps, package: Package) -> tuple[int, str]:
    """Commits since *package*'s last release tag, and the short sha.

    ``(0, sha)`` on the tag itself; with no tag at all, the whole
    history's count. Position only: the age marker in a dev version,
    not the content check (that is the unchanged refusal).
    """
    described = git._run(
        "describe", "--tags", "--long", "--match", f"{package.path}/v*"
    ).strip()
    if described:
        parts = described.rsplit("-", 2)
        if len(parts) == 3:
            return int(parts[1]), parts[2].lstrip("g")
    count = git._run("rev-list", "--count", "HEAD").strip()
    sha = git._run("rev-parse", "--short", "HEAD").strip()
    return (int(count) if count.isdigit() else 0), (sha or "0000000")


def dev_version(root: Path, git: GitOps, package: Package, *, stamp: str = "") -> str:
    """Derive *package*'s dev version, refusing an unchanged package.

    The refusal is content, not position, and judged against the
    release *tag*, never the stamped file version: HEAD can be far
    past a package's tag with no commit touching it, and git-cliff
    then answers the released version back. Building that content
    under ``<released>.devN`` mints a number that sorts below the
    release it repeats, so no floor naming the release can ever
    resolve it. A stamped-ahead ``pyproject.toml`` (a prepared bump
    whose tag is not cut yet) is not a refusal: the tag is the
    record of what was released.

    The date *stamp* rides in the local segment so a dev pin's age
    reads offline from a lock file; ``.dirty`` marks a tree whose
    wheel no commit describes.
    """
    released = latest_released(git.tags()).get(package.path, "")
    derived = _cliff.bumped_version(root, package)
    if not derived:
        fail(f"git-cliff derived no version for {package.name}; see its output above.")
    if released and derived == released:
        fail(
            f"nothing unreleased touches {package.name}: a dev build here"
            f" would carry the released code under {released}.dev<N>, which"
            f" sorts below {released} and can satisfy no floor that names"
            f" it. Pin the released {released} instead, or drop"
            f" {package.directory.name} from the set."
        )
    distance, sha = describe_distance(git, package)
    when = stamp or datetime.now(UTC).strftime("%Y%m%d")
    version = (
        f"{derived}-dev.{sanitise_branch(git.current_branch())}.{distance}+{sha}.{when}"
    )
    if not git.is_clean():
        version += ".dirty"
    return version


def build_dev(root: Path, plan: DevPlan) -> Path:
    """Build the dev wheel; the tree is byte-identical afterwards.

    The version is stamped and git-cliff's unreleased excerpt is
    spliced into ``README.md`` under ``## What's New`` for the build
    only; every touched file is restored from an in-memory snapshot,
    never from git, because a dirty tree is legal here and a
    checkout would discard its edits. The index page is the only
    place the excerpt exists.
    """
    package = plan.package
    touched = [package.directory / "pyproject.toml"]
    src = package.directory / "src"
    if src.is_dir():
        touched += sorted(src.rglob("__init__.py"))
    readme = package.directory / "README.md"
    if readme.is_file():
        touched.append(readme)
    snapshots = {path: path.read_bytes() for path in touched}
    try:
        _python.stamp_version(package).stamp(semver_to_pep440(plan.version))
        excerpt = _cliff.unreleased_entry(root, package)
        if excerpt and readme.is_file():
            original = readme.read_text("utf-8")
            readme.write_text(
                f"## What's New\n\n{excerpt}\n\n---\n\n{original}",
                encoding="utf-8",
            )
        return _python.build(package, root)
    finally:
        for path, content in snapshots.items():
            path.write_bytes(content)


def dev_release(
    root: Path,
    git: GitOps,
    members: tuple[Package, ...],
    *,
    local: bool = False,
) -> None:
    """The dev act for a set: derive, confirm, build, publish.

    Every member's version derives before anything builds, so a
    refusal costs nothing. With ``local`` (or with no custom index
    configured, which degrades to the same run and says so) nothing
    leaves the machine and nothing is asked. A publish always
    confirms per member; headless, ``footman.confirm`` answers its
    default no, and the refusal teaches the explicit ``--yes``.
    """
    branch = git.current_branch()
    index = os.environ.get(INDEX_VAR, "")
    plans = tuple(
        DevPlan(package=package, version=dev_version(root, git, package))
        for package in members
    )
    publishing = not local and bool(index)
    if not local and not index:
        print(
            f"  no custom index is configured ({INDEX_VAR} is unset), so"
            " this dev release ran as --local: wheels build into each"
            " member's dist/ and the publish is skipped. Set"
            f" {INDEX_VAR} (and UV_PUBLISH_TOKEN) to publish dev wheels;"
            " PyPI is never the fallback."
        )
    for plan in plans:
        if publishing and not footman.confirm(
            f"Publish a dev release of {plan.package.name}"
            f" {plan.version} from '{branch}'?"
        ):
            if not sys.stdin.isatty():
                raise SystemExit(
                    "a dev release without a terminal needs the explicit"
                    f" --yes global (`{runner_prog()} --yes workflow.release ...`);"
                    " silence never publishes."
                )
            print(f"  skipped {plan.package.name}")
            continue
        dist = build_dev(root, plan)
        wheel = next(iter(dist.glob("*.whl")), None)
        built = wheel.name if wheel else "dist/"
        print(f"  built {plan.package.name} {plan.version} -> {built}")
        if publishing:
            publish_wheels(
                plan.package,
                index_url=index,
                token=os.environ.get("UV_PUBLISH_TOKEN", ""),
            )
            print(f"  published {plan.package.name} {plan.version} to {index}")
