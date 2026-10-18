"""The pytest plugin footman ships: its fixtures and the guards of every session.

The fixtures `fm`, `fm_project` and `fm_record` are published API — a
user's `def test_x(fm_project)` is the whole point of the `pytest11` entry
point — so they are driven the way a user gets them: pytest loading the
installed plugin in a subprocess project, not by calling the fixture
functions directly. `pytester` runs a real pytest inside the test, which
also proves the entry point itself works. The session's guards, the
scratch directories and the registry guard, are driven the same way.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import cast

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
        "import livery.footman._pytest_plugin, sys; "
        "print('livery.footman.testing' in sys.modules"
        " or 'livery.footman._context' in sys.modules)"
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

    stub = SimpleNamespace(when=when, nodeid=nodeid, start=start, stop=stop)
    if worker is not None:
        stub.worker_id = worker
    return cast(pytest.TestReport, stub)


def test_recorder_records_phases_and_writes_a_fragment(tmp_path, monkeypatch):
    import json

    from livery.footman._pytest_plugin import _TraceRecorder

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

    from livery.footman import _pytest_plugin as pytest_plugin

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
    pytest_plugin._join_profile(worker)
    assert registered == []  # a real worker stays silent
    fresh = cast(
        pytest.Config,
        SimpleNamespace(
            pluginmanager=SimpleNamespace(
                register=lambda plug, name: registered.append(name)
            )
        ),
    )
    pytest_plugin._join_profile(fresh)
    assert registered == ["footman-profile-trace"]  # a nested controller records


def test_recorder_skips_reports_without_timing_and_writes_nothing_empty(tmp_path):
    from types import SimpleNamespace

    from livery.footman._pytest_plugin import _TraceRecorder

    rec = _TraceRecorder(str(tmp_path))
    bare = cast(pytest.TestReport, SimpleNamespace(when="call", nodeid="x"))
    rec.pytest_runtest_logreport(bare)
    rec.pytest_sessionfinish()
    assert list(tmp_path.iterdir()) == []


def test_a_broken_sink_is_a_note_never_a_failure(tmp_path, capsys, monkeypatch):
    from livery.footman._pytest_plugin import _TraceRecorder

    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    rec = _TraceRecorder(str(tmp_path / "never-made"))
    rec.pytest_runtest_logreport(_report())
    rec.pytest_sessionfinish()  # must not raise
    assert "not written" in capsys.readouterr().err


def test_configure_arms_only_under_a_profiled_run(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from livery.footman import _pytest_plugin as pytest_plugin

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
    pytest_plugin._join_profile(config)
    assert registered == []
    monkeypatch.setenv("FM_PROFILE_DIR", str(tmp_path))
    pytest_plugin._join_profile(config)
    assert registered == ["footman-profile-trace"]


def test_a_test_never_inherits_the_drop_box_but_the_suite_still_reports(
    pytester: pytest.Pytester,
) -> None:
    """Two things at once: the suite's timings are kept, the tests see no box.

    A CI leg runs its gate under a profile, so every test in it would
    otherwise read the leg's box out of its own environment, and a `Runner` in
    a test would drop a fragment of its own once per test. The recorder took
    the box's name when the session began, so it keeps reporting.
    """
    box = pytester.path / "box"
    box.mkdir()
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    try:
        pytester.makepyfile(
            """
            import os

            def test_the_box_is_not_in_here():
                assert os.environ.get("FM_PROFILE_DIR") is None
            """
        )
        pytester.runpytest_inprocess().assert_outcomes(passed=1)
    finally:
        monkeypatch.undo()
    (fragment,) = list(box.glob("*.json"))
    assert fragment.name.startswith("pytest-")


def test_what_a_test_writes_into_the_environment_is_gone_before_the_next_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The hard case first: a run writes a variable, and the test's own
    `monkeypatch.setenv` takes that written value as the one to put back.
    The next test finds the variable as the first test found it, absent,
    and finds one the test removed and one it changed as they were."""
    monkeypatch.setenv("FM_PLUGIN_REMOVED", "kept")
    monkeypatch.setenv("FM_PLUGIN_CHANGED", "before")
    monkeypatch.delenv("FM_PLUGIN_WRITTEN", raising=False)
    pytester.makepyfile(
        """
        import os

        def test_writes(monkeypatch):
            os.environ["FM_PLUGIN_WRITTEN"] = "from-the-run"
            monkeypatch.setenv("FM_PLUGIN_WRITTEN", "from-the-test")
            del os.environ["FM_PLUGIN_REMOVED"]
            os.environ["FM_PLUGIN_CHANGED"] = "after"

        def test_after():
            assert "FM_PLUGIN_WRITTEN" not in os.environ
            assert os.environ["FM_PLUGIN_REMOVED"] == "kept"
            assert os.environ["FM_PLUGIN_CHANGED"] == "before"
        """
    )
    pytester.runpytest_inprocess().assert_outcomes(passed=2)


