"""The profile built-in: `--profile` writes a Chrome-trace file of the run."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from livery.footman.testing import Runner

TASKS = textwrap.dedent(
    """
    import time
    from datetime import datetime, timedelta

    import livery.footman as footman
    from livery.footman import task
    from livery.footman.compose import plugin

    plugin("footman.profile")

    @task
    def fast():
        with footman.section("thinking"):
            time.sleep(0.01)
        footman.mark("done thinking")

    @task(pre=[fast])
    def slow():
        ci = footman.stream("ci")
        t1 = datetime.now()
        t0 = t1 - timedelta(seconds=0.2)
        ci.section("build-linux", start=t0, end=t1)
        ci.section("build-macos", start=t0, end=t1 - timedelta(seconds=0.1))
        with ci.section("poll"):
            time.sleep(0.01)
        footman.run(["python", "-c", "pass"])

    def _sleeper(tag):
        footman.run(["python", "-c", "import time; time.sleep(0.05)"])

    @task
    def fanned():
        footman.parallel(
            footman.step(_sleeper)("a"),
            footman.step(_sleeper)("b"),
        )
    """
)


def _tasks(tmp_path: Path) -> Path:
    src = tmp_path / "tasks.py"
    src.write_text(TASKS)
    return src


def _trace(path: Path) -> list[dict[str, Any]]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    events: list[dict[str, Any]] = payload["traceEvents"]
    return events


def test_bare_profile_writes_the_default_filename(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("--profile slow", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    target = tmp_path / "fm-profile.json"
    assert target.is_file()
    assert str(target) in result.stderr  # the receipt names the file


def test_attached_profile_names_the_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("--profile=custom.json fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    assert (tmp_path / "custom.json").is_file()
    assert not (tmp_path / "fm-profile.json").exists()


def test_without_the_flag_nothing_is_written(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("slow", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    assert list(tmp_path.glob("*.json")) == []


def test_the_space_form_is_taught(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("--profile out.json slow", tasks=_tasks(tmp_path))
    assert not result.ok
    assert "--profile=out.json" in result.stderr


def test_the_trace_carries_the_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("--profile slow", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")

    tasks = {e["name"]: e for e in events if e.get("cat") == "task"}
    assert set(tasks) == {"fast", "slow"}
    assert tasks["slow"]["args"]["state"] == "ok"
    assert "queue_ms" in tasks["slow"]["args"]  # it has a prerequisite

    # The task's own timeline: the section nests inside the task slice, the
    # mark is an instant on the same track.
    section = next(e for e in events if e.get("cat") == "section")
    fast = tasks["fast"]
    assert fast["ts"] <= section["ts"]
    assert section["ts"] + section["dur"] <= fast["ts"] + fast["dur"] + 1
    mark = next(e for e in events if e.get("cat") == "mark")
    assert mark["ph"] == "i" and "dur" not in mark

    # A named stream renders async — begin/end pairs, overlap legal — and a
    # retroactive window may predate the run (the trace zero absorbs it).
    streamed = [e for e in events if e.get("cat") == "stream: ci"]
    assert sorted(e["ph"] for e in streamed) == ["b", "b", "b", "e", "e", "e"]
    assert all(e["ts"] >= 0 for e in events if "ts" in e)

    # The run() step is a slice inside `slow`; the dependency edge is a flow
    # arrow; the writer timed its own serialisation.
    assert any(e.get("cat") == "step" and e["ph"] == "X" for e in events)
    assert {e["ph"] for e in events if e.get("cat") == "dep"} == {"s", "f"}
    assert any(e["name"] == "profile: write" for e in events)
    workers = [e for e in events if e["name"] == "thread_name"]
    assert workers  # every used track is named


SECRET_TASKS = textwrap.dedent(
    """
    import livery.footman as footman
    from livery.footman import task
    from livery.footman.compose import plugin
    from livery.footman.params import Secret

    plugin("footman.profile")

    @task
    def login():
        footman.run(["python", "-c", "pass", Secret("hunter2")])
    """
)


def test_a_span_is_named_by_the_shown_command(tmp_path, monkeypatch):
    """A trace is a file that gets attached to tickets and dropped into
    ui.perfetto.dev — SECURITY.md names it in scope. The span reads the
    shown line, not the record's."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(SECRET_TASKS)
    result = Runner().invoke("--profile login", tasks=src)
    assert result.ok, result.stderr
    target = tmp_path / "fm-profile.json"
    assert "hunter2" not in target.read_text(encoding="utf-8")
    step = next(e for e in _trace(target) if e.get("cat") == "step")
    assert step["name"] == "python -c pass ***"


