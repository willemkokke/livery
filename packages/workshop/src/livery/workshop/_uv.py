"""The one way the workshop runs uv.

Every uv invocation goes through livery.workshop._uv.run_uv, so
capture, the working directory, and the failure shape cannot drift
between call sites: a failure is uv's own words, raised through
footman's fail.
"""

from __future__ import annotations

from pathlib import Path

import toolroom
from footman import fail


def run_uv(*args: str, root: Path) -> None:
    """Run ``uv *args`` at *root*; a failure carries uv's own words."""
    result = toolroom.uv.opts(cwd=root, nofail=True, recorded=False)(*args)
    if result.code != 0:
        fail(
            f"uv {' '.join(args)} exited {result.code}:\n{result.stdout}{result.stderr}"
        )
