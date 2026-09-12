"""CI as points: refusals first, then the schedule and the runner."""

from __future__ import annotations

import os
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


def test_a_schedule_entry_with_an_unknown_cadence_refuses_at_load(
    tmp_path: Path,
) -> None:
    root = _root(
        tmp_path, '\n[[ci.schedule]]\npoint = "nightly"\ntask = "x"\nevery = "3d"\n'
    )
    with pytest.raises(_FAILURES, match=r"every '3d' is not a cadence"):
        _points.declared(root)


def test_a_cadence_is_due_on_its_monday_and_names_the_next_one() -> None:
    from datetime import date

    # No cadence: every run.
    assert _points.due("", date(2026, 9, 16)) == (True, date(2026, 9, 16))
    # Weekly: Monday runs, Wednesday waits for the next Monday.
    assert _points.due("1w", date(2026, 9, 14)) == (True, date(2026, 9, 14))
    assert _points.due("1w", date(2026, 9, 16)) == (False, date(2026, 9, 21))
    # Two-weekly: the Monday of an even ISO week. 2026-09-14 is week
    # 38, 2026-09-21 week 39, 2026-09-28 week 40.
    assert _points.due("2w", date(2026, 9, 14)) == (True, date(2026, 9, 14))
    assert _points.due("2w", date(2026, 9, 21)) == (False, date(2026, 9, 28))
    assert _points.due("2w", date(2026, 9, 16)) == (False, date(2026, 9, 28))
    assert _points.due("2w", date(2026, 9, 23)) == (False, date(2026, 9, 28))


def test_an_entry_off_its_day_is_skipped_and_says_when(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from datetime import date

    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "tools.refresh"\n'
        'args = ["--submit"]\nevery = "2w"\n',
    )
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    monkeypatch.setattr(_points, "today", lambda: date(2026, 9, 16))
    _points.run_point(root, "nightly", "nightly", spawn=green)
    out = capsys.readouterr().out
    assert (
        "tools.refresh (workshop.toml) runs every 2w; next on 2026-09-28, skipped"
        in out
    )
    assert [argv[-1] for argv in seen] == ["check"]
    monkeypatch.setattr(_points, "today", lambda: date(2026, 9, 28))
    seen.clear()
    _points.run_point(root, "nightly", "nightly", spawn=green)
    assert [argv[-1] for argv in seen] == ["check", "--submit"]


def test_the_nightly_carries_the_forge_token_where_the_repository_has_one() -> None:
    # A pull request the refresh opens with the job token starts no
    # workflow; the secret, when present, is what makes it a real one.
    from livery.workshop._ci_generate import generate

    files = generate(Path(__file__).resolve().parents[3])
    nightly = files[".github/workflows/nightly.yml"]
    assert "FORGE_TOKEN: ${{ secrets.FORGE_TOKEN || secrets.GITHUB_TOKEN }}" in nightly


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


def test_the_runner_hands_its_children_one_listing_of_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from livery.workshop._state import SNAPSHOT_VARIABLE

    monkeypatch.delenv(SNAPSHOT_VARIABLE, raising=False)
    named: list[str] = []
    present: list[bool] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        named.append(env.get(SNAPSHOT_VARIABLE, "<unset>"))
        present.append(Path(named[-1]).is_file())
        return 0

    # A root the listing fails on (no origin) publishes nothing: the
    # children list for themselves, and the runner's own environment
    # is never written.
    alone = tmp_path / "alone"
    alone.mkdir()
    _points.run_point(
        _root(alone),
        "gate",
        "check",
        os_label="ubuntu-latest",
        python="3.14",
        spawn=green,
    )
    assert named == ["<unset>"] * 2 and SNAPSHOT_VARIABLE not in os.environ
    named.clear()
    present.clear()
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "--initial-branch=main", str(origin)],
        check=True,
        capture_output=True,
    )
    work = tmp_path / "work"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(work)], check=True, capture_output=True
    )
    _points.run_point(
        _root(work),
        "gate",
        "check",
        os_label="ubuntu-latest",
        python="3.14",
        spawn=green,
    )
    # One file for the whole job, named to every child while the job
    # runs, taken for this checkout, and gone after it.
    assert len(set(named)) == 1 and named[0] != "<unset>", named
    assert present == [True, True]
    assert not Path(named[0]).exists()
    assert SNAPSHOT_VARIABLE not in os.environ


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
