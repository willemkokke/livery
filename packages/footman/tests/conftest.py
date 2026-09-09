"""Shared fixtures: load the sample task surface and build its manifest."""

from __future__ import annotations

import importlib.util
import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest

from livery.footman import _manifest, context, registry

FIXTURE = Path(__file__).parent / "fixtures" / "sample_tasks.py"
PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def pytest_configure(config: pytest.Config) -> None:
    """Measure the children too, but only when the parent is measuring.

    footman's most interesting code runs in processes it spawns: the
    completion hot path every shell hook invokes, the detached manifest
    refresh, the cache collector, the uv handoff, the `fm` inside a docs
    cast. `COVERAGE_PROCESS_START` makes coverage's installed `.pth` arm
    itself in each child; setting it unconditionally would start a
    coverage session in every subprocess of every plain test run, so it
    is set only when a `--cov` run is actually in progress.
    """
    plugin = config.pluginmanager.get_plugin("_cov")
    if plugin is not None and getattr(plugin, "cov_controller", None) is not None:
        os.environ["COVERAGE_PROCESS_START"] = str(PYPROJECT)
        # Absolute, or the data is lost: a child resolves a relative
        # COVERAGE_FILE against *its own* cwd, and footman's most
        # interesting children run somewhere else entirely — the shell
        # hooks and casts all invoke `fm` from a temp project. Their data
        # files landed in those temp dirs and vanished with them.
        data_file = Path(os.environ.get("COVERAGE_FILE", ".coverage"))
        os.environ["COVERAGE_FILE"] = str(data_file.resolve())


