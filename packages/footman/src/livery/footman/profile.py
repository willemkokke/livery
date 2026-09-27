"""Write the run as a profiler trace — `fm --profile … check`.

Open the file at ui.perfetto.dev (chrome://tracing and speedscope read it
too).

`plugin("footman.profile")` in a tasks file switches it on; unmounted, it is
inert metadata like any other plugin. Mounted, `--profile` writes
`fm-profile.json` in the invocation's directory and `--profile=FILE` chooses —
Chrome Trace Event Format, stdlib `json` only, whole-file at `post_tasks`
when every row is in.

What the trace shows, from what the run already records: one track per
worker, a slice per task (queue wait in its args), lane waits at the head of
the slot, every `run()` step nested inside its task, the task's own
`section()`/`stream()`/`mark()` records, and a flow arrow per dependency
edge. Anything that genuinely overlaps on one track — a `parallel()` child's
steps, a named stream's windows — renders as an async span instead, because
slices on a track must nest and the trace never lies to make a prettier
picture. The writer times itself: the last slice is `profile: write`, the
serialisation cost, appended just before the dump — everything but the file
write is in the profile.

Children may add their own detail. A profiled run exports `FM_PROFILE_DIR`
to every task's environment (so every child inherits it); any process may
drop Chrome-trace fragments there — `*.json`, a `{"traceEvents": […]}`
object or a bare event array, `ts` in **epoch microseconds**, `pid` its own
— and the writer sweeps the directory, shifts each fragment onto the run
clock, and embeds it as its own process group. footman's pytest plugin
speaks the convention out of the box: a profiled `run("pytest …")` puts
every test's setup/call/teardown on the timeline, xdist workers as named
tracks, under a `pytest` process beside `fm`'s own.

A child `fm` is one of those children. It finds the box in its environment
and writes its whole run there as a fragment, with its own process id,
instead of a file of its own — so a verb that spawns a verb shows what it
spawned from the inside, and the box is left for whoever opened it. A
process about to replace itself does it inside `handing_off()`, which drops
what the running task has recorded and hands the box to the successor, so
the work before an exec is in the trace and the directory is consumed rather
than leaked.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from collections.abc import Generator
from pathlib import Path
from typing import Annotated, Any

import livery.footman as footman
from livery.footman import GlobalOption, context, matching
from livery.footman._executor import reported_state

PROFILE = GlobalOption(
    "profile",
    # A trace is JSON, and Tab offering every file in the tree helped nobody.
    Annotated[Path, matching("*.json")],
    default=Path("fm-profile.json"),
    help="write the run as a trace for ui.perfetto.dev",
)

_PID = 1

DROP = "FM_PROFILE_DIR"
"""The drop box, named in every task's environment so that every child
inherits it. A child may leave Chrome-trace fragments there. One directory
per profiled run, under `profiles/` in footman's cache."""

HANDOFF = "FM_PROFILE_HANDOFF"
"""The box a process hands to the one replacing it, so the successor owns
the box its predecessor opened instead of opening a second one. Read and
removed by `arm`, so nothing further down inherits it."""

_OFF, _ROOT, _FRAGMENT = "off", "root", "fragment"

_mode: str = _OFF
"""What this run does with a trace: nothing, write the file (`--profile` on
this command line), or drop a fragment in the box a parent opened."""

_child_dir: str | None = None
"""Where this run's children may drop trace fragments — created at
`pre_tasks` when the line asked for a profile, swept and removed by the
writer. Module state, not invocation state: the two hooks are the only
readers and they bracket one run."""

_open_boxes: set[str] = set()
"""Every box this process opened and has not disposed of, which is what the
exit backstop removes. One process can hold several — a test session, an
embedded `Runner`, a run that refused before the writer — so the question at
exit is whether *this* box is still open, not whether it is the newest."""


