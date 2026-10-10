"""The python extension inside the wheel: its load order, its entry point, its level."""

from __future__ import annotations

import importlib.metadata
import subprocess
import sys


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
