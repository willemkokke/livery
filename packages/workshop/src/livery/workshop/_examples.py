"""What a documentation example is: a python file under ``docs/examples/``.

The examples plugin collects each example as one test, and the python
extension's examples runner names examples to pytest; both ask
[livery.workshop.is_example][]. This module imports no pytest, so the
answer reads the same in a process that has none.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def is_example(path: Path) -> bool:
    """Tell whether *path* is a documentation example.

    An example is a python file under a ``docs/examples/`` directory at
    any depth of *path*, which the examples plugin collects as one
    test. A ``conftest.py`` there is the package's setup around the
    examples, never an example. Nothing is read from the disk.

    Args:
        path: The file, relative or absolute.

    Returns:
        True for an example; False for any other file.
    """
    if path.suffix != ".py" or path.name == "conftest.py":
        return False
    parts = path.parts
    return any(parts[i : i + 2] == ("docs", "examples") for i in range(len(parts) - 1))
