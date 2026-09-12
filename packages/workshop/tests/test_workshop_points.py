"""CI as points: refusals first, then the schedule and the runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from livery.workshop import _points

_FAILURES = (BaseException,)


def _root(tmp_path: Path, schedule: str = "") -> Path:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nlayers = ["livery.workshop"]\n\n[forge]\nkind = "gitea"\n'
        'owner = "owner"\n' + schedule
    )
    return tmp_path


@pytest.fixture(autouse=True)
def _outside_ci(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_ACTIONS",
        "GITEA_ACTIONS",
        "GITLAB_CI",
        "GITHUB_EVENT_NAME",
        "GITHUB_EVENT_PATH",
        "GITHUB_SHA",
        "GITHUB_RUN_ID",
        "GITHUB_REF",
        "GITHUB_JOB",
    ):
        monkeypatch.delenv(name, raising=False)


# --- refusals -----------------------------------------------------------------


def test_an_unknown_point_names_the_four(tmp_path: Path) -> None:
    with pytest.raises(
        _FAILURES,
        match="'stage' is not a point; the points are gate, merge, nightly, release",
    ):
        _points.jobs_of(_root(tmp_path), "stage")


def test_each_point_names_its_workflow_and_events() -> None:
    from livery.workshop._points import DISPATCHABLE, EVENTS, POINTS, workflow_of
    from livery.workshop._release_driver import RELEASE_WORKFLOW

    with pytest.raises(_FAILURES) as caught:
        workflow_of("weekly")
    assert "not a point" in str(caught.value)
    assert workflow_of("gate") == workflow_of("merge") == "ci.yml"
    assert workflow_of("nightly") == "nightly.yml"
    assert workflow_of("release") == RELEASE_WORKFLOW
    assert set(EVENTS) == set(POINTS)
    assert EVENTS["nightly"] == ("schedule", "workflow_dispatch")
    assert DISPATCHABLE == ("nightly",)


def test_an_unknown_job_names_the_points_jobs(tmp_path: Path) -> None:
    with pytest.raises(
        _FAILURES,
        match="the gate point has no job 'deploy'; its jobs are check, docs, gate",
    ):
        _points.entries_for(_root(tmp_path), "gate", "deploy")


def test_a_schedule_entry_with_an_unknown_point_refuses_at_load(tmp_path: Path) -> None:
    root = _root(tmp_path, '\n[[ci.schedule]]\npoint = "stage"\ntask = "x"\n')
    with pytest.raises(_FAILURES, match=r"entry 1: point 'stage' is not a point"):
        _points.declared(root)


def test_a_schedule_entry_without_a_task_refuses_at_load(tmp_path: Path) -> None:
    root = _root(tmp_path, '\n[[ci.schedule]]\npoint = "nightly"\n')
    with pytest.raises(_FAILURES, match=r"entry 1 \(nightly\): names no task"):
        _points.declared(root)


def test_a_schedule_entry_with_junk_args_refuses_at_load(tmp_path: Path) -> None:
    root = _root(
        tmp_path, '\n[[ci.schedule]]\npoint = "nightly"\ntask = "x"\nargs = [1]\n'
    )
    with pytest.raises(_FAILURES, match="args must be strings"):
        _points.declared(root)


def test_a_red_entry_fails_the_job_and_stops(tmp_path: Path) -> None:
    root = _root(tmp_path)
    seen: list[list[str]] = []

    def red(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 3

    with pytest.raises(_FAILURES, match="gate/check: check exited 3"):
        _points.run_point(
            root, "gate", "check", os_label="linux", python="3.14", spawn=red
        )
    # The first entry failed, so the leg's row was never attempted.
    assert len(seen) == 1


# --- the schedule -------------------------------------------------------------


def test_the_builtin_jobs_of_each_point(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert _points.jobs_of(root, "gate") == ("check", "docs", "gate")
    # The merge point inherits the gate's jobs and adds its own.
    assert _points.jobs_of(root, "merge") == (
        "check",
        "docs",
        "gate",
        "deploy",
        "govern",
        "dispatch",
    )
    # The nightly point runs the whole check, its own tests selected in.
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    assert _points.entries_for(root, "nightly", "nightly") == (
        _points.Entry("nightly", "nightly", "check", profiled=True),
    )
    # A point's own job exists before its first entry: the shell runs
    # green and empty until the contract attaches a task.
    assert _points.jobs_of(root, "release") == ()
    assert _points.entries_for(root, "release", "release") == ()


def test_a_declared_entry_joins_its_point(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "release.replay"\n'
        'args = ["--all"]\n'
        '\n[[ci.schedule]]\npoint = "gate"\njob = "docs"\ntask = "docs.links"\n',
    )
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    entries = _points.entries_for(root, "nightly", "nightly")
    assert [e.task for e in entries] == ["check", "release.replay"]
    assert entries[-1] == _points.Entry(
        "nightly", "nightly", "release.replay", ("--all",), source="workshop.toml"
    )
    docs = _points.entries_for(root, "gate", "docs")
    assert [e.task for e in docs] == ["docs.build", "docs.links"]
    assert docs[-1].source == "workshop.toml"


def test_the_runner_spawns_each_entry_with_the_legs_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _root(tmp_path)
    seen: list[list[str]] = []

    legs: list[str] = []
    points: list[str] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        legs.append(env.get("WORKSHOP_LEG", "<unset>"))
        points.append(env.get("WORKSHOP_POINT", "<unset>"))
        return 0

    _points.run_point(
        root, "gate", "check", os_label="ubuntu-latest", python="3.14", spawn=green
    )
    # Every child's environment names the leg, the key of its stamps,
    # and the point, which selects the tests.
    assert legs == ["check-ubuntu-latest-3.14"] * len(seen)
    assert points == ["gate"] * len(seen)
    assert seen == [
        ["hse", "--profile=fm-profile.json", "check"],
        ["hse", "coverage.leg", "--job=check (ubuntu-latest, 3.14)"],
    ]
    seen.clear()
    _points.run_point(root, "gate", "gate", spawn=green)
    # The render gate and the provenance check live in the gate job,
    # the one place a scoped leg cannot skip them.
    assert seen == [
        ["hse", "workflow.release.check-title"],
        ["hse", "template.check"],
        ["hse", "provenance"],
        ["hse", "coverage.union"],
        ["hse", "ci.metrics.collect"],
        ["hse", "speed.judge"],
        ["hse", "ci.verdict", "--needs=check,docs"],
        ["hse", "ci.verified.stamp"],
    ]


def test_a_push_promotes_the_gate_to_the_merge_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _root(tmp_path)
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "push")
    assert _points.effective_point("gate") == "merge"
    assert _points.effective_point("nightly") == "nightly"
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    _points.run_point(root, "gate", "govern", spawn=green)
    assert seen == [["fm", "workflow.configure", "--if-changed"]]
    assert "point: gate on a push is the merge point" in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert _points.effective_point("gate") == "gate"
    with pytest.raises(_FAILURES, match="the gate point has no job 'govern'"):
        _points.run_point(root, "gate", "govern", spawn=green)


def test_the_nightly_point_runs_the_whole_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _root(tmp_path)
    seen: list[tuple[list[str], str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append((argv, env.get("WORKSHOP_POINT", "<unset>")))
        return 0

    _points.run_point(root, "nightly", "nightly", python="3.14", spawn=green)
    assert seen == [(["hse", "--profile=fm-profile.json", "check"], "nightly")]


def test_the_merge_points_gate_job_ends_with_the_janitor_and_the_gates_does_not(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    merge = [entry.task for entry in _points.entries_for(root, "merge", "gate")]
    assert merge[-2:] == ["ci.verified.stamp", "janitor"]
    gate = [entry.task for entry in _points.entries_for(root, "gate", "gate")]
    assert "janitor" not in gate and gate[-1] == "ci.verified.stamp"


def test_the_check_legs_are_one_per_runner_and_gate_python(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[ci]\nrunners = ["ubuntu-latest", "macos-latest"]\n'
        'python-versions = ["3.13", "3.14"]\n',
    )
    assert _points.check_legs(root) == [
        "check-ubuntu-latest-3.13",
        "check-ubuntu-latest-3.14",
        "check-macos-latest-3.13",
        "check-macos-latest-3.14",
    ]
