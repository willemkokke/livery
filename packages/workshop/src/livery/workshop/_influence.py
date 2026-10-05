"""Which files a workspace check reads, and which of them a change touched.

A workspace check declares its inputs on its record
([livery.workshop._checks.CheckRecord][] ``inputs``): the files it
judges, whether it judges each changed one on its own or runs whole
when any changed, and what widens it to every file it reads. The gate
hands every check the paths changed since the tree it measures from,
and [livery.workshop._influence.select][] answers for one check: skip
it, judge these files, or judge everything.

Two widenings hold for every check. A run that knows nothing of what
changed (the whole gate) judges everything. A check whose own code
changed judges everything, since its rule moved, not the files: the
member package that ships it changed under its sources, or, for a
check installed from an index, the lock moved.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from livery.workshop._categories import matches
from livery.workshop._packages import Package

#: The lock an installed check's version moves with.
LOCK = "uv.lock"

#: Patterns, root-relative and posix (``**`` spans directories), or a
#: function of the root answering them.
Patterns = tuple[str, ...] | Callable[[Path], tuple[str, ...]]


@dataclass(frozen=True)
class Changes:
    """What changed since the tree a gate measures from.

    Attributes:
        root: The workspace root.
        paths: The changed paths, root-relative posix, deleted ones
            included.
        before: The commit or tree the paths were taken from; empty
            when the caller named the paths itself.
    """

    root: Path
    paths: tuple[str, ...]
    before: str = ""

    def removed(self) -> tuple[str, ...]:
        """The changed paths the working tree no longer holds: deleted or moved."""
        return tuple(path for path in self.paths if not (self.root / path).exists())

    def text_before(self, path: str) -> str:
        """*path* where the change is measured from; empty if unknown."""
        if not self.before:
            return ""
        from livery.workshop._git_ops import GitOps

        return GitOps(self.root).file_at(self.before, path)


@dataclass(frozen=True)
class Inputs:
    """The files a workspace check reads, as the affected engine asks.

    Attributes:
        reads: The files the check judges.
        per_file: Whether the check judges each changed file it reads
            on its own; otherwise a change to any of them runs it
            whole.
        widens: Files the check does not judge whose change runs it
            over everything it reads: the contracts, for a check of
            what is composed from them.
        on_removal: Whether a deleted or moved file anywhere runs it
            whole: a link checker's, since a link in an unchanged page
            may point at what went away.
        widen: The check's own further reason to run whole, given the
            changes: a link checker's removed heading.
        ignores: Files that never count, though a pattern above names
            them: a package's readme beside its manifests.
    """

    reads: Patterns
    per_file: bool = True
    widens: Patterns = ()
    on_removal: bool = False
    widen: Callable[[Changes], bool] | None = None
    ignores: tuple[str, ...] = ()


@dataclass(frozen=True)
class Selection:
    """What one check judges in a run.

    Attributes:
        whole: Whether it judges everything it reads.
        files: Otherwise, the changed files it judges; with *whole*
            false and no files, the check does not run.
    """

    whole: bool
    files: tuple[str, ...] = ()

    @property
    def runs(self) -> bool:
        """Whether the check runs at all."""
        return self.whole or bool(self.files)


WHOLE = Selection(whole=True)
SKIP = Selection(whole=False)


def _patterns(declared: Patterns, root: Path) -> tuple[str, ...]:
    return declared if isinstance(declared, tuple) else declared(root)


def _matched(patterns: tuple[str, ...], paths: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        path for path in paths if any(matches(pattern, path) for pattern in patterns)
    )


def provider_of(extension: str, packages: tuple[Package, ...]) -> str:
    """The member package whose sources ship *extension*, by its path; empty if none.

    An extension installed from an index has no member: its code moves
    with the lock instead.
    """
    try:
        spec = importlib.util.find_spec(extension)
    except (ImportError, ValueError):
        return ""
    if spec is None:
        return ""
    places = [*(spec.submodule_search_locations or ())]
    if spec.origin:
        places.append(str(Path(spec.origin).parent))
    for place in places:
        resolved = Path(place).resolve()
        for package in packages:
            if resolved.is_relative_to(package.directory.resolve()):
                return package.path
    return ""


def select(inputs: Inputs, changes: Changes | None, *, provider: str = "") -> Selection:
    """What a check with *inputs* judges for *changes*.

    Everything when nothing is known of what changed, when the check's
    own code changed (*provider*, the member that ships it, changed
    under ``src/``; for a check with no member, the lock moved), or
    when a widening input changed; else the changed files it reads,
    each on its own, or everything when it does not judge per file;
    nothing when no file it reads changed.
    """
    if changes is None:
        return WHOLE
    paths = tuple(
        path for path in changes.paths if not _matched(inputs.ignores, (path,))
    )
    if provider:
        if any(path.startswith(f"{provider}/src/") for path in paths):
            return WHOLE
    elif LOCK in paths:
        return WHOLE
    root = changes.root
    if _matched(_patterns(inputs.widens, root), paths):
        return WHOLE
    if inputs.on_removal and changes.removed():
        return WHOLE
    if inputs.widen is not None and inputs.widen(changes):
        return WHOLE
    touched = _matched(_patterns(inputs.reads, root), paths)
    if not touched:
        return SKIP
    return Selection(whole=False, files=touched) if inputs.per_file else WHOLE
