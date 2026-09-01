"""The commit convention: the grammar every title is held to.

A pull request title (and so, on a squash-only main, every commit
subject) is ``type(scope): subject``, with ``!`` before the colon or
a ``BREAKING CHANGE:`` footer marking a break. The submit verb
enforces it here.

Reading the convention back is git-cliff's work, per package, through
the ``cliff.toml`` the template renders: it groups the entry, links
the pull requests, credits the authors, and derives the next version.
"""

from __future__ import annotations

import re

#: The commit types the grammar admits.
TYPES = ("feat", "fix", "docs", "chore", "refactor", "test")

#: The title grammar submit enforces.
TITLE_RE = re.compile(rf"^({'|'.join(TYPES)})(\([^)]+\))?(!)?: .+$")
