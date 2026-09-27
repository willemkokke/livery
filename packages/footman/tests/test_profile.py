"""The profile built-in: `--profile` writes a Chrome-trace file of the run."""

from __future__ import annotations

import json
import os
import shutil
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

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


CHILD_TASKS = textwrap.dedent(
    """
    import sys

    import livery.footman as footman
    from livery.footman import task
    from livery.footman.compose import plugin

    plugin("footman.profile")

    @task
    def deepest():
        with footman.section("deep thinking"):
            pass

    @task
    def inner():
        footman.run([sys.executable, "-m", "livery.footman", "deepest"])

    @task
    def outer():
        footman.run([sys.executable, "-m", "livery.footman", "inner"])
    """
)

HANDOFF_TASKS = textwrap.dedent(
    """
    import json
    import os
    import sys
    from pathlib import Path

    import livery.footman as footman
    from livery.footman import task
    from livery.footman.compose import plugin
    from livery.footman import profile

    plugin("footman.profile")

    @task
    def replaced():
        \"\"\"The reconcile's shape: work, then a block that does not return.\"\"\"
        footman.run([sys.executable, "-c", "pass"])
        with profile.handing_off() as traced:
            Path("handed.json").write_text(json.dumps(traced), encoding="utf-8")
            footman.fail("replaced by the successor")

    @task
    def stayed():
        \"\"\"The same, where replacing the process did not work.\"\"\"
        footman.run([sys.executable, "-c", "pass"])
        with profile.handing_off() as traced:
            Path("handed.json").write_text(json.dumps(traced), encoding="utf-8")

    @task
    def unwatched():
        r'''The same again, in a run nobody asked for a trace of.'''
        with profile.handing_off() as traced:
            Path("handed.json").write_text(json.dumps(traced), encoding="utf-8")

    @task
    def loses_the_box():
        r'''A box that goes while the run it belongs to is still going.'''
        import shutil

        shutil.rmtree(os.environ["FM_PROFILE_DIR"])
    """
)


@pytest.fixture(autouse=True)
def _own_cache_and_unarmed_after(tmp_path_factory, monkeypatch) -> Iterator[None]:
    """A cache of this file's own, and the plugin stood down afterwards.

    A box is made under `profiles/` in footman's cache, so without an
    override these tests would leave boxes in the developer's real one. And
    the module holds the mode and the box between the two hooks, so a test
    that arms one in this process would otherwise hand it to the next test.
    """
    monkeypatch.setenv("FOOTMAN_CACHE_DIR", str(tmp_path_factory.mktemp("cache")))
    yield
    import livery.footman as footman
    from livery.footman import profile

    os.environ.pop(profile.DROP, None)
    os.environ.pop(profile.HANDOFF, None)
    profile.arm(footman.Invocation())


def _fragments(box: Path) -> list[dict[str, Any]]:
    """Every event in *box*'s fragments, in file order."""
    events: list[dict[str, Any]] = []
    for fragment in sorted(box.glob("*.json")):
        payload: dict[str, Any] = json.loads(fragment.read_text(encoding="utf-8"))
        events += payload["traceEvents"]
    return events


def _groups(events: list[dict[str, Any]]) -> dict[int, str]:
    """The process groups a trace names, by process id."""
    return {
        e["pid"]: e["args"]["name"]
        for e in events
        if e["ph"] == "M" and e["name"] == "process_name"
    }


