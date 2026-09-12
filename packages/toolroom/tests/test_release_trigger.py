"""The graded release trigger's classifier: additions ship, drops wait."""

from __future__ import annotations

from typing import Any

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
