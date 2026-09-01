"""The one way the workshop runs uv.

Every uv invocation goes through livery.workshop._uv.run_uv, so
capture, the working directory, and the failure shape cannot drift
between call sites: a failure is uv's own words, raised through
footman's fail.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from footman import fail


def run_uv(*args: str, root: Path) -> None:
    """Run ``uv *args`` at *root*; a failure carries uv's own words."""
    result = subprocess.run(
        ["uv", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        fail(
            f"uv {' '.join(args)} exited {result.returncode}:\n"
            f"{result.stdout}{result.stderr}"
        )
