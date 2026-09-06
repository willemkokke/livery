"""The pytest fixtures footman ships: `fm`, `fm_project`, `fm_record`.

These are published API — a user's `def test_x(fm_project)` is the whole
point of the `pytest11` entry point — so they are driven the way a user
gets them: pytest loading the installed plugin in a subprocess project,
not by calling the fixture functions directly. `pytester` runs a real
pytest inside the test, which also proves the entry point itself works.
"""

from __future__ import annotations

import pytest

pytest_plugins = ["pytester"]


def test_fm_project_scaffolds_and_runs(pytester: pytest.Pytester):
    """The factory writes a project, chdirs into it, and hands back a
    Runner whose invoke() drives the real CLI."""
    pytester.makepyfile(
        """
        def test_release(fm_project):
            fm = fm_project('''
                from livery.footman import task, run

                @task
                def release(version: str):
                    "Cut a release."
                    run(f"git tag v{version}")
            ''')
            result = fm.invoke("--dry-run release 1.2.0")
            assert result.ok
            assert "git tag v1.2.0" in result.stdout  # the faked receipt
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_project_honours_a_custom_tasks_filename(pytester: pytest.Pytester):
    """`name=` writes that filename *and* wires `[tool.footman] tasks` so
    the cascade actually finds it."""
    pytester.makepyfile(
        """
        def test_named(fm_project):
            fm = fm_project('''
                from livery.footman import task

                @task
                def ship():
                    "Ship it."
            ''', name="acmetasks.py")
            assert "ship" in fm.invoke("--list").stdout
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_record_captures_commands_without_running_them(pytester: pytest.Pytester):
    """The recording fixture spans the whole test: task code runs, the
    commands it would issue are captured instead of executed."""
    pytester.makepyfile(
        """
        from livery.footman import run, task

        @task
        def lint(fix: bool = False):
            "Lint."
            run("ruff check ." + (" --fix" if fix else ""))

        def test_lint_fix(fm_record):
            lint(fix=True)
            assert fm_record[0].command == "ruff check . --fix"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_fm_runner_targets_the_current_project(pytester: pytest.Pytester):
    """The bare `fm` fixture drives whatever project the test runs in."""
    pytester.makepyfile(
        tasks="""
        from livery.footman import task

        @task
        def hello():
            "Say hello."
            print("hi from the project")
        """
    )
    pytester.makepyfile(
        """
        def test_hello(fm):
            result = fm.invoke("hello")
            assert result.ok
            assert "hi from the project" in result.stdout
        """
    )
    pytester.makepyprojecttoml('[project]\nname = "demo"\nversion = "0"\n')
    pytester.runpytest_inprocess().assert_outcomes(passed=1)


def test_plugin_loads_from_its_entry_point(pytester: pytest.Pytester):
    """A genuinely separate pytest process, no `-p` flag: the fixtures
    arrive through the `pytest11` entry point, which is the only path an
    installing project ever uses. (Subprocess, so coverage cannot see it —
    the point here is the install path, not the lines.)"""
    pytester.makepyfile(
        """
        def test_fixtures_exist(fm, fm_record):
            assert fm is not None and fm_record == []
        """
    )
    pytester.runpytest_subprocess().assert_outcomes(passed=1)


def test_importing_footman_stays_lazy_under_the_plugin():
    """The plugin module must not import footman at module level: pytest
    loads entry-point plugins before coverage starts, so an eager import
    would make every downstream project's footman coverage look wrong."""
    import subprocess
    import sys

    probe = (
        "import footman.pytest_plugin, sys; "
        "print('livery.footman.testing' in sys.modules"
        " or 'livery.footman.context' in sys.modules)"
    )
    out = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert out.stdout.strip() == "False"


# --- the profile fragment recorder -------------------------------------------


def _report(
    *,
    when: str = "call",
    nodeid: str = "test_a.py::t",
    start: float = 1000.0,
    stop: float = 1000.5,
    worker: str | None = None,
) -> pytest.TestReport:
    from types import SimpleNamespace
    from typing import cast

    stub = SimpleNamespace(when=when, nodeid=nodeid, start=start, stop=stop)
    if worker is not None:
        stub.worker_id = worker
    return cast(pytest.TestReport, stub)


def test_recorder_records_phases_and_writes_a_fragment(tmp_path, monkeypatch):
    import json

    from livery.footman.pytest_plugin import _TraceRecorder

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    rec = _TraceRecorder(str(tmp_path))
    rec.pytest_runtest_logreport(_report(when="setup", stop=1000.1))
    rec.pytest_runtest_logreport(_report())
    rec.pytest_runtest_logreport(_report(worker="gw1"))  # a controller's replay
    rec.pytest_sessionfinish()

    (fragment,) = tmp_path.glob("pytest-*.json")
    events = json.loads(fragment.read_text(encoding="utf-8"))["traceEvents"]
    tracks = {e["args"]["name"] for e in events if e.get("name") == "thread_name"}
    assert tracks == {"pytest", "gw1"}
    call = next(e for e in events if e.get("cat") == "test.call")
    assert call["ts"] == 1000.0 * 1e6  # epoch microseconds, the convention
    assert call["dur"] == 500_000.0


def test_an_xdist_worker_is_never_armed(tmp_path, monkeypatch):
    # The worker mark is process-local (`config.workerinput`), never the
    # inherited environment: a pytest spawned *by a test* inherits the outer
    # suite's PYTEST_XDIST_WORKER and must still record its own tests.
    from types import SimpleNamespace
    from typing import cast

    from livery.footman import pytest_plugin

    registered: list[str] = []
    monkeypatch.setenv("FM_PROFILE_DIR", str(tmp_path))
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")  # inherited noise
    worker = cast(
        pytest.Config,
        SimpleNamespace(
            workerinput={},
            pluginmanager=SimpleNamespace(
                register=lambda plug, name: registered.append(name)
            ),
        ),
    )
    pytest_plugin.pytest_configure(worker)
    assert registered == []  # a real worker stays silent
    fresh = cast(
        pytest.Config,
        SimpleNamespace(
            pluginmanager=SimpleNamespace(
                register=lambda plug, name: registered.append(name)
            )
        ),
    )
    pytest_plugin.pytest_configure(fresh)
    assert registered == ["footman-profile-trace"]  # a nested controller records


def test_recorder_skips_reports_without_timing_and_writes_nothing_empty(tmp_path):
    from types import SimpleNamespace
    from typing import cast

    from livery.footman.pytest_plugin import _TraceRecorder

    rec = _TraceRecorder(str(tmp_path))
    bare = cast(pytest.TestReport, SimpleNamespace(when="call", nodeid="x"))
    rec.pytest_runtest_logreport(bare)
    rec.pytest_sessionfinish()
    assert list(tmp_path.iterdir()) == []


def test_a_broken_sink_is_a_note_never_a_failure(tmp_path, capsys, monkeypatch):
    from livery.footman.pytest_plugin import _TraceRecorder

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    rec = _TraceRecorder(str(tmp_path / "never-made"))
    rec.pytest_runtest_logreport(_report())
    rec.pytest_sessionfinish()  # must not raise
    assert "not written" in capsys.readouterr().err


def test_configure_arms_only_under_a_profiled_run(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from typing import cast

    from livery.footman import pytest_plugin

    registered: list[str] = []
    config = cast(
        pytest.Config,
        SimpleNamespace(
            pluginmanager=SimpleNamespace(
                register=lambda plug, name: registered.append(name)
            )
        ),
    )
    monkeypatch.delenv("FM_PROFILE_DIR", raising=False)
    pytest_plugin.pytest_configure(config)
    assert registered == []
    monkeypatch.setenv("FM_PROFILE_DIR", str(tmp_path))
    pytest_plugin.pytest_configure(config)
    assert registered == ["footman-profile-trace"]