def test_a_submodule_a_test_imports_first_stays_on_the_package(
    pytester: pytest.Pytester,
):
    # The import system binds a submodule on its package at the first
    # import, and an attribute walk to it (monkeypatch's dotted targets
    # among them) needs that binding: the plugin unbinds what a test
    # bound, never a submodule. A fresh process, so the import is a first.
    pytester.makepyfile(
        """
        import sys

        import livery.footman as footman

        def test_one_imports_a_submodule_first():
            assert "livery.footman._fetch" not in sys.modules
            import livery.footman._fetch

        def test_two_reaches_it_by_attribute():
            assert footman._fetch is sys.modules["livery.footman._fetch"]
        """
    )
    pytester.runpytest_subprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)


def test_a_name_a_test_binds_on_the_package_leaves_it_when_the_test_ends(
    pytester: pytest.Pytester,
):
    # The package serves most names through its module __getattr__, so a
    # patch of the defining module reaches every reader. A test that patches
    # the package itself binds the name into its namespace, and monkeypatch's
    # restore then binds the original there for good: the next test's patch of
    # the defining module would reach nobody. The plugin unbinds it.
    pytester.makepyfile(
        """
        import livery.footman as footman
        import livery.footman._context as context

        def test_one_patches_the_package(monkeypatch):
            monkeypatch.setattr(footman, "data_dir", lambda: "fake")
            assert footman.data_dir() == "fake"

        def test_two_patches_the_defining_module(monkeypatch):
            assert "data_dir" not in vars(footman)
            monkeypatch.setattr(context, "data_dir", lambda: "patched")
            from livery.footman import data_dir
            assert data_dir() == "patched"
        """
    )
    pytester.runpytest_inprocess("-p", "no:cacheprovider").assert_outcomes(passed=2)


# --- the session's directories and the registry guard -------------------------


def _run(pytester: pytest.Pytester, *args: str) -> pytest.RunResult:
    """Run the inner session in this process, with a base temp of its own.

    Without one the inner session numbers a directory under the
    shared ``pytest-of-<user>`` base and prunes the oldest, which is
    the outer session's: a source a test wrote there is gone when the
    gate's coverage report reads it.
    """
    base = pytester.path / "basetemp"
    return pytester.runpytest("-p", "no:cacheprovider", f"--basetemp={base}", *args)


def _unredirected(monkeypatch: pytest.MonkeyPatch, cache_home: Path) -> None:
    """Clear the runner's directories, as a session outside any redirect starts.

    The cache home moves to *cache_home*, so the session's home, which
    lands in the cache it starts with, never lands in the machine's.
    """
    for suffix in ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR"):
        monkeypatch.delenv(f"FOOTMAN_{suffix}", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))


