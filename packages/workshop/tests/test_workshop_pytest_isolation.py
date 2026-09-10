"""The isolation plugin: tests run apart from the live home, the registry stays clean.

The plugin rides the ``pytest11`` entry point, so the inner sessions carry it
without a conftest.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from livery.workshop import _pytest_isolation

pytest_plugins = ("pytester",)


def test_the_plugin_rides_the_entry_point() -> None:
    names = {point.name for point in entry_points(group="pytest11")}
    assert "livery-workshop-isolation" in names
    assert "FOOTMAN_DATA_DIR" in _pytest_isolation.variables()


def test_every_test_and_its_children_run_away_from_the_real_home(
    pytester: pytest.Pytester,
) -> None:
    # Refusals first: the real home is never the target, whatever the
    # outer environment did; a child process sees the same redirect.
    real = str(Path.home() / ".local" / "share" / "footman")
    pytester.makepyfile(
        f"""
        import os, subprocess, sys
        from pathlib import Path

        REAL = {real!r}

        def test_redirected():
            for name in ("FOOTMAN_DATA_DIR", "FOOTMAN_CACHE_DIR", "FOOTMAN_CONFIG_DIR"):
                value = os.environ[name]
                assert value and value != REAL and Path(value).is_dir(), name
            code = "import os; print(os.environ['FOOTMAN_DATA_DIR'])"
            child = subprocess.run(
                [sys.executable, "-c", code],
                capture_output=True, text=True, check=True,
            )
            assert child.stdout.strip() == os.environ["FOOTMAN_DATA_DIR"]
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)


def test_an_outer_redirect_is_kept(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    outer = tmp_path / "outer"
    outer.mkdir()
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(outer))
    pytester.makepyfile(
        f"""
        import os

        def test_kept():
            assert os.environ["FOOTMAN_DATA_DIR"] == {str(outer)!r}
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider")
    result.assert_outcomes(passed=1)
    assert os.environ["FOOTMAN_DATA_DIR"] == str(outer)  # the inner session set nothing


def test_a_test_that_leaks_into_the_global_registry_fails_by_name(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        from livery.footman import registry

        def test_leaks():
            registry.root.tasks["leaked"] = object()

        def test_after():
            assert "leaked" not in registry.root.tasks
        """
    )
    result = pytester.runpytest("-p", "no:cacheprovider", "-p", "no:randomly")
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*left tasks in the global registry: leaked*"])
