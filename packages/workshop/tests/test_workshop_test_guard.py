"""The workshop's test guard: footman's plugin enters it around every test.

The guard rides footman's ``footman.test_guards`` entry-point group, and
footman's pytest plugin rides the ``pytest11`` one, so the inner sessions
carry the guard without a conftest.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path

import pytest

from livery.workshop import _test_guard
from workshop_installed import skip_unless_installed

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


def test_the_guard_rides_footman_s_test_guard_group() -> None:
    points = {
        point.name: point.value for point in entry_points(group="footman.test_guards")
    }
    assert points["livery.workshop"] == "livery.workshop._test_guard:guard"


def test_the_legs_label_is_scrubbed_so_a_test_runs_as_no_shard(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The job runner names the leg, shard and all, to every child it
    # spawns; a test that drives the gate in-process must not inherit
    # it, or the walk leaves every step without the flag to the first
    # shard. A test that wants a leg sets the variable itself.
    from livery.workshop._ci_context import LEG_VARIABLE

    monkeypatch.setenv(LEG_VARIABLE, "check-windows-latest-3.14-3of4")
    returned = _test_guard.guard(
        data_dir=tmp_path, cache_dir=tmp_path, config_dir=tmp_path
    )
    assert returned is _test_guard.mount_state_stays
    assert LEG_VARIABLE not in os.environ


def test_a_session_that_never_loads_the_mount_takes_nothing_from_it(
    pytester: pytest.Pytester,
) -> None:
    # The fallback first, in a process of its own: a suite that never
    # loads the workshop's extension module gets neither it nor the
    # registries it reads loaded by the guard.
    pytester.makepyfile(
        """
        import sys

        def test_plain():
            pass

        def test_after():
            loaded = [
                name
                for name in (
                    "livery.workshop._extensions",
                    "livery.workshop._steps",
                    "livery.workshop._points",
                )
                if name in sys.modules
            ]
            assert loaded == []
        """
    )
    base = pytester.path / "basetemp"
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", f"--basetemp={base}"
    )
    result.assert_outcomes(passed=2)


def test_what_a_mount_registered_is_gone_before_the_next_test(
    pytester: pytest.Pytester,
) -> None:
    skip_unless_installed("docs")
    # A test that mounts the docs extension registers its steps, its CI
    # jobs and the mount's record in the process. A docs step reads git
    # for the changes it is shown, so a later test that judges changes
    # outside a repository failed on it. The next test finds the
    # registries as the first test found them.
    pytester.makepyfile(
        """
        from livery.footman import _registry as registry
        from livery.workshop import _extensions, _points, _steps

        BEFORE = {}

        def _now():
            return _steps.snapshot(), _points.snapshot(), _extensions.snapshot()

        def test_mounts(tmp_path):
            BEFORE["state"] = _now()
            (tmp_path / "workshop.toml").write_text(
                '[workspace]\\nextensions = ["docs", "acme.missing"]\\n'
            )
            with registry.capture():
                assert _extensions.mount_extensions(tmp_path) == ("docs",)
            assert "lint.doclinks" in _steps.steps_by_name()
            assert _now() != BEFORE["state"]

        def test_after():
            assert _now() == BEFORE["state"]
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2)


def test_registries_a_test_loaded_itself_go_back_as_a_process_starts(
    pytester: pytest.Pytester,
) -> None:
    skip_unless_installed("docs")
    # In a process of its own, the first test loads the mount and mounts
    # the docs extension; the next finds each registry as a process
    # starts with it: the builtin steps alone, no job, and no mount.
    pytester.makepyfile(
        """
        def test_mounts(tmp_path):
            from livery.footman import _registry as registry
            from livery.workshop import _extensions, _steps

            (tmp_path / "workshop.toml").write_text(
                '[workspace]\\nextensions = ["docs"]\\n'
            )
            with registry.capture():
                assert _extensions.mount_extensions(tmp_path) == ("docs",)
            assert "lint.doclinks" in _steps.steps_by_name()

        def test_after():
            from livery.workshop import _extensions, _points, _steps

            assert _steps.snapshot() == _steps.snapshot(initial=True)
            assert _points.snapshot() == _points.snapshot(initial=True)
            assert _extensions.snapshot() == _extensions.snapshot(initial=True)
        """
    )
    base = pytester.path / "basetemp"
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", f"--basetemp={base}"
    )
    result.assert_outcomes(passed=2)
