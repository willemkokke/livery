"""The isolation plugin: tests run apart from the live home, the registry stays clean.

The plugin rides the ``pytest11`` entry point, so the inner sessions carry it
without a conftest.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path
from typing import cast

import pytest

from livery.workshop import _pytest_isolation
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


def test_the_home_is_this_process_s_scratch_and_goes_at_unconfigure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The cache stays redirected, so the home lands in the session's own
    # cache and never in the machine's; the other variables, cleared,
    # are the plugin's to set. A session that ends takes the home with
    # it; one that is killed leaves it to footman's collector, which
    # reads the owner's pid off the directory's name.
    import livery.footman as footman

    names = [name for name in _pytest_isolation.variables() if "CACHE" not in name]
    for name in names:
        monkeypatch.delenv(name, raising=False)

    class Config:
        stash = pytest.Stash()

    config = cast("pytest.Config", Config())
    _pytest_isolation.pytest_configure(config)
    home = Path(os.environ["FOOTMAN_DATA_DIR"]).parent
    assert home.parent == footman.cache_dir() / "scratch"
    assert home.name.startswith(f"workshop-isolation-{os.getpid()}-")
    assert all(Path(os.environ[name]).parent == home for name in names)
    _pytest_isolation.pytest_unconfigure(config)
    assert not home.exists()
    assert not any(name in os.environ for name in names)


def test_the_legs_label_is_scrubbed_so_a_test_runs_as_no_shard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The job runner names the leg, shard and all, to every child it
    # spawns; a test that drives the gate in-process must not inherit
    # it, or the walk leaves every step without the flag to the first
    # shard. A test that wants a leg sets the variable itself.
    from livery.workshop._ci_context import LEG_VARIABLE

    monkeypatch.setenv(LEG_VARIABLE, "check-windows-latest-3.14-3of4")

    class Config:
        stash = pytest.Stash()

    config = cast("pytest.Config", Config())
    _pytest_isolation.pytest_configure(config)
    try:
        assert LEG_VARIABLE not in os.environ
    finally:
        _pytest_isolation.pytest_unconfigure(config)


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


def test_a_module_first_imported_under_a_patch_keeps_the_original_after_it(
    pytester: pytest.Pytester,
) -> None:
    # A module that copies a function from another (`from owner import
    # name`) and is first imported while a test has the function
    # patched takes the patch; monkeypatch puts the owner back, and the
    # copy kept the patched function for every later test in the
    # worker. The next test finds the original in both places, under
    # whatever name the copy took, and a patch made twice goes back to
    # the first original. The cases left alone come first: a constant
    # of the module's own that is the very object a patch set, and an
    # attribute the patch added, which has no original to go back to.
    pytester.syspathinsert()
    pytester.makepyfile(
        acme_owner="FLAG = False\n\ndef root():\n    return 'real'\n",
        acme_copier=(
            "from acme_owner import extra, root\n"
            "from acme_owner import root as renamed\n"
            "\n"
            "FLAG = True\n"
        ),
    )
    pytester.makepyfile(
        """
        import acme_owner

        def test_patches(monkeypatch):
            monkeypatch.setattr(acme_owner, "FLAG", True)
            monkeypatch.setattr(acme_owner, "extra", [], raising=False)
            monkeypatch.setattr(acme_owner, "root", lambda: "first")
            monkeypatch.setattr("acme_owner.root", lambda: "patched")
            import acme_copier

            assert acme_copier.root() == "patched"
            assert acme_copier.renamed() == "patched"

        def test_after():
            import acme_copier

            assert acme_copier.FLAG is True
            assert acme_copier.extra == []
            assert not hasattr(acme_owner, "extra")
            assert acme_owner.root() == "real"
            assert acme_copier.root() == "real"
            assert acme_copier.renamed() == "real"
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2)


def test_no_fixture_of_the_plugin_asks_for_monkeypatch() -> None:
    # An autouse fixture that asks for `monkeypatch` sets it up before
    # the fixtures of plugins registered later, which reorders their
    # teardown: footman's environment restore then runs before
    # `monkeypatch` writes back a value the test had changed. The
    # plugin's guards read a test's `monkeypatch` from its resolved
    # fixtures instead.
    import inspect

    for name, value in vars(_pytest_isolation).items():
        marker = getattr(value, "_fixture_function_marker", None) or getattr(
            value, "_pytestfixturefunction", None
        )
        if marker is None:
            continue
        unwrap = getattr(value, "_get_wrapped_function", None)
        wrapped = getattr(value, "__wrapped__", None) or (
            unwrap() if unwrap is not None else value
        )
        assert "monkeypatch" not in inspect.signature(wrapped).parameters, name
