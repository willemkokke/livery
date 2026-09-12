"""The graded release trigger's classifier: additions ship, drops wait."""

from __future__ import annotations

from typing import Any

import pytest

from livery.toolroom.tools._machinery import _tasks


def _classify(monkeypatch, spans: dict[str, dict[str, Any]]) -> bool:
    monkeypatch.setattr(_tasks._toolhistory, "load", lambda path: {"chain": []})
    monkeypatch.setattr(_tasks, "_predecessor", lambda doc, version: "0.0.0")
    monkeypatch.setattr(
        _tasks._toolhistory,
        "changes",
        lambda doc, *, since, until: spans[until],
    )
    return _tasks._additions_only({k: [k] for k in spans})


def test_pure_additions_light_the_green(monkeypatch):
    # `changes()` steps back: forward additions land under `drop`.
    assert _classify(monkeypatch, {"1.1.0": {"drop": {"": ["fix"]}}}) is True


def _refreshed(events: dict[str, list[str]], *, additions_only: bool):
    return _tasks.Refreshed(
        read={},
        events=events,
        unreachable={},
        skipped=[],
        additions_only=additions_only,
    )


def test_a_refresh_that_moved_nothing_submits_nothing():
    calls: list[tuple[str, ...]] = []
    lines = _tasks.submit_refresh(
        _refreshed({}, additions_only=False),
        git=lambda *args: calls.append(args),
        submit=lambda argv: 0,
    )
    assert lines == ["  nothing moved: no branch, no pull request"]
    assert calls == []


def test_a_refused_submit_names_the_branch_that_stands():
    from datetime import date

    from livery.footman import Failed

    with pytest.raises(
        (Failed, SystemExit),
        match="exited 3; the branch chore/tools-refresh-20260914 stands",
    ):
        _tasks.submit_refresh(
            _refreshed({"ruff": ["0.9.0"]}, additions_only=True),
            git=lambda *args: None,
            submit=lambda argv: 3,
            on=date(2026, 9, 14),
        )


def test_additions_submit_armed_and_a_drop_waits_for_a_person():
    from datetime import date

    calls: list[tuple[str, ...]] = []
    argvs: list[list[str]] = []

    def submit(argv: list[str]) -> int:
        argvs.append(argv)
        return 0

    lines = _tasks.submit_refresh(
        _refreshed({"ruff": ["0.9.0"], "bun": ["1.3.14"]}, additions_only=True),
        git=lambda *args: calls.append(args),
        submit=submit,
        on=date(2026, 9, 14),
    )
    assert calls[0] == ("switch", "-c", "chore/tools-refresh-20260914")
    assert calls[1] == ("add", "-A")
    assert calls[2][:2] == ("commit", "-m") and "bun, ruff" in calls[2][2]
    assert argvs[0][1:] == [
        "submit",
        "--title=chore(toolroom): tool refresh 2026-09-14",
        "--armed",
    ]
    assert lines == [
        "  refreshed bun, ruff on chore/tools-refresh-20260914",
        "  submitted armed: additions only",
    ]
    argvs.clear()
    lines = _tasks.submit_refresh(
        _refreshed({"ruff": ["0.9.0"]}, additions_only=False),
        git=lambda *args: None,
        submit=submit,
        on=date(2026, 9, 14),
    )
    assert "--armed" not in argvs[0]
    assert lines[1] == "  submitted unarmed: a surface lost something, a person decides"


def test_any_removal_holds_for_a_human(monkeypatch):
    assert (
        _classify(
            monkeypatch,
            {"1.1.0": {"drop": {"": ["fix"]}}, "2.0.0": {"add": {"": ["old"]}}},
        )
        is False
    )


def test_no_events_is_not_safe_to_ship():
    assert _tasks._additions_only({}) is False
    assert _tasks._additions_only({"ruff": []}) is False
