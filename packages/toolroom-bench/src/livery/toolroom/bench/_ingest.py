"""Ingest verification: structural checks on a version's deployments, from any machine.

A new version is checked against the deployment already known before
its pull request merges. The checks need no execution, so every host
of the version is checked from whichever machine runs the refresh:
each host's artifact is staged through the store, unpacked and
looked at, and compared with the version before it.

The nine checks, in the order `CHECKS` lists them:

- `root`: the declared root is in the archive (a stage refuses it
  otherwise, and the refusal is the finding).
- `entry-points`: every declared entry point is a file in the tree.
- `paths`: every declared path directory is in the tree.
- `shims`: every shim's target is a file in the tree.
- `env`: every env value naming a path under the install points at
  something in the tree.
- `entry-points-kept`: no entry point the previous version declared
  for the host is gone.
- `hosts-kept`: no host the previous version had lost its build.
- `exclusions`: every exclusion pattern matches something in the
  unpacked tree.
- `stray-executables`: no executable sits in a declared path
  directory without being a declared entry point or shim.

A finding names the check, the host and what was found. The
structural diff of the paths added and removed since the previous
version, per host, is the reviewer's summary. The executable checks,
the entry point runs and the surface extracts, need a matching host
and are the six-host point's business.
"""

from __future__ import annotations

import fnmatch
import stat
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from livery.toolroom.store import (
    PACKAGE_VAR,
    Deployment,
    Record,
    Store,
    StoreError,
    resolve,
)

CHECKS = (
    "root",
    "entry-points",
    "paths",
    "shims",
    "env",
    "entry-points-kept",
    "hosts-kept",
    "exclusions",
    "stray-executables",
)
"""The nine structural checks, by name, in the order they run."""

_WINDOWS_EXECUTABLE = (".exe", ".cmd", ".bat", ".com")


@dataclass(frozen=True)
class Finding:
    """One check that did not pass, on one host.

    Attributes:
        check: The check's name, from `CHECKS`.
        host: The host key checked; empty for a finding about the
            version as a whole.
        detail: What was found, in the words a reviewer acts on.
    """

    check: str
    host: str
    detail: str

    def __str__(self) -> str:
        where = f" on {self.host}" if self.host else ""
        return f"{self.check}{where}: {self.detail}"


@dataclass(frozen=True)
class HostDiff:
    """The paths one host's tree gained and lost since the previous version."""

    host: str
    added: tuple[str, ...]
    removed: tuple[str, ...]


