"""The context plugin: quiet outside a metered run, then the one name per test."""

from __future__ import annotations

import sys
from importlib.metadata import entry_points

import coverage
import pytest
from coverage.exceptions import CoverageException

from livery.extensions.pytest import _contexts


class _Item:
    nodeid = "packages/x/tests/test_mod.py::test_one"


class _Meter:
    def __init__(self, *, started: bool = True) -> None:
        self.started = started
        self.contexts: list[str] = []

    def switch_context(self, context: str) -> str | None:
        if not self.started:
            raise CoverageException("Cannot switch context, coverage is not started")
        self.contexts.append(context)
        return None


def test_without_coverage_installed_the_plugin_finds_no_meter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The plugin loads in every pytest the venv starts, and a workspace
    # that lists no coverage installs none: armed by hand there, it
    # imports nothing it lacks. The import fails for the call alone, so
    # the meter of a measured run around this test is left alone.
    monkeypatch.setenv(_contexts.ARMED, "pyproject.toml")
    with monkeypatch.context() as patch:
        patch.setitem(sys.modules, "coverage", None)
        assert _contexts._meter() is None


def test_without_a_process_meter_every_hook_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_contexts.ARMED, "pyproject.toml")
    monkeypatch.delattr(coverage.process_startup, "coverage", raising=False)
    item = _Item()
    _contexts.pytest_runtest_setup(item)  # type: ignore[arg-type]
    _contexts.pytest_runtest_logfinish(item.nodeid, None)


def test_an_unarmed_run_leaves_even_a_present_meter_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pytest-cov's own workers carry a meter of their own; only a run
    # armed by coverage's variable is this plugin's to name.
    monkeypatch.delenv(_contexts.ARMED, raising=False)
    meter = _Meter()
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    _contexts.pytest_runtest_setup(_Item())  # type: ignore[arg-type]
    assert meter.contexts == []


def test_a_meter_that_is_not_collecting_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_contexts.ARMED, "pyproject.toml")
    meter = _Meter(started=False)
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    _contexts.pytest_runtest_setup(_Item())  # type: ignore[arg-type]
    assert meter.contexts == []


def test_a_test_records_under_its_node_id_from_setup_to_teardown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_contexts.ARMED, "pyproject.toml")
    meter = _Meter()
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    item = _Item()
    _contexts.pytest_runtest_setup(item)  # type: ignore[arg-type]
    _contexts.pytest_runtest_logfinish(item.nodeid, None)
    # One context per test: its setup, body and teardown alike, then
    # the empty context again once it is over.
    assert meter.contexts == ["packages/x/tests/test_mod.py::test_one", ""]
    assert not hasattr(_contexts, "pytest_runtest_call")
    assert not hasattr(_contexts, "pytest_runtest_teardown")


def test_the_plugin_rides_the_pytest11_entry_point() -> None:
    names = {ep.value for ep in entry_points(group="pytest11")}
    assert "livery.extensions.pytest._contexts" in names