@footman.pre_tasks
def arm(inv: footman.Invocation) -> None:
    """Open the fragment drop for a profiled run's children.

    Reads `inv.cli`, not `PROFILE.value`: the manifest refresh child runs
    this hook with no command line at all, and there the answer must be
    "not profiling" rather than an unbound-value error.

    Asks whether the option was *mentioned*, not whether its value is truthy.
    Those coincided only while a bare mention arrived as `True`; the question
    was always presence, and a value can be empty and still have been asked
    for.

    A line that mentions nothing and finds a box in its environment is a
    profiled run's child: it writes itself into that box instead of a file
    of its own, and leaves the box alone."""
    global _child_dir, _mode
    _child_dir, _mode = None, _OFF
    # Taken out of the environment whatever this run turns out to be: the
    # box goes to one successor, and a grandchild that adopted it would
    # sweep it away from underneath its owner.
    handed = os.environ.pop(HANDOFF, None)
    if "profile" not in inv.cli:
        if _box() is not None:
            _mode = _FRAGMENT
        return
    _mode = _ROOT
    # The box the process this one replaced opened, which already holds its
    # fragment of the work before the exec. Anything else starts fresh.
    if handed is not None and Path(handed).is_dir():
        _child_dir = handed
    else:
        _child_dir = tempfile.mkdtemp(dir=_boxes_home())
    # The embed pass consumes this directory. A run that mentions --profile
    # and never reaches it — a refusal after this hook, an interrupt — is
    # what the exit backstop is for: it removes any box still open, and the
    # consume path takes its own out of that set first, so a healthy run's
    # handler finds nothing to do.
    _open_boxes.add(_child_dir)
    atexit.register(_sweep_orphan, _child_dir)
    # The single-threaded moment: what lands in the environment here is in
    # every task's copy, and so in every child any task spawns.
    os.environ[DROP] = _child_dir


def _sweep_orphan(path: str) -> None:
    if path in _open_boxes:
        _open_boxes.discard(path)
        shutil.rmtree(path, ignore_errors=True)


def _boxes_home() -> Path:
    """Where a box is made: footman's own cache, not the system's temporary
    directory.

    The collector sweeps this folder by age, so a box left behind by a
    process nothing could run an exit handler for goes on its own. Under the
    system's temporary directory it was invisible and nobody's to sweep.
    """
    home = context.cache_dir() / "profiles"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _box() -> str | None:
    """The drop box this process may write into, while it is still there.

    A box named by a run that has already ended is gone: its owner removes
    it, so the name alone is not enough to write against.
    """
    sink = os.environ.get(DROP)
    return sink if sink and Path(sink).is_dir() else None


def _epoch_origin(moment: float) -> float:
    """*moment* on the run clock, as wall-clock microseconds.

    The two-clock anchor that both directions of the fragment convention go
    through: a fragment is written in epoch microseconds and embedded by
    subtracting the run's own origin, so one process's timeline lands beside
    another's exactly where it happened, on one machine or two.
    """
    anchor_wall, anchor_clock = context._WALL_ANCHOR
    return (anchor_wall + (moment - anchor_clock)) * 1e6


