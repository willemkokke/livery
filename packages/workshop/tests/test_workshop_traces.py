"""The legs' traces: the channel, the contract, and the run window.

Refusals first: a contract that says no, a contract that says something
wrong, a leg nothing named, a trace that is not there, and origin's own
refusal. Then the push that works, the mirror that brings none of it, and
the window the janitor applies.

Real git against a bare repository standing in for origin, so a refusal is
the transport's own words.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from livery.workshop import _state, _traces
from workshop_seeds import Seeds, _seed_home, seed_copier  # noqa: F401


def _git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    )
    return done.stdout


def _seed(base: Path) -> None:
    """A bare origin with one commit on main, and a clone of it."""
    origin = base / "origin.git"
    _git(base, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    work = base / "work"
    _git(base, "clone", "-q", str(origin), str(work))
    _git(work, "config", "user.name", "tester")
    _git(work, "config", "user.email", "tester@example.invalid")
    (work / "README.md").write_text("the repository\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-qm", "init")
    _git(work, "push", "-q", "-u", "origin", "main")


@pytest.fixture
def work(seeds: Seeds, tmp_path: Path) -> Path:
    """A clone whose origin is a bare repository, with a contract in it."""
    seeds("traces", _seed)
    root = tmp_path / "work"
    _contract(root)
    return root


def _contract(root: Path, **keys: object) -> None:
    """Write a workspace contract naming *keys* under ``[ci]``."""
    lines = ["[project]", 'name = "probe"', "", "[ci]"]
    for key, value in keys.items():
        spelled = str(value).lower() if isinstance(value, bool) else repr(value)
        lines.append(f"{key.replace('_', '-')} = {spelled}")
    root.mkdir(parents=True, exist_ok=True)
    (root / "workshop.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
        "GITHUB_RUN_ID",
        "GITHUB_EVENT_NAME",
        "GITHUB_REF",
        "WORKSHOP_LEG",
        "WORKSHOP_SNAPSHOT",
    ):
        monkeypatch.delenv(name, raising=False)


def _in_ci(
    monkeypatch: pytest.MonkeyPatch, *, run: str = "7", leg: str = "check"
) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", run)
    monkeypatch.setenv("WORKSHOP_LEG", leg)


def _trace_file(root: Path, name: str = "fm-profile.json") -> Path:
    path = root / name
    path.write_text(
        json.dumps({"traceEvents": [], "originEpochUs": 1.0}), encoding="utf-8"
    )
    return path


def test_the_contract_answers_its_defaults_when_it_says_nothing(work: Path) -> None:
    kept, why = _traces.policy(work)
    assert why == ""
    assert kept == _traces.Policy(
        legs=_traces.PROFILE_DEFAULT,
        window=_traces.WINDOW_DEFAULT,
        into=_traces.INTO_DEFAULT,
    )


def test_a_key_of_the_wrong_type_is_named_and_its_default_stands(work: Path) -> None:
    """A typo must be visible and must not stop a job.

    Whether a timeline is kept is not worth failing a leg over, and a
    key that reads as false by accident would keep nothing at all and
    say nothing about why.
    """
    _contract(work, profile="yes", profile_window="lots", profile_into=17)
    kept, why = _traces.policy(work)
    assert kept.legs is _traces.PROFILE_DEFAULT
    assert kept.window == _traces.WINDOW_DEFAULT
    assert kept.into == _traces.INTO_DEFAULT
    assert "[ci] profile is 'yes'; it is true or false" in why
    assert "[ci] profile-window is 'lots'; it is a whole number of runs" in why
    assert "[ci] profile-into is 17; it is a path" in why


def test_a_contract_that_wants_no_traces_pushes_nothing_and_says_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(work, profile=False)
    _in_ci(monkeypatch)
    assert _traces.push(work, _trace_file(work)) == ""
    assert _state.list_refs(work, _traces.TRACES.prefix) == {}


def test_a_run_that_is_not_ci_keeps_its_trace_on_the_machine(work: Path) -> None:
    assert "not a CI run" in _traces.push(work, _trace_file(work))


def test_a_leg_the_runner_never_named_is_not_pushed(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, leg="")
    monkeypatch.delenv("WORKSHOP_LEG")
    assert "WORKSHOP_LEG names no leg" in _traces.push(work, _trace_file(work))


def test_a_trace_that_was_never_written_is_named(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch)
    line = _traces.push(work, work / "fm-profile.json")
    assert "no trace at" in line and "nothing to push" in line


def test_a_push_origin_refuses_is_named_and_decides_nothing(
    work: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The trace is observational: origin's own words, and the job goes on."""
    from livery.workshop._points import _push_job

    _in_ci(monkeypatch)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    drop = work / "drop"
    drop.mkdir()
    (drop / "one.json").write_text(
        json.dumps(
            {
                "traceEvents": [
                    {"ph": "X", "name": "a", "pid": 1, "tid": 1, "ts": 1.0, "dur": 1.0}
                ]
            }
        ),
        encoding="utf-8",
    )
    _push_job(work, drop, job="check")
    printed = capsys.readouterr().out
    assert "the trace was not pushed" in printed


