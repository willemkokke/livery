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
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import sys
import tempfile
import threading
import time
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

_child_dir: str | None = None
"""Where this run's children may drop trace fragments — created at
`pre_tasks` when the line asked for a profile, swept and removed by the
writer. Module state, not invocation state: the two hooks are the only
readers and they bracket one run."""


@footman.pre_tasks
def arm(inv: footman.Invocation) -> None:
    """Open the fragment drop for a profiled run's children.

    Reads `inv.cli`, not `PROFILE.value`: the manifest refresh child runs
    this hook with no command line at all, and there the answer must be
    "not profiling" rather than an unbound-value error.

    Asks whether the option was *mentioned*, not whether its value is truthy.
    Those coincided only while a bare mention arrived as `True`; the question
    was always presence, and a value can be empty and still have been asked
    for."""
    global _child_dir
    _child_dir = None
    if "profile" not in inv.cli:
        return
    _child_dir = tempfile.mkdtemp(prefix="fm-profile-")
    # The embed pass consumes this directory — but a run that mentions
    # --profile and never reaches it (a refusal after this hook, an
    # interrupt) leaked it forever, one directory per mention. The atexit
    # sweep is the backstop: the consume path clears `_child_dir` first,
    # so a healthy run's handler finds nothing to do.
    atexit.register(_sweep_orphan, _child_dir)
    # The single-threaded moment: what lands in the environment here is in
    # every task's copy, and so in every child any task spawns.
    os.environ["FM_PROFILE_DIR"] = _child_dir


def _sweep_orphan(path: str) -> None:
    if _child_dir == path:
        shutil.rmtree(path, ignore_errors=True)


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


def _events(results: tuple[Any, ...]) -> tuple[list[dict[str, Any]], float]:
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
            "pid": _PID,
            "args": {"name": "fm"},
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
                    "pid": _PID,
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
                "pid": _PID,
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
                    "pid": _PID,
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
                "pid": _PID,
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
                        "pid": _PID,
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
                    "pid": _PID,
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
                "pid": _PID,
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
                    "pid": _PID,
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
                    "pid": _PID,
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
    os.environ.pop("FM_PROFILE_DIR", None)
    anchor_wall, anchor_clock = context._WALL_ANCHOR
    zero_wall_us = (anchor_wall + (zero - anchor_clock)) * 1e6
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
    shutil.rmtree(sink, ignore_errors=True)
    return embedded


@footman.post_tasks
def write(inv: footman.Invocation) -> None:
    # Three outcomes from one declared value: `.given` says whether a profile
    # was asked for at all, `.value` says where it goes — the declared default
    # when `--profile` was named bare, the attached path when it was not.
    if not PROFILE.given:
        return
    target = PROFILE.value
    begin = time.perf_counter()
    events, zero = _events(inv.results)
    events += _sweep_children(zero)
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


def _self_ts(events: list[dict[str, Any]]) -> float:
    """The writer slice's `ts`: past everything already in the trace."""
    latest = max(
        (e["ts"] + e.get("dur", 0.0) for e in events if "ts" in e), default=0.0
    )
    return round(latest + 10.0, 1)
