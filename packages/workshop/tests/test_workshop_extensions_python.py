"""The python extension inside the wheel: its load order, its entry point, its level."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]


def test_the_python_extension_imported_before_the_base_registers_the_kind() -> None:
    """The extension loads first and the kind still registers with its code.

    The kind registry imports the extension's code while it registers,
    so the extension's module imports the registry only when it is
    called. Imported first, in a fresh interpreter, it loads, and the
    python kind's backend is that module.
    """
    run = subprocess.run(
        [
            sys.executable,
            "-c",
            "from livery.extensions.python import _backend\n"
            "from livery.workshop._kinds import kind_for\n"
            "assert kind_for('python').backend is _backend\n"
            "extractor = kind_for('python').extractor\n"
            "print(extractor.name if extractor else '')",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    assert run.stdout.strip() == "python"


def test_the_wheel_ships_python_as_a_package_level_extension() -> None:
    from livery.workshop._extensions import declaration

    found = {
        entry.name: entry.value
        for entry in importlib.metadata.entry_points(group="workshop.extensions")
    }
    assert found["python"] == "livery.extensions.python"
    declared = declaration("python")
    assert declared is not None
    assert declared.levels == ("package",)


def test_the_extension_answers_each_python_package_as_the_kind_backend_does() -> None:
    """For every package here that lists it, the answers equal the backend's."""
    import livery.workshop
    from livery.extensions.python import _backend
    from livery.workshop._packages import discover_packages
    from livery.workshop._queries import (
        CURRENT_VERSION,
        MODULE_ROOTS,
        PUBLIC_MODULES,
        REQUIREMENTS,
        VERSION_FILES,
        answer,
    )

    if not Path(livery.workshop.__file__).resolve().is_relative_to(ROOT):
        # The release train's isolated leg installs the workshop's wheel
        # alone, beside no checkout's packages.
        pytest.skip("the comparison reads this checkout's packages")
    listing = [
        package for package in discover_packages(ROOT) if "python" in package.extensions
    ]
    assert listing, "no package here lists the python extension"
    for package in listing:
        assert answer(package, CURRENT_VERSION) == _backend.current_version(package)
        assert answer(package, VERSION_FILES) == tuple(
            _backend.stamp_version(package).homes()
        )
        requirements = answer(package, REQUIREMENTS)
        assert dict(requirements) == _backend.declared_requirements(package)
        assert answer(package, MODULE_ROOTS) == tuple(_backend.module_roots(package))
        assert answer(package, PUBLIC_MODULES) == tuple(
            _backend.public_modules(package)
        )
