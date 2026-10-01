"""The commit convention: the grammar every title is held to.

A pull request title (and so, on a squash-only main, every commit
subject) is ``type(scope): subject``, with ``!`` before the colon or
a ``BREAKING CHANGE:`` footer marking a break. The submit verb
enforces it here.

Two readers take the convention back. livery.workshop._versions
derives a package's next version from it. git-cliff, per package
through the ``cliff.toml`` the template renders, writes the changelog
entry: it groups the commits, links the pull requests and credits the
authors.
"""

from __future__ import annotations

import re

#: The commit types the grammar admits.
TYPES = ("feat", "fix", "docs", "chore", "refactor", "test")

#: The title grammar submit enforces.
TITLE_RE = re.compile(rf"^({'|'.join(TYPES)})(\([^)]+\))?(!)?: .+$")
