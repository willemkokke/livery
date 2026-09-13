"""Worker functions the tests hand to spawned processes.

A spawned worker unpickles its target by module and name and imports
that module afresh. Test modules are named by their path (pytest's
importlib mode), a name no other process can import, so a function a
test spawns lives here, in a helper the tests directory's place on
``pythonpath`` makes importable in the child too.
"""

from __future__ import annotations


def read_force_color(path: str) -> None:
    """Write the real environment's ``FORCE_COLOR`` to *path*."""
    import os

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(str(os.environ.get("FORCE_COLOR")))
