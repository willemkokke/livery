"""The per-suite coverage store: a skipped suite's data, keyed by its closure.

Coverage stays global under the affected mode. When a check leg runs
a package's test suite, the suite's measured lines are stamped on the
store under the leg, the package, and the identity of the package's
dependency closure. When a later leg skips that suite because nothing
in its closure changed, the gate job pulls the stamped lines into the
union instead, and the floors are judged on the same global union a
full run produces. A leg skips a suite only when the store holds its
data for this closure, so a miss runs the suite fresh and the store
needs no backfill.

The unit is the suite that ran, never the package covered: a suite
executes lines across packages (a dependant's tests run its
dependencies), so the stored lines are filtered to the files of the
suite's closure, the files whose identity the key names. Reach for
[livery.workshop._coverage_store.find][] to read a suite's data and
[livery.workshop._coverage_store.stamp][] to write it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from livery.workshop._git_ops import GitError, GitOps
from livery.workshop._packages import Package
from livery.workshop._state import Keyed, RunContext, slug

#: The entry shape; another schema reads as a miss, named.
SCHEMA = 1

#: The newest entries kept per suite and leg. A suite's closure changes
#: with every commit that touches it, so the store holds the recent
#: history of one branch's tips, not an archive.
WINDOW = 6

#: The root files whose identity every suite's key carries: a pin
#: change is a dependency change for every suite, whichever package
#: the closure walks.
ROOT_PINS = ("pyproject.toml", "uv.lock")

#: The workspace's own test directory, a stored unit like a package's
#: suite. Its tests reach any package, so its identity is the whole
#: tree, and every leg that runs a suite runs it.
WORKSPACE_TESTS = "tests"


def workspace_suite(root: Path) -> Package | None:
    """The workspace's own tests as a unit, or ``None`` when it has none."""
    directory = root / WORKSPACE_TESTS
    if not directory.is_dir():
        return None
    return Package(
        directory=directory,
        path=WORKSPACE_TESTS,
        name="workspace-tests",
        type="workspace",
        depends=(),
    )


def current_keys(root: Path) -> set[tuple[str, ...]] | None:
    """The keys the current matrix produces: each check leg with each stored unit.

    The janitor drops a ref outside this set, so a leg the matrix no
    longer produces, or a unit that no longer exists, stops holding
    entries. ``None`` when the contract or the packages cannot be
    read, and then nothing is dropped.
    """
    from livery.workshop._backends._python import units_of
    from livery.workshop._packages import discover_packages
    from livery.workshop._points import check_legs

    try:
        packages = discover_packages(root)
        legs = check_legs(root)
        units = units_of(root, packages)
    except (Exception, SystemExit):
        return None
    return {(slug(leg), slug(unit.name)) for leg in legs for unit in units}


#: The family: one series per check leg and suite, its entries named
#: by their moment and closure; a series of a leg or unit the current
#: matrix no longer produces is the janitor's to drop.
COVERAGE = Keyed(
    "coverage", ("leg", "package"), window=WINDOW, schema=SCHEMA, current=current_keys
)


@dataclass(frozen=True)
class Stored:
    """One suite's stored measurement.

    Attributes:
        leg: The check leg that measured it (``check-ubuntu-latest-3.14``).
        package: The suite's package path (``packages/forge``).
        closure: The closure identity the measurement is keyed by.
        run: The run that measured it.
        sha: The commit that run checked out.
        files: Measured lines per file, relative to the workspace root.
    """

    leg: str
    package: str
    closure: str
    run: str
    sha: str
    files: dict[str, list[int]]


def suite_ref(leg: str, package: Package) -> str:
    """The ref holding *package*'s suite data as measured on *leg*."""
    return COVERAGE.series(leg, package.name).ref


def closure(packages: tuple[Package, ...], package: Package) -> tuple[Package, ...]:
    """*package* and every package it depends on, transitively, in path order.

    An edge naming a path no package has is skipped: the workspace
    lint refuses such an edge before any gate runs, and the identity
    here is of the packages that exist.
    """
    by_path = {item.path: item for item in packages}
    seen: dict[str, Package] = {}
    pending = [package]
    while pending:
        current = pending.pop()
        if current.path in seen:
            continue
        seen[current.path] = current
        pending.extend(
            by_path[edge.path] for edge in current.depends if edge.path in by_path
        )
    return tuple(seen[path] for path in sorted(seen))


