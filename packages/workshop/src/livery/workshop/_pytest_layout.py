"""The test layout rule the suite enforces at session start.

Test modules are named by their path (pytest's importlib mode, which
the project template sets), so two packages may both have a
``tests/test_lifecycle.py``. That leaves the tests directories off
``sys.path``; the template puts every package's ``tests`` directory on
``pythonpath`` instead, one shared path, so a helper module in
``packages/<dir>/tests/`` starts with ``<dir>_`` (dashes as
underscores) and two packages never ship the same helper name. A
session whose layout breaks that is refused before any test runs,
naming each module and the name it wants.
"""

from __future__ import annotations

from pathlib import Path

import pytest

#: Files every tests directory may hold under any name.
UNNAMED = ("conftest.py", "__init__.py")


def offenders(root: Path) -> list[tuple[Path, str]]:
    """Helper modules under ``packages/*/tests/`` without their package's prefix.

    Each entry is the module and the name it wants. A directory under
    ``packages`` counts when it carries a ``workshop.toml``; test files
    and the files every directory may hold are never offenders.
    """
    found: list[tuple[Path, str]] = []
    packages = root / "packages"
    if not packages.is_dir():
        return found
    for package in sorted(packages.iterdir()):
        tests = package / "tests"
        if not (package / "workshop.toml").is_file() or not tests.is_dir():
            continue
        prefix = package.name.replace("-", "_") + "_"
        for module in sorted(tests.glob("*.py")):
            if module.name.startswith("test_") or module.name in UNNAMED:
                continue
            if not module.name.startswith(prefix):
                found.append((module, prefix + module.name))
    return found


def pytest_sessionstart(session: pytest.Session) -> None:
    """Refuse the session when a helper module lacks its package's prefix."""
    root = Path(session.config.rootpath)
    bad = offenders(root)
    if not bad:
        return
    listed = "\n".join(
        f"  {path.relative_to(root)}: rename to {wanted}" for path, wanted in bad
    )
    raise pytest.UsageError(
        "a helper module in a package's tests directory carries the package's"
        " name, so every tests directory can share one pythonpath without a"
        f" clash:\n{listed}"
    )
