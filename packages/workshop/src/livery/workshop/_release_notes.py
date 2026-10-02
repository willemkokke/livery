"""Release notes: the one registration the release train writes notes through.

A release stamps a version, and a package's history says what the
version holds. The train never writes that history itself: it asks
the provider an extension registers here for the entry the unreleased
commits earn, has the provider record it, and has it judge the
recorded notes before a tag is cut. A workspace with no provider
releases without notes, and the train says so where it would have
written them.

One provider at a time: a later registration replaces an earlier
one, and withdrawing it by its extension leaves none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from pathlib import Path

    from livery.workshop._packages import Package


class ReleaseNotes(Protocol):
    """What a release-notes provider answers."""

    def entry(self, root: Path, package: Package, version: str = "") -> str:
        """The notes the commits since *package*'s last release earn.

        Headed by *version* when one is given, as unreleased otherwise;
        empty when nothing unreleased touches the package.
        """
        ...

    def record(self, package: Package, version: str, entry: str) -> list[str]:
        """Write *version*'s *entry* into *package*'s history.

        An empty *entry* records a placeholder for a person to write.
        Returns the files changed, each with a note for the reader,
        empty when the history already holds the version's entry.
        """
        ...

    def verify(self, package: Package, version: str) -> list[str]:
        """The problems with *version*'s recorded notes; empty when sound."""
        ...


_PROVIDER: list[tuple[ReleaseNotes, str]] = []


def register_release_notes(notes: ReleaseNotes, *, extension: str) -> None:
    """Make *notes* the provider, registered by *extension*; it replaces any other."""
    _PROVIDER[:] = [(notes, extension)]


def unregister_release_notes(*, extension: str) -> None:
    """Withdraw the provider *extension* registered; nothing when it registered none."""
    _PROVIDER[:] = [(n, owner) for n, owner in _PROVIDER if owner != extension]


def release_notes() -> ReleaseNotes | None:
    """The registered provider, or None when no extension records release notes."""
    return _PROVIDER[0][0] if _PROVIDER else None


#: What the train prints where it would have written notes and has no provider.
NO_PROVIDER = "no release notes: no mounted extension records them, so none are written"
