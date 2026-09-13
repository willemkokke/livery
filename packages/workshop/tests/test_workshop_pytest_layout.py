"""The layout plugin: helpers carry their package's name; test names may repeat.

The plugin rides the ``pytest11`` entry point, so the inner sessions
carry it without a conftest.
"""

from __future__ import annotations

from importlib.metadata import entry_points
from pathlib import Path

import pytest

from livery.workshop._pytest_layout import offenders

pytest_plugins = ("pytester",)


def _workspace(root: Path, *packages: str) -> None:
    for name in packages:
        package = root / "packages" / name
        (package / "tests").mkdir(parents=True)
        (package / "workshop.toml").write_text(f'type = "python"\nname = "{name}"\n')


def test_the_plugin_rides_the_entry_point() -> None:
    names = {point.name for point in entry_points(group="pytest11")}
    assert "livery-workshop-layout" in names


def test_a_helper_without_its_packages_name_is_refused_before_any_test(
    pytester: pytest.Pytester,
) -> None:
    _workspace(pytester.path, "x", "tool-room")
    (pytester.path / "packages" / "x" / "tests" / "helper.py").write_text("HELP = 1\n")
    (pytester.path / "packages" / "x" / "tests" / "test_a.py").write_text(
        "def test_a(): pass\n"
    )
    (pytester.path / "packages" / "tool-room" / "tests" / "rig.py").write_text(
        "R = 1\n"
    )
    # The files every tests directory may hold under any name.
    (pytester.path / "packages" / "x" / "tests" / "conftest.py").write_text("")
    (pytester.path / "packages" / "x" / "tests" / "__init__.py").write_text("")
    found = offenders(pytester.path)
    assert [(path.name, wanted) for path, wanted in found] == [
        ("rig.py", "tool_room_rig.py"),
        ("helper.py", "x_helper.py"),
    ]
    result = pytester.runpytest("-p", "no:cacheprovider")
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*helper.py: rename to x_helper.py*"])
    result.stderr.fnmatch_lines(["*rig.py: rename to tool_room_rig.py*"])


def test_helpers_with_their_packages_name_pass(pytester: pytest.Pytester) -> None:
    _workspace(pytester.path, "x")
    (pytester.path / "packages" / "x" / "tests" / "x_helper.py").write_text(
        "HELP = 1\n"
    )
    (pytester.path / "packages" / "x" / "tests" / "test_a.py").write_text(
        "def test_a(): pass\n"
    )
    assert offenders(pytester.path) == []
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_two_packages_may_share_a_test_files_name_in_importlib_mode(
    pytester: pytest.Pytester,
) -> None:
    _workspace(pytester.path, "x", "y")
    for name in ("x", "y"):
        (pytester.path / "packages" / name / "tests" / "test_lifecycle.py").write_text(
            f"def test_{name}(): pass\n"
        )
    # The default import mode names a test module by its basename, so
    # the second file is a mismatch: the reason the template sets the
    # importlib mode.
    result = pytester.runpytest("-p", "no:cacheprovider", "--import-mode=prepend")
    assert result.ret != 0
    result.stdout.fnmatch_lines(["*import file mismatch*"])
    result = pytester.runpytest("-p", "no:cacheprovider", "--import-mode=importlib")
    result.assert_outcomes(passed=2)