def test_overlapping_child_steps_render_async_not_stacked(tmp_path, monkeypatch):
    # parallel() folds child steps onto the parent with their real, mutually
    # overlapping times: those must leave the X lane (which renders by
    # containment) for begin/end pairs. The nesting invariant on every
    # track is the pin.
    monkeypatch.chdir(tmp_path)
    result = Runner().invoke("--profile fanned", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    slices: dict[int, list[tuple[float, float]]] = {}
    for e in events:
        if e["ph"] == "X":
            slices.setdefault(e.get("tid", 0), []).append((e["ts"], e["ts"] + e["dur"]))
    for spans in slices.values():
        open_ends: list[float] = []
        for start, end in sorted(spans, key=lambda s: (s[0], -(s[1] - s[0]))):
            while open_ends and open_ends[-1] <= start + 0.001:
                open_ends.pop()
            assert not open_ends or end <= open_ends[-1] + 0.001
            open_ends.append(end)


FRAGMENT_TASKS = textwrap.dedent(
    """
    import json
    import os
    import sys
    import time

    import livery.footman as footman
    from livery.footman import task
    from livery.footman.compose import plugin

    plugin("footman.profile")

    @task
    def drops():
        sink = os.environ["FM_PROFILE_DIR"]
        now = time.time() * 1e6
        with open(os.path.join(sink, "a-child.json"), "w") as f:
            json.dump({"traceEvents": [
                {"ph": "X", "cat": "child", "name": "child work", "pid": 4242,
                 "tid": 1, "ts": now - 50_000, "dur": 50_000.0},
            ]}, f)
        with open(os.path.join(sink, "bare.json"), "w") as f:
            json.dump([{"ph": "i", "s": "g", "cat": "child", "name": "bare form",
                        "pid": 4242, "tid": 1, "ts": now}], f)
        with open(os.path.join(sink, "broken.json"), "w") as f:
            f.write("not json")

    @task
    def suite():
        footman.run([sys.executable, "-m", "pytest", "test_tiny.py",
                     "-p", "no:cacheprovider", "-q"])
    """
)


def test_a_child_fragment_is_embedded_on_the_run_clock(tmp_path, monkeypatch):
    import os

    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(FRAGMENT_TASKS)
    result = Runner().invoke("--profile drops", tasks=src)
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    child = [e for e in events if e.get("cat") == "child"]
    assert {e["name"] for e in child} == {"child work", "bare form"}
    slice_ = next(e for e in child if e["name"] == "child work")
    assert slice_["pid"] == 4242  # the child keeps its own process group
    assert -1e6 < slice_["ts"] < 60e6  # shifted onto the run clock, near the run
    assert "broken.json" in result.stderr  # the bad drop is named, never fatal
    assert "FM_PROFILE_DIR" not in os.environ  # the drop is consumed


def test_pytest_joins_the_profile(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "test_tiny.py").write_text(
        "def test_one():\n    assert True\n\ndef test_two():\n    assert True\n"
    )
    src = tmp_path / "tasks.py"
    src.write_text(FRAGMENT_TASKS)
    result = Runner().invoke("--profile suite", tasks=src)
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    calls = [e for e in events if e.get("cat") == "test.call"]
    assert {e["name"] for e in calls} == {
        "test_tiny.py::test_one",
        "test_tiny.py::test_two",
    }
    (pid,) = {e["pid"] for e in calls}
    assert pid != 1  # its own process group, beside fm's
    assert any(
        e["name"] == "process_name" and e.get("args", {}).get("name") == "pytest"
        for e in events
        if e["ph"] == "M"
    )


def test_partial_overlap_is_classified_async_containment_is_not():
    # The deterministic pin for the routing above: containment may share a
    # track, a partial overlap may not.
    from livery.footman.profile import _nests

    a = {"ts": 0.0, "dur": 100.0, "name": "a"}
    contained = {"ts": 10.0, "dur": 20.0, "name": "contained"}
    straddles = {"ts": 50.0, "dur": 100.0, "name": "straddles"}
    nested, overflow = _nests([a, straddles, contained])
    assert [s["name"] for s in nested] == ["a", "contained"]
    assert [s["name"] for s in overflow] == ["straddles"]