def _nests(
    spans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split candidate spans for one track into (nesting, overlapping).

    Chrome `X` events on a tid render by containment, so a span may share a
    track only when it nests inside whatever is open; a partial overlap — a
    `parallel()` child's step against its sibling's — goes to the async lane
    instead. Spans arrive as dicts with `ts`/`dur` already set (µs)."""
    nested: list[dict[str, Any]] = []
    async_: list[dict[str, Any]] = []
    stack: list[float] = []  # open ends, µs
    for span in sorted(spans, key=lambda s: (s["ts"], -s["dur"])):
        start, end = span["ts"], span["ts"] + span["dur"]
        while stack and stack[-1] <= start + 0.001:
            stack.pop()
        if not stack or end <= stack[-1] + 0.001:
            nested.append(span)
            stack.append(end)
        else:
            async_.append(span)
    return nested, async_


def _events(
    results: tuple[Any, ...], *, pid: int = _PID, name: str = "fm"
) -> tuple[list[dict[str, Any]], float]:
    """The run's rows as trace events, and the run clock's zero.

    *pid* and *name* are the process group the events belong to: the run's
    own, or the one a fragment claims for itself.
    """
    rows = [r for r in results if r.started is not None]
    moments = [r.started for r in rows]
    for r in rows:
        moments += [s.started for s in r.steps if s.started is not None]
        moments += [s.started for s in r.sections]
    zero = min(moments, default=0.0)

    def us(clock: float) -> float:
        return round((clock - zero) * 1e6, 1)

    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "process_name",
            "pid": pid,
            "args": {"name": name},
        }
    ]
    named: set[int] = set()
    flow = 0
    by_address = {r.address: r for r in rows if r.address}
    for r in rows:
        tid = r.thread_id
        if tid and tid not in named:
            named.add(tid)
            events.append(
                {
                    "ph": "M",
                    "name": "thread_name",
                    "pid": pid,
                    "tid": tid,
                    "args": {"name": r.thread},
                }
            )
        label = r.address or r.task
        args: dict[str, Any] = {"state": reported_state(r), "code": r.code}
        if r.eligible is not None:
            args["queue_ms"] = round(max(r.started - r.eligible, 0.0) * 1000, 3)
        events.append(
            {
                "ph": "X",
                "cat": "task",
                "name": label,
                "pid": pid,
                "tid": tid,
                "ts": us(r.started),
                "dur": round(r.duration * 1e6, 1),
                "args": args,
            }
        )
        cursor = r.started
        for lane, seconds in r.lane_waits:
            events.append(
                {
                    "ph": "X",
                    "cat": "lane",
                    "name": f"lane: {lane}",
                    "pid": pid,
                    "tid": tid,
                    "ts": us(cursor),
                    "dur": round(seconds * 1e6, 1),
                }
            )
            cursor += seconds
        spans = [
            {
                "ph": "X",
                "cat": "step",
                # A trace is a file that gets attached to tickets and dropped
                # into ui.perfetto.dev — the shown line, never the record's.
                "name": s.shown,
                "pid": pid,
                "tid": tid,
                "ts": us(s.started),
                "dur": round(s.duration * 1e6, 1),
            }
            for s in r.steps
            if s.started is not None
        ]
        for s in r.sections:
            if s.stream:
                continue  # a named stream is async by design, below
            if s.duration == 0.0:
                events.append(
                    {
                        "ph": "i",
                        "s": "t",  # thread scope: a tick on this track
                        "cat": "mark",
                        "name": s.name,
                        "pid": pid,
                        "tid": tid,
                        "ts": us(s.started),
                    }
                )
                continue
            spans.append(
                {
                    "ph": "X",
                    "cat": "section",
                    "name": s.name,
                    "pid": pid,
                    "tid": tid,
                    "ts": us(s.started),
                    "dur": round(s.duration * 1e6, 1),
                }
            )
        nested, overlapping = _nests(spans)
        events += nested
        streamed = [
            {
                "cat": f"stream: {s.stream}",
                "name": s.name,
                "ts": us(s.started),
                "dur": round(s.duration * 1e6, 1),
            }
            for s in r.sections
            if s.stream
        ]
        for n, span in enumerate(
            overlapping + [{**s, "tid": tid} for s in streamed],
        ):
            ident = f"{label}#{n}"
            begin = {
                "ph": "b",
                "cat": span.get("cat", "step"),
                # Process-local scope: a plain `id` is global, and Perfetto
                # would file the pair under "Global Legacy Events" instead
                # of with fm's own tracks.
                "id2": {"local": ident},
                "name": span["name"],
                "pid": pid,
                "tid": span.get("tid", tid),
                "ts": span["ts"],
            }
            events.append(begin)
            events.append({**begin, "ph": "e", "ts": span["ts"] + span["dur"]})
        for dep_addr in r.after:
            dep = by_address.get(dep_addr)
            if dep is None or dep.started is None:
                continue
            flow += 1
            done = dep.started + dep.duration
            events.append(
                {
                    "ph": "s",
                    "cat": "dep",
                    "id": flow,
                    "name": "after",
                    "pid": pid,
                    "tid": dep.thread_id,
                    # A hair inside the finishing slice, so the arrow binds
                    # to it rather than to whatever came next on the track.
                    "ts": max(us(dep.started), us(done) - 1.0),
                }
            )
            events.append(
                {
                    "ph": "f",
                    "bp": "e",
                    "cat": "dep",
                    "id": flow,
                    "name": "after",
                    "pid": pid,
                    "tid": tid,
                    "ts": us(r.started),
                }
            )
    return events, zero


def _sweep_children(zero: float) -> list[dict[str, Any]]:
    """Embed what the run's children dropped in `FM_PROFILE_DIR`.

    Fragments carry `ts` in epoch microseconds and their own `pid`; the
    shift onto the run clock goes through the same two-clock anchor the
    retroactive stream sections use, so a child's timeline lands beside the
    parent's exactly where it happened. The drop directory is consumed:
    swept, embedded, removed."""
    global _child_dir
    sink, _child_dir = _child_dir, None
    if sink is None:
        return []
    os.environ.pop(DROP, None)
    zero_wall_us = _epoch_origin(zero)
    embedded: list[dict[str, Any]] = []
    for fragment in sorted(Path(sink).glob("*.json")):
        try:
            payload = json.loads(fragment.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            print(f"profile: skipped fragment {fragment.name}: {exc}", file=sys.stderr)
            continue
        found = payload.get("traceEvents") if isinstance(payload, dict) else payload
        if not isinstance(found, list):
            continue
        for event in found:
            if not isinstance(event, dict):
                continue
            if isinstance(ts := event.get("ts"), (int, float)):
                event = {**event, "ts": round(ts - zero_wall_us, 1)}
            embedded.append(event)
    _open_boxes.discard(sink)
    shutil.rmtree(sink, ignore_errors=True)
    return embedded


@footman.post_tasks
def write(inv: footman.Invocation) -> None:
    """Write the run: the trace file, or a fragment in a parent's box.

    A handoff has already written this process's share and stood the mode
    down, so a parent that waited for its replacement (Windows, where there
    is no exec) writes nothing here and cannot overwrite the file its
    replacement wrote.
    """
    if _mode == _FRAGMENT:
        _write_fragment(inv)
        return
    # Three outcomes from one declared value: `.given` says whether a profile
    # was asked for at all, `.value` says where it goes — the declared default
    # when `--profile` was named bare, the attached path when it was not.
    if _mode != _ROOT or not PROFILE.given:
        return
    target = PROFILE.value
    begin = time.perf_counter()
    events, zero = _events(inv.results)
    events += _sweep_children(zero)
    # An embedded fragment may predate this run's first task: the process
    # that re-exec'd into this one began before the run it became. The whole
    # trace slides so its earliest moment is zero, which keeps every stamp
    # positive and moves nothing relative to anything else.
    if (first := min((e["ts"] for e in events if "ts" in e), default=0.0)) < 0:
        for event in events:
            if "ts" in event:
                event["ts"] = round(event["ts"] - first, 1)
    path = Path(inv.cwd or ".") / target  # an absolute target wins the join
    tid = threading.get_native_id()
    events.append(
        {
            "ph": "M",
            "name": "thread_name",
            "pid": _PID,
            "tid": tid,
            "args": {"name": "fm (report)"},
        }
    )
    events.append(
        {
            # The writer's own receipt: serialisation, timed to just before
            # the dump. The file write is the one thing a closed file cannot
            # contain.
            "ph": "X",
            "cat": "profile",
            "name": "profile: write",
            "pid": _PID,
            "tid": tid,
            "ts": _self_ts(events),
            "dur": round((time.perf_counter() - begin) * 1e6, 1),
        }
    )
    path.write_text(
        json.dumps({"traceEvents": events, "displayTimeUnit": "ms"}), encoding="utf-8"
    )
    print(f"profile: {path}", file=sys.stderr)


def _write_fragment(inv: footman.Invocation) -> None:
    """Drop this whole run in the box a parent opened.

    A child `fm` has no file of its own: its tasks belong inside the run
    that spawned it, as a process group of their own. The box is left where
    it is for its owner to sweep, so whatever this run's own children
    dropped there goes up with it.
    """
    sink = _box()
    if sink is None:
        return
    events, zero = _events(inv.results, pid=os.getpid(), name="fm (child)")
    _drop(sink, _stamped(events, zero))


@contextlib.contextmanager
def handing_off() -> Generator[dict[str, str]]:
    """Hand this run's trace to the process about to replace this one.

    Wraps the block that does the replacing, which is a block that ends by
    not returning: an exec that works never comes back, and a Windows
    handoff waits for its replacement and exits with its code. So returning
    normally means the replacement did not happen, and the handoff is taken
    back — the fragment is removed and this run writes its own file as
    usual.

    Drops what the running task has recorded so far as a fragment, and
    yields the environment entries that give the box to the successor: hand
    them to `os.execve`, or to `subprocess.run(env=…)` where there is no
    exec. Nothing is written to `os.environ`, because a task-time write to
    the process environment is ambient and footman notes it.

    What lands in the fragment is this process's own span and the `run()`
    steps of the task that calls this, which is what the exec would
    otherwise lose. Tasks that finished earlier in the same process are not
    in it: their rows belong to the run, and the run is what is being
    replaced.

    Yields:
        The entries the successor needs, empty when this run writes no trace
        at all.
    """
    global _child_dir, _mode
    if _mode == _OFF or (sink := _box()) is None:
        yield {}
        return
    fragment = _drop(sink, _handoff_events())
    kept = _child_dir, _mode
    # The box is the successor's now: this process neither sweeps it at the
    # end of the run nor removes it on the way out. Only a box this run
    # opened is given away; in a child, the box belongs to an ancestor and
    # was never this process's to hand over or to sweep.
    if _child_dir is not None:
        _open_boxes.discard(_child_dir)
    _child_dir, _mode = None, _OFF
    yield {HANDOFF: sink}
    # Reached only when the replacement did not happen, so the trace is this
    # run's after all. No `finally`: an exception here is the handoff
    # working — Windows raises `SystemExit` with the replacement's code —
    # and taking it back then would delete a trace that stands.
    _child_dir, _mode = kept
    if _child_dir is not None:
        _open_boxes.add(_child_dir)
    if fragment is not None:
        fragment.unlink(missing_ok=True)


def _handoff_events() -> list[dict[str, Any]]:
    """This process's life so far, as one fragment's events."""
    ctx = context.current()
    pid = os.getpid()
    tid = threading.get_native_id()
    _wall, anchor_clock = context._WALL_ANCHOR
    events: list[dict[str, Any]] = [
        {
            "ph": "M",
            "name": "process_name",
            "pid": pid,
            "args": {"name": "fm (before re-exec)"},
        },
        {
            "ph": "X",
            "cat": "task",
            "name": ctx.address or ctx.task or "fm",
            "pid": pid,
            "tid": tid,
            # The anchor is sampled as this module's context is imported,
            # which is the closest this process has to its own start.
            "ts": round(_epoch_origin(anchor_clock), 1),
            "dur": round((time.perf_counter() - anchor_clock) * 1e6, 1),
        },
    ]
    events += [
        {
            "ph": "X",
            "cat": "step",
            "name": s.shown,
            "pid": pid,
            "tid": tid,
            "ts": round(_epoch_origin(s.started), 1),
            "dur": round(s.duration * 1e6, 1),
        }
        for s in ctx.steps
        if s.started is not None
    ]
    return events


def _stamped(events: list[dict[str, Any]], zero: float) -> list[dict[str, Any]]:
    """*events* restamped for the box: epoch microseconds, in place."""
    origin = _epoch_origin(zero)
    for event in events:
        if isinstance(ts := event.get("ts"), (int, float)):
            event["ts"] = round(ts + origin, 1)
    return events


def _drop(sink: str, events: list[dict[str, Any]]) -> Path | None:
    """Write *events* as one fragment in *sink*.

    Named by this process and the moment: a re-exec keeps the process id, so
    both halves of one process land in the same directory and a name of the
    pid alone would have the second overwrite the first. A drop that cannot
    be written is said and never fatal, because the run it rode in is the
    point.

    Returns:
        The file, or `None` when it could not be written.
    """
    fragment = Path(sink, f"fm-{os.getpid()}-{time.time_ns() // 1000}.json")
    try:
        fragment.write_text(json.dumps({"traceEvents": events}), encoding="utf-8")
    except OSError as exc:
        print(f"profile: fragment not written: {exc}", file=sys.stderr)
        return None
    return fragment


def _self_ts(events: list[dict[str, Any]]) -> float:
    """The writer slice's `ts`: past everything already in the trace."""
    latest = max(
        (e["ts"] + e.get("dur", 0.0) for e in events if "ts" in e), default=0.0
    )
    return round(latest + 10.0, 1)
