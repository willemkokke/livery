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


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner session with a base temp of its own.

    Without one the inner session numbers a directory under the
    shared ``pytest-of-<user>`` base and prunes the oldest, which is
    the outer session's: a source a test wrote there is gone when the
    gate's coverage report reads it.
    """
    base = pytester.path / "basetemp"
    return pytester.runpytest("-p", "no:cacheprovider", f"--basetemp={base}", *args)


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
    result = _run(pytester)
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
    result = _run(pytester)
    result.assert_outcomes(passed=1)
    assert os.environ["FOOTMAN_DATA_DIR"] == str(outer)  # the inner session set nothing


def test_a_test_that_leaks_into_the_global_registry_fails_by_name(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        from livery.footman import _registry as registry

        def test_leaks():
            registry.root.tasks["leaked"] = object()

        def test_after():
            assert "leaked" not in registry.root.tasks
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*left tasks in the global registry: leaked*"])


def test_a_slot_a_test_filled_never_reaches_the_test_after_it(
    pytester: pytest.Pytester,
) -> None:
    # Two tests in one file run in order on one worker: the first
    # declares and fills a slot, the second finds the registry as it
    # was before. Without the plugin's restore the second would see
    # the first's slot and contribution.
    pytester.makepyfile(
        """
        from livery.workshop._slots import composed, contribute, register_slot, slots

        def test_fills():
            register_slot("acme.left")
            contribute("acme.left", "value", extension="acme")
            contribute("python.dev-group", "acme>=1", extension="acme")
            assert composed("acme.left") == ["value"]

        def test_finds_nothing_of_it():
            assert "acme.left" not in slots()
            assert "acme>=1" not in composed("python.dev-group")
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2)
