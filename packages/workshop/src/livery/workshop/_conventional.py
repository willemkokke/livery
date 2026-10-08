"""The commit convention: the grammar every title is held to.

A pull request title (and so, on a squash-only main, every commit
subject) is ``type(scope): subject``, with ``!`` before the colon or
a ``BREAKING CHANGE:`` footer marking a break. The submit verb
enforces it here.

Two readers take the convention back. livery.workshop._versions
derives a package's next version from it, and the release notes'
provider, when an extension registers one, writes the entry the
commits earn from it.
"""

from __future__ import annotations

import re

#: The commit types the grammar admits.
TYPES = ("feat", "fix", "docs", "chore", "refactor", "test")

#: The title grammar submit enforces.
TITLE_RE = re.compile(rf"^({'|'.join(TYPES)})(\([^)]+\))?(!)?: .+$")
