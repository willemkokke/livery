"""The commit convention: one grammar for titles, bumps, and entries.

Every pull request title (and so, on a squash-only main, every commit
subject) is ``type(scope): subject``, with ``!`` before the colon or
a ``BREAKING CHANGE:`` footer marking a break. The submit verb
enforces the grammar; the release verbs read it back to derive the
next version and the changelog entry.

The version rules follow footman's practice: after 1.0, a break bumps
major, a feature minor, everything else patch; before 1.0, a feature
bumps minor with breaks riding along in minors, and everything else
is a patch.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: The commit types the grammar admits.
TYPES = ("feat", "fix", "docs", "chore", "refactor", "test")

#: The title grammar submit enforces and release reads back.
TITLE_RE = re.compile(rf"^({'|'.join(TYPES)})(\([^)]+\))?(!)?: .+$")

_SUBJECT_RE = re.compile(
    rf"^(?P<type>{'|'.join(TYPES)})(\((?P<scope>[^)]+)\))?(?P<bang>!)?: "
    r"(?P<description>.+)$"
)


@dataclass(frozen=True)
class Commit:
    """One conventional commit, as the miner reads it.

    Attributes:
        sha: The full commit hash.
        type: The conventional type, empty when the subject does not
            parse (a commit from before the grammar, kept visible).
        scope: The parenthesised scope, or empty.
        breaking: Whether ``!`` or a ``BREAKING CHANGE:`` footer marks
            a break.
        description: The subject after the prefix, or the whole
            subject when nothing parsed.
        author_name: The commit author's name.
        author_email: The commit author's address.
    """

    sha: str
    type: str
    scope: str
    breaking: bool
    description: str
    author_name: str
    author_email: str


def parse_commit(
    sha: str, subject: str, body: str, author_name: str, author_email: str
) -> Commit:
    """Read one commit's conventional shape; an unparsed subject keeps whole."""
    match = _SUBJECT_RE.match(subject)
    breaking_footer = "BREAKING CHANGE:" in body
    if match is None:
        return Commit(
            sha=sha,
            type="",
            scope="",
            breaking=breaking_footer,
            description=subject,
            author_name=author_name,
            author_email=author_email,
        )
    return Commit(
        sha=sha,
        type=match.group("type"),
        scope=match.group("scope") or "",
        breaking=bool(match.group("bang")) or breaking_footer,
        description=match.group("description"),
        author_name=author_name,
        author_email=author_email,
    )


def next_version(current: str, commits: list[Commit]) -> str:
    """The version the *commits* earn on top of *current*.

    footman's practice: after 1.0 a break bumps major, a feature
    minor, everything else patch; before 1.0 a feature bumps minor
    (breaks ride along in minors) and everything else is a patch. No
    commits earn no bump, and the caller decides what that means.
    """
    major, minor, patch = (int(part) for part in current.split("."))
    if not commits:
        return current
    breaking = any(commit.breaking for commit in commits)
    feature = any(commit.type == "feat" for commit in commits)
    if major == 0:
        if breaking or feature:
            return f"0.{minor + 1}.0"
        return f"0.{minor}.{patch + 1}"
    if breaking:
        return f"{major + 1}.0.0"
    if feature:
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


#: Which Keep a Changelog section each type lands in.
SECTIONS = (
    ("Added", ("feat",)),
    ("Fixed", ("fix",)),
    ("Changed", ("docs", "chore", "refactor", "test", "")),
)

_PR_REF_RE = re.compile(r"\(#(\d+)\)\s*$")

#: github's noreply addresses encode the login: ``12345+login@users...``.
_NOREPLY_RE = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$")


def _link(description: str, pr_url_base: str) -> str:
    """The description with its trailing ``(#N)`` turned into a link."""
    if not pr_url_base:
        return description
    match = _PR_REF_RE.search(description)
    if match is None:
        return description
    number = match.group(1)
    return _PR_REF_RE.sub(f"([#{number}]({pr_url_base}/{number}))", description)


def changelog_entry(
    version: str,
    date: str,
    commits: list[Commit],
    *,
    pr_url_base: str = "",
) -> str:
    """One Keep a Changelog entry: grouped sections, links, contributors."""
    lines = [f"## [{version}] - {date}", ""]
    for section, types in SECTIONS:
        rows = [commit for commit in commits if commit.type in types]
        if not rows:
            continue
        lines.append(f"### {section}")
        lines.append("")
        for commit in rows:
            lines.append(f"- {_link(commit.description, pr_url_base)}")
        lines.append("")
    contributors = []
    for commit in commits:
        match = _NOREPLY_RE.match(commit.author_email)
        credit = f"@{match.group(1)}" if match else commit.author_name
        if credit not in contributors:
            contributors.append(credit)
    if contributors:
        lines.append(f"Contributors: {', '.join(contributors)}")
        lines.append("")
    return "\n".join(lines)
