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
        legs=_traces.LEGS_DEFAULT,
        window=_traces.WINDOW_DEFAULT,
        into=_traces.INTO_DEFAULT,
    )


def test_a_key_of_the_wrong_type_is_named_and_its_default_stands(work: Path) -> None:
    """A typo must be visible and must not stop a job.

    Whether a timeline is kept is not worth failing a leg over, and a
    key that reads as false by accident would keep nothing at all and
    say nothing about why.
    """
    _contract(work, profile_legs="yes", profile_window="lots", profile_into=17)
    kept, why = _traces.policy(work)
    assert kept.legs is _traces.LEGS_DEFAULT
    assert kept.window == _traces.WINDOW_DEFAULT
    assert kept.into == _traces.INTO_DEFAULT
    assert "[ci] profile-legs is 'yes'; it is true or false" in why
    assert "[ci] profile-window is 'lots'; it is a whole number of runs" in why
    assert "[ci] profile-into is 17; it is a path" in why


def test_a_contract_that_wants_no_traces_pushes_nothing_and_says_nothing(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _contract(work, profile_legs=False)
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
    from livery.workshop._ci_tasks import profile_push_flow

    _in_ci(monkeypatch)
    _git(work, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    _trace_file(work)
    profile_push_flow(work, Path("fm-profile.json"))
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
    assert "pushed" in _traces.push(work, trace)


def _epoch(stamp: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp() * 1e6


def test_a_run_the_forge_cannot_be_asked_about_assembles_nothing(work: Path) -> None:
    from typing import Any, cast

    from livery.forge import Repository

    repo = cast(Repository, cast(Any, _forge(())))
    events, lines = _traces.assemble(work, repo, "not-a-number")
    assert events == [] and "not a number" in lines[0]
    events, lines = _traces.assemble(work, repo, "77")
    assert events == [] and "lists no job for it" in lines[0]


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
    events, lines = _traces.assemble(work, repo, "77")
    assert any("docs: the trace does not parse" in line for line in lines)
    assert any("check: " in line and "event(s) of its own" in line for line in lines)
    # Both jobs are still drawn, and only the readable leg brought its own.
    drawn = {e["name"] for e in events if e.get("cat") == "job"}
    assert drawn == {"check", "docs"}
    assert any(e.get("cat") == "task" and e["name"] == "check" for e in events)


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
    events, lines = _traces.assemble(work, repo, "77", head_sha="abc123", now=now)
    by_name = {e["name"]: e for e in events if e.get("cat") == "job"}
    # The run's zero is the run's own acceptance, so a job's slice sits at
    # its wait's length.
    assert by_name["check-a"]["ts"] == 5_000_000.0
    assert by_name["check-a"]["dur"] == 4_000_000.0
    assert by_name["check-b"]["ts"] == 30_000_000.0
    # The skip is an event, never a span of no length.
    skipped = next(e for e in events if e["name"] == "govern")
    assert skipped["ph"] == "i" and "dur" not in skipped
    assert skipped["args"]["conclusion"] == "skipped"
    # A running job is drawn up to the reading, and says so.
    running = by_name["gate"]
    assert running["args"]["running"] is True
    assert running["ts"] + running["dur"] == 50_000_000.0
    # The wait before a job is its own span, and the steps are inside the job.
    waits = {e["name"]: e for e in events if e.get("cat") == "queue"}
    assert waits["check-a: queued"]["dur"] == 5_000_000.0
    step = next(e for e in events if e.get("cat") == "step")
    assert step["ts"] == by_name["check-a"]["ts"]
    # Each leg's own trace is inside its job's span, and in groups of its own.
    tasks = {e["name"]: e for e in events if e.get("cat") == "task"}
    for leg, task in (("check-a", "a"), ("check-b", "b")):
        job, own = by_name[leg], tasks[task]
        assert job["ts"] <= own["ts"]
        assert own["ts"] + own["dur"] <= job["ts"] + job["dur"]
    assert tasks["a"]["pid"] != tasks["b"]["pid"]
    groups = {
        e["args"]["name"]
        for e in events
        if e["ph"] == "M" and e["name"] == "process_name"
    }
    assert groups == {"run 77", "check-a: fm", "check-b: fm"}
    assert not [line for line in lines if "could not" in line]


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
    events, lines = _traces.assemble(work, repo, "77")
    assert any("could not be listed" in line for line in lines)
    assert [e["name"] for e in events if e.get("cat") == "job"] == ["check"]


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
    events, _lines = _traces.assemble(work, repo, "77")
    drawn = {e["name"]: e for e in events if e.get("cat") == "job"}
    assert drawn["check"]["ph"] == "i"  # no start the forge could be read on
    assert drawn["docs"]["ph"] == "X"
    assert not [e for e in events if e.get("cat") == "step"]


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
    events, lines = _traces.assemble(work, repo, "77", head_sha="abc123")
    assert any("not among the forge's runs for abc123" in line for line in lines)
    assert not [e for e in events if e.get("cat") == "queue"]


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
    _events, lines = _traces.assemble(work, repo, "77")
    assert any(f"carries no {_traces.TRACE_FILE}" in line for line in lines)


def test_a_ref_the_channel_cannot_read_names_the_job_it_belonged_to(
    work: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whatever the store refuses, the line says which job it was about."""
    from typing import Any, cast

    from livery.forge import Repository

    _pushed(work, monkeypatch, leg="check", origin=1.0, tasks={"check": 1.0})
    monkeypatch.setattr(
        _state.Series,
        "file",
        lambda self, root, name: (None, "the remote hung up"),
    )
    began = "2026-09-27T10:00:00Z"
    jobs = (_job("check", started=began, completed="2026-09-27T10:00:01Z"),)
    repo = cast(Repository, cast(Any, _forge(jobs)))
    _events, lines = _traces.assemble(work, repo, "77")
    assert "check: the remote hung up" in lines


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
            events, _lines = _traces.assemble(work, repo, "77", head_sha="abc123")
            spans.append(next(e for e in events if e.get("cat") == "job")["ts"])
    finally:
        # The zone is process state, and tzset has to follow the variable
        # back or every later test in this worker reads Amsterdam.
        if had is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = had
        time.tzset()
    assert spans[0] == spans[1] == 10_000_000.0