def closure_id(git: GitOps, packages: tuple[Package, ...], package: Package) -> str:
    """The identity of *package*'s suite inputs at ``HEAD``.

    A digest over the tree ids of the package's closure directories
    and the blob ids of `ROOT_PINS`, so an identical identity means
    identical code and identical dependencies: the argument that lets
    the suite's run be skipped at all. A root pin the tree lacks
    contributes nothing.

    The workspace's own tests reach any package, so their identity is
    every package's tree, their own directory's tree, and the root
    pins: prose leaves it untouched, and a root configuration change
    forces a full run anyway, which stores the unit afresh.

    Raises:
        GitError: When a closure directory is not in ``HEAD``.
    """
    parts: list[str] = []
    if package.path == WORKSPACE_TESTS:
        for member in sorted(packages, key=lambda item: item.path):
            parts.append(f"{member.path}={git.object_id(f'HEAD:{member.path}')}")
        parts.append(f"{WORKSPACE_TESTS}={git.object_id(f'HEAD:{WORKSPACE_TESTS}')}")
        for pin in ROOT_PINS:
            try:
                parts.append(f"{pin}={git.object_id(f'HEAD:{pin}')}")
            except GitError:
                continue
        return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
    for member in closure(packages, package):
        parts.append(f"{member.path}={git.object_id(f'HEAD:{member.path}')}")
    for pin in ROOT_PINS:
        try:
            parts.append(f"{pin}={git.object_id(f'HEAD:{pin}')}")
        except GitError:
            continue
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def in_closure(packages: tuple[Package, ...], package: Package, filename: str) -> bool:
    """Whether *filename* (workspace-relative) belongs to *package*'s closure.

    Every file belongs to the workspace tests' closure.
    """
    if package.path == WORKSPACE_TESTS:
        return True
    roots = tuple(member.path + "/" for member in closure(packages, package))
    return filename.replace("\\", "/").startswith(roots)


def _entry_name(when: datetime, closure_key: str) -> str:
    """The entry's file name: its moment to the microsecond, then its closure.

    A read picks the newest entry for a closure by name, so the moment
    leads and is fine enough that two stamps never share a name.
    """
    return f"{when.strftime('%Y%m%dT%H%M%S.%fZ')}--{closure_key}"


def stamp(
    root: Path,
    run: RunContext,
    *,
    leg: str,
    package: Package,
    closure_key: str,
    sha: str,
    files: dict[str, list[int]],
) -> str:
    """Store *package*'s suite lines for *closure_key* on *leg*; ``""`` or the reason.

    Only a CI run writes. The entry is named by its time and its
    closure, so the window keeps the newest measurements and a read
    picks the newest entry for a closure.
    """
    if not leg:
        return "refusing: the leg has no label, so the measurement has no key"
    entry = {
        "leg": leg,
        "package": package.path,
        "closure": closure_key,
        "run": run.run_id,
        "sha": sha,
        "forge": run.forge,
        "files": {name: sorted(lines) for name, lines in sorted(files.items())},
    }
    return COVERAGE.series(leg, package.name).put(
        root,
        {_entry_name(datetime.now(UTC), closure_key): entry},
        message=f"coverage: {package.path} on {leg} by run {run.run_id}",
    )


def find(
    root: Path, *, leg: str, package: Package, closure_key: str
) -> tuple[Stored | None, str]:
    """The newest stored measurement of *package* on *leg* for *closure_key*.

    Returns ``(None, "")`` for a plain miss (no leg label, no ref,
    no entry for the closure) and ``(None, reason)`` when the store
    could not be read or the entry is unreadable, so a caller can
    fall back on either and still print why.
    """
    if not leg:
        return None, ""
    found = COVERAGE.series(leg, package.name).rows(root)
    if found.failed:
        return None, found.reason
    suffix = f"--{closure_key}"
    rows = {row.name: row.data for row in found.rows if row.name.endswith(suffix)}
    bad = {item.name: item.why for item in found.skipped if item.name.endswith(suffix)}
    if not rows and not bad:
        return None, ""
    # The names lead with the moment, so the newest entry sorts last.
    # A newest entry that is not a row is a reason, never a miss: the
    # leg runs the suite fresh and its stamp replaces the entry.
    newest = max([*rows, *bad])
    if newest in bad:
        return None, f"the entry {newest}: {bad[newest]}"
    entry = rows[newest]
    raw = entry.get("files")
    if not isinstance(raw, dict):
        return None, f"the entry {newest} carries no files"
    files = {
        str(name): [int(line) for line in lines]
        for name, lines in raw.items()
        if isinstance(lines, list)
    }
    return (
        Stored(
            leg=str(entry.get("leg", leg)),
            package=str(entry.get("package", package.path)),
            closure=str(entry.get("closure", closure_key)),
            run=str(entry.get("run", "")),
            sha=str(entry.get("sha", "")),
            files=files,
        ),
        "",
    )