def test_a_child_whose_box_has_gone_writes_nothing(tmp_path, monkeypatch):
    """A box named by a run that has already ended is not written into.

    The name outlives the directory: a parent removes its box at the end,
    and a stale name in the environment is all a late child sees.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FM_PROFILE_DIR", str(tmp_path / "gone"))
    result = Runner().invoke("fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    assert list(tmp_path.glob("*.json")) == []


def test_the_exit_sweep_removes_every_box_not_only_the_newest(monkeypatch):
    """Two armed runs in one process leave two boxes, and both are swept.

    A run that mentions `--profile` and never reaches the writer — a refusal,
    an interrupt — leaves its box for the exit handler. The handler asks
    whether *this* box is still open, because asking whether it is the newest
    left one directory behind per run in every process that armed more than
    once: a test session, or a runner embedding several invocations.
    """
    import livery.footman as footman
    from livery.footman import profile

    boxes: list[Path] = []
    for _ in range(2):
        profile.arm(footman.Invocation(cli={"profile": Path("fm-profile.json")}))
        boxes.append(Path(os.environ["FM_PROFILE_DIR"]))
    first, second = boxes
    assert first != second
    assert first.is_dir() and second.is_dir()
    # Footman's own cache, where the collector can reach a box that outlives
    # the process which opened it.
    assert first.parent == Path(os.environ["FOOTMAN_CACHE_DIR"]) / "profiles"
    for box in boxes:  # what atexit calls, in the order it registered them
        profile._sweep_orphan(str(box))
    assert not first.exists()
    assert not second.exists()


def test_a_child_run_drops_itself_in_the_box(tmp_path, monkeypatch):
    """No file of its own: the whole run goes in the box, for its owner."""
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    result = Runner().invoke("fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    assert list(tmp_path.glob("*.json")) == []  # the parent owns the file
    assert os.environ["FM_PROFILE_DIR"] == str(box)  # left for a grandchild
    events = _fragments(box)
    assert _groups(events) == {os.getpid(): "fm (child)"}
    task_ = next(e for e in events if e.get("cat") == "task")
    assert task_["name"] == "fast"
    assert task_["ts"] > 1.7e15  # epoch microseconds, the box's convention


def test_a_handoff_taken_back_writes_the_file_after_all(tmp_path, monkeypatch):
    """Returning from the block means nothing replaced this process.

    The trace is this run's after all, so the fragment goes and the file is
    written — a replacement that could not start must not cost the trace of
    the run that tried.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(HANDOFF_TASKS)
    result = Runner().invoke("--profile stayed", tasks=src)
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    assert "fm (before re-exec)" not in _groups(events).values()
    box = Path(json.loads((tmp_path / "handed.json").read_text())["FM_PROFILE_HANDOFF"])
    assert not box.exists()  # swept by the writer, as an unhanded box is


