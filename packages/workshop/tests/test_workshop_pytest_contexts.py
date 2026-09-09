"""The context plugin: quiet outside a metered run, then the names it records."""

from __future__ import annotations

from importlib.metadata import entry_points

import coverage
import pytest
from coverage.exceptions import CoverageException

from livery.workshop import _pytest_contexts


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


def test_without_a_process_meter_every_hook_is_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_pytest_contexts.ARMED, "pyproject.toml")
    monkeypatch.delattr(coverage.process_startup, "coverage", raising=False)
    item = _Item()
    _pytest_contexts.pytest_runtest_setup(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_call(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_teardown(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_logfinish(item.nodeid, None)


def test_an_unarmed_run_leaves_even_a_present_meter_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pytest-cov's own workers carry a meter of their own; only a run
    # armed by coverage's variable is this plugin's to name.
    monkeypatch.delenv(_pytest_contexts.ARMED, raising=False)
    meter = _Meter()
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    _pytest_contexts.pytest_runtest_call(_Item())  # type: ignore[arg-type]
    assert meter.contexts == []


def test_a_meter_that_is_not_collecting_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_pytest_contexts.ARMED, "pyproject.toml")
    meter = _Meter(started=False)
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    _pytest_contexts.pytest_runtest_call(_Item())  # type: ignore[arg-type]
    assert meter.contexts == []


def test_each_phase_records_under_the_tests_node_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_pytest_contexts.ARMED, "pyproject.toml")
    meter = _Meter()
    monkeypatch.setattr(coverage.process_startup, "coverage", meter, raising=False)
    item = _Item()
    _pytest_contexts.pytest_runtest_setup(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_call(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_teardown(item)  # type: ignore[arg-type]
    _pytest_contexts.pytest_runtest_logfinish(item.nodeid, None)
    assert meter.contexts == [
        "packages/x/tests/test_mod.py::test_one|setup",
        "packages/x/tests/test_mod.py::test_one|run",
        "packages/x/tests/test_mod.py::test_one|teardown",
        "",
    ]


def test_the_plugin_rides_the_pytest11_entry_point() -> None:
    names = {ep.value for ep in entry_points(group="pytest11")}
    assert "livery.workshop._pytest_contexts" in names
