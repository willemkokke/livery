"""No file the repository tracks matches its own ignore rules.

A file that was tracked before an ignore line named it stays tracked,
and the docs build publishes whatever a package's ``docs/`` tree holds,
so such a file reaches the site. ``git ls-files`` names every tracked
file the ignore rules match.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_tracked_file_matches_the_ignore_rules() -> None:
    listed = subprocess.run(
        ["git", "ls-files", "--cached", "--ignored", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    tracked_and_ignored = [line for line in listed.stdout.splitlines() if line]
    assert tracked_and_ignored == [], (
        "these files are tracked and match the ignore rules; untrack them"
        f" with `git rm --cached`: {tracked_and_ignored}"
    )
