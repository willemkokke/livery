"""CI as points: refusals first, then the schedule and the runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from livery.workshop import _points

# The coverage leg and the union are the coverage extension's, added as
# the mount adds them for a workspace that lists coverage.
from workshop_coverage_declared import coverage_jobs  # noqa: F401

# The site's jobs are the docs extension's, added as the mount adds them.
from workshop_docs_declared import docs_jobs  # noqa: F401

# The wheels job is nanobind's, added as the mount adds it.
from workshop_nanobind_declared import nanobind_jobs  # noqa: F401
from workshop_seeds import Seeds, _seed_home, pushed, seed_copier  # noqa: F401

_FAILURES = (BaseException,)


def _root(tmp_path: Path, schedule: str = "") -> Path:
    (tmp_path / "workshop.toml").write_text(
        '[workspace]\nextensions = []\n\n[forge]\nkind = "gitea"\n'
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


def test_a_declaration_a_shell_cannot_render_from_refuses_at_load() -> None:
    from livery.workshop._points import Job, Point, verify_points

    gate = Point("gate", "ci.yml", ("pull_request",), jobs=(Job("check"),))

    def refusal(*points: Point) -> str:
        with pytest.raises(_FAILURES) as caught:
            verify_points(points)
        return str(caught.value)

    assert "the gate point is declared twice" in refusal(gate, gate)
    assert "runs on 'weekly', which is not an event" in refusal(
        Point("clock", "clock.yml", ("weekly",))
    )
    assert "inherits 'gate', which is not a declared point" in refusal(
        Point("merge", "ci.yml", ("push",), inherits="gate")
    )
    # Two points on one file whose events overlap: a run would belong
    # to both, and neither reader could tell.
    assert "share ci.yml and both run on pull_request" in refusal(
        gate, Point("twin", "ci.yml", ("pull_request", "push"))
    )
    assert "declares the job 'check' twice" in refusal(
        Point("gate", "ci.yml", ("pull_request",), jobs=(Job("check"), Job("check")))
    )
    # An inherited job counts as declared twice too.
    assert "declares the job 'check' twice" in refusal(
        gate, Point("merge", "ci.yml", ("push",), inherits="gate", jobs=(Job("check"),))
    )
    assert "needs 'lint', which the point does not have; its jobs are check" in refusal(
        Point(
            "gate", "ci.yml", ("pull_request",), jobs=(Job("check", needs=("lint",)),)
        )
    )
    assert "collects 'wheels', which no job of the point publishes" in refusal(
        Point(
            "release",
            "release.yml",
            ("workflow_dispatch",),
            jobs=(Job("publish", collects="wheels"),),
        )
    )
    # A job starts once the jobs it needs end: a collect from a job it
    # does not wait for may find nothing uploaded yet.
    assert refusal(
        Point(
            "release",
            "release.yml",
            ("workflow_dispatch",),
            jobs=(Job("wheels", publishes="wheels"), Job("publish", collects="wheels")),
        )
    ) == (
        "the release point's publish job collects 'wheels' and waits for no job"
        " that publishes it (wheels); a job collects only from the jobs it needs"
    )
    # A join waits for what the job it joins waits for: this gate job
    # waits for the check alone, so a join collecting the site the docs
    # job publishes refuses.
    site_gate = Point(
        "gate",
        "ci.yml",
        ("pull_request",),
        jobs=(Job("check"), Job("docs"), Job("gate", needs=("check",))),
    )
    site_merge = Point(
        "merge",
        "ci.yml",
        ("push",),
        inherits="gate",
        jobs=(
            Job("docs", joins=True, publishes="site", paths=("site",)),
            Job("gate", joins=True, collects="site"),
        ),
    )
    assert refusal(site_gate, site_merge) == (
        "the merge point's gate job collects 'site' and waits for no job that"
        " publishes it (docs); a job collects only from the jobs it needs"
    )
    # A need on an inherited job, and an artifact an inherited job
    # publishes, are both satisfied, as is a join collecting what a job
    # the joined job waits for publishes.
    verify_points(
        (
            Point(
                "gate",
                "ci.yml",
                ("pull_request",),
                jobs=(Job("check", publishes="trace"),),
            ),
            Point(
                "merge",
                "ci.yml",
                ("push",),
                inherits="gate",
                jobs=(Job("deploy", needs=("check",), collects="trace"),),
            ),
        )
    )
    verify_points(
        (
            Point(
                "gate",
                "ci.yml",
                ("pull_request",),
                jobs=(Job("check"), Job("docs"), Job("gate", needs=("check", "docs"))),
            ),
            site_merge,
        )
    )


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
    assert EVENTS["gate"] == ("pull_request", "workflow_dispatch")
    assert EVENTS["merge"] == ("push",)
    assert EVENTS["nightly"] == ("schedule", "workflow_dispatch")
    # A dispatch entry and no inputs: the release takes inputs, so
    # the merge point dispatches it through its own verb.
    assert DISPATCHABLE == ("gate", "nightly")
    from livery.workshop._points import INHERITS, POINT_BY_NAME

    assert INHERITS == {"merge": "gate"}
    # The base declares the check and the verdict; the docs job is the
    # docs extension's, in place once its declaration registers.
    assert [job.name for job in POINT_BY_NAME["gate"].jobs] == ["check", "gate"]
    from livery.workshop._points import point_by_name

    assert [job.name for job in point_by_name(None)["gate"].jobs] == [
        "check",
        "docs",
        "gate",
    ]
    assert POINT_BY_NAME["release"].ref_input == "ref"
    assert [i.name for i in POINT_BY_NAME["release"].inputs] == ["ref", "workshop"]


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
    with pytest.raises(_FAILURES, match=r"ci\.schedule\[\]\.args is a list"):
        _points.declared(root)


def test_a_schedule_entry_with_an_unknown_cadence_refuses_at_load(
    tmp_path: Path,
) -> None:
    root = _root(
        tmp_path, '\n[[ci.schedule]]\npoint = "nightly"\ntask = "x"\nevery = "3d"\n'
    )
    with pytest.raises(_FAILURES, match=r"ci\.schedule\[\]\.every is '3d'"):
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
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "toolroom.refresh"\n'
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
        "toolroom.refresh (workshop.toml) runs every 2w; next on 2026-09-28, skipped"
        in out
    )
    assert [argv[-1] for argv in seen] == ["doctor", "check"]
    monkeypatch.setattr(_points, "today", lambda: date(2026, 9, 28))
    seen.clear()
    _points.run_point(root, "nightly", "nightly", spawn=green)
    assert [argv[-1] for argv in seen] == ["doctor", "check", "--submit"]


def test_a_pull_requests_legs_follow_its_runners_and_every_leg_otherwise(
    tmp_path: Path,
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.14"\n'
    )
    root = _root(tmp_path, '\n[ci]\nrunners = ["a", "b"]\n')
    assert _points.pull_request_legs(root) == _points.check_legs(root)
    (tmp_path / "workshop.toml").write_text(
        (tmp_path / "workshop.toml").read_text() + 'pull-request-runners = ["b"]\n'
    )
    assert _points.check_legs(root) == ["check-a-3.14", "check-b-3.14"]
    assert _points.pull_request_legs(root) == ["check-b-3.14"]


def test_an_entry_that_runs_once_runs_on_the_first_leg_alone(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.11"\n'
    )
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "ci.e2e"\n'
        'args = ["--scenario=release", "--from-main"]\nonce = true\n',
    )
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    first, *rest = _points_pythons(root)
    _points.run_point(root, "nightly", "nightly", python=rest[-1], spawn=green)
    out = capsys.readouterr().out
    assert (
        f"ci.e2e (workshop.toml) runs once, on the first leg ({first}); skipped" in out
    )
    assert [argv[-1] for argv in seen] == ["doctor", "check"]
    seen.clear()
    _points.run_point(root, "nightly", "nightly", python=first, spawn=green)
    assert [argv[-3:] for argv in seen][-1] == [
        "ci.e2e",
        "--scenario=release",
        "--from-main",
    ]
    # Without a leg to name, the entry runs: a job with no matrix has one leg.
    assert _points.first_leg(root, "nightly", "govern") == ("", "")


def _points_pythons(root: Path) -> list[str]:
    from livery.workshop._pythons import python_matrix

    versions = python_matrix(root)
    assert len(versions) > 1, versions
    return versions


def test_the_nightly_carries_the_forge_token_where_the_repository_has_one() -> None:
    # A pull request the refresh opens with the job token starts no
    # workflow; the secret, when present, is what makes it a real one.
    from livery.workshop._ci_generate import generate

    files = generate(Path(__file__).resolve().parents[3])
    nightly = files[".github/workflows/nightly.yml"]
    assert "FORGE_TOKEN: ${{ secrets.FORGE_TOKEN || secrets.GITHUB_TOKEN }}" in nightly


def _rendered(tmp_path: Path, kind: str) -> dict[str, str]:
    from livery.workshop._ci_generate import generate

    root = tmp_path / kind
    root.mkdir()
    (root / "workshop.toml").write_text(
        "[workspace]\nextensions = []\n\n[forge]\n"
        f'kind = "{kind}"\nowner = "owner"\n\n[ci]\nrunners = ["ubuntu-latest"]\n'
        "affected-legs = true\n"
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "scratch"\nrequires-python = ">=3.11"\n'
    )
    return generate(root)


def test_the_gate_carries_a_dispatch_entry_and_spells_one_call_on_every_event(
    tmp_path: Path,
) -> None:
    # A dispatched run pays the full gate because the check verb reads
    # the event, so the shell never spells --full: one rendered call
    # serves a pull request, a push and a dispatch alike.
    for kind in ("github", "gitea"):
        gate = _rendered(tmp_path, kind)[f".{kind}/workflows/ci.yml"]
        triggers = gate.split("jobs:", 1)[0]
        assert "  pull_request:\n" in triggers
        assert "  workflow_dispatch:\n" in triggers
        assert "--full" not in gate
        assert gate.count("ci.run --point=gate --job=check") == 1
    # GitLab names a dispatched pipeline after the workflow asked for,
    # so a dispatched gate is told from a dispatched wave.
    pipeline = _rendered(tmp_path, "gitlab")[".gitlab-ci.yml"]
    assert "workflow:\n  name: $FORGE_WORKFLOW\n" in pipeline
    assert "--full" not in pipeline


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


_TWO_SCHEDULED = (
    '\n[[ci.schedule]]\npoint = "nightly"\ntask = "acme.first"\n'
    '\n[[ci.schedule]]\npoint = "nightly"\ntask = "acme.second"\n'
)


def _task_of(argv: list[str]) -> str:
    return next(word for word in argv[1:] if not word.startswith("--"))


def test_a_dispatch_s_only_naming_a_task_the_point_does_not_run_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _root(tmp_path, _TWO_SCHEDULED)
    monkeypatch.setenv("only", "acme.second, acme.bogus")
    ran: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        ran.append(_task_of(argv))
        return 0

    with pytest.raises(
        _FAILURES, match=r"--only names acme\.bogus, which it does not run"
    ):
        _points.run_point(root, "nightly", "nightly", spawn=spawn)
    assert ran == []


def test_a_dispatch_s_only_runs_the_tasks_it_names_and_says_what_it_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A rehearsal names the loop alone: the builtin entries and every
    # other scheduled task are skipped, each said.
    root = _root(tmp_path, _TWO_SCHEDULED)
    monkeypatch.setenv("only", "acme.second")
    ran: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        ran.append(_task_of(argv))
        return 0

    _points.run_point(root, "nightly", "nightly", spawn=spawn)
    assert ran == ["acme.second"]
    out = capsys.readouterr().out
    for skipped in ("doctor (builtin)", "acme.first (workshop.toml)"):
        assert (
            f"nightly/nightly: {skipped} is not among the tasks this dispatch"
            " asked for; skipped" in out
        )
    names = {item.name for item in _points.point_by_name(root)["nightly"].inputs}
    assert _points.ONLY in names


@pytest.mark.usefixtures("coverage_jobs")
def test_a_red_union_stops_the_gate_job_before_the_stamp(tmp_path: Path) -> None:
    root = _root(tmp_path)
    seen: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        task = _task_of(argv)
        seen.append(task)
        return 3 if task == "coverage.union" else 0

    with pytest.raises(_FAILURES, match=r"gate/gate: coverage\.union exited 3"):
        _points.run_point(root, "gate", "gate", spawn=spawn)
    # The union judges the floors, so a red union ends the job: the
    # run's row is never collected, no verdict is reached, and no stamp
    # records the tree as proved.
    tasks = [entry.task for entry in _points.entries_for(root, "gate", "gate")]
    union = tasks.index("coverage.union")
    assert {"ci.metrics.collect", "ci.verdict", "ci.verified.stamp"} <= set(
        tasks[union + 1 :]
    )
    assert seen == tasks[: union + 1]


def test_a_red_judge_stops_the_gate_job_before_the_stamp(tmp_path: Path) -> None:
    from livery.workshop._declaration import Additions
    from livery.workshop._extensions import register_declared
    from workshop_docs_declared import jobs_restored

    root = _root(tmp_path)
    judge = _points.Job("gate", joins=True)
    entry = _points.Entry("gate", "gate", "acme.judge", judges=True)
    seen: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        task = _task_of(argv)
        seen.append(task)
        return 3 if task == "acme.judge" else 0

    with jobs_restored():
        register_declared(
            "acme",
            Additions((), (_points.JobContribution("gate", judge, (entry,)),)),
        )
        tasks = [entry.task for entry in _points.entries_for(root, "gate", "gate")]
        with pytest.raises(_FAILURES, match=r"gate/gate: acme\.judge exited 3"):
            _points.run_point(root, "gate", "gate", spawn=spawn)
    # A judge runs before the collect, so a red one ends the job: the
    # run's row is never collected, no verdict is reached, and no stamp
    # records the tree as proved.
    at = tasks.index("acme.judge")
    assert tasks[at + 1] == _points.COLLECT
    assert {"ci.verdict", "ci.verified.stamp"} <= set(tasks[at + 1 :])
    assert seen == tasks[: at + 1]


def test_a_workspace_without_coverage_runs_no_coverage_leg(
    seeds: Seeds, monkeypatch: pytest.MonkeyPatch
) -> None:
    from livery import git
    from livery.workshop._ci_context import LEG_VARIABLE, state_store
    from livery.workshop._ci_tasks import leg_flow
    from livery.workshop._extensions import declaration, register_jobs
    from livery.workshop._metrics import ROW_FILE, RUNS
    from livery.workshop._verified import FULL, write_marker
    from workshop_docs_declared import jobs_restored

    root = seeds("pushed", pushed) / "work"
    (root / "workshop.toml").write_text(
        '[workspace]\nextensions = ["ruff"]\n\n[forge]\nkind = "gitea"\n'
        'owner = "owner"\n'
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITEA_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_RUN_ID", "7")
    monkeypatch.setattr(_points, "_tracing", lambda root: False)
    spawned: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        task = _task_of(argv)
        spawned.append(task)
        if task == "check":
            # The gate's own trace and its scope, as a profiled gate leaves them.
            event = {"ph": "X", "cat": "task", "name": "check", "pid": 1, "tid": 1}
            (root / _points.TRACE).write_text(
                json.dumps({"traceEvents": [{**event, "ts": 0, "dur": 9000}]})
            )
            write_marker(root, FULL, leg=env[LEG_VARIABLE])
        if task == "ci.leg":
            monkeypatch.setenv(LEG_VARIABLE, env[LEG_VARIABLE])
            job = argv[-1].removeprefix("--job=")
            leg_flow(root, job=job, trace=Path(_points.TRACE))
        return 0

    with jobs_restored():
        # The mount registers the jobs of what the workspace lists: ruff alone.
        _points.restore(_points.snapshot(initial=True))
        found = declaration("ruff")
        assert found is not None
        register_jobs("ruff", found.additions.jobs)
        for point in _points.point_by_name(root):
            for job in _points.jobs_of(root, point):
                tasks = [e.task for e in _points.entries_for(root, point, job)]
                assert not [task for task in tasks if task.startswith("coverage.")]
        _points.run_point(
            root, "gate", "check", os_label="ubuntu-latest", python="3.14", spawn=spawn
        )
        assert spawned == ["check", "ci.leg"]
        # The leg's one write carries its row alone.
        series = RUNS.series("7", "check-ubuntu-latest-3.14")
        held = git.read(state_store(root), series.ref).files
        assert held is not None and set(held) == {ROW_FILE}
        # The gate job runs no union, and every entry green is a green job.
        spawned.clear()
        _points.run_point(root, "gate", "gate", spawn=spawn)
    assert spawned and not [task for task in spawned if task.startswith("coverage.")]


def test_a_red_scheduled_entry_leaves_the_ones_after_it_running(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # Two scheduled entries stand on their own: the first's red must not
    # hide the second's verdict, and the job names every red one.
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "acme.first"\n'
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "acme.second"\n',
    )
    seen: list[str] = []

    def spawn(argv: list[str], env: dict[str, str]) -> int:
        task = next(word for word in argv[1:] if not word.startswith("--"))
        seen.append(task)
        return 3 if task == "acme.first" else 0

    with pytest.raises(_FAILURES, match=r"nightly/nightly: acme\.first exited 3"):
        _points.run_point(root, "nightly", "nightly", spawn=spawn)
    assert seen[-2:] == ["acme.first", "acme.second"]
    assert (
        "nightly/nightly: acme.first exited 3; the scheduled entries after it"
        " still run" in capsys.readouterr().out
    )


# --- the schedule -------------------------------------------------------------


def test_the_builtin_jobs_of_each_point(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert _points.jobs_of(root, "gate") == ("check", "docs", "gate")
    # The merge point inherits the gate's jobs and adds its own.
    assert _points.jobs_of(root, "merge") == ("check", "docs", "gate", "govern")
    # The nightly point runs the whole check, its own tests selected in.
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    assert _points.entries_for(root, "nightly", "nightly") == (
        _points.Entry("nightly", "nightly", "doctor"),
        _points.Entry("nightly", "nightly", "check"),
    )
    # The release's job is declared; a point's own name is a job of it
    # too, green and empty until the contract attaches a task. A
    # workspace whose packages hold no nanobind has no wheels job.
    assert _points.jobs_of(root, "release") == ("publish",)
    # The wave's verbs are the release's entries, at the dispatched ref.
    assert [e.task for e in _points.entries_for(root, "release", "publish")] == [
        "workflow.release.publish"
    ]
    assert _points.entries_for(root, "release", "publish")[0].args == ("--ref={ref}",)
    assert _points.entries_for(root, "release", "release") == ()


@pytest.mark.usefixtures("nanobind_jobs")
def test_a_nanobind_roots_release_builds_its_wheels_then_publishes(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    member = root / "packages" / "native"
    (member / "src").mkdir(parents=True)
    (member / "workshop.toml").write_text(
        'kind = "python-nanobind"\nname = "acme-native"\n\n[nanobind]\n'
        'wheel-platforms = ["ubuntu-latest"]\n'
    )
    (member / "pyproject.toml").write_text('[project]\nname = "acme-native"\n')
    assert _points.jobs_of(root, "release") == ("wheels", "publish")
    # The wheels job builds this platform's wheels with nanobind's verb,
    # at the release squash its checkout is.
    assert _points.entries_for(root, "release", "wheels") == (
        _points.Entry("release", "wheels", "nanobind.wheels", source="nanobind"),
    )
    # The publish job waits for the wheels job and collects its artifact.
    publish = next(
        job
        for job in _points.point_by_name(root)["release"].jobs
        if job.name == "publish" and not job.joins
    )
    assert publish.needs == ("wheels",)


def test_a_declared_entry_joins_its_point(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "release.verify"\n'
        'args = ["--all"]\n'
        '\n[[ci.schedule]]\npoint = "gate"\njob = "docs"\ntask = "docs.links"\n',
    )
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    # A job an entry names that no declaration has lists after the
    # declared ones.
    assert _points.jobs_of(root, "gate") == ("check", "docs", "gate")
    entries = _points.entries_for(root, "nightly", "nightly")
    assert [e.task for e in entries] == ["doctor", "check", "release.verify"]
    assert entries[-1] == _points.Entry(
        "nightly", "nightly", "release.verify", ("--all",), source="workshop.toml"
    )
    docs = _points.entries_for(root, "gate", "docs")
    assert [e.task for e in docs] == ["docs.build", "docs.links"]
    assert docs[-1].source == "workshop.toml"


@pytest.mark.usefixtures("coverage_jobs")
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
    # Every entry under a trace of its own, named after the task it ran.
    assert seen == [
        ["hse", "--profile=fm-profile-check.json", "check"],
        ["hse", "--profile=fm-profile-coverage-leg.json", "coverage.leg"],
        [
            "hse",
            "--profile=fm-profile-ci-leg.json",
            "ci.leg",
            "--job=check (ubuntu-latest, 3.14)",
        ],
    ]
    # On GitLab the job is named by its key alone, the leg's label
    # unchanged: the collect step joins the row with the forge's job
    # by that name.
    seen.clear()
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setenv("GITLAB_CI", "true")
    monkeypatch.setenv("CI_PIPELINE_SOURCE", "merge_request_event")
    _points.run_point(
        root, "gate", "check", os_label="ubuntu-latest", python="3.14", spawn=green
    )
    assert seen[-1] == [
        "hse",
        "--profile=fm-profile-ci-leg.json",
        "ci.leg",
        "--job=check",
    ]
    assert legs[-1] == "check-ubuntu-latest-3.14"
    monkeypatch.delenv("GITLAB_CI")
    monkeypatch.delenv("CI_PIPELINE_SOURCE")
    seen.clear()
    _points.run_point(root, "gate", "gate", spawn=green)
    # The drift and provenance steps are the legs' own walk, selected
    # by what changed, as a local gate selects them; the gate job holds
    # none of them.
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-workflow-release-check-title.json",
            "workflow.release.check-title",
        ],
        ["hse", "--profile=fm-profile-coverage-union.json", "coverage.union"],
        ["hse", "--profile=fm-profile-ci-metrics-collect.json", "ci.metrics.collect"],
        ["hse", "--profile=fm-profile-speed-judge.json", "speed.judge"],
        [
            "hse",
            "--profile=fm-profile-ci-verdict.json",
            "ci.verdict",
            "--needs=check,docs",
        ],
        ["hse", "--profile=fm-profile-ci-verified-stamp.json", "ci.verified.stamp"],
        [
            "hse",
            "--profile=fm-profile-workflow-release-check-fresh.json",
            "workflow.release.check-fresh",
        ],
    ]


def test_the_runner_hands_its_children_one_listing_of_the_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    from livery.workshop._ci_context import SNAPSHOT_VARIABLE

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
    entries = len(_points.entries_for(_root(alone), "gate", "check"))
    assert named == ["<unset>"] * entries and SNAPSHOT_VARIABLE not in os.environ
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
    assert present == [True] * entries
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
    assert seen == [
        [
            "fm",
            "--profile=fm-profile-workflow-configure.json",
            "workflow.configure",
            "--if-changed",
        ]
    ]
    assert "point: gate on a push is the merge point" in capsys.readouterr().out
    monkeypatch.setenv("GITHUB_EVENT_NAME", "pull_request")
    assert _points.effective_point("gate") == "gate"
    with pytest.raises(_FAILURES, match="the gate point has no job 'govern'"):
        _points.run_point(root, "gate", "govern", spawn=green)


def test_a_dispatched_points_inputs_reach_its_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json

    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _root(tmp_path)
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    # Outside a dispatch the input is empty: the verb takes HEAD.
    _points.run_point(root, "release", "publish", spawn=green)
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-workflow-release-publish.json",
            "workflow.release.publish",
            "--ref=",
        ]
    ]
    seen.clear()
    # GitHub and Gitea carry the inputs in the event payload.
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"ref": "abc123", "workshop": ""}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    _points.run_point(root, "release", "publish", spawn=green)
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-workflow-release-publish.json",
            "workflow.release.publish",
            "--ref=abc123",
        ]
    ]
    seen.clear()
    # GitLab carries them as pipeline variables, environment by name.
    monkeypatch.delenv("GITHUB_EVENT_PATH")
    monkeypatch.setenv("ref", "def456")
    _points.run_point(root, "release", "publish", spawn=green)
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-workflow-release-publish.json",
            "workflow.release.publish",
            "--ref=def456",
        ]
    ]


@pytest.mark.usefixtures("nanobind_jobs")
def test_the_wheels_job_runs_nanobinds_verb_at_the_checkout_with_no_ref(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _root(tmp_path)
    seen: list[list[str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        return 0

    # The dispatch names the squash; the job's checkout already is it.
    event = tmp_path / "event.json"
    event.write_text(json.dumps({"inputs": {"ref": "abc123", "workshop": ""}}))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))
    _points.run_point(root, "release", "wheels", spawn=green)
    assert seen == [
        ["hse", "--profile=fm-profile-nanobind-wheels.json", "nanobind.wheels"]
    ]


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
    # The doctor opens the night's record, then the whole check.
    assert seen == [
        (["hse", "--profile=fm-profile-doctor.json", "doctor"], "nightly"),
        (["hse", "--profile=fm-profile-check.json", "check"], "nightly"),
    ]


def test_the_merge_points_gate_job_ends_with_the_janitor_and_the_gates_does_not(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    merge = [entry.task for entry in _points.entries_for(root, "merge", "gate")]
    # The stamp, the freshness check, then the merge point's own: the
    # dispatch, what the extensions add, and the janitor last.
    assert merge[-1] == "janitor"
    order = [
        merge.index(task)
        for task in (
            "ci.verified.stamp",
            "workflow.release.check-fresh",
            "workflow.release.dispatch",
            "janitor",
        )
    ]
    assert order == sorted(order)
    gate = [entry.task for entry in _points.entries_for(root, "gate", "gate")]
    # The freshness check last, so the merge it allows follows it as
    # closely as the forge can.
    assert "janitor" not in gate and gate[-1] == "workflow.release.check-fresh"


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


def test_a_job_keeps_one_trace_of_every_entry_it_ran(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The job's own timeline is the union of the work it did.

    Every entry writes its own trace and drops a copy in the job's box, so
    one file holds them all and the runner pushes that. The box is named to
    the entries through the environment the runner builds for them: writing
    this process's own from inside a task is ambient, which footman notes.
    """
    from livery.workshop import _traces

    root = _root(tmp_path)
    boxes: list[str] = []
    pushed: list[tuple[str, str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        boxes.append(env.get("FM_PROFILE_DIR", "<unset>"))
        # Each entry leaves a fragment, as a profiled child does.
        drop = Path(env["FM_PROFILE_DIR"])
        (drop / f"{len(boxes)}.json").write_text(
            json.dumps(
                {
                    "traceEvents": [
                        {
                            "ph": "X",
                            "cat": "task",
                            "name": argv[-1],
                            "pid": len(boxes),
                            "tid": 1,
                            "ts": 1_000_000.0 + len(boxes),
                            "dur": 10.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return 0

    def fake_push(
        root: Path,
        trace: Path,
        *,
        job: str = "",
        leg: str = "",
        run: object = None,
    ) -> str:
        # The label is an argument, not an environment read: this
        # process spawned the entries that carry it and is not one of
        # them, so its own environment never had it.
        assert leg == "gate"
        pushed.append((job, trace.read_text(encoding="utf-8")))
        return f"profile: {trace.stat().st_size} bytes pushed"

    monkeypatch.setattr(_traces, "push", fake_push)
    _points.run_point(root, "gate", "gate", spawn=green)
    # One box for the whole job, named to every entry.
    assert len(set(boxes)) == 1 and boxes[0] != "<unset>"
    assert not Path(boxes[0]).exists()  # and taken away with the job
    (job, text) = pushed[0]
    assert job == "gate"
    drawn = json.loads(text)["traceEvents"]
    assert [e["name"] for e in drawn] == [
        "workflow.release.check-title",
        "ci.metrics.collect",
        "speed.judge",
        "--needs=check,docs",
        "ci.verified.stamp",
        "workflow.release.check-fresh",
    ]
    assert min(e["ts"] for e in drawn) == 0.0  # the file starts at its own zero
    assert "pushed" in capsys.readouterr().out


def test_a_job_that_went_red_still_keeps_its_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A red job's timeline is the one most worth having."""
    from livery.footman import Failed
    from livery.workshop import _traces

    root = _root(tmp_path)
    pushed: list[str] = []

    def red(argv: list[str], env: dict[str, str]) -> int:
        drop = Path(env["FM_PROFILE_DIR"])
        (drop / "one.json").write_text(
            json.dumps(
                {
                    "traceEvents": [
                        {
                            "ph": "X",
                            "name": "up to here",
                            "pid": 1,
                            "tid": 1,
                            "ts": 5.0,
                            "dur": 1.0,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return 1

    def kept(
        root: Path,
        trace: Path,
        *,
        job: str = "",
        leg: str = "",
        run: object = None,
    ) -> str:
        pushed.append(job)
        return "profile: kept"

    monkeypatch.setattr(_traces, "push", kept)
    with pytest.raises(Failed):
        _points.run_point(root, "merge", "govern", spawn=red)
    assert pushed == ["govern"]


def test_a_workspace_that_keeps_no_traces_pays_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Off is zero cost: no box, no flag, no push, and no plugin loaded."""
    from livery.workshop import _traces

    root = _root(tmp_path, schedule="\n[ci]\nprofile = false\n")
    seen: list[list[str]] = []
    boxed: list[str] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append(argv)
        boxed.append(env.get("FM_PROFILE_DIR", "<unset>"))
        return 0

    def refuse(*args: object, **kwargs: object) -> str:
        raise AssertionError("a workspace that keeps no traces pushed one")

    monkeypatch.setattr(_traces, "push", refuse)
    _points.run_point(root, "merge", "govern", spawn=green)
    assert seen == [["fm", "workflow.configure", "--if-changed"]]
    assert boxed == ["<unset>"]


def test_a_job_only_an_entry_names_is_emitted_on_one_runner(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\njob = "scale"\ntask = "ci.scale"\n'
        '\n[[ci.schedule]]\npoint = "nightly"\njob = "scale"\ntask = "status"\n',
    )
    by_name = {point.name: point for point in _points.emitted_points(root)}
    nightly = by_name["nightly"]
    assert [job.name for job in nightly.jobs] == ["nightly", "scale"]
    model, added = nightly.jobs
    # One runner, no matrix; the point's own checkout and credential.
    assert added.matrix == ""
    assert (added.fetch, added.token, added.writes) == (
        model.fetch,
        model.token,
        model.writes,
    )
    # The shell the job's call runs is the one `jobs_of` lists.
    assert _points.jobs_of(root, "nightly") == ("nightly", "scale")


def test_an_entry_on_a_declared_or_inherited_job_adds_no_job(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "gate"\njob = "docs"\ntask = "docs.links"\n'
        '\n[[ci.schedule]]\npoint = "merge"\njob = "check"\ntask = "status"\n'
        '\n[[ci.schedule]]\npoint = "release"\ntask = "status"\n',
    )
    assert _points.emitted_points(root) == _points.points(root)