def test_every_test_and_its_children_run_away_from_the_real_home(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Refusals first: the real home is never the target, whatever the
    # outer environment did; a child process sees the same redirect.
    _unredirected(monkeypatch, tmp_path / "cache-home")
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
    _run(pytester).assert_outcomes(passed=1)


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
    _run(pytester).assert_outcomes(passed=1)
    assert os.environ["FOOTMAN_DATA_DIR"] == str(outer)  # the inner session set nothing


def test_a_session_whose_directories_are_all_set_makes_no_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # An xdist worker inherits its controller's redirect: nothing is
    # left to point away, so no home is made and the end removes nothing.
    from livery.footman import _pytest_plugin as pytest_plugin

    for suffix in ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR"):
        monkeypatch.setenv(f"FOOTMAN_{suffix}", str(tmp_path / suffix.lower()))
    before = dict(os.environ)

    class Config:
        stash = pytest.Stash()

    config = cast("pytest.Config", Config())
    pytest_plugin.pytest_configure(config)
    assert dict(os.environ) == before
    assert not (tmp_path / "cache_dir" / "scratch").exists()
    pytest_plugin.pytest_unconfigure(config)
    assert dict(os.environ) == before


def test_the_home_is_this_process_s_scratch_and_goes_at_unconfigure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The cache is redirected to this test's directory, so the home lands
    # there and never in the machine's; the other variables, cleared, are
    # the plugin's to set. A session that ends takes the home with it;
    # one that is killed leaves it to the collector, which reads the
    # owner's pid off the directory's name.
    import livery.footman as footman
    from livery.footman import _pytest_plugin as pytest_plugin

    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path / "cache"))
    names = ["FOOTMAN_DATA_DIR", "FOOTMAN_CONFIG_DIR"]
    for name in names:
        monkeypatch.delenv(name, raising=False)

    class Config:
        stash = pytest.Stash()

    config = cast("pytest.Config", Config())
    pytest_plugin.pytest_configure(config)
    home = Path(os.environ["FOOTMAN_DATA_DIR"]).parent
    assert home.parent == footman.cache_dir() / "scratch"
    assert home.name.startswith(f"test-session-{os.getpid()}-")
    assert all(Path(os.environ[name]).parent == home for name in names)
    pytest_plugin.pytest_unconfigure(config)
    assert not home.exists()
    assert not any(name in os.environ for name in names)
    assert os.environ["FOOTMAN_CACHE_DIR"] == str(tmp_path / "cache")


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


def test_collection_drops_a_production_module_s_tasks_and_keeps_a_test_file_s(
    pytester: pytest.Pytester,
) -> None:
    # A test file may register tasks at import, its own subject; the
    # production module it imports registers its whole surface, which
    # no test asked for, and that goes before the first test. The inner
    # session registers into a captured tree, so nothing reaches this
    # session's registry.
    from livery.footman import _registry as registry

    pytester.syspathinsert()
    pytester.makepyfile(
        acme_surface=(
            "from livery.footman import task\n\n\n"
            "@task\ndef produce():\n"
            '    """Registered by a production module."""\n'
        ),
        test_registers="""
        import acme_surface
        from livery.footman import root_group, task

        @task
        def examine():
            '''Registered by the test file.'''

        def test_collected():
            assert "examine" in root_group.tasks
            assert "produce" not in root_group.tasks
        """,
    )
    with registry.capture():
        result = _run(pytester)
    result.assert_outcomes(passed=1)


def test_a_production_module_a_test_imports_is_dropped_without_failing_it(
    pytester: pytest.Pytester,
) -> None:
    from livery.footman import _registry as registry

    pytester.syspathinsert()
    pytester.makepyfile(
        acme_lazy=(
            "from livery.footman import task\n\n\n"
            "@task\ndef produce():\n"
            '    """Registered by a production module."""\n'
        ),
        test_imports="""
        from livery.footman import root_group

        def test_imports_it_lazily():
            import acme_lazy

            assert "produce" in root_group.tasks

        def test_after():
            assert "produce" not in root_group.tasks
        """,
    )
    with registry.capture():
        result = _run(pytester)
    result.assert_outcomes(passed=2)


def test_a_group_a_test_leaks_fails_it_by_the_group_s_name(
    pytester: pytest.Pytester,
) -> None:
    pytester.makepyfile(
        """
        from livery.footman import Group, root_group

        def test_leaks_a_group():
            root_group.groups["leaked"] = Group("leaked")

        def test_after():
            assert "leaked" not in root_group.groups
        """
    )
    result = _run(pytester)
    result.assert_outcomes(passed=2, errors=1)
    result.stdout.fnmatch_lines(["*leaked.[*]*"])


