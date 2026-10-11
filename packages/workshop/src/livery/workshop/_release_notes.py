"""Release notes: the one-answer query the release train writes notes through.

A release stamps a version, and a package's history says what the
version holds. The train never writes that history itself: it asks
the provider the listed extensions answer the ``release-notes`` query
with for the entry the unreleased commits earn, has the provider
record it, and has it judge the recorded notes before a tag is cut. A
workspace whose extensions answer nothing releases without notes, and
the train says so where it would have written them.

One answer at a time: the query combines by ``nearest``, so two
listed extensions answering, neither requiring the other, refuse
naming both.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from livery.workshop._queries import Query, answer

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


#: The release notes' provider, a workspace-level query the base
#: declares; the extension that writes the history answers it with a
#: function called with the workspace root.
RELEASE_NOTES: Query[ReleaseNotes | None] = Query("release-notes")


def release_notes(root: Path) -> ReleaseNotes | None:
    """The provider the extensions listed at *root* answer; None when none answers.

    The answer is imported when first asked, so a mount imports none
    of the answering extension's code.

    Raises:
        QueryError: when two listed extensions answer and neither
            requires the other, naming both.
    """
    return answer(root, RELEASE_NOTES)


#: What the train prints where it would have written notes and has no provider.
NO_PROVIDER = "no release notes: no listed extension writes them, so none are written"
