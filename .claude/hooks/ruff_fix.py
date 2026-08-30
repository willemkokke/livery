"""PostToolUse hook: keep every edited Python file ruff-clean.

Reads the hook payload on stdin, and for a .py file runs ``ruff check
--fix`` then ``ruff format`` on it. Exits 0 always. The gate, not the
hook, is the arbiter; this only saves round trips.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    raw = (payload.get("tool_input") or {}).get("file_path", "")
    path = Path(raw)
    if path.suffix != ".py" or not path.is_file():
        return 0
    for args in (["check", "--fix", "--quiet"], ["format", "--quiet"]):
        subprocess.run(
            ["uv", "run", "--no-sync", "ruff", *args, str(path)],
            check=False,
            capture_output=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