@dataclass(frozen=True)
class Report:
    """What the checks found for one version of one tool.

    Attributes:
        name: The tool.
        version: The version checked.
        previous: The version it was compared with; empty for the
            first version with a host.
        hosts: The hosts checked, in the record's order.
        findings: Every check that did not pass; empty means every
            check passed on every host.
        diffs: Per host, the structural diff against the previous
            version; a host the previous version lacked diffs against
            nothing, so every path is added.
    """

    name: str
    version: str
    previous: str
    hosts: tuple[str, ...]
    findings: tuple[Finding, ...] = ()
    diffs: tuple[HostDiff, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        """Whether every check passed on every host."""
        return not self.findings


def previous_with_hosts(record: Record, version: str) -> str:
    """The nearest earlier version that has a host, or empty when there is none."""
    before = ""
    for delta in record.deltas:
        if delta.version == version:
            return before
        if delta.hosts:
            before = delta.version
    return before


def verify(
    record: Record,
    version: str,
    *,
    store: Store,
    hosts: tuple[str, ...] = (),
    previous: str | None = None,
) -> Report:
    """Run the nine checks on *version* of *record* for its hosts; the report.

    Each host's artifact is staged through *store* into a scratch
    directory, which goes when the host's checks are done. *hosts*
    narrows to some of the version's hosts; *previous* names the
    version to compare with, the nearest earlier version with a host
    otherwise. A version with no host has nothing to check and passes
    with no diff.

    Raises:
        RecordError: when the record does not track *version*.
    """
    delta = record.delta_for(version)
    before = previous_with_hosts(record, version) if previous is None else previous
    wanted = tuple(host for host in delta.hosts if not hosts or host in hosts)
    findings: list[Finding] = []
    diffs: list[HostDiff] = []
    if before:
        for host in record.hosts_of(before):
            if host not in delta.hosts:
                findings.append(
                    Finding(
                        "hosts-kept",
                        host,
                        f"{before} had a build and {version} has none",
                    )
                )
    for host in wanted:
        deployment = resolve(record, version, host)
        earlier = (
            resolve(record, before, host)
            if before and host in record.hosts_of(before)
            else None
        )
        with tempfile.TemporaryDirectory(prefix=f"ingest-{record.name}-") as scratch:
            into = Path(scratch) / "tree"
            into.mkdir()
            try:
                members = store.stage(
                    record.name, record.kind, version, deployment, into
                )
            except StoreError as error:
                findings.append(Finding("root", host, str(error)))
                continue
            findings.extend(_check_tree(host, deployment, earlier, members, into))
            current = _paths_in(into)
        if earlier is not None:
            with tempfile.TemporaryDirectory(
                prefix=f"ingest-{record.name}-"
            ) as scratch:
                into = Path(scratch) / "tree"
                into.mkdir()
                try:
                    store.stage(record.name, record.kind, before, earlier, into)
                except StoreError:
                    older: set[str] = set()
                else:
                    older = _paths_in(into)
        else:
            older = set()
        diffs.append(
            HostDiff(
                host,
                tuple(sorted(current - older)),
                tuple(sorted(older - current)),
            )
        )
    return Report(record.name, version, before, wanted, tuple(findings), tuple(diffs))


def _paths_in(into: Path) -> set[str]:
    return {p.relative_to(into).as_posix() for p in into.rglob("*")}


def _check_tree(
    host: str,
    deployment: Deployment,
    earlier: Deployment | None,
    members: tuple[str, ...],
    into: Path,
) -> list[Finding]:
    """The checks over one host's unpacked tree, exclusions applied."""
    found: list[Finding] = []
    windows = host.startswith("windows")
    for entry in deployment.entry_points:
        if not (into / entry).is_file():
            found.append(
                Finding(
                    "entry-points",
                    host,
                    f"entry point {entry!r} is not a file in the tree",
                )
            )
    for directory in deployment.paths:
        if not (into / directory).is_dir():
            found.append(
                Finding(
                    "paths", host, f"path directory {directory!r} is not in the tree"
                )
            )
    for link_name, target in deployment.shims.items():
        target_path = into / (f"{target}.exe" if windows else target)
        if not target_path.is_file():
            found.append(
                Finding(
                    "shims",
                    host,
                    f"shim {link_name!r} names {target!r}, which is not a file in"
                    " the tree",
                )
            )
    for key, value in deployment.env.items():
        if PACKAGE_VAR not in value:
            continue
        relative = value.replace(PACKAGE_VAR, "").lstrip("/\\")
        if relative and not (into / relative).exists():
            found.append(
                Finding(
                    "env", host, f"{key} names {relative!r}, which is not in the tree"
                )
            )
    if earlier is not None:
        gone = [
            entry
            for entry in earlier.entry_points
            if entry not in deployment.entry_points
        ]
        if gone:
            found.append(
                Finding(
                    "entry-points-kept",
                    host,
                    "entry point(s) the previous version declared are gone: "
                    + ", ".join(gone),
                )
            )
    for pattern in deployment.exclude:
        if not any(fnmatch.fnmatch(member, pattern) for member in members):
            found.append(
                Finding(
                    "exclusions",
                    host,
                    f"exclusion {pattern!r} matches nothing in the tree",
                )
            )
    declared = set(deployment.entry_points) | set(deployment.shims)
    for directory in deployment.paths:
        base = into / directory
        if not base.is_dir():
            continue
        for path in sorted(base.iterdir()):
            relative = path.relative_to(into).as_posix()
            if relative in declared or path.is_dir():
                continue
            if _looks_executable(path, windows=windows):
                found.append(
                    Finding(
                        "stray-executables",
                        host,
                        f"{relative!r} is executable in path directory {directory!r}"
                        " and no annotation names it",
                    )
                )
    return found


def _looks_executable(path: Path, *, windows: bool) -> bool:
    """Whether a file reads as executable for the host.

    The suffix decides for a Windows host and the mode bit elsewhere.

    A Windows artifact inspected on another platform carries its
    suffixes; a POSIX artifact inspected on Windows carries no bit, so
    the bit is read where it exists and the suffix stands in for it.
    """
    if windows:
        return path.suffix.lower() in _WINDOWS_EXECUTABLE
    if sys.platform == "win32":
        return path.suffix.lower() in _WINDOWS_EXECUTABLE
    return bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def summary(report: Report) -> list[str]:
    """The report as lines: the verdict, each finding, and each host's diff."""
    lines = [
        f"  {report.name} {report.version}: "
        + (
            f"every check passed on {len(report.hosts)} host(s)"
            if report.passed
            else f"{len(report.findings)} finding(s)"
        )
        + (
            f", against {report.previous}"
            if report.previous
            else ", the first version with a host"
        )
    ]
    lines.extend(f"    {finding}" for finding in report.findings)
    for diff in report.diffs:
        if not diff.added and not diff.removed:
            lines.append(f"    {diff.host}: no path added or removed")
            continue
        lines.append(
            f"    {diff.host}: +{len(diff.added)} -{len(diff.removed)} path(s)"
        )
        lines.extend(f"      + {path}" for path in diff.added[:20])
        if len(diff.added) > 20:
            lines.append(f"      + ... {len(diff.added) - 20} more")
        lines.extend(f"      - {path}" for path in diff.removed[:20])
        if len(diff.removed) > 20:
            lines.append(f"      - ... {len(diff.removed) - 20} more")
    return lines
