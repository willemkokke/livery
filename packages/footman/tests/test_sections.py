"""Task-authored profiling: `section()`, `stream()`, `mark()` — where the
records land, how they reach the `--json` envelope, and the taught edges."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest

import livery.footman as footman
from livery.footman import context
from livery.footman.testing import Runner

TASKS = textwrap.dedent(
    """
    import time
    from datetime import datetime, timedelta

    import livery.footman as footman
    from livery.footman import task

    @task
    def worked():
        with footman.section("outer"):
            with footman.section("inner"):
                time.sleep(0.01)
        footman.mark("moment")
        ci = footman.stream("ci")
        now = datetime.now()
        ci.section("early", start=now - timedelta(seconds=5), end=now)
        footman.run(["python", "-c", "pass"])

    @task(pre=[worked])
    def gated():
        pass

    @task
    def fanned():
        footman.parallel(
            footman.step(_noted)("one"),
            footman.step(_noted)("two"),
        )

    def _noted(label):
        with footman.section(label):
            time.sleep(0.005)
    """
)


def _run_json(tmp_path: Path, line: str) -> dict[str, Any]:
    src = tmp_path / "tasks.py"
    src.write_text(TASKS)
    result = Runner().invoke(f"--json {line}", tasks=src)
    assert result.ok, result.stderr
    payload: dict[str, Any] = json.loads(result.stdout)
    return payload


def _item(payload: dict[str, Any], task: str) -> dict[str, Any]:
    return next(i for i in payload["items"] if i.get("task") == task)


def test_sections_reach_the_envelope_with_their_placement(tmp_path):
    payload = _run_json(tmp_path, "worked")
    sections = _item(payload, "worked")["sections"]
    by_name = {s["name"]: s for s in sections}
    assert set(by_name) == {"outer", "inner", "moment", "early"}

    # Nesting is containment: inner sits inside outer, on the task's own
    # timeline (no stream key), placed relative to the task's start.
    outer, inner = by_name["outer"], by_name["inner"]
    assert "stream" not in outer
    assert outer["at_ms"] <= inner["at_ms"]
    assert inner["at_ms"] + inner["duration_ms"] <= (
        outer["at_ms"] + outer["duration_ms"] + 0.001
    )
    assert by_name["moment"]["duration_ms"] == 0.0  # a mark is an instant

    # A retroactive stream window keeps its wall-clock placement — before
    # the task even started, so the offset is negative, and that is legal.
    early = by_name["early"]
    assert early["stream"] == "ci"
    assert early["at_ms"] < 0
    assert early["duration_ms"] == pytest.approx(5000, abs=500)


def test_steps_carry_their_placement(tmp_path):
    payload = _run_json(tmp_path, "worked")
    step = next(i for i in payload["items"] if "command" in i)
    assert step["at_ms"] >= 0  # inside the task's span, on the same clock


def test_the_plan_edges_ride_the_row(tmp_path):
    payload = _run_json(tmp_path, "gated")
    assert _item(payload, "gated")["after"] == ["worked"]
    assert "after" not in _item(payload, "worked")  # a root has no edges


def test_parallel_children_fold_into_the_requester(tmp_path):
    payload = _run_json(tmp_path, "fanned")
    names = {s["name"] for s in _item(payload, "fanned")["sections"]}
    assert names == {"one", "two"}


def test_outside_a_task_is_taught():
    for reach in (
        lambda: footman.section("x"),
        lambda: footman.stream("ci"),
        lambda: footman.mark("x"),
    ):
        with pytest.raises(RuntimeError, match=r"inside a task body"):
            reach()


def test_the_empty_stream_name_is_taught():
    with pytest.raises(ValueError, match=r"the task's own timeline"):
        context.stream("")


def test_half_a_window_and_an_inside_out_window_are_refused():
    ci = context.Stream(context.Context(), "ci")
    with pytest.raises(ValueError, match=r"both start= and end="):
        ci.section("x", start=1.0)  # type: ignore[call-overload]
    with pytest.raises(ValueError, match=r"inside out"):
        ci.section("x", start=2.0, end=1.0)
    with pytest.raises(TypeError, match=r"datetime or epoch seconds"):
        ci.section("x", start="yesterday", end="today")  # type: ignore[call-overload]


def test_a_wall_clock_window_lands_on_the_run_clock():
    import time as _time

    ci = context.Stream(context.Context(), "ci")
    now = _time.time()
    ci.section("w", start=now - 1.0, end=now)
    (record,) = ci._ctx.sections
    assert record.duration == pytest.approx(1.0)
    # The mapped moment sits near "one second ago" on the run clock.
    assert record.started + record.duration == pytest.approx(
        _time.perf_counter(), abs=1.0
    )
