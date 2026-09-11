"""The conformance scenarios, run through the package's own harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from livery.strongroom import (
    ConformanceFailure,
    Namespace,
    PythonHooks,
    Scenario,
    Store,
    _lifecycle,
    _rungs,
    load_scenarios,
    run_scenario,
)

CONFORMANCE = Path(__file__).resolve().parents[1] / "spec" / "conformance"


def _open(tmp_path: Path) -> Any:
    def open_store(namespaces: tuple[Namespace, ...]) -> Store:
        return Store.create(
            tmp_path / "store",
            namespaces=list(namespaces),
            lock_timeout=0.1,
            lock_stale=60,
        )

    return open_store


def _scenario(
    *steps: dict[str, Any], namespaces: tuple[Namespace, ...] = ()
) -> Scenario:
    return Scenario("synthetic", "case", namespaces, tuple(steps))


# The harness's own refusals first: a harness that passes a wrong
# scenario proves nothing.


def test_an_unknown_operation_fails_naming_the_step(tmp_path: Path) -> None:
    with pytest.raises(
        ConformanceFailure, match=r"synthetic:case step 0 \(teleport\): no such"
    ):
        run_scenario(
            _scenario({"op": "teleport"}), _open(tmp_path), PythonHooks(), tmp_path
        )


def test_an_unknown_expectation_fails(tmp_path: Path) -> None:
    scenario = _scenario(
        {"op": "put", "data": "a", "as": "A"},
        {
            "op": "set",
            "namespace": "pins",
            "path": "x",
            "digest": "A",
            "previous": None,
            "expect": "maybe",
        },
    )
    with pytest.raises(ConformanceFailure, match="unknown expectation 'maybe'"):
        run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)


def test_a_refusal_that_does_not_happen_fails(tmp_path: Path) -> None:
    scenario = _scenario(
        {"op": "put", "data": "a", "as": "A"},
        {
            "op": "set",
            "namespace": "pins",
            "path": "x",
            "digest": "A",
            "previous": None,
            "expect": "conflict",
        },
    )
    with pytest.raises(
        ConformanceFailure, match="expected conflict, nothing was refused"
    ):
        run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)


def test_the_wrong_refusal_fails_naming_both(tmp_path: Path) -> None:
    scenario = _scenario(
        {"op": "put", "data": "a", "as": "A"},
        {
            "op": "set",
            "namespace": "nope",
            "path": "x",
            "digest": "A",
            "previous": None,
            "expect": "conflict",
        },
    )
    with pytest.raises(
        ConformanceFailure, match="expected conflict, got UnknownNamespace"
    ):
        run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)


def test_a_wrong_observation_fails_with_what_was_found(tmp_path: Path) -> None:
    checks: list[tuple[dict[str, Any], str | None]] = [
        (
            {"op": "state", "digest": "A", "expect": "absent"},
            "state is present, not absent",
        ),
        (
            {"op": "ref", "namespace": "pins", "path": "x", "expect": "A"},
            "ref names None",
        ),
        ({"op": "pending", "expect": []}, None),
        ({"op": "sweep", "expect_removed": []}, "removed"),
        ({"op": "exists", "at": "v", "path": "x", "expect": True}, "exists is False"),
    ]
    for step, message in checks:
        scenario = _scenario({"op": "put", "data": "a", "as": "A"}, step)
        if message is None:
            run_scenario(
                scenario, _open(tmp_path / step["op"]), PythonHooks(), tmp_path
            )
        else:
            with pytest.raises(ConformanceFailure, match=message):
                run_scenario(
                    scenario, _open(tmp_path / step["op"]), PythonHooks(), tmp_path
                )


def test_a_null_where_a_name_is_required_fails(tmp_path: Path) -> None:
    scenario = _scenario({"op": "begin", "target": None, "as": "P", "expect": "ok"})
    with pytest.raises(ConformanceFailure, match="None names nothing"):
        run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)


def test_view_checks_fail_with_what_was_found(tmp_path: Path) -> None:
    base = _scenario(
        {"op": "put", "data": "a", "as": "A"},
        {"op": "tree", "entries": {"a": "A"}, "as": "T"},
        {"op": "view", "tree": "T", "at": "v", "as": "V"},
        {"op": "entry", "view": "V", "path": "missing", "expect_rung": ["copy"]},
    )
    with pytest.raises(ConformanceFailure, match="no entry 'missing'"):
        run_scenario(base, _open(tmp_path / "1"), PythonHooks(), tmp_path / "1")
    wrong_rung = Scenario(
        "synthetic",
        "case",
        (),
        (
            *base.steps[:3],
            {"op": "entry", "view": "V", "path": "a", "expect_rung": ["parked"]},
        ),
    )
    with pytest.raises(ConformanceFailure, match=r"was made by .*, not one of"):
        run_scenario(wrong_rung, _open(tmp_path / "2"), PythonHooks(), tmp_path / "2")
    wrong_collect = Scenario(
        "synthetic",
        "case",
        (),
        (*base.steps[:3], {"op": "collect", "at": "v", "declared": [], "expect": "A"}),
    )
    with pytest.raises(ConformanceFailure, match=r"collected .*, not"):
        run_scenario(
            wrong_collect, _open(tmp_path / "3"), PythonHooks(), tmp_path / "3"
        )
    wrong_drop = Scenario(
        "synthetic",
        "case",
        (),
        (
            *base.steps[:3],
            {"op": "drop-view", "view": "V", "expect_left": ["something"]},
        ),
    )
    with pytest.raises(ConformanceFailure, match="drop left"):
        run_scenario(wrong_drop, _open(tmp_path / "4"), PythonHooks(), tmp_path / "4")


def test_the_hooks_are_restored_when_a_scenario_fails(tmp_path: Path) -> None:
    symlink, after_mark = _rungs.symlink, _lifecycle.after_mark
    scenario = _scenario(
        {"op": "put", "data": "a", "as": "A"},
        {"op": "tree", "entries": {"a": "A"}, "as": "T"},
        {"op": "refuse-symlinks"},
        {"op": "sweep", "begin_during": "T", "expect_removed": ["A"]},
    )
    with pytest.raises(ConformanceFailure):
        run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)
    assert _rungs.symlink is symlink
    assert _lifecycle.after_mark is after_mark


# Then the scenarios themselves, every file under spec/conformance.


@pytest.mark.parametrize(
    "scenario", [pytest.param(s, id=s.name) for s in load_scenarios(CONFORMANCE)]
)
def test_scenario(scenario: Scenario, tmp_path: Path) -> None:
    run_scenario(scenario, _open(tmp_path), PythonHooks(), tmp_path)


def test_every_conformance_file_has_a_scenario() -> None:
    files = {scenario.file for scenario in load_scenarios(CONFORMANCE)}
    assert files == {"lifecycle", "refs", "views"}
    assert sorted(path.stem for path in CONFORMANCE.glob("*.json")) == sorted(files)