def test_the_handoff_hands_the_box_on_and_writes_no_file(tmp_path, monkeypatch):
    """The work before a re-exec lands in the box, and the file does not.

    On Windows the handoff waits for its replacement instead of becoming it,
    so the process is still here at `post_tasks` with the replacement's own
    file already written. It must not write over it.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(HANDOFF_TASKS)
    result = Runner().invoke("--profile replaced", tasks=src)
    assert not result.ok  # the task failed where the exec would have landed
    assert not (tmp_path / "fm-profile.json").exists()
    box = Path(json.loads((tmp_path / "handed.json").read_text())["FM_PROFILE_HANDOFF"])
    assert box.is_dir()  # the successor's to sweep
    events = _fragments(box)
    assert _groups(events) == {os.getpid(): "fm (before re-exec)"}
    spans = [e for e in events if e["ph"] == "X"]
    (step,) = [e for e in spans if e["cat"] == "step"]
    (lived,) = [e for e in spans if e["cat"] == "task"]
    assert lived["name"] == "replaced"
    assert lived["ts"] <= step["ts"]  # the step ran inside the span
    assert step["ts"] + step["dur"] <= lived["ts"] + lived["dur"]
    shutil.rmtree(box)


def test_the_successor_adopts_the_box_it_was_handed(tmp_path, monkeypatch):
    """The replacement owns the box, embeds its predecessor, and consumes it.

    A fragment from before the exec predates the run that reads it, so the
    whole trace slides: the earliest thing in the file is zero, and no stamp
    is negative.
    """
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "handed"
    box.mkdir()
    before = (time.time() - 30) * 1e6
    (box / "fm-1-2.json").write_text(
        json.dumps(
            {
                "traceEvents": [
                    {
                        "ph": "M",
                        "name": "process_name",
                        "pid": 4243,
                        "args": {"name": "fm (before re-exec)"},
                    },
                    {
                        "ph": "X",
                        "cat": "task",
                        "name": "sync",
                        "pid": 4243,
                        "tid": 1,
                        "ts": before,
                        "dur": 1000.0,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("FM_PROFILE_HANDOFF", str(box))
    result = Runner().invoke("--profile fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    assert _groups(events)[4243] == "fm (before re-exec)"
    assert not box.exists()  # adopted, swept, removed
    assert "FM_PROFILE_HANDOFF" not in os.environ  # never inherited further
    stamps = [e["ts"] for e in events if "ts" in e]
    assert min(stamps) == 0.0
    predecessor = next(e for e in events if e.get("name") == "sync")
    own = next(e for e in events if e.get("name") == "fast")
    assert predecessor["ts"] == 0.0 < own["ts"]


def test_a_profiled_parent_gets_the_inside_of_the_fm_it_spawned(tmp_path, monkeypatch):
    """The point of the phase: a verb that spawns a verb shows both."""
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(CHILD_TASKS)
    result = Runner().invoke("--profile outer", tasks=src)
    assert result.ok, result.stderr
    events = _trace(tmp_path / "fm-profile.json")
    groups = _groups(events)
    assert groups[1] == "fm"
    # Two spawned runs, two groups of their own: the box stays in the
    # environment, so a child of a child finds it the same way.
    assert sorted(groups.values()) == ["fm", "fm (child)", "fm (child)"]
    tasks = {e["name"]: e for e in events if e.get("cat") == "task"}
    assert tasks["outer"]["pid"] == 1
    assert len({tasks["inner"]["pid"], tasks["deepest"]["pid"], 1}) == 3
    for outer, inner in (
        (tasks["outer"], tasks["inner"]),
        (tasks["inner"], tasks["deepest"]),
    ):
        assert outer["ts"] <= inner["ts"]  # inside the step that spawned it
        assert inner["ts"] + inner["dur"] <= outer["ts"] + outer["dur"]
    deep = tasks["deepest"]["pid"]
    assert any(e.get("cat") == "section" and e["pid"] == deep for e in events)


def test_a_re_exec_in_an_unwatched_run_hands_nothing_on(tmp_path, monkeypatch):
    """The common case: a verb that re-execs in a run nobody profiled.

    The block still wraps the exec, so it has to cost nothing and say nothing
    when there is no trace to hand over.
    """
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "tasks.py"
    src.write_text(HANDOFF_TASKS)
    result = Runner().invoke("unwatched", tasks=src)
    assert result.ok, result.stderr
    assert json.loads((tmp_path / "handed.json").read_text(encoding="utf-8")) == {}
    assert list(tmp_path.glob("*profile*.json")) == []


def test_a_box_that_goes_mid_run_is_not_written_into(tmp_path, monkeypatch):
    """The owner of a box can die while a child of it is still running.

    So the box is checked where the fragment is written, not only where the
    run armed: the name in the environment outlives the directory.
    """
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    src = tmp_path / "tasks.py"
    src.write_text(HANDOFF_TASKS)
    result = Runner().invoke("loses-the-box", tasks=src)
    assert result.ok, result.stderr
    assert not box.exists()
    assert list(tmp_path.glob("*.json")) == []


def test_a_fragment_that_cannot_be_written_is_said_and_never_fatal(tmp_path, capsys):
    """A drop is an extra, and an extra never takes the run down with it."""
    from livery.footman import profile

    assert profile._drop(str(tmp_path / "not-a-directory"), []) is None
    assert "fragment not written" in capsys.readouterr().err


def test_the_exit_sweep_leaves_a_box_it_does_not_own(tmp_path):
    """A box this process does not hold is not a box to remove.

    The handler asks whether the box is still open here, so a directory a
    healthy run already swept, or one another process owns, is left alone.
    """
    from livery.footman import profile

    stranger = tmp_path / "someone-elses"
    stranger.mkdir()
    profile._sweep_orphan(str(stranger))
    assert stranger.is_dir()


def test_a_child_hands_on_a_box_that_was_never_its_own(tmp_path, monkeypatch):
    """A child re-execs too, and the box it hands on belongs to an ancestor.

    The successor is told where the box is, exactly as a run that opened one
    would tell it, and the sweeping is still left to whoever opened it. This
    is the shape a CI leg takes: the leg's own `fm` is a child, and it may
    replace itself before it is done.
    """
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    src = tmp_path / "tasks.py"
    src.write_text(HANDOFF_TASKS)
    result = Runner().invoke("replaced", tasks=src)
    assert not result.ok  # the task failed where the exec would have landed
    handed = json.loads((tmp_path / "handed.json").read_text(encoding="utf-8"))
    assert handed == {"FM_PROFILE_HANDOFF": str(box)}
    assert box.is_dir()
    # One fragment, the work before the replacement. The run wrote no second
    # one of its own: it stood down when it handed the box over.
    assert len(list(box.glob("*.json"))) == 1
    assert _groups(_fragments(box)) == {os.getpid(): "fm (before re-exec)"}


def test_the_trace_records_the_epoch_its_own_zero_sits_at(tmp_path, monkeypatch):
    """Every stamp in a file is relative to its run's zero.

    A reader laying one machine's timeline beside another's needs to know
    where that zero was on the wall clock, and no stamp in the file says.
    """
    monkeypatch.chdir(tmp_path)
    before = time.time() * 1e6
    result = Runner().invoke("--profile fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    payload = json.loads((tmp_path / "fm-profile.json").read_text(encoding="utf-8"))
    assert before - 60e6 < payload["originEpochUs"] < time.time() * 1e6


def test_a_profiled_child_hands_its_whole_trace_up_as_well(tmp_path, monkeypatch):
    """A run with a file of its own can still be somebody's child.

    The leg of a CI run is the case: an entry writes the file a later entry
    reads, and the run that spawned it wants that entry's inside rather than
    the one step that spawned it.
    """
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    result = Runner().invoke("--profile=child.json fast", tasks=_tasks(tmp_path))
    assert result.ok, result.stderr
    own = _trace(tmp_path / "child.json")
    assert _groups(own) == {1: "fm"}  # its own file is its own run
    handed = _fragments(box)
    assert _groups(handed) == {os.getpid(): "fm (child)"}
    mine = next(e for e in handed if e.get("cat") == "task")
    assert mine["name"] == "fast"
    assert mine["ts"] > 1.7e15  # epoch microseconds, the box's convention
    # The same task, once as the run's own and once handed up.
    assert [e["name"] for e in own if e.get("cat") == "task"] == ["fast"]


LAID = json.dumps(
    {
        "originEpochUs": 1_000_000.0,
        "traceEvents": [
            {"ph": "M", "name": "process_name", "pid": 1, "args": {"name": "fm"}},
            {
                "ph": "X",
                "cat": "task",
                "name": "check",
                "pid": 1,
                "tid": 7,
                "ts": 0.0,
                "dur": 500.0,
            },
            {
                "ph": "X",
                "cat": "test.call",
                "name": "one",
                "pid": 99,
                "tid": 1,
                "ts": 100.0,
                "dur": 50.0,
            },
            "not an event",
        ],
    }
)


def test_a_trace_that_cannot_be_laid_on_a_clock_says_why():
    """Fallbacks first: the three ways a trace refuses to be placed."""
    import itertools

    from livery.footman import profile

    pids = itertools.count(1000)
    assert profile.laid_on("not json", zero=0.0, pids=pids) == (
        [],
        "the trace does not parse (Expecting value: line 1 column 1 (char 0))",
    )
    assert profile.laid_on("[]", zero=0.0, pids=pids) == (
        [],
        "the trace is not a trace object",
    )
    # A trace with no origin cannot be placed at all: every stamp in it is
    # relative to a moment it never wrote down.
    assert profile.laid_on('{"traceEvents": []}', zero=0.0, pids=pids) == (
        [],
        "the trace records no originEpochUs, so it cannot be placed",
    )
    assert profile.laid_on('{"originEpochUs": 1.0}', zero=0.0, pids=pids) == (
        [],
        "the trace carries no events",
    )


def test_a_trace_is_laid_on_another_clock_by_the_origins_alone():
    """Two clocks, written down independently, are what aligns the two."""
    import itertools

    from livery.footman import profile

    # The reading clock's zero is half a second before this trace's.
    placed, why = profile.laid_on(
        LAID, zero=500_000.0, pids=itertools.count(1000), label="check (ubuntu)"
    )
    assert why == ""
    # Every stamp moved by the difference, and nothing else moved.
    spans = {e["name"]: e for e in placed if e["ph"] == "X"}
    assert spans["check"]["ts"] == 500_000.0
    assert spans["check"]["dur"] == 500.0
    assert spans["one"]["ts"] == 500_100.0
    # Two groups in, two fresh numbers out, and the label says whose they are.
    assert {e["pid"] for e in placed} == {1000, 1001}
    named = next(e for e in placed if e["ph"] == "M")
    assert named["args"]["name"] == "check (ubuntu): fm"
    assert len(placed) == 3  # what is not an event is not carried


def test_a_grandchild_stays_a_group_of_its_own_on_the_way_up(tmp_path, monkeypatch):
    """What a child embedded rides up with the child, still its own.

    A run hands its whole trace to the box above it, the fragments it
    embedded included, and those keep the process ids they came with: a leg
    that spawns a run that spawns a test runner shows all three.
    """
    monkeypatch.chdir(tmp_path)
    box = tmp_path / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    src = tmp_path / "tasks.py"
    src.write_text(FRAGMENT_TASKS)
    result = Runner().invoke("--profile=child.json drops", tasks=src)
    assert result.ok, result.stderr
    handed = _fragments(box)
    # Its own group renumbered to this process, and the group it embedded
    # carried through as it was.
    assert _groups(handed) == {os.getpid(): "fm (child)"}
    assert {e["pid"] for e in handed if e.get("cat") == "child"} == {4242}