def test_the_trace_lands_on_the_run_s_ref_and_no_mirror_brings_it(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _in_ci(monkeypatch, run="41", leg="check-ubuntu-latest-3.14")
    trace = _trace_file(work)
    line = _traces.push(work, trace)
    ref = _traces.TRACES.series("41", "check-ubuntu-latest-3.14").ref
    # The family spells every key part ref-safe, so the leg's dotted
    # python arrives as a dash.
    assert ref == "refs/workshop-trace/run/41/check-ubuntu-latest-3-14"
    assert ref in line
    found = _state.read(work, ref)
    assert not found.failed and found.files is not None
    assert json.loads(found.files[_traces.TRACE_FILE])["originEpochUs"] == 1.0
    # The one refspec a sync mirrors is the store's own namespace, so a
    # fetch brings no trace and the gate reads none.
    assert _state.fetch_store(work) == (0, "")
    assert _state.list_refs(work, _state.FETCHED_NAMESPACE) == {}


def test_the_janitor_keeps_the_window_s_newest_runs_and_drops_the_rest(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The count is the whole bound: the files are traces, not rows.

    Ranked by the run id, which every forge hands out increasing, so
    the janitor never fetches a trace to learn how old it is.
    """
    _contract(work, profile_window=2)
    trace = _trace_file(work)
    for run in ("9", "10", "11"):
        for leg in ("check", "docs"):
            _in_ci(monkeypatch, run=run, leg=leg)
            assert _traces.TRACES.series(run, leg).ref in _traces.push(work, trace)
    lines = _state.sweep(work, (_traces.TRACES,), remote=True)
    held = _state.list_refs(work, _traces.TRACES.prefix)
    assert held is not None
    assert sorted(ref.split("/")[-2] for ref in held) == ["10", "10", "11", "11"]
    assert any("beyond the newest 2 run(s) kept" in line for line in lines)
    # And a second sweep finds nothing left to do.
    assert not [
        line
        for line in _state.sweep(work, (_traces.TRACES,), remote=True)
        if "dropped" in line
    ]


def test_a_good_into_is_read_and_a_wrong_key_stops_the_push(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A contract that names where an assembled file lands is read.

    And a key of the wrong type stops the push rather than pushing
    against a policy nobody meant: the line names the key, so the
    workspace can be corrected before the next run.
    """
    _contract(work, profile_into="build/traces")
    kept, why = _traces.policy(work)
    assert (kept.into, why) == ("build/traces", "")
    _contract(work, profile_window=-4)
    _in_ci(monkeypatch)
    line = _traces.push(work, _trace_file(work))
    assert line == "profile: [ci] profile-window is -4; it is a whole number of runs"
    assert _state.list_refs(work, _traces.TRACES.prefix) == {}


# --- the assembler: one trace of a whole run ----------------------------------


def _forge(jobs: tuple[object, ...], *, created: str = "") -> object:
    """A repository answering one run's jobs, and the run itself when asked."""
    from livery.forge import Run

    run = Run(
        77,
        "ci.yml",
        "abc123",
        "push",
        "completed",
        "success",
        created_at=created,
        started_at=created,
    )

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[object, ...]:
            return (run,) if created else ()

        def jobs(self, run: int) -> tuple[object, ...]:
            return jobs

    class _Repo:
        checks = _Checks()

    return _Repo()


def _job(
    name: str,
    *,
    started: str = "",
    completed: str = "",
    status: str = "completed",
    conclusion: str = "success",
    steps: tuple[object, ...] = (),
) -> object:
    from livery.forge import Job

    return Job(1, name, status, conclusion, started, completed, steps)  # type: ignore[arg-type]


def _pushed(
    work: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    leg: str,
    origin: float,
    tasks: dict[str, float],
    job: str = "",
) -> None:
    """Push a trace for *leg* whose zero sits at *origin*, with named tasks."""
    trace = work / f"{leg}.json"
    trace.write_text(
        json.dumps(
            {
                "originEpochUs": origin,
                "traceEvents": [
                    {
                        "ph": "M",
                        "name": "process_name",
                        "pid": 1,
                        "args": {"name": "fm"},
                    },
                    *(
                        {
                            "ph": "X",
                            "cat": "task",
                            "name": name,
                            "pid": 1,
                            "tid": 1,
                            "ts": 0.0,
                            "dur": dur,
                        }
                        for name, dur in tasks.items()
                    ),
                ],
            }
        ),
        encoding="utf-8",
    )
    _in_ci(monkeypatch, run="77", leg=leg)
    assert "pushed" in _traces.push(work, trace, job=job)


def _epoch(stamp: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1e6


def test_a_run_the_forge_cannot_be_asked_about_assembles_nothing(work: Path) -> None:
    from typing import Any, cast

    from livery.forge import Repository

    repo = cast(Repository, cast(Any, _forge(())))
    made = _traces.assemble(work, repo, "not-a-number")
    assert made.events == [] and "not a number" in made.lines[0]
    made = _traces.assemble(work, repo, "77")
    assert made.events == [] and "lists no job for it" in made.lines[0]


def test_a_leg_whose_trace_will_not_parse_is_named_and_the_others_assemble(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    _pushed(
        work, monkeypatch, leg="check", origin=_epoch(began), tasks={"check": 1000.0}
    )
    broken = work / "broken.json"
    broken.write_text("{ not json", encoding="utf-8")
    _in_ci(monkeypatch, run="77", leg="docs")
    assert "pushed" in _traces.push(work, broken)
    jobs = (
        _job("check", started=began, completed="2026-09-27T10:00:01Z"),
        _job("docs", started=began, completed="2026-09-27T10:00:02Z"),
    )
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    assert any("docs: the trace does not parse" in line for line in made.lines)
    assert any(
        "check: " in line and "event(s) of its own" in line for line in made.lines
    )
    # Both jobs are still drawn, and only the readable leg brought its own.
    drawn = {e["name"] for e in made.events if e.get("cat") == "job"}
    assert drawn == {"check", "docs"}
    assert any(e.get("cat") == "task" and e["name"] == "check" for e in made.events)


def test_the_run_is_its_jobs_their_steps_and_every_leg_that_left_a_trace(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The legs land inside their own jobs, by the epochs and not by order.

    The traces are pushed in one order and their origins say another, so a
    leg that lands in the right place can only have been placed by its
    recorded origin.
    """
    from typing import Any, cast

    from livery.forge import Repository, Step

    created = "2026-09-27T10:00:00Z"
    late, early = "2026-09-27T10:00:30Z", "2026-09-27T10:00:05Z"
    # Pushed newest first, so order and time disagree.
    _pushed(
        work, monkeypatch, leg="check-b", origin=_epoch(late), tasks={"b": 2_000_000.0}
    )
    _pushed(
        work, monkeypatch, leg="check-a", origin=_epoch(early), tasks={"a": 3_000_000.0}
    )
    jobs = (
        _job(
            "check-a",
            started=early,
            completed="2026-09-27T10:00:09Z",
            steps=(Step("Set up job", "success", early, "2026-09-27T10:00:06Z"),),
        ),
        _job("check-b", started=late, completed="2026-09-27T10:00:35Z"),
        # A skipped job: the forge times it not at all.
        _job("govern", status="completed", conclusion="skipped"),
        # And one still running when the reading was taken.
        _job(
            "gate", started="2026-09-27T10:00:40Z", status="in_progress", conclusion=""
        ),
    )
    repo = cast(Repository, cast(Any, _forge(jobs, created=created)))
    now = datetime.fromisoformat("2026-09-27T10:00:50+00:00")
    made = _traces.assemble(work, repo, "77", head_sha="abc123", now=now)
    by_name = {e["name"]: e for e in made.events if e.get("cat") == "job"}
    # The run's zero is the run's own acceptance, so a job's slice sits at
    # its wait's length.
    assert by_name["check-a"]["ts"] == 5_000_000.0
    assert by_name["check-a"]["dur"] == 4_000_000.0
    assert by_name["check-b"]["ts"] == 30_000_000.0
    # The skip is an event, never a span of no length.
    skipped = next(e for e in made.events if e["name"] == "govern")
    assert skipped["ph"] == "i" and "dur" not in skipped
    assert skipped["args"]["conclusion"] == "skipped"
    # A running job is drawn up to the reading, and says so.
    running = by_name["gate"]
    assert running["args"]["running"] is True
    assert running["ts"] + running["dur"] == 50_000_000.0
    # The wait before a job is its own span, and the steps are inside the job.
    waits = {e["name"]: e for e in made.events if e.get("cat") == "queue"}
    assert waits["check-a: queued"]["dur"] == 5_000_000.0
    step = next(e for e in made.events if e.get("cat") == "step")
    assert step["ts"] == by_name["check-a"]["ts"]
    # Each leg's own trace is inside its job's span, and in groups of its own.
    tasks = {e["name"]: e for e in made.events if e.get("cat") == "task"}
    for leg, task in (("check-a", "a"), ("check-b", "b")):
        job, own = by_name[leg], tasks[task]
        assert job["ts"] <= own["ts"]
        assert own["ts"] + own["dur"] <= job["ts"] + job["dur"]
    assert tasks["a"]["pid"] != tasks["b"]["pid"]
    groups = {
        e["args"]["name"]
        for e in made.events
        if e["ph"] == "M" and e["name"] == "process_name"
    }
    assert groups == {"run 77", "check-a: fm", "check-b: fm"}
    assert not [line for line in made.lines if "could not" in line]


def test_a_channel_that_cannot_be_listed_still_assembles_the_skeleton(
    work: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Origin unreachable: the forge's own times are the whole answer."""
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    assert any("could not be listed" in line for line in made.lines)
    assert [e["name"] for e in made.events if e.get("cat") == "job"] == ["check"]


def test_a_stamp_the_forge_spells_wrongly_is_not_a_moment(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job whose times do not parse is drawn as one that has none."""
    from typing import Any, cast

    from livery.forge import Repository, Step

    jobs = (
        _job("check", started="yesterday", completed="today"),
        # A job with real times whose step has none: the job is drawn and
        # the step is not, rather than a span from nowhere to nowhere.
        _job(
            "docs",
            started="2026-09-27T10:00:00Z",
            completed="2026-09-27T10:00:01Z",
            steps=(Step("Set up job", "success", "whenever", ""),),
        ),
    )
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    drawn = {e["name"]: e for e in made.events if e.get("cat") == "job"}
    assert drawn["check"]["ph"] == "i"  # no start the forge could be read on
    assert drawn["docs"]["ph"] == "X"
    assert not [e for e in made.events if e.get("cat") == "step"]


def test_a_run_the_forge_does_not_list_draws_no_waits(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The wait before a job needs the run's own acceptance; without it, none."""
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    # A repository whose runs() answers nothing for the head.
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77", head_sha="abc123")
    assert any("not among the forge's runs for abc123" in line for line in made.lines)
    assert not [e for e in made.events if e.get("cat") == "queue"]


def test_a_legs_ref_without_a_trace_on_it_is_named(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ref the channel holds that carries no trace is said, not assumed."""
    from typing import Any, cast

    from livery.forge import Repository

    _in_ci(monkeypatch, run="77", leg="check")
    ref = _traces.TRACES.series("77", "check").ref
    assert _state.put(work, ref, {"something-else.json": "{}"}, message="odd") == ""
    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    assert any(f"carries no {_traces.TRACE_FILE}" in line for line in made.lines)


def test_a_ref_the_channel_cannot_read_names_the_job_it_belonged_to(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the store refuses, the line says which job it was about."""
    from typing import Any, cast

    from livery.forge import Repository

    _pushed(work, monkeypatch, leg="check", origin=1.0, tasks={"check": 1.0})
    monkeypatch.setattr(
        _traces,
        "state_read",
        lambda root, ref: _state.Read(
            None, None, failed=True, reason="the remote hung up"
        ),
    )
    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    assert "check: the remote hung up" in made.lines


def test_a_forge_stamp_without_an_offset_is_read_as_utc(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forge means UTC by a bare stamp, whatever zone the reader sits in.

    The standard library reads a naive stamp as local time, which would put a
    leg's trace as far from its job as the reader's own offset, silently. The
    zone is forced here so a machine anywhere reads the same instant.
    """
    from typing import Any, cast

    from livery.forge import Repository

    if not hasattr(time, "tzset"):
        pytest.skip("no tzset: the zone cannot be moved from inside the process")
    aware = (
        _job("check", started="2026-09-27T10:00:00Z", completed="2026-09-27T10:00:01Z"),
    )
    naive = (
        _job("check", started="2026-09-27T10:00:00", completed="2026-09-27T10:00:01"),
    )
    had = os.environ.get("TZ")
    os.environ["TZ"] = "Europe/Amsterdam"  # where the drift would be two hours
    time.tzset()
    try:
        spans: list[float] = []
        for jobs in (aware, naive):
            repo = cast(
                Repository, cast(Any, _forge(jobs, created="2026-09-27T09:59:50Z"))
            )
            made = _traces.assemble(work, repo, "77", head_sha="abc123")
            spans.append(next(e for e in made.events if e.get("cat") == "job")["ts"])
    finally:
        # The zone is process state, and tzset has to follow the variable
        # back or every later test in this worker reads Amsterdam.
        if had is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = had
        time.tzset()
    assert spans[0] == spans[1] == 10_000_000.0


# --- the two callers: a command's own trace, and a file to open ---------------


def test_a_command_keeping_no_trace_drops_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ordinary case: a submit that nobody asked for a trace of.

    It costs one environment read, and in particular it does not ask the
    forge for a run it has no use for.
    """
    from typing import Any, cast

    from livery.forge import Repository

    asked: list[str] = []

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[object, ...]:
            asked.append(head_sha)
            return ()

        def jobs(self, run: int) -> tuple[object, ...]:
            asked.append(str(run))
            return ()

    class _Repo:
        checks = _Checks()

    monkeypatch.delenv("FM_PROFILE_DIR", raising=False)
    repo = cast(Repository, cast(Any, _Repo()))
    assert _traces.drop_run(repo, _git_at(work)) == ""
    assert asked == []


def test_the_run_a_command_followed_joins_its_own_trace(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One file for the local command and the run it caused.

    The fragment is stamped on the wall clock, which is the box's own
    convention, so this command's writer lays it wherever it happened.
    """
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    _pushed(
        work, monkeypatch, leg="check", origin=_epoch(began), tasks={"check": 1000.0}
    )
    box = work / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs, created=began)))
    line = _traces.drop_run(repo, _git_at(work))
    assert line == "profile: run 77 joins this trace, 1 job(s)"
    (fragment,) = list(box.glob("*.json"))
    events = json.loads(fragment.read_text(encoding="utf-8"))["traceEvents"]
    job = next(e for e in events if e.get("cat") == "job")
    assert job["ts"] == _epoch(began)  # the wall clock, not a run's own zero


def test_a_run_is_written_where_a_person_can_open_it(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    _pushed(
        work, monkeypatch, leg="check", origin=_epoch(began), tasks={"check": 1000.0}
    )
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs, created=began)))
    path, lines = _traces.write_run(work, repo, run_id="77")
    assert path is not None
    # The contract's own directory, under the workspace root.
    assert path == work / _traces.INTO_DEFAULT / "run-77.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["displayTimeUnit"] == "ms"
    # The file says where its zero sits, so another timeline can join it.
    assert payload["originEpochUs"] == _epoch(began)
    assert min(e["ts"] for e in payload["traceEvents"] if "ts" in e) == 0.0
    assert any("event(s) of its own" in line for line in lines)


def test_a_run_whose_traces_have_aged_out_writes_the_skeleton_alone(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window drops a run's traces; the forge's own times remain.

    Which is what makes the window safe to keep short: what ages out is the
    detail, never the shape of the run.
    """
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs, created=began)))
    path, lines = _traces.write_run(work, repo, run_id="77", into=work / "out")
    assert path is not None
    assert path == work / "out" / "run-77.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    drawn = [e for e in payload["traceEvents"] if e.get("cat") == "job"]
    assert [e["name"] for e in drawn] == ["check"]
    assert not [e for e in payload["traceEvents"] if e.get("cat") == "task"]
    assert not [line for line in lines if "event(s) of its own" in line]


def test_a_run_nobody_can_name_refuses_to_write_a_file(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import Any, cast

    from livery.forge import Repository

    repo = cast(Repository, cast(Any, _forge(())))
    path, lines = _traces.write_run(work, repo, head_sha="abc123")
    assert path is None
    assert lines == ["profile: the forge lists no run for abc123"]
    # And a contract that says something wrong stops before any of it.
    _contract(work, profile_window="lots")
    path, lines = _traces.write_run(work, repo, run_id="77")
    assert path is None and "profile-window" in lines[0]


def _git_at(root: Path) -> Any:
    """A git handle answering for *root*, as the submit hands one over."""
    from livery.workshop._git_ops import GitOps

    return GitOps(root)


def test_a_trace_can_never_fail_the_command_it_watched(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observational means observational, git's own failures included.

    A CI leg runs its own tests under a profile, so a submit test's rig with
    no repository in it turned a watched merge into a failed submit. Whatever
    goes wrong here is the line to print and nothing else.
    """
    from typing import Any, cast

    from livery.forge import Repository

    box = work / "box"
    box.mkdir()
    monkeypatch.setenv("FM_PROFILE_DIR", str(box))
    repo = cast(Repository, cast(Any, _forge(())))
    line = _traces.drop_run(repo, _git_at(work / "nowhere"))
    # The shape, never the words: what comes back is git's own reason on one
    # platform and the operating system's on another, and a reason is printed
    # verbatim rather than read.
    assert line.startswith("profile: the run was not traced (")
    assert line.endswith(")") and len(line) > len("profile: the run was not traced ()")
    assert list(box.glob("*.json")) == []


def test_a_matrix_leg_joins_its_job_by_the_name_the_forge_uses(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ref's key and its job's name are different spellings of one leg.

    The runner labels a leg ``check-ubuntu-latest-3.14`` and the forge lists
    the job as ``check (ubuntu-latest, 3.14)``. Joining on a slug of either
    matched nothing, so every matrix leg's own trace was dropped on the floor
    while the skeleton looked complete. The name is written down beside the
    trace and joined on.
    """
    from typing import Any, cast

    from livery.forge import Repository

    began = "2026-09-27T10:00:00Z"
    forge_name = "check (ubuntu-latest, 3.14)"
    _pushed(
        work,
        monkeypatch,
        leg="check-ubuntu-latest-3.14",
        origin=_epoch(began),
        tasks={"check": 1000.0},
        job=forge_name,
    )
    jobs = (_job(forge_name, started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    assert any(f"{forge_name}: " in line for line in made.lines)
    assert [e["name"] for e in made.events if e.get("cat") == "task"] == ["check"]


def test_a_job_the_forge_gave_no_extent_is_an_event_and_never_a_span(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A forge times in whole seconds, so a skip can end before it began.

    A real run drew ``govern`` as a slice of minus one second, and two more
    at zero length. Nothing that had no extent is a span.
    """
    from typing import Any, cast

    from livery.forge import Repository, Step

    jobs = (
        # Ends a second before it starts, as a skipped job came back.
        _job(
            "govern",
            started="2026-09-27T10:00:01Z",
            completed="2026-09-27T10:00:00Z",
            conclusion="skipped",
        ),
        # Starts and ends in the same second.
        _job(
            "dispatch",
            started="2026-09-27T10:00:05Z",
            completed="2026-09-27T10:00:05Z",
            steps=(
                Step(
                    "Set up job",
                    "success",
                    "2026-09-27T10:00:05Z",
                    "2026-09-27T10:00:05Z",
                ),
            ),
        ),
    )
    repo = cast(Repository, cast(Any, _forge(jobs)))
    made = _traces.assemble(work, repo, "77")
    drawn = {e["name"]: e for e in made.events if e.get("cat") in {"job", "step"}}
    assert drawn["govern"]["ph"] == "i"
    assert drawn["govern"]["args"]["conclusion"] == "skipped"
    assert drawn["dispatch"]["ph"] == "i"
    assert drawn["Set up job"]["ph"] == "i"
    assert not [e for e in made.events if e.get("ph") == "X" and e.get("dur", 1) <= 0]


def test_a_trace_outside_its_jobs_span_is_said_and_never_drawn(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusals first: a residue is only drawn where the arithmetic holds.

    A job's span and a leg's trace are two clocks written down
    independently. They usually agree, and when they do not, saying so
    is the whole value: a span of negative length would read as work
    that happened.
    """
    from typing import Any, cast

    from livery.forge import Repository

    created = "2026-09-27T11:00:00Z"
    # A trace that began before the job the forge timed.
    _pushed(
        work,
        monkeypatch,
        leg="early",
        origin=_epoch("2026-09-27T10:59:58Z"),
        tasks={"a": 1_000_000.0},
    )
    # One that ran past the job's end.
    _pushed(
        work,
        monkeypatch,
        leg="late",
        origin=_epoch("2026-09-27T11:00:01Z"),
        tasks={"b": 60_000_000.0},
    )
    # One with no stamp at all: a trace of nothing but its own name.
    _pushed(work, monkeypatch, leg="bare", origin=_epoch(created), tasks={})
    # And a job the forge never timed, which a skip is.
    _pushed(work, monkeypatch, leg="govern", origin=_epoch(created), tasks={"c": 1.0})
    jobs = (
        _job("early", started=created, completed="2026-09-27T11:00:10Z"),
        _job("late", started=created, completed="2026-09-27T11:00:10Z"),
        _job("bare", started=created, completed="2026-09-27T11:00:10Z"),
        _job("govern", status="completed", conclusion="skipped"),
    )
    repo = cast(Repository, cast(Any, _forge(jobs, created=created)))
    made = _traces.assemble(work, repo, "77", head_sha="abc123")
    assert not [event for event in made.events if event.get("cat") == "runner"]
    said = {line.split(":")[0]: line for line in made.lines}
    assert "reaches past the job's own span (setup)" in said["early"]
    assert "reaches past the job's own span (teardown)" in said["late"]
    # Nothing to subtract from, and nothing to subtract: the job's line
    # says what it has, its own events, and stops there.
    for leg in ("bare", "govern"):
        assert said[leg].endswith("event(s) of its own")


def test_a_jobs_entries_and_its_residue_add_up_to_the_span_the_forge_reported(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the residue: a reader adds a job up and nothing is missing."""
    from typing import Any, cast

    from livery.forge import Repository

    created = "2026-09-27T12:00:00Z"
    _pushed(
        work,
        monkeypatch,
        leg="check",
        origin=_epoch("2026-09-27T12:00:05Z"),
        tasks={"test": 12_000_000.0},
    )
    jobs = (
        _job("check", started="2026-09-27T12:00:02Z", completed="2026-09-27T12:00:22Z"),
    )
    repo = cast(Repository, cast(Any, _forge(jobs, created=created)))
    made = _traces.assemble(work, repo, "77", head_sha="abc123")
    job = next(event for event in made.events if event.get("cat") == "job")
    runner = {
        event["name"]: event for event in made.events if event.get("cat") == "runner"
    }
    # The checkout, the caches and the store, before the first entry.
    assert runner["setup"]["ts"] == job["ts"]
    assert runner["setup"]["dur"] == 3_000_000.0
    # The post-job save, after the last one.
    assert runner["teardown"]["dur"] == 5_000_000.0
    assert (
        runner["teardown"]["ts"] + runner["teardown"]["dur"] == job["ts"] + job["dur"]
    )
    entries = next(event for event in made.events if event.get("cat") == "task")
    assert (
        runner["setup"]["dur"] + entries["dur"] + runner["teardown"]["dur"]
        == (job["dur"])
    )
    # A track of its own, under its job's.
    tracks = {
        event["tid"]: event["args"]["name"]
        for event in made.events
        if event["ph"] == "M" and event["name"] == "thread_name"
    }
    assert tracks[runner["setup"]["tid"]] == "check: runner"
    order = {
        event["tid"]: event["args"]["sort_index"]
        for event in made.events
        if event["ph"] == "M" and event["name"] == "thread_sort_index"
    }
    assert order[runner["setup"]["tid"]] == order[job["tid"]] + 1
    # And the line carries the arithmetic.
    assert "setup 3.0s, teardown 5.0s" in made.lines[0]


class _Head:
    """A git stand-in answering one commit as its head.

    The fake forge mints shas of its own, and the commit a wave checks
    out is one of those; a real checkout of it is nothing this test
    needs.
    """

    def __init__(self, root: Path, sha: str) -> None:
        self.root = root
        self._sha = sha

    def head_sha(self) -> str:
        return self._sha


def _wave(repo: Any, commit: str) -> Any:
    """The dispatched run the forge filed under *commit*."""
    return next(run for run in repo.checks.runs(head_sha=commit) if run.event != "push")


def test_a_commit_nobody_can_walk_from_is_a_root_and_a_loop_cannot_spin(
    work: Path,
) -> None:
    """The refusals first: nothing is guessed, and no walk runs away.

    A commit with no run and no merge is a node with nothing under it.
    A forge that answers a merge commit which leads back to a commit
    already walked ends the walk there, and a depth of zero walks one
    commit however many merges there are.
    """
    from typing import cast

    from livery.forge import PullRequest, Repository

    class _Checks:
        def runs(self, *, head_sha: str = "", event: str = "") -> tuple[object, ...]:
            return ()

        def jobs(self, run: int) -> tuple[object, ...]:
            return ()

    class _Pulls:
        def find_by_head_sha(self, sha: str) -> PullRequest:
            # Every commit's merge leads back to itself: a forge cannot
            # answer this, and the walk must not depend on it not doing so.
            return PullRequest(
                number=1,
                title="t",
                body="",
                state="closed",
                merged=True,
                head_branch="",
                head_sha=sha,
                base_branch="main",
                url="fake://pr/1",
                author="someone",
                merged_sha=sha,
            )

    class _Repo:
        checks = _Checks()
        pr = _Pulls()

    repo = cast(Repository, cast(Any, _Repo()))
    walked = _traces.chain(work, repo, "a" * 40, depth=3)
    assert [node.commit for node in walked.nodes] == ["a" * 40]
    assert walked.nodes[0].runs == ()
    assert walked.events == []
    assert any("no run assembled" in line for line in walked.lines)
    # And a depth of zero never asks for the merge at all.
    assert len(_traces.chain(work, repo, "b" * 40, depth=0).nodes) == 1


def test_a_contract_that_wants_no_traces_records_nothing_about_a_run(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typing import cast

    _contract(work, profile=False)
    _in_ci(monkeypatch, run="91", leg="publish")
    context = _state.RunContext(
        "github", "91", "workflow_dispatch", "refs/heads/main", "f" * 40
    )
    assert _traces.about(work, cast(Any, _Head(work, "e" * 40)), run=context) == ""
    assert _state.read(work, _traces.ABOUT.series("e" * 40).ref).files is None


def test_a_run_the_forge_files_under_another_commit_records_what_it_ran_on(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record exists for one case and stays quiet in every other.

    A push run and a pull request run are filed under the commit they
    ran on, and then there is nothing to write down.
    """
    from typing import cast

    _in_ci(monkeypatch, run="91", leg="publish")
    ran_on, filed_under = "e" * 40, "f" * 40
    same = _state.RunContext("github", "91", "push", "refs/heads/main", ran_on)
    assert _traces.about(work, cast(Any, _Head(work, ran_on)), run=same) == ""
    moved = _state.RunContext(
        "github", "91", "workflow_dispatch", "refs/heads/main", filed_under
    )
    line = _traces.about(work, cast(Any, _Head(work, ran_on)), run=moved)
    assert "run 91 recorded as the run of eeeeeeeeeeee" in line
    rows = _traces.ABOUT.series(ran_on).rows(work)
    assert [row.data["run"] for row in rows.rows] == ["91"]
    assert rows.rows[0].data["event"] == "workflow_dispatch"


def test_a_release_walks_from_its_branch_into_the_wave_its_squash_dispatched(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole point: every run a release caused, found by recorded fact.

    A release is two commits and one hop. The branch commit carries its
    own run, the merge commit carries the base's run, and the wave is
    dispatched on the base, so the forge files it under whatever the
    base's tip is by then. Another merge lands first here, which moves
    that tip past the release squash, and the wave is still found,
    through the commit it recorded rather than through a time.
    """
    from typing import cast

    from livery.forge.testing import FakeDriver

    driver = FakeDriver()
    repo = driver.fresh_repo()
    branch = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", "main", "release: the set", "")
    repo.pr.merge_now(pr.number, title="release: the set")
    merged = repo.pr.get(pr.number)
    assert merged is not None and merged.merged_sha
    squash = merged.merged_sha
    # The base's own run of the squash, as a forge files one for the
    # commit a merge put there, and then an unrelated merge that moves
    # the base past it.
    driver.fake.push(repo.owner, repo.name, "main", sha=squash)
    later = driver.fake.push(repo.owner, repo.name, "main")
    repo.checks.dispatch("release.yml", ref="main", inputs={"ref": squash})
    wave = _wave(repo, later)
    # The wave writes down the commit it checked out, as its job does.
    _in_ci(monkeypatch, run=str(wave.id), leg="publish")
    context = _state.RunContext(
        "github", str(wave.id), wave.event, "refs/heads/main", later
    )
    assert "recorded as the run of" in _traces.about(
        work, cast(Any, _Head(work, squash)), run=context
    )

    walked = _traces.chain(work, cast(Any, repo), branch, depth=1)
    assert [node.commit for node in walked.nodes] == [branch, squash]
    about_branch = set(walked.nodes[0].runs)
    assert about_branch == {str(run.id) for run in repo.checks.runs(head_sha=branch)}
    # The squash carries the base's own run and the wave beside it, and
    # not the run of the commit that merged after it.
    base_run = next(
        str(run.id) for run in repo.checks.runs(head_sha=squash) if run.event == "push"
    )
    assert set(walked.nodes[1].runs) == {base_run, str(wave.id)}
    assert any(f"merged as {squash[:12]}" in line for line in walked.lines)
    assert any("recorded)" in line for line in walked.lines)
    # Each run is its own process group, and each says which commit it
    # is about, because two runs of a chain are told apart by that.
    groups = {
        event["args"]["name"]
        for event in walked.events
        if event.get("ph") == "M" and event.get("name") == "process_name"
    }
    assert f"run {wave.id} of {squash[:12]} ({wave.event})" in groups
    assert {event["pid"] for event in walked.events} == {1, 2, 3}


def test_the_same_chain_walked_live_and_afterwards_is_the_same_tree(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The property the design exists for: no edge comes from a moment.

    The first walk happens with the runs still queued, the second after
    they finished. The timelines differ, because the jobs ran in
    between; the tree does not.
    """
    from typing import cast

    from livery.forge.testing import FakeDriver

    driver = FakeDriver()
    repo = driver.fresh_repo()
    branch = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", "main", "feat: change", "")
    repo.pr.merge_now(pr.number, title="feat: change")
    merged = repo.pr.get(pr.number)
    assert merged is not None
    driver.fake.push(repo.owner, repo.name, "main", sha=merged.merged_sha)
    live = _traces.chain(work, cast(Any, repo), branch, depth=1)
    for sha in (branch, merged.merged_sha):
        driver.fake.settle(repo.owner, repo.name, sha)
    after = _traces.chain(work, cast(Any, repo), branch, depth=1)
    assert [(n.commit, n.runs) for n in live.nodes] == [
        (n.commit, n.runs) for n in after.nodes
    ]


def test_a_chain_written_for_a_person_records_the_zero_it_is_measured_from(
    work: Path,
) -> None:
    """A file of a chain reads on one clock, and says where that clock starts.

    Every run of the walk keeps the stamps it was written with, so the
    file records the earliest of them and measures from there. Without
    the origin, another timeline could not be laid on this one.
    """
    import json as _json
    from typing import cast

    from livery.forge.testing import FakeDriver

    driver = FakeDriver()
    repo = driver.fresh_repo()
    branch = driver.push(repo.owner, repo.name, "feature")
    pr = repo.pr.open("feature", "main", "feat: change", "")
    repo.pr.merge_now(pr.number, title="feat: change")
    merged = repo.pr.get(pr.number)
    assert merged is not None
    driver.fake.push(repo.owner, repo.name, "main", sha=merged.merged_sha)
    driver.fake.settle(repo.owner, repo.name, branch)
    driver.fake.settle(repo.owner, repo.name, merged.merged_sha)
    path, lines = _traces.write_chain(work, cast(Any, repo), branch, depth=1)
    assert path is not None
    assert path == work / ".fm" / "profiles" / f"chain-{branch[:12]}.json"
    written = _json.loads(path.read_text(encoding="utf-8"))
    # Measured from the earliest stamp of the whole walk, so nothing sits
    # before zero, and the wall-clock moment of that zero is in the file.
    assert written["originEpochUs"] > 0
    stamps = [e["ts"] for e in written["traceEvents"] if "ts" in e]
    assert stamps and min(stamps) == 0.0
    assert any("merged as" in line for line in lines)
    # A commit the forge knows nothing about writes no file, and says so.
    nothing, why = _traces.write_chain(work, cast(Any, repo), "a" * 40, depth=1)
    assert nothing is None
    assert any("no run assembled" in line for line in why)