def test_the_brand_s_spelling_of_the_directories_points_away_too(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # A branded CLI reads its directories from its own prefix, and the
    # session points those away as well. The brand places its cache in
    # this test's directory, so the session's home lands there.
    from livery.footman import _paths

    for suffix in ("DATA_DIR", "CACHE_DIR", "CONFIG_DIR"):
        monkeypatch.delenv(f"ACME_{suffix}", raising=False)
    pytester.makepyfile(
        """
        import os
        from pathlib import Path

        def test_redirected():
            for name in ("ACME_DATA_DIR", "ACME_CACHE_DIR", "ACME_CONFIG_DIR"):
                assert Path(os.environ[name]).is_dir(), name
        """
    )
    before = _paths.child_args()
    _paths.configure(prefix="ACME", cache_dir=tmp_path / "cache")
    try:
        result = _run(pytester)
    finally:
        _paths.configure_child(*before)
    result.assert_outcomes(passed=1)
    assert not [name for name in os.environ if name.startswith("ACME_")]


# --- the test-guard group -----------------------------------------------------


def _members(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, **members: str
) -> None:
    """Register each module source as a member of the test-guard group.

    Each member is the module's ``guard``, named for its keyword: the
    group answers them alone, and every other group what is installed.
    """
    from importlib.metadata import EntryPoint

    from livery.footman import _entries

    pytester.syspathinsert()
    points = []
    for name, source in members.items():
        pytester.makepyfile(**{f"acme_{name}": source})
        points.append(EntryPoint(name, f"acme_{name}:guard", "footman.test_guards"))
    installed = _entries.installed_entry_points

    def answer(group: str | None = None) -> tuple[EntryPoint, ...]:
        if group == "footman.test_guards":
            return tuple(points)
        return installed(group)

    monkeypatch.setattr(_entries, "installed_entry_points", answer)


#: A guard that writes when it enters and leaves, and the facts it was given.
_RECORDING = """
import contextlib
import os
from pathlib import Path

EVENTS = Path(__file__).with_name("events.txt")


def _note(line):
    with EVENTS.open("a", encoding="utf-8") as events:
        events.write(line + "\\n")


def guard(**facts):
    _note("{name} called with " + ",".join(sorted(facts)))
    data_dir = Path(os.environ["FOOTMAN_DATA_DIR"])
    _note("{name} data_dir " + str(facts["data_dir"] == data_dir))

    @contextlib.contextmanager
    def around():
        _note("{name} enter")
        yield
        _note("{name} leave")

    return around
"""


def test_a_member_that_cannot_load_stops_the_session_naming_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(pytester, monkeypatch, broken="import acme_missing_module\n")
    pytester.makepyfile("def test_never_runs():\n    pass\n")
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*broken*could not load*"])


def test_a_member_that_raises_stops_the_session_naming_it(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(
        pytester,
        monkeypatch,
        raising="def guard(**facts):\n    raise RuntimeError('no guard here')\n",
    )
    pytester.makepyfile("def test_never_runs():\n    pass\n")
    result = _run(pytester)
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*raising*no guard here*"])


def test_a_guard_that_raises_as_it_is_left_is_an_error_at_teardown(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    _members(
        pytester,
        monkeypatch,
        leaving=(
            "import contextlib\n\n\n"
            "@contextlib.contextmanager\ndef around():\n"
            "    yield\n    raise RuntimeError('left badly')\n\n\n"
            "def guard(**facts):\n    return around\n"
        ),
    )
    pytester.makepyfile("def test_runs():\n    pass\n")
    result = _run(pytester)
    result.assert_outcomes(passed=1, errors=1)
    result.stdout.fnmatch_lines(["*left badly*"])


def test_members_run_in_name_order_and_their_guards_wrap_every_test(
    pytester: pytest.Pytester, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A member that returns None adds nothing; the others are called
    # once for the session, with the runner's directories as they are
    # after the redirect, and their guards wrap each test, the first
    # name outermost.
    _members(
        pytester,
        monkeypatch,
        second=_RECORDING.replace("{name}", "second"),
        first=_RECORDING.replace("{name}", "first"),
        none="def guard(**facts):\n    return None\n",
    )
    pytester.makepyfile("def test_one():\n    pass\n\ndef test_two():\n    pass\n")
    _run(pytester).assert_outcomes(passed=2)
    events = (pytester.path / "events.txt").read_text(encoding="utf-8").splitlines()
    wrapped = ["first enter", "second enter", "second leave", "first leave"]
    assert events == [
        "first called with cache_dir,config_dir,data_dir",
        "first data_dir True",
        "second called with cache_dir,config_dir,data_dir",
        "second data_dir True",
        *wrapped,
        *wrapped,
    ]


# --- the patch guard ----------------------------------------------------------

#: An owner of a function, and a module that copies it at its first import.
_PATCHED_MODULES = {
    "acme_owner": "FLAG = False\n\ndef root():\n    return 'real'\n",
    "acme_copier": (
        "from acme_owner import extra, root\n"
        "from acme_owner import root as renamed\n"
        "\n"
        "FLAG = True\n"
    ),
}


def test_a_pytest_without_the_record_s_marker_turns_the_patch_guard_off(
    pytester: pytest.Pytester,
) -> None:
    # The fallback first, in a process of its own: a pytest whose
    # monkeypatch keeps no NOTSET marker records patches in a shape the
    # guard cannot read, so the session runs and the guard does nothing:
    # the copy keeps the patched function after the test.
    pytester.syspathinsert()
    pytester.makepyfile(**_PATCHED_MODULES)
    pytester.makeconftest("import _pytest.compat\n\ndel _pytest.compat.NOTSET\n")
    pytester.makepyfile(
        """
        import acme_owner

        def test_patches(monkeypatch):
            monkeypatch.setattr(acme_owner, "extra", [], raising=False)
            monkeypatch.setattr(acme_owner, "root", lambda: "patched")
            import acme_copier

        def test_after():
            import acme_copier

            assert acme_owner.root() == "real"
            assert acme_copier.root() == "patched"
        """
    )
    base = pytester.path / "basetemp"
    result = pytester.runpytest_subprocess(
        "-p", "no:cacheprovider", f"--basetemp={base}"
    )
    result.assert_outcomes(passed=2)


def test_monkeypatch_keeps_the_record_the_patch_guard_reads() -> None:
    # The canary: the guard reads pytest's private record of a patch, as
    # (owner, name, value before), with NOTSET for an attribute the owner
    # did not have. A pytest that changes either fails here, in the
    # workspace and in footman's own legs, before the guard reads it
    # wrongly.
    from _pytest.compat import NOTSET

    class Owner:
        present = "before"

    patch = pytest.MonkeyPatch()
    try:
        patch.setattr(Owner, "present", "after")
        patch.setattr(Owner, "added", "new", raising=False)
        assert list(patch._setattr) == [  # pyright: ignore[reportPrivateUsage]
            (Owner, "present", "before"),
            (Owner, "added", NOTSET),
        ]
    finally:
        patch.undo()


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
    pytester.makepyfile(**_PATCHED_MODULES)
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
    _run(pytester).assert_outcomes(passed=2)


def test_fixtures_asking_for_monkeypatch_sort_after_the_environment_restore() -> None:
    # pytest sets a plugin's autouse fixtures up in name order, and an
    # autouse fixture that asks for `monkeypatch` sets it up with it.
    # Set up before `_environment_stays`, monkeypatch would be torn down
    # after it and write back a value the test had changed. So every
    # fixture here that asks for it sorts after `_environment_stays`, and
    # the patch guard reads a test's `monkeypatch` from its resolved
    # fixtures, as hooks that request none.
    import inspect

    from livery.footman import _pytest_plugin as pytest_plugin

    asking: list[str] = []
    fixtures: list[str] = []
    for name, value in vars(pytest_plugin).items():
        marker = getattr(value, "_fixture_function_marker", None) or getattr(
            value, "_pytestfixturefunction", None
        )
        if marker is None:
            continue
        fixtures.append(name)
        unwrap = getattr(value, "_get_wrapped_function", None)
        wrapped = getattr(value, "__wrapped__", None) or (
            unwrap() if unwrap is not None else value
        )
        if "monkeypatch" in inspect.signature(wrapped).parameters:
            asking.append(name)
    assert "_environment_stays" in fixtures
    assert "pytest_runtest_teardown" not in fixtures
    assert [name for name in asking if name < "_environment_stays"] == []
