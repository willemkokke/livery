"""The next version a package's unreleased commits earn.

The release train's version comes from the commit convention the
base already enforces (livery.workshop._conventional): the commits
since the package's newest receipt tag that touch its directory,
each read for a break and a feature. Before 1.0 a break or a feature
bumps the minor version and anything else the patch; from 1.0 a break
bumps the major, a feature the minor, anything else the patch. A
release commit (``chore(release)``) earns nothing, and a commit whose
subject follows no convention counts as a patch.

A package with no receipt tag yet releases at its ``[release]
baseline``, else at 0.0.0: the first release is the starting point,
not a bump from it.
"""

from __future__ import annotations

import re
from pathlib import Path

from livery.workshop._contract import load_contract
from livery.workshop._git_ops import GitOps
from livery.workshop._packages import Package

#: A subject that marks a break: ``!`` before the colon.
_BREAK_SUBJECT_RE = re.compile(r"^[A-Za-z]+(\([^)]*\))?!:")
#: A footer that marks a break.
_BREAK_FOOTER_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
#: A feature's subject.
_FEATURE_RE = re.compile(r"^feat(\([^)]*\))?!?:")
#: A release commit's subject, which earns no bump.
_RELEASE_RE = re.compile(r"^chore\(release\)")

#: The separators of one commit in the log read below.
_FIELD = "\x1f"
_RECORD = "\x1e"


def baseline(package: Package) -> str:
    """The version *package* first releases at: its declared baseline, else 0.0.0."""
    contract = load_contract(package.directory / "workshop.toml")
    release = contract.get("release") or {}
    declared = release.get("baseline", "") if isinstance(release, dict) else ""
    return str(declared) or "0.0.0"


def bump(version: str, messages: list[tuple[str, str]]) -> str:
    """*version* bumped by the (subject, body) pairs *messages*, or unchanged.

    Raises:
        ValueError: when *version* is not ``major.minor.patch``.
    """
    major, minor, patch = (int(part) for part in version.split("."))
    counted = [m for m in messages if not _RELEASE_RE.match(m[0])]
    if not counted:
        return version
    breaking = any(
        _BREAK_SUBJECT_RE.match(subject) or _BREAK_FOOTER_RE.search(body)
        for subject, body in counted
    )
    feature = any(_FEATURE_RE.match(subject) for subject, _body in counted)
    if breaking and major >= 1:
        return f"{major + 1}.0.0"
    if breaking or feature:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def derive_version(root: Path, package: Package, *, released: str = "") -> str:
    """The version *package*'s unreleased commits earn, bare semver.

    *released* is the newest receipt's version, read from the tags
    when empty. With no receipt the answer is the baseline. With one,
    an answer equal to it means nothing unreleased touches the
    package, which the caller reports.
    """
    git = GitOps(root)
    if not released:
        from livery.workshop._update import latest_released

        released = latest_released(git.tags()).get(package.path, "")
    if not released:
        return baseline(package)
    tag = f"{package.path}/v{released}"
    log = git._run(
        "log",
        f"--format=%s{_FIELD}%b{_RECORD}",
        f"{tag}..HEAD",
        "--",
        package.path,
    )
    messages: list[tuple[str, str]] = []
    for record in log.split(_RECORD):
        if not record.strip():
            continue
        subject, _, body = record.strip("\n").partition(_FIELD)
        messages.append((subject, body))
    return bump(released, messages)