@pytest.fixture(autouse=True)
def _no_cache_override(monkeypatch, tmp_path_factory):
    """A real FOOTMAN_CACHE_DIR would bypass every cache_home patch — the
    override is env-first by design, so the suite must clear it. The same
    goes for the user-level config file: the developer's own
    ~/.config/footman/config.toml must never leak settings into the suite,
    so FOOTMAN_CONFIG points at a path that doesn't exist. The
    step-alignment width is a per-run learning global for the same reason:
    reset it, or one test's wide command pads another's lines. NO_COLOR /
    FORCE_COLOR are cleared too: the colour resolution reads them, and the suite
    dogfoods footman — which now pushes one or the other into every child it
    spawns — so a run under `fm check` would otherwise leak an ambient colour
    decision into tests that assume a clean environment (a test that needs one
    sets it with monkeypatch). The uv-handoff guards are cleared for a third
    reason: the handoff paths write FOOTMAN_UV_REEXEC straight into
    `os.environ` before their exec, so a handoff test poisons every later
    test in its worker — child_python answered None only when the scheduler
    happened to run one first, which wore a flake's face for days."""
    monkeypatch.delenv("FOOTMAN_CACHE_DIR", raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.delenv("FOOTMAN_UV_REEXEC", raising=False)
    monkeypatch.delenv("FOOTMAN_NO_UV", raising=False)
    monkeypatch.setenv(
        "FOOTMAN_CONFIG",
        str(tmp_path_factory.getbasetemp() / "no-global-config.toml"),
    )
    # And the config *directory*: the user tasks file resolves through it, so
    # without this a developer's real ~/.config/footman/tasks.py would answer
    # in every test that expects an empty cascade — an environment-dependent
    # flake wearing a "no tasks file found" regression's face.
    monkeypatch.setenv(
        "FOOTMAN_CONFIG_DIR",
        str(tmp_path_factory.getbasetemp() / "no-global-config-dir"),
    )
    from livery.footman import context

    context.seed_cmd_width(0)


def load_tasks(path: Path) -> registry.Group:
    """Import a tasks file into an isolated registry (no global leak).

    Importing under `registry.capture()` keeps the ~25 sample tasks out of the
    process-global `registry.root` — isolating both directions: prior session
    state can't pollute the fixture, and the fixture can't pollute later tests.
    """
    spec = importlib.util.spec_from_file_location("sample_tasks", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    with registry.capture() as captured:
        spec.loader.exec_module(module)
    return captured


@pytest.fixture
def root() -> registry.Group:
    return load_tasks(FIXTURE)


@pytest.fixture
def tree(root: registry.Group) -> dict[str, Any]:
    built: dict[str, Any] = _manifest.build_manifest(root)["tree"]
    return built


@pytest.fixture(autouse=True)
def _isolated_data_home(tmp_path_factory, monkeypatch):
    """No test reads the developer's own data directory.

    `builtins.json` lives there — the discovered built-in list `fm
    self.install` writes — and `_builtin()` reads it on every run that
    reaches global mode. So a maintainer whose *global* `fm` has a plugin
    installed beside it gets that plugin's entry point demanded by this
    repo's test suite, where it is not installed, and `_app.run(["--help"])`
    exits 64 with "a discovered built-in did not mount". Green or red
    depending on what is installed on the machine, which is the one thing
    a test must never be. (Seen for real: a `self.install` after a release
    recorded `livery.workshop` and turned five tests red locally while CI —
    which has no such file — stayed green.)
    """
    monkeypatch.setenv("FOOTMAN_DATA_DIR", str(tmp_path_factory.mktemp("data-home")))


@pytest.fixture(autouse=True)
def _stock_brand_locations():
    """Every test starts reading stock footman's locations.

    `App.run` points `_paths` at its brand's world and deliberately does
    *not* restore it: a real process runs one brand for its whole life, and
    `run` may `execv` away before any restore could fire. Inside one xdist
    worker that persistence leaks — a test driving `App(name="acme")` leaves
    the config table set to `[tool.acme]`, and the next test to read a
    `[tool.footman]` table gets nothing back, which surfaces as a setting
    silently not applying rather than as an error. `Runner` restores around
    each invocation; this does the same for tests that drive `App.run` or
    `_app.run` directly.
    """
    from livery.footman import _paths

    before = _paths.child_args()
    yield
    _paths.configure_child(*before)


@pytest.fixture(autouse=True)
def _clean_abort_state():
    """Every test starts with the abort latch clear.

    The latch is process-global and deliberately survives an exception
    unwind (a Ctrl-C must keep reaping children spawned after it). Inside
    one xdist worker that durability leaks across tests: a test that
    drives an abort and unwinds by exception leaves the latch set, and
    the next test on that worker to spawn a bare run() child has it
    reaped at registration (SIGTERM, code -15) — or, through the nofail
    paths, silently read back empty output. Seen four times in two days
    as machine-dependent flakes; order-dependent, so no single test
    reproduces it. run_plan resets the latch at run start for real runs;
    this does the same for every test, and clears it again afterwards:
    the worker goes on to run other packages' tests, which spawn bare
    run() children of their own and have no fixture of this kind, so a
    latch left by the last footman test on the worker reaped a
    workshop birth test's `git init` (code -15) under load.
    """
    context.reset_abort()
    yield
    context.reset_abort()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Append discovery forensics to the two flake families' failures.

    The branding and docs-plugin tests have failed under full-suite
    load with a foreign tasks file entering discovery (livery#263),
    and the failures resist on-demand reproduction. When one fails,
    the report carries the state that steers discovery, so the next
    natural occurrence names its own leak.
    """
    report = yield
    if call.when != "call" or not report.failed:
        return report
    name = str(getattr(item, "fspath", ""))
    if "test_branding" not in name and "test_tasks_plugin" not in name:
        return report
    import os
    import sys
    from importlib.metadata import entry_points

    lines = ["", "--- discovery forensics (livery#263) ---", f"cwd: {os.getcwd()}"]
    for key in sorted(os.environ):
        if any(part in key for part in ("XDG_", "FOOTMAN", "FM_", "ACME")):
            lines.append(f"env {key}={os.environ[key]!r}")
    # The lookup that fails reads the entry points of whatever
    # sys.path holds at that moment, in this very process: the path,
    # the interpreter, and the census as seen from here decide
    # whether the leak is a rewritten environment or a polluted
    # import system on this worker.
    lines.append(f"sys.executable: {sys.executable}")
    lines.append(f"sys.prefix: {sys.prefix}")
    lines.extend(f"sys.path[{i}]: {entry}" for i, entry in enumerate(sys.path))
    census = sorted(ep.name for ep in entry_points(group="footman.tasks"))
    lines.append(f"footman.tasks entry points now: {', '.join(census) or 'none'}")
    tasks_modules = [
        f"sys.modules[{module_name!r}] <- {getattr(module, '__file__', None)}"
        for module_name, module in sorted(sys.modules.items())
        if module_name.startswith("footman_tasks")
    ]
    lines.extend(tasks_modules or ["no footman_tasks_* modules loaded"])
    report.longrepr = f"{report.longrepr}\n" + "\n".join(lines)
    return report
