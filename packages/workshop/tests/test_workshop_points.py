"""CI as points: refusals first, then the schedule and the runner."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# The site's jobs are the docs extension's: importing its task module
# contributes them to the builtin points, as the mount does.
import livery.extensions.docs._tasks  # noqa: F401
from livery.workshop import _points

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
    # A need on an inherited job, and an artifact an inherited job
    # publishes, are both satisfied.
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
    # docs extension's, in place once its tasks module is imported.
    assert [job.name for job in POINT_BY_NAME["gate"].jobs] == ["check", "gate"]
    from livery.extensions.docs import _tasks as docs_tasks
    from livery.workshop._points import point_by_name

    del docs_tasks
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


# --- the schedule -------------------------------------------------------------


def test_the_builtin_jobs_of_each_point(tmp_path: Path) -> None:
    root = _root(tmp_path)
    assert _points.jobs_of(root, "gate") == ("check", "docs", "gate")
    # The merge point inherits the gate's jobs and adds its own.
    assert _points.jobs_of(root, "merge") == (
        "check",
        "docs",
        "gate",
        "govern",
        "dispatch",
        "deploy",
    )
    # The nightly point runs the whole check, its own tests selected in.
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    assert _points.entries_for(root, "nightly", "nightly") == (
        _points.Entry("nightly", "nightly", "check"),
    )
    # The release's jobs are declared, with nothing scheduled on them
    # until the wave's verbs become entries; a point's own name is a
    # job of it too, green and empty until the contract attaches a
    # task.
    assert _points.jobs_of(root, "release") == ("wheels", "publish")
    # The wave's verbs are the release's entries, at the dispatched ref.
    assert [e.task for e in _points.entries_for(root, "release", "publish")] == [
        "workflow.release.publish"
    ]
    assert _points.entries_for(root, "release", "publish")[0].args == ("--ref={ref}",)
    assert _points.entries_for(root, "release", "release") == ()


def test_a_declared_entry_joins_its_point(tmp_path: Path) -> None:
    root = _root(
        tmp_path,
        '\n[[ci.schedule]]\npoint = "nightly"\ntask = "release.replay"\n'
        'args = ["--all"]\n'
        '\n[[ci.schedule]]\npoint = "gate"\njob = "docs"\ntask = "docs.links"\n',
    )
    assert _points.jobs_of(root, "nightly") == ("nightly",)
    # A job an entry names that no declaration has lists after the
    # declared ones.
    assert _points.jobs_of(root, "gate") == ("check", "docs", "gate")
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
    import livery.footman.api as footman

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
        [
            "hse",
            "--profile=fm-profile-coverage-leg.json",
            "coverage.leg",
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
        "--profile=fm-profile-coverage-leg.json",
        "coverage.leg",
        "--job=check",
    ]
    assert legs[-1] == "check-ubuntu-latest-3.14"
    monkeypatch.delenv("GITLAB_CI")
    monkeypatch.delenv("CI_PIPELINE_SOURCE")
    seen.clear()
    _points.run_point(root, "gate", "gate", spawn=green)
    # The render gate and the provenance check live in the gate job,
    # the one place a scoped leg cannot skip them.
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-workflow-release-check-title.json",
            "workflow.release.check-title",
        ],
        ["hse", "--profile=fm-profile-drift-check.json", "drift.check"],
        ["hse", "--profile=fm-profile-provenance.json", "provenance"],
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

    import livery.footman.api as footman

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
    _points.run_point(root, "release", "wheels", spawn=green)
    assert seen == [
        [
            "hse",
            "--profile=fm-profile-release-wheels.json",
            "release.wheels",
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


def test_the_nightly_point_runs_the_whole_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import livery.footman.api as footman

    monkeypatch.setattr(footman, "prog", lambda: "hse")
    root = _root(tmp_path)
    seen: list[tuple[list[str], str]] = []

    def green(argv: list[str], env: dict[str, str]) -> int:
        seen.append((argv, env.get("WORKSHOP_POINT", "<unset>")))
        return 0

    _points.run_point(root, "nightly", "nightly", python="3.14", spawn=green)
    assert seen == [(["hse", "--profile=fm-profile-check.json", "check"], "nightly")]


def test_the_merge_points_gate_job_ends_with_the_janitor_and_the_gates_does_not(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    merge = [entry.task for entry in _points.entries_for(root, "merge", "gate")]
    assert merge[-3:] == [
        "ci.verified.stamp",
        "workflow.release.check-fresh",
        "janitor",
    ]
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
        "drift.check",
        "provenance",
        "coverage.union",
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
    from livery.footman.api import Failed
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
